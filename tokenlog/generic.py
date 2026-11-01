"""Normalize explicit API usage receipts from any user-named harness.

This is a file importer, not an interceptor. It requires real usage and a stable
call identity, session identity, and time. It never estimates tokens from text.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterator

from .parsers import ParseResult, _merge_event

FORMATS = ("openai", "anthropic", "gemini", "tokenlog")
SCHEMA = "tokenlog.usage.v1"


class ReceiptError(ValueError):
    """Invalid or ambiguous usage; the message never includes source content."""


def _int(value, required=False):
    if value is None and not required:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReceiptError("token 字段必须为非负整数")
    return value


def _timestamp(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ReceiptError("时间缺少时区")
        else:
            raise ReceiptError("缺少调用时间")
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        raise ReceiptError("调用时间无效或缺少时区") from None


def _rows(path: Path, result: ParseResult) -> Iterator[dict]:
    with path.open("r", encoding="utf-8-sig") as handle:
        if path.suffix.lower() == ".json":
            try:
                value = json.load(handle)
            except (ValueError, RecursionError):
                result.skipped += 1
                result.warnings.append("JSON 文件无效或尚未写完，未推测用量。")
                return
            if isinstance(value, list):
                records = value
            elif isinstance(value, dict) and isinstance(value.get("events"), list):
                records = value["events"]
            else:
                records = [value]
            for record in records:
                if isinstance(record, dict):
                    yield record
                else:
                    result.skipped += 1
            return
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, RecursionError):
                result.skipped += 1
                continue
            yield value


def _dict(value):
    return value if isinstance(value, dict) else {}


def _pick(containers, keys):
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is not None and value != "":
                return value
    return None


def _merge_usage(old: dict, current: dict, kind: str) -> dict:
    """Retain only token fields; partial streaming snapshots share one call ID."""
    fields = {
        "openai": ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens",
                   "cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens",
                   "prompt_tokens_details", "completion_tokens_details", "input_tokens_details", "output_tokens_details"),
        "anthropic": ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "cache_creation", "output_tokens_details"),
        "gemini": ("promptTokenCount", "toolUsePromptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "cachedContentTokenCount"),
        "tokenlog": ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens", "reasoning_tokens", "cache_write_short_tokens", "cache_write_long_tokens"),
    }[kind]
    merged = dict(old)
    # A previous total is stale when a later fragment adds output. Only validate
    # a total explicitly supplied by the current fragment.
    total_key = "totalTokenCount" if kind == "gemini" else "total_tokens"
    merged.pop(total_key, None)
    for key in (*fields, total_key):
        if key not in current:
            continue
        value = current[key]
        if key.endswith("_details") and isinstance(value, dict):
            details = dict(merged.get(key) or {})
            for name in ("cached_tokens", "thinking_tokens", "reasoning_tokens", "cache_write_tokens", "cache_write_tokens_details"):
                if name in value:
                    v, previous = value[name], details.get(name)
                    details[name] = max(previous, v) if type(previous) is int and type(v) is int and previous >= 0 and v >= 0 else v
            merged[key] = details
        else:
            previous = merged.get(key)
            merged[key] = max(previous, value) if type(previous) is int and type(value) is int and previous >= 0 and value >= 0 and key != total_key else value
    return merged


def normalize_usage(usage: dict, kind: str) -> dict[str, int]:
    if not isinstance(usage, dict):
        raise ReceiptError("缺少 usage")
    short = long = 0
    if kind == "openai":
        if "prompt_tokens" in usage or "completion_tokens" in usage:
            i = _int(usage.get("prompt_tokens"), True)
            o = _int(usage.get("completion_tokens"), True)
            inputs = _dict(usage.get("prompt_tokens_details"))
            outputs = _dict(usage.get("completion_tokens_details"))
        else:
            i = _int(usage.get("input_tokens"), True)
            o = _int(usage.get("output_tokens"), True)
            inputs = _dict(usage.get("input_tokens_details"))
            outputs = _dict(usage.get("output_tokens_details"))
        read = _int(inputs.get("cached_tokens", usage.get("cached_input_tokens")))
        write = _int(usage.get("cache_write_input_tokens", inputs.get("cache_write_tokens")))
        ttl = _dict(inputs.get("cache_write_tokens_details"))
        short, long = _int(ttl.get("ttl_30m_tokens")), _int(ttl.get("ttl_12h_tokens"))
        reasoning = _int(outputs.get("reasoning_tokens", usage.get("reasoning_output_tokens")))
        total = usage.get("total_tokens")
    elif kind == "anthropic":
        i = _int(usage.get("input_tokens"), True)
        o = _int(usage.get("output_tokens"), True)
        read = _int(usage.get("cache_read_input_tokens"))
        write = _int(usage.get("cache_creation_input_tokens"))
        ttl = _dict(usage.get("cache_creation"))
        short, long = _int(ttl.get("ephemeral_5m_input_tokens")), _int(ttl.get("ephemeral_1h_input_tokens"))
        i += read + write
        details = _dict(usage.get("output_tokens_details"))
        reasoning = _int(details.get("thinking_tokens", details.get("reasoning_tokens")))
        total = usage.get("total_tokens")
    elif kind == "gemini":
        # Standard GenerateContent only; Live's responseTokenCount accounting is
        # different and intentionally rejected rather than silently guessed.
        i = _int(usage.get("promptTokenCount"), True) + _int(usage.get("toolUsePromptTokenCount"))
        o = _int(usage.get("candidatesTokenCount"), True)
        reasoning = _int(usage.get("thoughtsTokenCount"))
        o += reasoning
        read, write = _int(usage.get("cachedContentTokenCount")), 0
        total = usage.get("totalTokenCount")
    elif kind == "tokenlog":
        i = _int(usage.get("input_tokens"), True)
        o = _int(usage.get("output_tokens"), True)
        read = _int(usage.get("cached_input_tokens"))
        write = _int(usage.get("cache_write_tokens"))
        short, long = _int(usage.get("cache_write_short_tokens")), _int(usage.get("cache_write_long_tokens"))
        reasoning = _int(usage.get("reasoning_tokens"))
        total = usage.get("total_tokens")
    else:
        raise ReceiptError("不支持的 usage 格式")
    if read + write > i or reasoning > o or short+long > write:
        raise ReceiptError("缓存或推理子计数超过所属总计")
    if total is not None and _int(total, True) != i + o:
        raise ReceiptError("total 与输入加输出不一致")
    return {"input_tokens": i, "cached_input_tokens": read, "cache_write_tokens": write,
            "output_tokens": o, "reasoning_tokens": reasoning, "total_tokens": i + o,
            "cache_write_short_tokens": short, "cache_write_long_tokens": long}


def parse_file(path: Path, provider: str, kind: str) -> ParseResult:
    if kind not in FORMATS:
        raise ValueError("不支持的通用 usage 格式。")
    result = ParseResult()
    events = {}
    pending = {}
    reasons = {}
    unknown_model = unknown_project = 0
    for row in _rows(Path(path), result):
        try:
            if kind == "tokenlog" and row.get("schema") != SCHEMA:
                raise ReceiptError("缺少 tokenlog.usage.v1 schema")
            containers = [row]
            # Common logging wrappers. Inspect structured metadata only; message
            # content, prompts, request headers and credentials are never copied.
            nested = [_dict(row.get(key)) for key in ("response", "message", "data", "payload")]
            containers += [value for value in nested if value]
            containers += [_dict(row.get("metadata"))]
            usage = _pick(containers, ("usageMetadata", "usage_metadata")) if kind == "gemini" else _pick(containers, ("usage",))
            if kind == "tokenlog":
                identifier = row.get("event_id")
                session = row.get("session_id")
            else:
                identifier = (_pick(containers, ("response_id", "responseId", "request_id", "requestId"))
                              or _pick(containers[1:], ("id",)) or row.get("event_id") or row.get("id"))
                session = _pick(containers, ("session_id", "sessionId", "thread_id", "conversation_id"))
            if not isinstance(identifier, str) or not identifier.strip():
                raise ReceiptError("缺少稳定调用 ID")
            if not isinstance(usage, dict):
                raise ReceiptError("缺少 usage")
            previous = pending.setdefault(identifier, {})
            assembled = _merge_usage(previous.get("usage", {}), usage, kind)
            previous["usage"] = assembled
            session = session if session is not None else previous.get("session_id")
            if not isinstance(session, str) or not session.strip():
                raise ReceiptError("缺少会话 ID")
            when = _pick(containers, ("timestamp", "created_at", "created", "time"))
            stamp = _timestamp(when if when is not None else previous.get("timestamp"))
            model = _pick(containers, ("model", "modelVersion", "model_version")) or previous.get("model")
            project = _pick(containers, ("project", "cwd", "workspace")) or previous.get("project")
            previous.update(session_id=session, timestamp=stamp, model=model, project=project)
            counts = normalize_usage(assembled, kind)
            if not isinstance(model, str) or not model:
                model = "unknown"
                unknown_model += 1
            if not isinstance(project, str) or not project:
                project = "unknown"
                unknown_project += 1
            event = {"provider": provider, "event_key": f"{kind}:receipt:{identifier}",
                     "session_id": session, "timestamp": stamp, "model": model,
                     "project": project, "method": "receipt", **counts}
            _merge_event(events, event)
        except ReceiptError as exc:
            result.skipped += 1
            message = str(exc)
            reasons[message] = reasons.get(message, 0) + 1
    result.events = sorted(events.values(), key=lambda row: (row["timestamp"], row["event_key"]))
    for reason, count in reasons.items():
        result.warnings.append(f"{count} 条记录未导入：{reason}；未按零用量处理。")
    if unknown_model:
        result.warnings.append(f"{unknown_model} 条记录未保存模型名称，归入 unknown。")
    if unknown_project:
        result.warnings.append(f"{unknown_project} 条记录未保存项目路径，归入 unknown。")
    if result.skipped and not reasons and not result.warnings:
        result.warnings.append(f"跳过 {result.skipped} 条无效或尚未写完的记录，未推测用量。")
    return result
