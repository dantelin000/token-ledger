"""Read usage metadata from local transcripts without retaining message content.

The normalized input count always includes cache reads and cache writes. Reasoning
is a subset of output, not an additional amount. Codex response usage records are
authoritative; its token_count messages are cumulative UI snapshots of those same
requests. Old turns that have no response records use counter deltas instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator


@dataclass
class ParseResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0


_COUNTS = (
    "input_tokens", "cached_input_tokens", "cache_write_tokens",
    "output_tokens", "reasoning_tokens", "total_tokens", "cache_write_short_tokens", "cache_write_long_tokens",
)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _valid_primary(usage: Any) -> bool:
    return isinstance(usage, dict) and all(
        isinstance(usage.get(k), int) and not isinstance(usage[k], bool) and usage[k] >= 0
        for k in ("input_tokens", "output_tokens")
    )


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Do not guess the timezone of a source record.
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rows(path: Path, result: ParseResult) -> Iterator[dict[str, Any]]:
    malformed = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    malformed += 1
                    continue
                if isinstance(row, dict):
                    yield row
                else:
                    malformed += 1
    except OSError:
        # Let the ledger retain previously collected calls and retry later.
        # An unreadable source is not a successful empty scan.
        raise
    if malformed:
        result.skipped += malformed
        result.warnings.append(f"跳过 {malformed} 条无效或尚未写完的 JSON 记录")


def _normalized(usage: dict[str, Any], provider: str) -> dict[str, int]:
    read = _count(usage.get("cache_read_input_tokens" if provider == "claude" else "cached_input_tokens"))
    input_details = usage.get("input_tokens_details") or {}
    if not isinstance(input_details, dict):
        input_details = {}
    write = _count(usage.get("cache_creation_input_tokens") if provider == "claude" else
                   usage.get("cache_write_input_tokens", input_details.get("cache_write_tokens")))
    if provider == "claude":
        ttl = usage.get("cache_creation") or {}
        short_key, long_key = "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"
    else:
        ttl = input_details.get("cache_write_tokens_details") or {}
        short_key, long_key = "ttl_30m_tokens", "ttl_12h_tokens"
    ttl = ttl if isinstance(ttl, dict) else {}
    short, long = _count(ttl.get(short_key)), _count(ttl.get(long_key))
    if short + long > write:
        short = long = 0
    input_count = _count(usage.get("input_tokens"))
    if provider == "claude":
        input_count += read + write
    details = usage.get("output_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}
    reasoning = _count(usage.get("reasoning_output_tokens"))
    reasoning = max(reasoning, _count(details.get("thinking_tokens")), _count(details.get("reasoning_tokens")))
    output = _count(usage.get("output_tokens"))
    return {
        "input_tokens": input_count,
        "cached_input_tokens": read,
        "cache_write_tokens": write,
        "output_tokens": output,
        "reasoning_tokens": reasoning,
        "total_tokens": input_count + output,
        "cache_write_short_tokens": short, "cache_write_long_tokens": long,
    }


def _merge_event(events: dict[str, dict[str, Any]], event: dict[str, Any]) -> None:
    old = events.get(event["event_key"])
    if old is None:
        events[event["event_key"]] = event
        return
    # Streaming content blocks repeat the same message usage. Later snapshots can
    # have larger output or fill in cache details; no block is a separate request.
    for key in _COUNTS:
        old[key] = max(old.get(key, 0), event.get(key, 0))
    old["total_tokens"] = old["input_tokens"] + old["output_tokens"]
    if old["model"] == "unknown" and event["model"] != "unknown":
        old["model"] = event["model"]
    # First appearance gives a stable call timestamp while streaming continues.
    old["timestamp"] = min(old["timestamp"], event["timestamp"])


def _claude(path: Path) -> ParseResult:
    result = ParseResult()
    events: dict[str, dict[str, Any]] = {}
    missing = invalid_time = 0
    for row in _rows(path, result):
        if row.get("type") != "assistant":
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            missing += 1
            continue
        usage = message.get("usage")
        if not _valid_primary(usage):
            missing += 1
            continue
        # Synthetic interrupted/error messages do not represent model requests.
        if message.get("model") in ("<synthetic>", "synthetic"):
            continue
        stamp = _timestamp(row.get("timestamp"))
        if stamp is None:
            invalid_time += 1
            continue
        session = str(row.get("sessionId") or path.stem)
        message_id = message.get("id") or row.get("requestId") or row.get("uuid")
        if not message_id:
            missing += 1
            continue
        # Message IDs are global and are copied intact when a session is resumed,
        # forked, or represented in both a parent and a subagent transcript.
        project = str(row.get("cwd") or path.parent.name)
        event = {
            "provider": "claude", "event_key": "message:" + str(message_id),
            "session_id": session, "timestamp": stamp,
            "model": str(message.get("model") or "unknown"),
            "project": project, "method": "message",
            **_normalized(usage, "claude"),
        }
        _merge_event(events, event)
    result.events = list(events.values())
    result.skipped += missing + invalid_time
    if missing:
        result.warnings.append(f"{missing} 条助手记录缺少完整 usage 或稳定消息 ID，未计为零用量")
    if invalid_time:
        result.warnings.append(f"{invalid_time} 条用量记录缺少有效时区时间，未导入")
    return result


def _codex(path: Path) -> ParseResult:
    result = ParseResult()
    metadata: dict[str, Any] = {}
    file_metadata: dict[str, Any] = {}
    context: dict[str, Any] = {}
    responses: dict[str, dict[str, Any]] = {}
    response_turns: set[str] = set()
    counters: list[dict[str, Any]] = []
    previous: dict[str, int] | None = None
    compacted_snapshot = False
    task_started = False
    bootstrap = 0
    invalid = invalid_time = resets = inherited = 0
    for row in _rows(path, result):
        typ = row.get("type")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if typ == "session_meta":
            metadata = payload
            if not file_metadata:
                file_metadata = payload
            continue
        if typ == "turn_context":
            context = payload
            continue
        if typ == "compacted":
            compacted_snapshot = True
        if typ == "event_msg" and payload.get("type") in ("task_started", "turn_started"):
            task_started = True
        is_record = typ == "token_usage_record"
        is_counter = typ == "event_msg" and payload.get("type") == "token_count"
        if not is_record and not is_counter:
            continue
        stamp = _timestamp(row.get("timestamp"))
        if stamp is None:
            invalid_time += 1
            continue
        started = _timestamp(file_metadata.get("timestamp"))
        historical = bool(started and stamp < started)
        counter_metadata = metadata if historical else file_metadata
        session = str(payload.get("thread_id") or counter_metadata.get("id") or path.stem)
        turn = str(payload.get("turn_id") or context.get("turn_id") or "")
        project = str(context.get("cwd") or metadata.get("cwd") or "unknown")
        # A parent transcript can carry a child's usage record. Its current turn
        # context describes the parent, so it cannot establish the child's model.
        child_record = bool(is_record and payload.get("turn_id") and context.get("turn_id") != payload.get("turn_id"))
        context_model = context.get("model") if not child_record else None
        model = str(payload.get("model") or context_model or (metadata.get("model") if not child_record else None) or "unknown")
        common = {"provider": "codex", "session_id": session,
                  "timestamp": stamp, "model": model, "project": project}
        if is_record:
            usage = payload.get("usage")
            if not _valid_primary(usage):
                invalid += 1
                continue
            response_id = payload.get("response_id")
            if response_id:
                key = "response:" + str(response_id)
            else:
                # A record without a response ID can still have exact counts.
                # Keep a deterministic identity across copied transcripts.
                key = "record:" + _hash([session, turn, stamp, usage])
            event = {**common, "event_key": key, "method": "response", **_normalized(usage, "codex")}
            _merge_event(responses, event)
            response_turns.add(turn)
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            # Quota-only updates are not missing-usage requests.
            continue
        total_usage = info.get("total_token_usage")
        last_usage = info.get("last_token_usage")
        if not _valid_primary(total_usage):
            invalid += 1
            continue
        total = _normalized(total_usage, "codex")
        if previous is None and compacted_snapshot and not task_started and file_metadata.get("parent_thread_id"):
            # Restored guardian threads begin with the previous thread's UI
            # counters before task_started. They establish a baseline, not calls.
            previous = total
            bootstrap += 1
            continue
        if previous is None:
            # A fork starts with the parent's cumulative counters. Only the last
            # request can belong to this new thread; never book the inherited sum.
            if file_metadata.get("forked_from_id") and not historical:
                delta = _normalized(last_usage, "codex") if _valid_primary(last_usage) else None
                inherited += 1
            else:
                delta = total.copy()
        elif total["input_tokens"] < previous["input_tokens"] or total["output_tokens"] < previous["output_tokens"]:
            resets += 1
            # Compaction/resume can reset counters. Its last request is the only
            # unambiguous increment at that boundary.
            delta = _normalized(last_usage, "codex") if _valid_primary(last_usage) else None
        else:
            delta = {key: max(0, total[key] - previous[key]) for key in _COUNTS}
            delta["total_tokens"] = delta["input_tokens"] + delta["output_tokens"]
        previous = total
        if delta is None or not delta["total_tokens"]:
            continue
        # The preserved turn ID + snapshot timestamp survive transcript copies.
        # For legacy files lacking turn IDs, timestamp and cumulative values are
        # used without the current session ID so forked history still deduplicates.
        key = "counter:" + _hash([turn, stamp, total])
        counters.append({**common, "event_key": key, "method": "counter_delta", "_turn": turn, **delta})
    events = responses
    for counter in counters:
        turn = counter.pop("_turn")
        if turn not in response_turns:
            _merge_event(events, counter)
    result.events = list(events.values())
    result.skipped += invalid + invalid_time
    if invalid:
        result.warnings.append(f"{invalid} 条用量记录缺少完整 input/output usage，未计为零用量")
    if invalid_time:
        result.warnings.append(f"{invalid_time} 条用量记录缺少有效时区时间，未导入")
    if resets and any(e["method"] == "counter_delta" for e in result.events):
        result.warnings.append(f"检测到 {resets} 次累计计数重置，边界使用 last_token_usage")
    if inherited and any(e["method"] == "counter_delta" for e in result.events):
        result.warnings.append("分叉会话的首条累计值包含历史，仅采用本次 last_token_usage")
    if bootstrap:
        result.warnings.append("恢复线程的初始累计快照仅作为基线，未重复计入本次调用")
    unknown_models = sum(e["model"] == "unknown" for e in result.events)
    if unknown_models:
        result.warnings.append(f"{unknown_models} 条用量记录缺少可确认的模型，归入 unknown")
    return result


def parse_file(path: Path, provider: str) -> ParseResult:
    """Parse one transcript. Original files are always opened read-only."""
    path = Path(path)
    if provider == "claude":
        return _claude(path)
    if provider == "codex":
        return _codex(path)
    raise ValueError(f"Unsupported transcript provider: {provider}")
