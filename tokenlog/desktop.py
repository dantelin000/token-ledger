"""Native Tk desktop UI. The UI calls the ledger directly; no browser or server."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import hashlib
import colorsys

from . import __version__
from .adapters import AdapterRegistry
from .platforms import user_data_dir
from .settings import AppSettings, load_settings, save_settings
from .store import Ledger
from .i18n import LANGUAGES, Translator

BG = "#f3f5f8"
INK = "#192637"
MUTED = "#6a7789"
ACCENT = "#3367d6"
COLORS = ("#db8c6d", "#2b9e80", "#8e6bcc", "#4288d3", "#cc9b39", "#bb6595", "#5b9f9b", "#7d89bd")
SOURCE_COLORS = dict(zip(("claude","codex","agy","opencode","gemini-cli","cline","roo","cursor"), COLORS))


def source_color(provider):
    if provider in SOURCE_COLORS:
        return SOURCE_COLORS[provider]
    hue = int(hashlib.sha256(provider.encode()).hexdigest()[:8],16) / 0xffffffff
    rgb = colorsys.hls_to_rgb(hue,.45,.6)
    return "#"+"".join(f"{round(c*255):02x}" for c in rgb)


def number(value):
    return f"{int(value or 0):,}"


def short(value):
    return f"{value/1e9:.1f}B" if value >= 1e9 else f"{value/1e6:.1f}M" if value >= 1e6 else f"{value/1000:.0f}K" if value >= 1000 else str(value)


def display_time(value, tz=None):
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz).strftime("%m/%d %H:%M")


class Desktop:
    def __init__(self, root, ledger, settings, settings_path):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.ledger, self.settings, self.settings_path = root, ledger, settings, settings_path
        self.t = Translator(settings.language)
        root.ui_language = settings.language
        self.report = None
        self.results = queue.Queue()
        self.busy, self.pending, self.closing = False, False, False
        self.generation = 0
        self.timer = None
        self.period = tk.StringVar(value=self.t('全部历史'))
        self.provider = tk.StringVar(value=self.t('全部来源'))
        self.start = tk.StringVar(value=(date.today() - timedelta(days=6)).isoformat())
        self.end = tk.StringVar(value=date.today().isoformat())
        self.search = tk.StringVar()
        self.status = tk.StringVar(value=self.t('正在读取本机用量记录…'))
        self.provider_names = {self.t('全部来源'): "all"}
        self.metric_values = {}
        self.call_page = 0
        self.call_search = tk.StringVar()
        self.last_filters = {"start": None, "end": None, "provider": "all"}
        self._style()
        root.title(self.t('Token Ledger · 本机用量账本'))
        root.geometry("1300x900")
        root.minsize(1040, 710)
        root.configure(background=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._menu()
        self._build()
        self.search.trace_add("write", lambda *_: self.render_tables())
        self.call_search.trace_add("write", lambda *_: self.reset_calls())
        root.bind("<Command-r>" if sys.platform == "darwin" else "<Control-r>", lambda _: self.refresh())
        root.bind("<Command-e>" if sys.platform == "darwin" else "<Control-e>", lambda _: self.export())
        root.after(100, self._poll)
        root.after(150, self.refresh)

    def _style(self):
        style = self.ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        family = ("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI") if self.t.language == "zh_CN" else ("Helvetica Neue" if sys.platform == "darwin" else "Segoe UI")
        font = (family, 12 if sys.platform == "darwin" else 10)
        # An option-database *Font would override every ttk label's style font,
        # including the large totals and heading, so use ttk inheritance instead.
        style.configure(".", font=font, foreground=INK, background=BG)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background=BG)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Card.TLabel", background="white", foreground=MUTED)
        style.configure("Metric.TLabel", background="white", foreground=INK, font=("Arial", 25, "bold"))
        style.configure("Title.TLabel", font=(font[0], 23, "bold"), foreground=INK)
        style.configure("Section.TLabel", font=(font[0], 12, "bold"))
        style.configure("TButton", padding=(12, 7), background="white", borderwidth=1)
        style.map("TButton", background=[("active", "#e7eefc")])
        style.configure("Accent.TButton", foreground="white", background=ACCENT)
        style.map("Accent.TButton", background=[("active", "#2452b7")])
        style.configure("TCombobox", padding=5, fieldbackground="white")
        style.configure("TEntry", padding=5, fieldbackground="white")
        style.configure("Treeview", rowheight=33, background="white", fieldbackground="white", borderwidth=0, font=(font[0], 11))
        style.configure("Treeview.Heading", padding=(8, 9), background="#eaf0f7", font=(font[0], 10, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#dce9ff")], foreground=[("selected", INK)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 9))

    def _menu(self):
        menu = self.tk.Menu(self.root)
        file_menu = self.tk.Menu(menu, tearoff=False)
        file_menu.add_command(label=self.t('导出当前用量 CSV…'), command=self.export)
        file_menu.add_command(label=self.t('打开数据文件夹'), command=self.open_folder)
        file_menu.add_separator()
        file_menu.add_command(label=self.t('退出'), command=self.close)
        menu.add_cascade(label=self.t('文件'), menu=file_menu)
        view = self.tk.Menu(menu, tearoff=False)
        view.add_command(label=self.t('刷新统计'), command=self.refresh)
        view.add_command(label=self.t('数据源与设置…'), command=self.sources)
        view.add_command(label=self.t('API 对照单价…'), command=self.prices)
        menu.add_cascade(label=self.t('工具'), menu=view)
        help_menu = self.tk.Menu(menu, tearoff=False)
        help_menu.add_command(label=self.t('统计口径'), command=self.about)
        menu.add_cascade(label=self.t('帮助'), menu=help_menu)
        self.root.config(menu=menu)
        previous = getattr(self, "menu", None)
        self.menu = menu
        if previous is not None:
            previous.destroy()

    def _build(self):
        ttk = self.ttk
        outer = ttk.Frame(self.root, padding=(26, 20, 26, 12))
        self.content = outer
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(outer)
        top.pack(fill="x", pady=(0, 18))
        title = ttk.Frame(top)
        title.pack(side="left")
        ttk.Label(title, text="Token Ledger", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title, text=self.t('本机用量账本  ·  多 harness  ·  原生桌面'), style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Button(top, text=self.t('数据源与设置'), command=self.sources).pack(side="right", padx=(8, 0))
        ttk.Button(top, text=self.t('API 单价'), command=self.prices).pack(side="right", padx=(8, 0))
        ttk.Button(top, text=self.t('导出 CSV'), command=self.export).pack(side="right", padx=(8, 0))
        self.refresh_button = ttk.Button(top, text=self.t('刷新统计'), command=self.refresh, style="Accent.TButton")
        self.refresh_button.pack(side="right")
        filters = ttk.Frame(outer)
        filters.pack(fill="x", pady=(0, 16))
        ttk.Label(filters, text=self.t('时间范围')).pack(side="left", padx=(0, 8))
        periods = ttk.Combobox(filters, textvariable=self.period, state="readonly", width=12,
                              values=(self.t('今日'), self.t('近 7 天'), self.t('本月'), self.t('全部历史'), self.t('自定义')))
        periods.pack(side="left")
        periods.bind("<<ComboboxSelected>>", lambda _: self.filter_changed())
        self.start_entry = ttk.Entry(filters, textvariable=self.start, width=12, state="disabled")
        self.start_entry.pack(side="left", padx=(12, 5))
        ttk.Label(filters, text=self.t('至')).pack(side="left")
        self.end_entry = ttk.Entry(filters, textvariable=self.end, width=12, state="disabled")
        self.end_entry.pack(side="left", padx=5)
        ttk.Button(filters, text=self.t('应用日期'), command=self.refresh).pack(side="left", padx=(4, 15))
        ttk.Label(filters, text=self.t('来源')).pack(side="left", padx=(0, 8))
        self.provider_combo = ttk.Combobox(filters, textvariable=self.provider, state="readonly", width=22)
        self.provider_combo.pack(side="left")
        self.provider_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh(False))
        self._provider_options()
        metrics = ttk.Frame(outer)
        metrics.pack(fill="x", pady=(0, 16))
        for index, (key, label, note) in enumerate((
            ("total_tokens", self.t('已记录 Token'), self.t('输入 + 输出，按调用身份去重')),
            ("input_tokens", self.t('输入 Token'), self.t('已包含缓存读取与写入')),
            ("output_tokens", self.t('输出 Token'), self.t('已包含推理 Token')),
            ("api_estimate", self.t('预计 API 等价值 · USD'), self.t('价目快照估算，不是实际订阅账单')),
        )):
            metrics.columnconfigure(index, weight=1, uniform="metrics")
            card = ttk.Frame(metrics, style="Card.TFrame", padding=(17, 15))
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0 if index == 3 else 6))
            ttk.Label(card, text=label, style="Card.TLabel").pack(anchor="w")
            value = self.tk.StringVar(value="—")
            self.metric_values[key] = value
            label = ttk.Label(card, textvariable=value, style="Metric.TLabel")
            if key == "api_estimate":
                label.configure(font=("Arial",18,"bold"))
            label.pack(anchor="w", pady=(7,5))
            ttk.Label(card, text=note, style="Card.TLabel", font=("Arial", 9), wraplength=260).pack(anchor="w")
        self.price_note = self.tk.StringVar(value=self.t('正在按逐条记录核算 API 对照价值…'))
        ttk.Label(outer, textvariable=self.price_note, style="Muted.TLabel", wraplength=1200).pack(fill="x", pady=(0,10))
        analysis = ttk.Frame(outer)
        analysis.pack(fill="x", pady=(0, 16))
        analysis.columnconfigure(0, weight=1)
        analysis.columnconfigure(1, weight=1)
        left = ttk.Frame(analysis, style="Card.TFrame", padding=(14, 10))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        ttk.Label(left, text=self.t('各工具独立统计'), style="Card.TLabel").pack(anchor="w", pady=(0, 6))
        self.source_table = self._table(left, (
            ("label", self.t('Harness / 客户端'), 180, "w"), ("total", self.t('Token 合计'), 145, "e"),
            ("cost", self.t('API 等价值 USD'), 145, "e"), ("state", self.t('记录状态'), 90, "w")), height=5)
        self.source_table.bind("<Double-1>", self.select_source)
        right = ttk.Frame(analysis, style="Card.TFrame", padding=(14, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        ttk.Label(right, text=self.t('每日用量趋势 · Token（颜色表示客户端）'), style="Card.TLabel").pack(anchor="w")
        self.chart_legend = ttk.Frame(right, style="Card.TFrame")
        self.chart_legend.pack(fill="x", pady=(4,4))
        self.chart = self.tk.Canvas(right, height=165, background="white", highlightthickness=0)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda _: self.render_chart())
        self.chart.bind("<Motion>", self.chart_motion)
        self.chart.bind("<Leave>", lambda _: self.chart_hover.set(self.t('颜色表示 Harness / 客户端；移到柱上可查看分项。')))
        self.chart_hover = self.tk.StringVar(value=self.t('颜色表示 Harness / 客户端；移到柱上可查看分项。'))
        ttk.Label(right, textvariable=self.chart_hover, style="Card.TLabel", wraplength=460).pack(anchor="w")
        self.chart_note = self.tk.StringVar(value=self.t('正在读取记录…'))
        ttk.Label(right, textvariable=self.chart_note, style="Card.TLabel", font=("Arial", 9)).pack(anchor="w")
        tabs = ttk.Notebook(outer)
        self.tabs = tabs
        tabs.pack(fill="both", expand=True)
        sessions = ttk.Frame(tabs, padding=(0, 10, 0, 0))
        tabs.add(sessions, text=self.t('会话明细'))
        searchbar = ttk.Frame(sessions)
        searchbar.pack(fill="x", pady=(0, 8))
        ttk.Label(searchbar, text=self.t('搜索项目、模型或会话'), style="Muted.TLabel").pack(side="left", padx=(0, 12))
        ttk.Entry(searchbar, textvariable=self.search, width=35).pack(side="left")
        self.session_note = self.tk.StringVar(value="")
        ttk.Label(searchbar, textvariable=self.session_note, style="Muted.TLabel").pack(side="right")
        self.sessions_table = self._table(sessions, (
            ("provider", self.t('来源'), 120, "w"), ("project", self.t('项目 / 会话'), 210, "w"),
            ("model", self.t('模型'), 160, "w"), ("input", self.t('输入'), 115, "e"),
            ("output", self.t('输出'), 95, "e"), ("total", self.t('Token 合计'), 120, "e"), ("cost", self.t('API 等价值 USD'), 150, "e"),
            ("seen", self.t('最后记录'), 105, "w")), height=9)
        self.sessions_table.bind("<Double-1>", self.session_detail)
        call_tab = ttk.Frame(tabs, padding=(0,10,0,0))
        tabs.add(call_tab, text=self.t('逐次调用与估值'))
        tools = ttk.Frame(call_tab)
        tools.pack(fill="x", pady=(0,8))
        ttk.Label(tools,text=self.t('搜索模型、调用 ID、时间或项目')).pack(side="left",padx=(0,10))
        ttk.Entry(tools,textvariable=self.call_search,width=28).pack(side="left")
        self.call_note = self.tk.StringVar()
        ttk.Button(tools,text=self.t('下一页'),command=lambda:self.change_page(1)).pack(side="right")
        ttk.Button(tools,text=self.t('上一页'),command=lambda:self.change_page(-1)).pack(side="right",padx=6)
        ttk.Label(tools,textvariable=self.call_note,style="Muted.TLabel").pack(side="right",padx=10)
        self.calls_table = self._table(call_tab, (
            ("source",self.t('来源'),120,"w"),("time",self.t('时间'),110,"w"),("model",self.t('模型'),170,"w"),
            ("input",self.t('输入总计'),110,"e"),("read",self.t('缓存读取'),110,"e"),("write",self.t('缓存写入'),100,"e"),
            ("output",self.t('输出'),100,"e"),("reason",self.t('其中推理'),95,"e"),("cost",self.t('API 等价值 USD'),180,"e")), height=9)
        self.calls_table.bind("<Double-1>",self.call_detail)
        models = ttk.Frame(tabs, padding=(0, 10, 0, 0))
        tabs.add(models, text=self.t('模型用量'))
        self.models_table = self._table(models, (
            ("provider", self.t('来源'), 150, "w"), ("model", self.t('模型'), 250, "w"),
            ("input", self.t('输入'), 130, "e"), ("output", self.t('输出'), 110, "e"),
            ("cache", self.t('缓存读取'), 130, "e"), ("write", self.t('缓存写入'), 110, "e"),
            ("reasoning", self.t('其中推理'), 100, "e"), ("total", self.t('Token 合计'), 135, "e"), ("cost",self.t('API 等价值 USD'),150,"e")), height=10)
        coverage = ttk.Frame(tabs, padding=12)
        self.coverage_tab = coverage
        tabs.add(coverage, text=self.t('数据覆盖与口径'))
        self.coverage_text = self.tk.Text(coverage, wrap="word", background="white", foreground=INK,
            relief="flat", padx=16, pady=14, font=("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI", 11))
        self.coverage_text.pack(fill="both", expand=True)
        self.coverage_text.config(state="disabled")
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=75)
        self.progress.pack(side="left", padx=(0, 10))
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, text=self.t('v{0}  ·  本机保存 / 原始记录只读', __version__), style="Muted.TLabel").pack(side="right")

    def _table(self, parent, columns, height):
        from tkinter.font import Font
        frame = self.ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = self.ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height)
        heading_font = Font(root=self.root, font=self.ttk.Style(self.root).lookup("Treeview.Heading", "font"))
        for key, title, width, anchor in columns:
            tree.heading(key, text=title)
            minimum = max(70, heading_font.measure(title) + 24)
            tree.column(key, width=max(width, minimum), minwidth=minimum, anchor=anchor, stretch=True)
        vertical = self.ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = self.ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree.tag_configure("odd", background="#f7f9fc")
        return tree

    def _provider_options(self):
        self.provider_names = {self.t('全部来源'): "all", **{f"{self.t.source_label(pid, label)} ({pid})": pid for pid, label in self.ledger.registry.labels.items()}}
        if self.report:
            for item in self.report["providers"]:
                if item["provider"] not in self.provider_names.values():
                    self.provider_names[f"{self.t.source_label(item['provider'], item['label'])} ({item['provider']})"] = item["provider"]
        self.provider_combo.configure(values=list(self.provider_names))
        if self.provider.get() not in self.provider_names:
            self.provider.set(self.t('全部来源'))

    def filters(self):
        current = datetime.now(self.ledger.tz).date() if self.ledger.tz else date.today()
        period = self.period.get()
        start = end = None
        if period == self.t('今日'):
            start = end = current.isoformat()
        elif period == self.t('近 7 天'):
            start, end = (current - timedelta(days=6)).isoformat(), current.isoformat()
        elif period == self.t('本月'):
            start, end = current.replace(day=1).isoformat(), current.isoformat()
        elif period == self.t('自定义'):
            start, end = self.start.get().strip(), self.end.get().strip()
        result = {"start": start, "end": end, "provider": self.provider_names.get(self.provider.get(), "all")}
        self.ledger._bounds(start, end)
        return result

    def filter_changed(self):
        state = "normal" if self.period.get() == self.t('自定义') else "disabled"
        self.start_entry.config(state=state)
        self.end_entry.config(state=state)
        self.refresh(False)

    def refresh(self, scan=True):
        from tkinter import messagebox
        if self.closing:
            return
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        try:
            filters = self.filters()
        except (ValueError, OverflowError):
            messagebox.showerror(self.t('日期范围'), self.t('请输入有效的 YYYY-MM-DD 日期，开始日期应不晚于结束日期。'), parent=self.root)
            return
        if self.busy:
            self.pending = True
            return
        self.last_filters = filters
        self.busy = True
        generation, ledger = self.generation, self.ledger
        self.refresh_button.config(state="disabled")
        self.progress.start(12)
        self.status.set(self.t('正在扫描本机记录…') if scan else self.t('正在读取统计…'))
        def work():
            try:
                if scan:
                    ledger.sync()
                self.results.put((generation, ledger.report(**filters, include_calls=True), None))
            except Exception as exc:
                self.results.put((generation, None, type(exc).__name__))
        threading.Thread(target=work, daemon=True, name="tokenledger-scan").start()

    def _poll(self):
        if self.closing:
            return
        try:
            generation, report, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.progress.stop()
            self.refresh_button.config(state="normal")
            if generation == self.generation:
                if error:
                    self.status.set(self.t('读取失败（{0}），已保留账本；可点击刷新重试。', error))
                else:
                    self.report = report
                    self.render()
            else:
                self.pending = True
            if self.pending:
                self.pending = False
                self.root.after(20, self.refresh)
            else:
                self.timer = self.root.after(self.settings.interval * 1000, self.refresh)
        self.root.after(100, self._poll)

    def render(self):
        report = self.report
        for key, var in self.metric_values.items():
            var.set(self.t.money(report["totals"].get("api_estimate")) if key == "api_estimate" else number(report["totals"][key]))
        priced = report["totals"].get("api_estimate",{})
        self.price_note.set(self.t('按 {0} 价目快照 · USD · 已估值 {1} 条 / 未定价 {2} 条；缓存时长不全时显示范围。', report['pricing']['as_of'], number(priced.get('priced_records', 0)), number(priced.get('unpriced_records', 0))))
        self._provider_options()
        self.source_table.delete(*self.source_table.get_children())
        self.source_rows = {}
        for index, source in enumerate(report["providers"]):
            known = source["status"] == "ok" or source["sessions"] > 0
            state = self.t('已记录') if source["status"] == "ok" else self.t('已停用') if source["status"] == "disabled" else self.t('无可用用量') if source["status"] == "no_usage" else self.t('未找到记录')
            iid = self.source_table.insert("", "end", values=(self.t.source_label(source["provider"], source["label"]), number(source["total_tokens"]) if known else "—",
                self.t.money(source.get("api_estimate")) if known else "—", state), tags=("odd",) if index % 2 else ())
            self.source_rows[iid] = source["provider"]
        self.render_chart()
        self.render_tables()
        self.render_calls()
        coverage = report["coverage"]
        text = [self.t('只统计本机有实际 usage 的用量记录。'), "",
            self.t('当前已采集 {0} 个来源文件，时间覆盖 {1} 至 {2}。', number(coverage['files']), coverage['first_seen'] or '—', coverage['last_seen'] or '—'),
            self.t('统计时区：{0}。', self.t('系统本地时区') if report['timezone'] == 'local' else report['timezone']),
            "", *map(self.t.message, coverage["notes"]), "", self.t('来源状态：')]
        for item in report["providers"]:
            label = self.t.source_label(item["provider"], item["label"])
            warning = self.t.message(item.get("warning")) or str(item.get("files", 0)) + self.t(' 个可读取的日志文件')
            text.append(f"  {label}: {warning}")
        if coverage["errors"]:
            text.extend(["", self.t('{0} 项读取提示：', len(coverage['errors'])), *map(self.t.message, coverage["errors"])])
        text.extend(["", self.t('API 等价值：'), self.t.message(report["pricing"]["basis"]), *map(self.t.message, report["pricing"]["notes"]),
            self.t('官方价目核验日期：')+report["pricing"]["as_of"], *report["pricing"]["sources"]])
        if report["pricing"]["unpriced_models"]:
            text.extend(["", self.t('尚无可靠单价的模型（不纳入金额）：'), *[self.t('  {0}: {1} 条', model, count) for model,count in report["pricing"]["unpriced_models"].items()]])
        text.extend(["", self.t('账本路径：{0}', self.ledger.db), "", self.t('支持的 harness 与导入格式可在“数据源与设置”查看。没有 token 字段的记录无法精确补算。')])
        self.coverage_text.config(state="normal")
        self.coverage_text.delete("1.0", "end")
        self.coverage_text.insert("1.0", "\n".join(text))
        self.coverage_text.config(state="disabled")
        count = len(coverage["errors"])
        self.coverage_tab.master.tab(self.coverage_tab, text=self.t('数据覆盖与口径{0}', self.t(' · {0} 项提示', count) if count else ''))
        updated = display_time(report["sync"]["last_sync"], self.ledger.tz)
        self.status.set(self.t.message(report["sync"].get("error")) or self.t('{0} 个会话 · {1} 条记录  |  最近同步 {2}  |  每 {3} 秒刷新', number(report['totals']['sessions']), number(report['totals']['requests']), updated, self.settings.interval))

    def render_tables(self):
        if not self.report:
            return
        labels = {p["provider"]: self.t.source_label(p["provider"], p["label"]) for p in self.report["providers"]}
        query = self.search.get().strip().lower()
        rows = [r for r in self.report["sessions"] if not query or query in " ".join(str(r[k]) for k in ("project", "model", "session_id", "provider")).lower()]
        self.sessions_table.delete(*self.sessions_table.get_children())
        self.session_rows = {}
        for index, row in enumerate(rows[:1000]):
            name = str(row["project"]).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            iid = self.sessions_table.insert("", "end", values=(labels.get(row["provider"], row["provider"]), name or "—", row["model"],
                number(row["input_tokens"]), number(row["output_tokens"]), number(row["total_tokens"]), self.t.money(row.get("api_estimate")), display_time(row["last_seen"], self.ledger.tz)), tags=("odd",) if index % 2 else ())
            self.session_rows[iid] = row
        self.session_note.set(self.t('{0} 个会话 · 双击查看明细', len(rows)) + (self.t(' · 显示前 1000 条') if len(rows) > 1000 else ""))
        self.models_table.delete(*self.models_table.get_children())
        for index, row in enumerate(self.report["models"]):
            self.models_table.insert("", "end", values=(labels.get(row["provider"], row["provider"]), row["model"],
                *(number(row[k]) for k in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens", "reasoning_tokens", "total_tokens")),self.t.money(row.get("api_estimate"))), tags=("odd",) if index % 2 else ())

    def reset_calls(self):
        self.call_page = 0
        self.render_calls()

    def change_page(self, step):
        self.call_page = max(0,self.call_page+step)
        self.render_calls()

    def render_calls(self):
        if not self.report:
            return
        q = self.call_search.get().strip().lower()
        labels = {p['provider']:self.t.source_label(p['provider'],p['label']) for p in self.report['providers']}
        rows = [r for r in self.report.get('calls',[]) if not q or q in ' '.join(str(r.get(k,'')) for k in ('model','event_key','timestamp','project','session_id','provider')).lower()]
        pages = max(1,(len(rows)+199)//200)
        self.call_page = min(self.call_page,pages-1)
        self.call_note.set(self.t('{0:,} 条 · 第 {1}/{2} 页 · 双击明细', len(rows), self.call_page + 1, pages))
        self.calls_table.delete(*self.calls_table.get_children())
        self.call_rows = {}
        for i,r in enumerate(rows[self.call_page*200:(self.call_page+1)*200]):
            source=labels.get(r['provider'],r['provider'])
            if r['method']=='counter_delta':
                source+=self.t('（累计增量）')
            iid=self.calls_table.insert('','end',values=(source,display_time(r['timestamp'],self.ledger.tz),r['model'],
                *(number(r[k]) for k in ('input_tokens','cached_input_tokens','cache_write_tokens','output_tokens','reasoning_tokens')),
                self.t.money(r.get('api_estimate'),6)),tags=('odd',) if i%2 else ())
            self.call_rows[iid]=r

    def call_detail(self, _event=None):
        selected=self.calls_table.selection()
        row=self.call_rows.get(selected[0]) if selected else None
        if row:
            from .price_ui import show_detail
            show_detail(self.root,row)

    def render_chart(self):
        canvas = self.chart
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if not self.report:
            return
        daily = self.report["daily"]
        active = sorted({row['provider'] for row in daily})
        names = {p['provider']:self.t.source_label(p['provider'],p['label']) for p in self.report['providers']}
        signature=tuple((pid,names.get(pid,pid)) for pid in active)
        if getattr(self,'_legend_signature',None) != signature:
            self._legend_signature=signature
            for widget in self.chart_legend.winfo_children():
                widget.destroy()
            for index,pid in enumerate(active):
                part=self.ttk.Frame(self.chart_legend,style='Card.TFrame')
                part.grid(row=index//2,column=index%2,sticky='w',padx=(0,16),pady=1)
                square=self.tk.Canvas(part,width=11,height=11,background='white',highlightthickness=0)
                square.create_rectangle(1,1,10,10,fill=source_color(pid),outline='')
                square.pack(side='left',padx=(0,5))
                self.ttk.Label(part,text=names.get(pid,pid),style='Card.TLabel').pack(side='left')
        # Legend data does not depend on the canvas being mapped yet. Windows
        # can deliver its first usable canvas size after the initial render.
        if width < 40:
            return
        if not daily:
            self.chart_buckets=[]
            self.chart_hover.set(self.t('所选时期没有已记录用量。'))
            canvas.create_text(width / 2, height / 2, text=self.t('所选范围没有已记录用量'), fill=MUTED)
            self.chart_note.set(self.t('可调整日期或在数据源设置中接入更多 harness'))
            return
        grouped = defaultdict(lambda: defaultdict(int))
        for row in daily:
            grouped[row["date"]][row["provider"]] += row["total_tokens"]
        start, end = date.fromisoformat(min(grouped)), date.fromisoformat(max(grouped))
        days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        bucket_size = max(1, (len(days) + 69) // 70)
        buckets = []
        self.chart_bucket_ends={}
        for i in range(0, len(days), bucket_size):
            counts = defaultdict(int)
            for day in days[i:i+bucket_size]:
                for provider, value in grouped[day].items():
                    counts[provider] += value
            buckets.append((days[i], counts))
            self.chart_bucket_ends[days[i]]=days[min(len(days)-1,i+bucket_size-1)]
        maximum = max(1, *(sum(b[1].values()) for b in buckets))
        left, top, bottom = 43, 14, height - 24
        plot_width, plot_height = width - left - 10, max(1, bottom - top)
        for i in range(3):
            y = top + plot_height * i / 2
            canvas.create_line(left, y, width - 5, y, fill="#e7edf4", dash=(3, 4))
            canvas.create_text(left - 6, y, text=short(int(maximum * (1 - i/2))), anchor="e", fill=MUTED, font=("Arial", 8))
        self.chart_buckets=buckets
        self.chart_bounds=(left,top,plot_width,plot_height)
        for index, (day, counts) in enumerate(buckets):
            x = left + index * plot_width / len(buckets)
            bar = max(1, plot_width / len(buckets) * .65)
            y = bottom
            for provider, value in counts.items():
                h = value / maximum * plot_height
                color = source_color(provider)
                canvas.create_rectangle(x, y-h, x+bar, y, fill=color, outline="")
                y -= h
        for i in {0, len(buckets)//2, len(buckets)-1}:
            canvas.create_text(left + (i+.5) * plot_width / len(buckets), height - 9,
                text=buckets[i][0][5:], fill=MUTED, font=("Arial", 8))
        peak = max(sum(v.values()) for v in grouped.values())
        self.chart_note.set(self.t('{0} — {1}  ·  单日最高 {2}', start, end, number(peak)) + (self.t('  ·  每 {0} 天合并', bucket_size) if bucket_size > 1 else ""))

    def chart_motion(self,event):
        if not getattr(self,'chart_buckets',None):
            return
        left,top,width,height=self.chart_bounds
        if not left <= event.x <= left+width or not top <= event.y <= top+height:
            return
        index=min(len(self.chart_buckets)-1,int((event.x-left)/width*len(self.chart_buckets)))
        day,counts=self.chart_buckets[index]
        names={p['provider']:self.t.source_label(p['provider'],p['label']) for p in self.report['providers']}
        end=self.chart_bucket_ends.get(day,day)
        period=day if end==day else day+self.t(' 至 ')+end
        self.chart_hover.set(period+' · '+' / '.join(f"{names.get(k,k)} {number(v)}" for k,v in counts.items()))

    def select_source(self, _event=None):
        selected = self.source_table.selection()
        pid = self.source_rows.get(selected[0]) if selected else None
        if pid:
            for name, value in self.provider_names.items():
                if value == pid:
                    self.provider.set(name)
                    self.refresh(False)
                    break

    def session_detail(self, _event=None):
        selected = self.sessions_table.selection()
        row = self.session_rows.get(selected[0]) if selected else None
        if row:
            from .price_ui import show_detail
            show_detail(self.root,row,aggregate=True)

    def prices(self):
        from .price_ui import PriceEditor
        models = [r['model'] for r in self.report.get('models',[])] if self.report else []
        PriceEditor(self.root,self.ledger.pricebook,models,on_save=lambda:self.refresh(False))

    def sources(self):
        from .dialogs import SourceManager
        dialog = SourceManager(self.root, self.settings)
        # Return from menu callbacks immediately. A nested wait_window can block
        # macOS accessibility menu actions until the dialog closes.
        def closed(event):
            if event.widget is dialog and dialog.result is not None:
                self.root.after_idle(lambda: self.apply_settings(dialog.result))
        dialog.bind("<Destroy>", closed, add="+")

    def apply_settings(self, result):
        from tkinter import messagebox
        try:
            from .platforms import resolve_timezone
            resolve_timezone(result.timezone)
            registry = AdapterRegistry(include_builtin=False, extra_sources=result.sources)
            sources_changed = (result.timezone != self.settings.timezone or
                               [s.to_dict() for s in result.sources] != [s.to_dict() for s in self.settings.sources])
            save_settings(result, self.settings_path)
            changed_language = result.language != self.settings.language
            self.settings = result
            if sources_changed:
                self.generation += 1
                self.ledger = Ledger(self.ledger.db, self.ledger.codex_home, self.ledger.claude_home,
                                     result.timezone, registry=registry)
            if changed_language:
                self.retranslate(result.language)
            else:
                self._provider_options()
            self.refresh(scan=sources_changed)
        except (ValueError, OSError, KeyError) as exc:
            messagebox.showerror(self.t('无法保存设置'), self.t.message(str(exc)), parent=self.root)

    def retranslate(self, language):
        """Rebuild presentation while preserving filters, queries and selection."""
        labels = ("今日", "近 7 天", "本月", "全部历史", "自定义")
        period = next((key for key in labels if self.period.get() == self.t(key)), "全部历史")
        provider = self.provider_names.get(self.provider.get(), "all")
        tab = self.tabs.index(self.tabs.select())
        self.content.destroy()
        self.t = Translator(language)
        self.root.ui_language = language
        self.period.set(self.t(period))
        self.provider.set(self.t('全部来源'))
        self.root.title(self.t('Token Ledger · 本机用量账本'))
        self.metric_values = {}
        self._legend_signature = None
        self._style()
        self._menu()
        self._build()
        for label, value in self.provider_names.items():
            if value == provider:
                self.provider.set(label)
                break
        self.tabs.select(tab)
        state = "normal" if period == "自定义" else "disabled"
        self.start_entry.config(state=state)
        self.end_entry.config(state=state)
        if self.report:
            self.render()
        if self.busy:
            self.refresh_button.config(state="disabled")
            self.progress.start(12)
        for window in self.root.winfo_children():
            if isinstance(window, self.tk.Toplevel) and hasattr(window, "retranslate"):
                window.retranslate(language)

    def export(self):
        from tkinter import filedialog, messagebox
        try:
            filters = self.filters()
            path = filedialog.asksaveasfilename(parent=self.root, title=self.t('导出当前日期与来源的用量记录'),
                defaultextension=".csv", initialfile=f"token-usage-{date.today()}.csv", filetypes=((self.t('CSV 文件'), "*.csv"),))
            if path:
                Path(path).write_text(self.ledger.export_csv(**filters), encoding="utf-8", newline="")
                self.status.set(self.t('已导出：{0}', path))
        except (ValueError, OSError):
            messagebox.showerror(self.t('无法导出'), self.t('请检查日期范围和文件保存位置。'), parent=self.root)

    def open_folder(self):
        folder = self.settings_path.parent
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        elif sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def about(self):
        from tkinter import messagebox
        messagebox.showinfo("Token Ledger", self.t('Token Ledger {0}\n\n原生桌面界面，直接读取本地用量账本，无需浏览器或 Web 服务。\n\n输入包含缓存，输出包含推理；子计数不能再次相加。\n不同 harness 通过数据源适配器接入。没有 usage 的记录无法精确补算。\n\n账号订阅额度及其他设备用量不在此范围。', __version__), parent=self.root)

    def close(self):
        self.closing = True
        if self.timer:
            self.root.after_cancel(self.timer)
        self.root.destroy()


def migrate_ledger(source, destination):
    """Copy a legacy ledger through SQLite backup; never move or alter its source."""
    if not source or not Path(source).is_file() or destination.exists():
        return
    from contextlib import closing
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def main(argv=None):
    t = Translator()
    parser = argparse.ArgumentParser(description=t('Token Ledger 原生桌面版 · macOS / Windows'))
    parser.add_argument("--data-dir", type=Path, default=user_data_dir())
    parser.add_argument("--db", type=Path)
    parser.add_argument("--timezone")
    parser.add_argument("--interval", type=int)
    parser.add_argument("--language", choices=tuple(LANGUAGES), help="UI language: en or zh_CN")
    parser.add_argument("--migrate-from", type=Path)
    parser.add_argument("--self-test", action="store_true", help=t('验证打包后的依赖与原生窗口，不扫描任何私人日志'))
    args = parser.parse_args(argv)
    import tkinter as tk
    from tkinter import messagebox
    if args.self_test:
        root = tk.Tk()
        root.withdraw()
        from . import agy, generic, dialogs, adapters
        root.update_idletasks()
        result = {"app": "TokenLedger", "version": __version__, "platform": sys.platform,
                  "native_ui": "tkinter", "tk": root.tk.call("info", "patchlevel"), "adapters": list(adapters.KINDS),
                  "pricing_as_of": __import__('tokenlog.pricing',fromlist=['AS_OF']).AS_OF,
                  "default_language": "en", "languages": list(LANGUAGES)}
        root.destroy()
        if sys.stdout is not None:
            print(json.dumps(result), flush=True)
        return
    root = tk.Tk()
    root.withdraw()
    try:
        settings_path = args.data_dir / "settings.json"
        settings = load_settings(settings_path)
        if args.language:
            settings.language = args.language
        t = Translator(settings.language)
        if args.timezone:
            settings.timezone = args.timezone
        if args.interval is not None:
            settings.interval = args.interval
        settings = AppSettings.from_dict(settings.to_dict())
        db = args.db or args.data_dir / "token-usage.sqlite3"
        legacy = args.migrate_from
        if not legacy and not getattr(sys, "frozen", False):
            legacy = Path(__file__).resolve().parent.parent / "data/token-usage.sqlite3"
        migrate_ledger(legacy, db)
        registry = AdapterRegistry(include_builtin=False, extra_sources=settings.sources)
        ledger = Ledger(db, Path.home()/".codex", Path.home()/".claude", settings.timezone, registry)
        save_settings(settings, settings_path)
        app = Desktop(root, ledger, settings, settings_path)
        root.deiconify()
        root.mainloop()
    except Exception as exc:
        messagebox.showerror(t('Token Ledger 无法启动'), t('{0}: {1}\n\n现有日志和账本会保留。', type(exc).__name__, t.message(str(exc))), parent=root)
        root.destroy()
        raise


if __name__ == "__main__":
    main()
