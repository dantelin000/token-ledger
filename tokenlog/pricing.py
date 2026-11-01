"""Dated public API token-equivalent values, never subscription charges.

All input components are disjoint. Reasoning is already part of output.
Unknown models are excluded, and unknown cache-write TTLs produce a range.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile

AS_OF = "2026-11-01"
OPENAI = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
GOOGLE = "https://ai.google.dev/gemini-api/docs/pricing"
MILLION = Decimal(1_000_000)


def decimal(value):
    d = Decimal(str(value))
    if not d.is_finite() or d < 0:
        raise ValueError("单价必须为有限的非负数字")
    return d


@dataclass(frozen=True)
class Rate:
    input: str
    output: str
    read: str
    write_short: str
    write_long: str
    source: str
    short_ttl: str = "短时缓存"
    long_ttl: str = "长时缓存"
    threshold: int | None = None
    long_input_multiplier: str = "1"
    long_output_multiplier: str = "1"
    note: str = ""
    verified_at: str = AS_OF

    def values(self, long_context=False):
        i = decimal(self.long_input_multiplier) if long_context else Decimal(1)
        o = decimal(self.long_output_multiplier) if long_context else Decimal(1)
        return {"input": decimal(self.input)*i, "output": decimal(self.output)*o,
                "read": decimal(self.read)*i, "write_short": decimal(self.write_short)*i,
                "write_long": decimal(self.write_long)*i}


def _claude(i, o, read, *, threshold=None, multiplier="1"):
    return Rate(str(i), str(o), str(read), str(Decimal(str(i))*Decimal("1.25")),
                str(Decimal(str(i))*2), ANTHROPIC, "5 分钟", "1 小时", threshold, multiplier, multiplier)


def _openai(i, o, read):
    return Rate(str(i), str(o), str(read), str(Decimal(str(i))*Decimal("1.25")),
                str(Decimal(str(i))*Decimal("2.5")), OPENAI, "30 分钟", "12 小时",
                272000, "2", "1.5")


# Standard, global-routing, paid text-token rates, USD per million tokens.
# Deliberately no fuzzy mapping of proxy IDs, hidden review models or free/local models.
BUILTINS = {
    "gpt-6.1-sol": _openai(2, 10, ".10"),
    "gpt-6.1-terra": _openai(1, 5, ".05"),
    "gpt-6-luna": _openai(".10", ".50", ".01"),
    "gpt-6-astra": _openai(10, 50, 1),
    "gpt-6-sol": replace(_openai(2, 10, ".20"), threshold=272000,
        long_input_multiplier="2", long_output_multiplier="1.5",
        source="https://developers.openai.com/api/docs/models/gpt-6-sol"),
    "gpt-5.6-sol": _openai(4, 20, ".40"),
    "claude-fable-5-1": _claude(10, 50, ".25"),
    "claude-opus-5-5": _claude(4, 20, ".20"),
    "claude-sonnet-5-5": _claude(2, 10, ".10"),
    "claude-haiku-5-5": _claude(".10", ".50", ".01", threshold=100000, multiplier="5"),
    "claude-opus-5": _claude(5, 25, ".50"),
    "claude-sonnet-5": _claude(2, 10, ".20"),
    "claude-opus-4-6": _claude(5, 25, ".50"),
    "claude-haiku-4-5-20251001": _claude(1, 5, ".10"),
    "gemini-3.8-flash": Rate(".75", "3.75", ".075", ".75", ".75", GOOGLE,
        note="按 2026-11-01 促销价目；未含显式缓存存储时长费用及工具调用费用。"),
    "gemini-4-argon": Rate("2", "10", ".10", "2", "2", GOOGLE,
        note="按 2026-11-01 促销价目；仅隐式缓存，未含工具调用费用。"),
}


def empty_total():
    return {"low_usd": Decimal(0), "high_usd": Decimal(0), "priced_records": 0,
            "unpriced_records": 0, "unpriced_tokens": 0, "range_records": 0}


def add_total(total, quote, tokens):
    if quote["low_usd"] is None:
        total["unpriced_records"] += 1
        total["unpriced_tokens"] += tokens
    else:
        total["low_usd"] += Decimal(quote["low_usd"])
        total["high_usd"] += Decimal(quote["high_usd"])
        total["priced_records"] += 1
        total["range_records"] += quote["low_usd"] != quote["high_usd"]


def finish_total(total):
    result = dict(total)
    for key in ("low_usd", "high_usd"):
        result[key] = format(result[key], "f") if result["priced_records"] else None
    return result


def money(estimate, places=2):
    if not estimate or estimate.get("low_usd") is None:
        return "未定价"
    lo, hi = Decimal(estimate["low_usd"]), Decimal(estimate["high_usd"])
    text = f"${lo:,.{places}f}" if lo == hi else f"${lo:,.{places}f}–${hi:,.{places}f}"
    return text + ("（部分）" if estimate.get("unpriced_records") else "")


class PriceBook:
    def __init__(self, overrides_path=None):
        self.path = Path(overrides_path) if overrides_path else None
        self.rates = dict(BUILTINS)
        self.custom_models = set()
        if self.path and self.path.exists():
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(values, dict) or values.get("version") != 1 or not isinstance(values.get("models"), dict):
                raise ValueError("自定义价格文件格式无效")
            for model, fields in values["models"].items():
                self.rates[model] = self.validate_override(fields)
                self.custom_models.add(model)

    @staticmethod
    def validate_override(fields):
        if not isinstance(fields, dict):
            raise ValueError("单价配置无效")
        values = {key: str(decimal(fields[key])) for key in ("input", "output", "read", "write_short", "write_long")}
        return Rate(**values, source="用户自定义", short_ttl="自定义短时", long_ttl="自定义长时",
                    note=str(fields.get("note", "用户自定义的对照单价，非官方核验。")),
                    verified_at=str(fields.get("verified_at", AS_OF)))

    def save_override(self, model, fields):
        model = model.strip()
        if not model or len(model) > 200 or not self.path:
            raise ValueError("请输入有效模型 ID")
        rate = self.validate_override(fields)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"version": 1, "models": {}}
        data["models"][model] = {key: getattr(rate, key) for key in
            ("input", "output", "read", "write_short", "write_long", "note", "verified_at")}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False, encoding="utf-8") as f:
                temporary = Path(f.name)
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        self.rates[model] = rate
        self.custom_models.add(model)

    def quote(self, event):
        model = event.get("model", "unknown")
        rate = self.rates.get(model)
        if rate is None:
            return {"low_usd": None, "high_usd": None, "model": model,
                    "notes": ["没有该模型 ID 的已核验单价；未纳入金额，请在价格表中手动配置。"]}
        inp, out, read, write = (int(event.get(k, 0)) for k in
            ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens"))
        short, long = (int(event.get(k, 0)) for k in ("cache_write_short_tokens", "cache_write_long_tokens"))
        if min(inp, out, read, write, short, long) < 0 or read+write > inp or short+long > write:
            return {"low_usd": None, "high_usd": None, "model": model, "notes": ["token 分项不一致，无法估值。"]}
        ordinary, unknown_write = inp-read-write, write-short-long
        notes = [rate.note] if rate.note else []
        long_context = bool(rate.threshold is not None and inp > rate.threshold)
        ambiguous_tier = bool(rate.threshold and event.get("method") == "counter_delta")
        if long_context and not ambiguous_tier:
            notes.append(f"本次输入超过 {rate.threshold:,} token，按长上下文单价。")
        if ambiguous_tier:
            notes.append("旧版累计增量可能合并多次调用，上下文档位未知，金额显示范围。")
        lo_rates = rate.values(False if ambiguous_tier else long_context)
        hi_rates = rate.values(True if ambiguous_tier else long_context)
        def components(values, choose):
            w = (short*values["write_short"] + long*values["write_long"]
                 + unknown_write*choose(values["write_short"], values["write_long"])) / MILLION
            return {"ordinary_input": ordinary*values["input"]/MILLION,
                    "cache_read": read*values["read"]/MILLION, "cache_write": w,
                    "output": out*values["output"]/MILLION}
        low, high = components(lo_rates, min), components(hi_rates, max)
        if unknown_write and (lo_rates["write_short"] != hi_rates["write_long"]):
            notes.append(f"{unknown_write:,} 个写入 token 未记录 TTL，按{rate.short_ttl}至{rate.long_ttl}报价范围。")
        return {"low_usd": format(sum(low.values()), "f"), "high_usd": format(sum(high.values()), "f"),
                "components_low": {k: format(v, "f") for k,v in low.items()},
                "components_high": {k: format(v, "f") for k,v in high.items()},
                "ordinary_input_tokens": ordinary, "model": model,
                "rates_low": {k: str(v) for k,v in lo_rates.items()},
                "rates_high": {k: str(v) for k,v in hi_rates.items()},
                "source": rate.source, "verified_at": rate.verified_at, "notes": notes}

    def info(self):
        return {"currency": "USD", "as_of": AS_OF,
            "basis": "按当前价目快照的 Standard / 全局路由付费文本 token API 等价值，不是订阅账单或历史实付。",
            "notes": ["缓存时长缺失时显示区间；未核验模型不填零，未纳入金额。",
                      "输出已包含推理，不再重复计价；普通输入 = 全部输入 − 缓存读取 − 缓存写入。",
                      "未含工具调用、地区/服务等级溢价、税、显式缓存存储时长费用及音视频模态差价。",
                      "所有历史记录统一按此日期的单价估值；不是历史各日账单复算。"],
            "sources": [OPENAI, ANTHROPIC, GOOGLE]}
