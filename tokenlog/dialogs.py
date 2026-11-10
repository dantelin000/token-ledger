"""Native Tk dialogs for portable source configuration."""
from __future__ import annotations

import re
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .adapters import SourceSpec, KIND_LABELS
from .settings import AppSettings
from .i18n import LANGUAGES, Translator
from .responsive import ScrollableFrame, fit_window


FORMATS = dict(KIND_LABELS)
BUILTINS = {"claude", "codex", "agy", "opencode", "gemini-cli", "cline", "roo", "cursor"}
GENERIC_KINDS = tuple(FORMATS)


def _center(window, parent, width, height):
    fit_window(window, width, height, parent)


class SourceEditor(tk.Toplevel):
    def __init__(self, parent, existing=None, taken=()):
        super().__init__(parent)
        self.ui_language = getattr(parent, "ui_language", "en")
        self.t = Translator(self.ui_language)
        self.formats = {key: self.t(value) for key, value in FORMATS.items()}
        self.title(self.t('编辑数据源') if existing else self.t('添加 Harness 数据源'))
        self.transient(parent)
        self.minsize(400, 300)
        self.result = None
        self.existing = existing
        self.taken = set(taken)
        self.paths = [str(path) for path in existing.roots] if existing else []
        self.name = tk.StringVar(value=existing.provider if existing else "")
        self.label = tk.StringVar(value=existing.label if existing else "")
        kind = existing.kind if existing else "tokenlog"
        self.kind = tk.StringVar(value=self.formats[kind])
        self.enabled = tk.BooleanVar(value=existing.enabled if existing else True)
        viewport = ScrollableFrame(self, padding=20)
        viewport.pack(fill="both", expand=True)
        body = viewport.content
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text=self.t('显示名称')).grid(row=0, column=0, sticky="w", padx=(0, 14), pady=5)
        label_entry = ttk.Entry(body, textvariable=self.label)
        label_entry.grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(body, text=self.t('数据源 ID')).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(body, textvariable=self.name, state="readonly" if existing else "normal").grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(body, text=self.t('例如 cursor、cline、my-harness；保存后 ID 保持不变。'), wraplength=440).grid(row=2, column=1, sticky="w", pady=(0, 8))
        ttk.Label(body, text=self.t('日志格式')).grid(row=3, column=0, sticky="w", pady=5)
        formats = [self.formats[k] for k in GENERIC_KINDS]
        if existing and existing.provider in BUILTINS:
            formats = [self.formats[existing.kind]]
        ttk.Combobox(body, values=formats, textvariable=self.kind, state="readonly").grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(body, text=self.t('启用扫描'), variable=self.enabled).grid(row=4, column=1, sticky="w", pady=6)
        ttk.Label(body, text=self.t('日志文件或文件夹')).grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 6))
        frame = ttk.Frame(body)
        frame.grid(row=6, column=0, columnspan=2, sticky="nsew")
        body.rowconfigure(6, weight=1)
        self.path_list = tk.Listbox(frame, height=6, selectmode="browse", exportselection=False)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.path_list.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.path_list.xview)
        self.path_list.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.path_list.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tools = ttk.Frame(body)
        tools.grid(row=7, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(tools, text=self.t('选择文件夹…'), command=self.choose_folder).pack(side="left", padx=(0, 8))
        ttk.Button(tools, text=self.t('选择文件…'), command=self.choose_file).pack(side="left", padx=(0, 8))
        ttk.Button(tools, text=self.t('移除路径'), command=self.remove_path).pack(side="left")
        self.help_label = ttk.Label(body, wraplength=550, justify="left")
        self.help_label.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 12))
        self.kind.trace_add("write", lambda *_: self.update_help())
        self.update_help()
        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text=self.t('取消'), command=self.destroy).pack(side="left", padx=8)
        ttk.Button(buttons, text=self.t('保存数据源'), command=self.accept).pack(side="left")
        self.refresh_paths()
        self.bind("<Escape>", lambda _: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        _center(self, parent, 680, 660)
        self.grab_set()
        label_entry.focus_set()

    def update_help(self):
        kind = next((k for k, label in self.formats.items() if label == self.kind.get()), "tokenlog")
        hints = {
            "claude": self.t('选择 Claude 配置根目录（例如 ~/.claude），程序读取其中 projects/ 下的 JSONL。'),
            "codex": self.t('选择 Codex 配置根目录（例如 ~/.codex），程序读取 sessions/ 和 archived_sessions/。'),
            "agy": self.t('选择 Antigravity 根目录，程序读取其中 conversations/ 下的 SQLite 会话库。'),
            "opencode": self.t('选择 OpenCode 数据目录（包含 opencode.db 或 storage/）；读取可确认的逐消息用量。'),
            "gemini_cli": self.t('选择 Gemini CLI 的 tmp/ 日志目录；读取其中 chats/ 下的 session JSON / JSONL。'),
            "cline": self.t('选择 Cline 的扩展存储根目录，读取 tasks/ 下的用量；缓存口径无法确认时会提示。'),
            "roo": self.t('选择 Roo Code 的扩展存储根目录，读取 tasks/ 下的用量；缓存口径无法确认时会提示。'),
            "cursor_csv": self.t('从 Cursor 官方 Usage 页面导出 CSV，选择该文件并启用。无需账号凭据；没有逐会话 ID 的行会归到导入分组。'),
        }
        text = hints.get(kind, self.t('选择该工具实际保存的 JSONL / JSON 用量日志；格式必须与文件中的 usage 字段一致。没有原始 usage 的历史记录无法精确补算。'))
        self.help_label.configure(text=text)

    def refresh_paths(self):
        self.path_list.delete(0, "end")
        for path in self.paths:
            self.path_list.insert("end", path)

    def choose_folder(self):
        value = filedialog.askdirectory(parent=self, title=self.t('选择本机日志目录'))
        if value and value not in self.paths:
            self.paths.append(value)
            self.refresh_paths()

    def choose_file(self):
        value = filedialog.askopenfilename(parent=self, title=self.t('选择本机用量日志'),
            filetypes=[(self.t('用量日志'), "*.jsonl *.json *.db *.csv"), (self.t('所有文件'), "*")])
        if value and value not in self.paths:
            self.paths.append(value)
            self.refresh_paths()

    def remove_path(self):
        chosen = self.path_list.curselection()
        if chosen:
            self.paths.pop(chosen[0])
            self.refresh_paths()

    def accept(self):
        name = self.name.get().strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) or name == "all":
            messagebox.showerror(self.t('数据源 ID 无效'), self.t('使用小写字母开头的字母、数字、下划线或连字符，最长 64 位。'), parent=self)
            return
        if name in self.taken and (not self.existing or name != self.existing.provider):
            messagebox.showerror(self.t('数据源重复'), self.t('这个数据源 ID 已存在。'), parent=self)
            return
        kind = next((k for k, label in self.formats.items() if label == self.kind.get()), None)
        try:
            candidate = SourceSpec.from_dict({"provider": name, "label": self.label.get().strip(),
                "kind": kind, "roots": self.paths, "enabled": bool(self.enabled.get())})
            if not candidate.roots:
                raise ValueError(self.t('请选择至少一个日志文件或文件夹。'))
        except (ValueError, TypeError) as exc:
            messagebox.showerror(self.t('请检查数据源'), self.t.message(str(exc)), parent=self)
            return
        self.result = candidate
        self.destroy()


