"""Read Antigravity's recorded ModelUsageStats from private SQLite snapshots.

The small protobuf reader uses field numbers verified against the descriptors
embedded in the installed agy executable (2026-10-17 build). It deliberately
extracts only usage, timestamps, model names and workspace paths. Neither source
databases nor their journal files are opened for writing.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from urllib.parse import unquote, urlparse

from .parsers import ParseResult


def roots() -> list[Path]:
    configured = os.environ.get("TOKENLOG_AGY_ROOTS")
    if configured is not None:
        return [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
    return [Path.home() / ".gemini" / name
            for name in ("antigravity-cli", "antigravity")]


def discover_files() -> list[Path]:
    return sorted({p.resolve() for root in roots()
                   for p in (root / "conversations").glob("*.db") if p.is_file()})


def status() -> dict:
    files = discover_files()
    return {"status": "ok" if files else "missing", "files": len(files),
            "warning": "" if files else "未发现本机 Antigravity 会话库；缺少记录不代表没有使用。"}


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("truncated protobuf integer")
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise ValueError("oversized protobuf integer")


def _fields(data: bytes) -> dict[int, list]:
    result = defaultdict(list)
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError("invalid protobuf field")
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire in (1, 5):
            end = pos + (8 if wire == 1 else 4)
            value, pos = data[pos:end], end
        elif wire == 2:
            length, pos = _varint(data, pos)
            end = pos + length
            value, pos = data[pos:end], end
        else:
            raise ValueError("unsupported protobuf wire type")
        if pos > len(data):
            raise ValueError("truncated protobuf field")
        result[number].append(value)
    return dict(result)


def _first(fields: dict, number: int, default=b""):
    return fields.get(number, [default])[0]


def _text(fields: dict, number: int) -> str:
    value = _first(fields, number)
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else ""


def _number(fields: dict, number: int) -> int:
    value = _first(fields, number, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _timestamp(fields: dict, number: int) -> str | None:
    raw = _first(fields, number)
    if not isinstance(raw, bytes) or not raw:
        return None
    ts = _fields(raw)
    seconds = _number(ts, 1)
    nanos = _number(ts, 2)
    if not seconds or nanos >= 1_000_000_000:
        return None
    try:
        return datetime.fromtimestamp(seconds + nanos / 1e9, timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _signature(path: Path):
    result = []
    for candidate in [path, Path(str(path) + "-wal")]:
        try:
            info = candidate.stat()
            result.append((info.st_size, info.st_mtime_ns))
        except FileNotFoundError:
            result.append(None)
    return result


def _workspace(metadata: dict) -> str:
    candidates = metadata.get(7, [])
    for workspace in metadata.get(1, []):
        candidates.extend(_fields(workspace).get(1, []))
    for raw in candidates:
        if not isinstance(raw, bytes):
            continue
        text = raw.decode("utf-8", errors="replace")
        parsed = urlparse(text)
        if parsed.scheme == "file":
            return unquote(parsed.path)
        if text.startswith("/"):
            return text
    return "Antigravity"


def _indices(generator: dict) -> list[int]:
    result = []
    for raw in generator.get(2, []):
        if isinstance(raw, int):
            result.append(raw)
        else:
            pos = 0
            while pos < len(raw):
                item, pos = _varint(raw, pos)
                result.append(item)
    return result


def _parse_connection(connection: sqlite3.Connection, path: Path) -> ParseResult:
    result = ParseResult()
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"steps", "gen_metadata", "trajectory_meta"} <= tables:
        result.warnings.append("Antigravity 会话库结构不受支持，未推测 token。")
        return result
    trajectory = connection.execute("SELECT trajectory_id,cascade_id FROM trajectory_meta LIMIT 1").fetchone()
    trajectory_id = trajectory[0] if trajectory else path.stem
    session_id = trajectory[1] if trajectory else path.stem
    metadata = {}
    malformed = 0
    if "trajectory_metadata_blob" in tables:
        row = connection.execute("SELECT data FROM trajectory_metadata_blob LIMIT 1").fetchone()
        if row and row[0]:
            try:
                metadata = _fields(row[0])
            except (ValueError, TypeError, IndexError):
                malformed += 1
    project = _workspace(metadata)
    steps = {}
    for idx, blob in connection.execute("SELECT idx,metadata FROM steps"):
        try:
            steps[idx] = _fields(blob or b"")
        except (ValueError, TypeError, IndexError):
            malformed += 1
    events = {}
    used_steps = set()

    def add(usage_blob, step, model, fallback):
        nonlocal malformed
        try:
            usage = _fields(usage_blob)
            # Proto3 omits zero scalar fields. A model/ID plus at least one count
            # is required so an empty/error record cannot become fabricated zero.
            if not any(n in usage for n in (2, 3, 4, 5, 9, 10)):
                result.skipped += 1
                return
            when = (_timestamp(step, 8) or _timestamp(step, 7)
                    or _timestamp(step, 1) or _timestamp(metadata, 2))
            if when is None:
                result.skipped += 1
                return
            identifier = (_text(usage, 11) or _text(usage, 12) or _text(usage, 7))
            if not identifier:
                source = _fields(_first(step, 20))
                source_id = _text(source, 1) or trajectory_id
                source_idx = _number(source, 2) if source else fallback
                identifier = hashlib.sha256(
                    f"{source_id}:{source_idx}:{when}:{usage_blob.hex()}".encode()).hexdigest()
            cached = _number(usage, 5)
            cache_write = _number(usage, 4)
            # Antigravity records uncached input separately from cache reads/writes.
            input_tokens = _number(usage, 2) + cached + cache_write
            output_tokens = _number(usage, 3)
            reasoning = _number(usage, 9)
            if reasoning > output_tokens:
                result.warnings.append("agy 存在 reasoning 超过 output 的记录，保留原 output 并限制子计数。")
                reasoning = output_tokens
            event = {
                "provider": "agy", "event_key": "agy:response:" + identifier,
                "session_id": session_id, "timestamp": when,
                "model": model or f"agy-model-{_number(usage, 1)}",
                "project": project, "input_tokens": input_tokens,
                "cached_input_tokens": cached, "cache_write_tokens": cache_write,
                "output_tokens": output_tokens, "reasoning_tokens": reasoning,
                "total_tokens": input_tokens + output_tokens, "method": "response",
            }
            old = events.get(event["event_key"])
            if not old or event["total_tokens"] > old["total_tokens"]:
                events[event["event_key"]] = event
        except (ValueError, TypeError, IndexError):
            malformed += 1

    for idx, blob in connection.execute("SELECT idx,data FROM gen_metadata ORDER BY idx"):
        try:
            generator = _fields(blob or b"")
            chat = _fields(_first(generator, 1))
            indices = _indices(generator)
            chosen = next((i for i in indices if i in steps and 9 in steps[i]),
                          next((i for i in indices if i in steps), None))
            step = steps.get(chosen, {})
            model = _text(chat, 19) or _text(chat, 22) or _text(chat, 21)
            if not model and 24 in step:
                info = _fields(_first(step, 24))
                model = _text(info, 12) or _text(info, 8) or _text(info, 20)
            if 4 in chat:
                add(_first(chat, 4), step, model, idx)
                used_steps.update(indices)
            # RetryInfo includes the successful attempt too. IDs deduplicate it
            # with ChatModelMetadata.usage, while distinct paid retries survive.
            for retry in chat.get(17, []):
                retry_info = _fields(retry)
                if 2 in retry_info:
                    add(_first(retry_info, 2), step, model, idx)
                    used_steps.update(indices)
        except (ValueError, TypeError, IndexError):
            malformed += 1
    # Older/partial generations can keep counters only on a step.
    for idx, step in steps.items():
        if idx in used_steps:
            continue
        if 9 in step:
            model_info = _fields(_first(step, 24))
            model = _text(model_info, 12) or _text(model_info, 8) or _text(model_info, 20)
            add(_first(step, 9), step, model, idx)
    if malformed:
        result.warnings.append(f"{malformed} 条 agy 元数据解析失败，未推测其 token。")
    if not events and steps:
        result.warnings.append("该 agy 会话未记录可用 token 字段，无法补算。")
    result.events = sorted(events.values(), key=lambda item: (item["timestamp"], item["event_key"]))
    result.warnings = list(dict.fromkeys(result.warnings))
    return result


def parse_file(path: Path) -> ParseResult:
    path = Path(path)
    # Read a private snapshot so SQLite can create its SHM file only in a
    # temporary directory. Include WAL, unlike immutable=1 which ignores it.
    for attempt in range(3):
        before = _signature(path)
        with tempfile.TemporaryDirectory(prefix="tokenlog-agy-") as directory:
            snapshot = Path(directory) / "conversation.db"
            shutil.copyfile(path, snapshot)
            wal = Path(str(path) + "-wal")
            if wal.exists():
                shutil.copyfile(wal, str(snapshot) + "-wal")
            after = _signature(path)
            if before != after and attempt < 2:
                continue
            with closing(sqlite3.connect(snapshot)) as connection:
                connection.execute("PRAGMA query_only=ON")
                result = _parse_connection(connection, path)
            if before != after:
                result.warnings.append("agy 源会话仍在写入，统计为当前快照，下次刷新会继续核对。")
            return result
    raise RuntimeError("unreachable snapshot state")
