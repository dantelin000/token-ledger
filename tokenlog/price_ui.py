"""Native price table and copyable per-call cost explanations."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import tkinter as tk
from tkinter import ttk, messagebox

from .pricing import AS_OF
from .i18n import Translator
from .responsive import ScrollableFrame, fit_window


class PriceEditor(tk.Toplevel):
    def __init__(self, parent, book, observed=(), on_save=None):
        super().__init__(parent)
        self.t = Translator(getattr(parent, "ui_language", "en"))
        self.book, self.on_save = book, on_save
        self.observed = tuple(observed)
        self.minsize(400, 300)
        self.transient(parent)
        self._build()
        fit_window(self, 840, 690, parent)

    def _build(self):
        self.title(self.t('API 对照单价 · USD / 百万 token'))
        viewport = ScrollableFrame(self, padding=22)
        viewport.pack(fill="both", expand=True)
        body = viewport.content
        ttk.Label(body, text=self.t('预计 API 等价值的对照单价'), font=("Arial", 17, "bold")).pack(anchor="w")
        ttk.Label(body, text=self.t('官方价目核验日期 {0} · Standard / 全局路由 · USD / 1,000,000 token', AS_OF), wraplength=740).pack(anchor="w", pady=(6,15))
        self.model = tk.StringVar()
        ttk.Label(body, text=self.t('模型 ID（可选择已记录的模型或输入新的 ID）')).pack(anchor="w")
        choose = ttk.Combobox(body, textvariable=self.model, values=sorted(set(self.book.rates)|set(self.observed)))
        choose.pack(fill="x", pady=(5,15))
        choose.bind("<<ComboboxSelected>>", lambda _: self.load_model())
        fields = ttk.Frame(body)
        fields.pack(fill="x")
        self.values = {}
        for index, (key, label) in enumerate((("input",self.t('普通输入')),("read",self.t('缓存读取')),
            ("write_short",self.t('缓存写入：短时')),("write_long",self.t('缓存写入：长时')),("output",self.t('输出（含推理）')))):
            ttk.Label(fields, text=label).grid(row=index, column=0, sticky="w", pady=7, padx=(0,20))
            value = tk.StringVar()
            self.values[key] = value
            ttk.Entry(fields, textvariable=value, width=20).grid(row=index, column=1, sticky="w", pady=7)
            ttk.Label(fields, text=self.t('USD / 百万 token')).grid(row=index, column=2, sticky="w", padx=12)
        self.source = tk.StringVar(value=self.t('选择模型以查看官方来源。'))
        ttk.Label(body, textvariable=self.source, wraplength=740, justify="left").pack(fill="x", pady=(15,8))
        ttk.Label(body, text=self.t('保存会覆盖这个模型的对照价，并明确标为“用户自定义”。\n如果模型有长上下文档位，自定义固定价不会自动套用该档位。缓存时长缺失时按短／长单价显示范围。'), wraplength=740, justify="left").pack(fill="x", pady=(0,15))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text=self.t('关闭'), command=self.destroy).pack(side="right")
        ttk.Button(buttons, text=self.t('保存自定义单价'), command=self.save).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _: self.destroy())
        if self.observed:
            self.model.set(next(iter(self.observed)))
        elif self.book.rates:
            self.model.set(next(iter(self.book.rates)))
        self.load_model()

    def retranslate(self, language):
        model = self.model.get()
        edited = {key: value.get() for key, value in self.values.items()}
        for child in self.winfo_children():
            child.destroy()
        self.t = Translator(language)
        self._build()
        self.model.set(model)
        self.load_model()
        for key, value in edited.items():
            self.values[key].set(value)

    def load_model(self):
        rate = self.book.rates.get(self.model.get())
        for key, value in self.values.items():
            value.set(getattr(rate, key) if rate else "")
        if rate:
            extra = self.t(' · {0} / {1}写入', self.t.message(rate.short_ttl), self.t.message(rate.long_ttl)) if rate.write_short != rate.write_long else ""
            self.source.set(self.t('来源：{0}\n核验／设置日期：{1}{2}', self.t.message(rate.source), rate.verified_at, extra) +
                (self.t('\n输入超过 {0:,} token 时有长上下文档位，正式估值逐次判断。', rate.threshold) if rate.threshold else ""))
        else:
            self.source.set(self.t('此模型尚无已核验单价；请依据供应商 API 价目填写，程序不会猜测代理／内部模型的映射。'))

    def save(self):
        try:
            values = {k:v.get().strip() for k,v in self.values.items()}
            values["verified_at"] = date.today().isoformat()
            self.book.save_override(self.model.get(), values)
        except (ValueError, ArithmeticError, KeyError, OSError):
            messagebox.showerror(self.t('请检查单价'), self.t('请输入模型 ID 和五项非负数字；请勿使用千位逗号或货币符号。'), parent=self)
            return
        self.load_model()
        if self.on_save:
            self.on_save()


def detail_text(row, *, aggregate=False, language="en"):
    t = Translator(language)
    q = row.get("api_estimate", {})
    fmt = lambda x: f"{int(x or 0):,}"
    inp = row["input_tokens"]
    read, write, out = (row[k] for k in ("cached_input_tokens","cache_write_tokens","output_tokens"))
    ordinary = inp-read-write
    lines = [t('来源：{0}', row['provider']), t('模型：{0}', row['model']), t('会话：{0}', row['session_id']),
        t('项目：{0}', row['project'])]
    if not aggregate:
        lines += [t('用量记录 ID：{0}', row['event_key']), t('时间：{0}', row['timestamp'])]
    lines += ["", t('输入总计：{0} = 普通 {1} + 缓存读取 {2} + 缓存写入 {3}', fmt(inp), fmt(ordinary), fmt(read), fmt(write)),
        t('输出总计：{0}（其中推理 {1}，已包含，不再加一次）', fmt(out), fmt(row['reasoning_tokens'])),
        t('Token 合计：{0}', fmt(row['total_tokens'])), "", t('预计 API 等价值：{0} USD', t.money(q, 6))]
    if aggregate:
        lines += [t('会话金额为逐条估值后求和，不能用会话总输入判断上下文档位。'),
            t('已估值 {0} 条；未估值 {1} 条。', q.get('priced_records', 0), q.get('unpriced_records', 0))]
    elif q.get("low_usd") is not None:
        lines += ["", t('分项                     Token          单价 USD / 百万          等价值 USD')]
        counts = {"ordinary_input": ordinary, "cache_read": read, "cache_write": write, "output": out}
        names = {"ordinary_input":t('普通输入'), "cache_read":t('缓存读取'), "cache_write":t('缓存写入'), "output":t('输出（含推理）')}
        rate_keys = {"ordinary_input":"input","cache_read":"read","output":"output"}
        for key in counts:
            lo = Decimal(q["components_low"][key]); hi = Decimal(q["components_high"][key])
            cost = f"{lo:.8f}" if lo == hi else f"{lo:.8f}–{hi:.8f}"
            if key in rate_keys:
                a=q["rates_low"][rate_keys[key]]; b=q["rates_high"][rate_keys[key]]
                unit = a if a == b else f"{a}–{b}"
            else:
                unit = t('短时 {0} / 长时 {1}', q['rates_low']['write_short'], q['rates_high']['write_long'])
            lines.append(f"{names[key]:<14}  {fmt(counts[key]):>12}    {unit:>22}    {cost}")
        short, long = row.get("cache_write_short_tokens",0), row.get("cache_write_long_tokens",0)
        if write:
            lines.append(t('写入 TTL 记录：短时 {0}；长时 {1}；未记录时长 {2}。', fmt(short), fmt(long), fmt(write - short - long)))
        lines += ["", t('单价来源：{0}', t.message(q['source'])), t('核验／设置日期：{0}', q['verified_at'])]
    lines += ["", *map(t.message, q.get("notes", [])), "", t('此为按价目快照估算的 API 对照价值，不是订阅实际账单。'), t('不包含税、工具费用、缓存存储时长及地区／服务等级溢价。')]
    if row.get("method") == "counter_delta":
        lines.append(t('此条为旧日志累计增量，可能合并多次请求，并非可确认的单次调用。'))
    return "\n".join(lines)


def show_detail(parent, row, *, aggregate=False):
    language = getattr(parent, "ui_language", "en")
    t = Translator(language)
    window = tk.Toplevel(parent)
    window.title(t('会话 API 等价值') if aggregate else t('单次调用 · Token 与 API 等价值'))
    window.minsize(400, 300)
    fit_window(window, 1050, 700, parent)
    box = ttk.Frame(window, padding=18)
    box.pack(fill="both", expand=True)
    text = tk.Text(box, wrap="word", padx=14, pady=14, font=("Menlo",11))
    scroll = ttk.Scrollbar(box, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(fill="both", expand=True)
    def retranslate(language):
        current = Translator(language)
        window.title(current('会话 API 等价值') if aggregate else current('单次调用 · Token 与 API 等价值'))
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", detail_text(row, aggregate=aggregate, language=language))
        text.configure(state="disabled")
    window.retranslate = retranslate
    retranslate(language)
    window.bind("<Escape>", lambda _: window.destroy())
    return window