class SourceManager(tk.Toplevel):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.ui_language = settings.language
        self.t = Translator(self.ui_language)
        self.formats = {key: self.t(value) for key, value in FORMATS.items()}
        self.title(self.t('数据源与偏好设置'))
        self.transient(parent)
        self.minsize(400, 300)
        self.result = None
        self.settings = AppSettings.from_dict(settings.to_dict())
        self.timezone = tk.StringVar(value=settings.timezone)
        self.interval = tk.StringVar(value=str(settings.interval))
        self.language = tk.StringVar(value=LANGUAGES[settings.language])
        viewport = ScrollableFrame(self, padding=20)
        viewport.pack(fill="both", expand=True)
        body = viewport.content
        ttk.Label(body, text=self.t('AI Harness 数据源'), font=("", 15, "bold")).pack(anchor="w")
        ttk.Label(body, text=self.t('内置工具自动查找本机日志；其他工具可通过通用 usage 格式接入。'), wraplength=680).pack(anchor="w", pady=(5, 16))
        table = ttk.Frame(body)
        table.pack(fill="both", expand=True)
        columns = ("label", "format", "state", "roots")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse", height=8)
        for name, text, width in (("label", self.t('工具'), 130), ("format", self.t('记录格式'), 175), ("state", self.t('状态'), 65), ("roots", self.t('本机路径'), 300)):
            self.tree.heading(name, text=text)
            self.tree.column(name, width=width, stretch=name == "roots")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _: self.edit_source())
        tools = ttk.Frame(body)
        tools.pack(fill="x", pady=(10, 16))
        for text, command in ((self.t('添加数据源…'), self.add_source), (self.t('编辑路径…'), self.edit_source),
                              (self.t('启用 / 停用'), self.toggle_source), (self.t('移除自定义源'), self.remove_source)):
            ttk.Button(tools, text=text, command=command).pack(side="left", padx=(0, 8))
        prefs = ttk.LabelFrame(body, text=self.t('显示与刷新'), padding=12)
        prefs.pack(fill="x", pady=(0, 12))
        ttk.Label(prefs, text=self.t('时区')).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Entry(prefs, textvariable=self.timezone, width=28).grid(row=0, column=1, sticky="ew")
        ttk.Label(prefs, text=self.t('local 使用本机时区；也可填写 IANA 名称。'), wraplength=270).grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Label(prefs, text=self.t('刷新间隔（秒）')).grid(row=1, column=0, sticky="w", pady=(9, 0), padx=(0, 12))
        ttk.Spinbox(prefs, from_=5, to=3600, textvariable=self.interval, width=10).grid(row=1, column=1, sticky="w", pady=(9, 0))
        ttk.Label(prefs, text="Language / 语言").grid(row=2, column=0, sticky="w", pady=(9, 0), padx=(0, 12))
        ttk.Combobox(prefs, textvariable=self.language, values=list(LANGUAGES.values()), state="readonly", width=26).grid(row=2, column=1, sticky="w", pady=(9, 0))
        ttk.Label(prefs, text=self.t('保存后立即生效。')).grid(row=2, column=2, sticky="w", padx=(12, 0), pady=(9, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Label(buttons, text=self.t('移除或停用数据源会保留账本内已采集的历史。'), wraplength=470).pack(side="left")
        ttk.Button(buttons, text=self.t('保存'), command=self.accept).pack(side="right")
        ttk.Button(buttons, text=self.t('取消'), command=self.destroy).pack(side="right", padx=8)
        self.refresh_sources()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _: self.destroy())
        _center(self, parent, 900, 760)
        self.grab_set()

    def refresh_sources(self, selected=None):
        self.tree.delete(*self.tree.get_children())
        for source in self.settings.sources:
            self.tree.insert("", "end", iid=source.provider, values=(self.t.source_label(source.provider, source.label),
                self.formats.get(source.kind, source.kind), self.t('启用') if source.enabled else self.t('停用'),
                "; ".join(str(path) for path in source.roots)))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)

    def selected(self):
        chosen = self.tree.selection()
        return next((source for source in self.settings.sources if chosen and source.provider == chosen[0]), None)

    def add_source(self):
        editor = SourceEditor(self, taken=[source.provider for source in self.settings.sources])
        self.wait_window(editor)
        self.grab_set()
        if editor.result:
            self.settings.sources.append(editor.result)
            self.refresh_sources(editor.result.provider)

    def edit_source(self):
        source = self.selected()
        if not source:
            return
        editor = SourceEditor(self, source, [s.provider for s in self.settings.sources])
        self.wait_window(editor)
        self.grab_set()
        if editor.result:
            self.settings.sources = [editor.result if s.provider == source.provider else s for s in self.settings.sources]
            self.refresh_sources(source.provider)

    def toggle_source(self):
        source = self.selected()
        if source:
            data = source.to_dict()
            data["enabled"] = not source.enabled
            updated = SourceSpec.from_dict(data)
            self.settings.sources = [updated if s.provider == source.provider else s for s in self.settings.sources]
            self.refresh_sources(source.provider)

    def remove_source(self):
        source = self.selected()
        if not source:
            return
        if source.provider in BUILTINS:
            messagebox.showinfo(self.t('保留内置采集器'), self.t('内置数据源可以停用或修改路径。'), parent=self)
            return
        self.settings.sources = [s for s in self.settings.sources if s.provider != source.provider]
        self.refresh_sources()

    def accept(self):
        try:
            raw = self.settings.to_dict()
            language = next(key for key, label in LANGUAGES.items() if label == self.language.get())
            raw.update(timezone=self.timezone.get().strip(), interval=int(self.interval.get()), language=language)
            if raw["timezone"] != "local":
                from zoneinfo import ZoneInfo
                ZoneInfo(raw["timezone"])
            self.result = AppSettings.from_dict(raw)
        except (ValueError, KeyError):
            messagebox.showerror(self.t('请检查偏好设置'), self.t('请输入有效时区（local 或 IANA 名称）和 5–3600 秒刷新间隔。'), parent=self)
            return
        self.destroy()


def show_source_manager(parent, settings: AppSettings) -> AppSettings | None:
    """Return a validated copy only when Save is clicked; never mutate caller settings."""
    dialog = SourceManager(parent, settings)
    parent.wait_window(dialog)
    return dialog.result
