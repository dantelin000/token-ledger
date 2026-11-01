"""Usage-only readers for OpenCode and Gemini CLI's recorded local history.

Schema references are documented in docs/SUPPORTED_HARNESSES.md. No model calls,
credential queries, source database writes, or conversation content in results.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile

from .parsers import ParseResult


def default_roots(kind: str) -> list[Path]:
    home = Path.home()
    if kind == "opencode":
        roots = [Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "opencode"]
        if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
            roots.append(Path(os.environ["LOCALAPPDATA"]) / "opencode")
        return list(dict.fromkeys(roots))
    if kind == "gemini_cli":
        home = Path(os.environ.get("GEMINI_CLI_HOME", home)).expanduser()
        return [home / ".gemini", home / ".cache/.gemini"]
    raise ValueError("Unsupported mainstream source")


def discover_files(kind: str, roots: list[Path]) -> list[Path]:
    found = set()
    for item in roots:
        root = Path(item).expanduser()
        if root.is_file():
            if root.suffix.lower() in ((".db", ".json") if kind == "opencode" else (".json", ".jsonl")):
                found.add(root.resolve())
            continue
        if not root.is_dir():
            continue
        if kind == "opencode":
            database = root / "opencode.db"
            if database.is_file():
                found.add(database.resolve())
            folder = root / "storage/message"
            if root.name == "message":
                folder = root
            if folder.is_dir():
                found.update(p.resolve() for p in folder.rglob("*.json") if p.is_file())
        elif kind == "gemini_cli":
            folder = root / "tmp" if (root / "tmp").is_dir() else root
            candidates = folder.glob("session*.*") if folder.name == "chats" else folder.glob("**/chats/session*.*")
            found.update(p.resolve() for p in candidates if p.is_file() and p.suffix in (".json", ".jsonl"))
        else:
            raise ValueError("Unsupported mainstream source")
    return sorted(found)


def _nat(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _timestamp(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stamp = datetime.fromtimestamp(value / 1000, timezone.utc)
        elif isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None
        else:
            return None
        return stamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _warn(result, message):
    if message not in result.warnings:
        result.warnings.append(message)


def _merge(events, event):
    previous = events.get(event["event_key"])
    if previous is None or event["total_tokens"] > previous["total_tokens"]:
        events[event["event_key"]] = event


def _event(provider, key, sid, timestamp, model, project, inp, out, cached, write, reasoning):
    return dict(provider=provider, event_key=key, session_id=str(sid), timestamp=timestamp,
        model=str(model or "unknown"), project=str(project or "unknown"), input_tokens=inp,
        output_tokens=out, cached_input_tokens=cached, cache_write_tokens=write,
        reasoning_tokens=reasoning, total_tokens=inp + out, method="message")


def _opencode_event(message, provider, result, *, mode="legacy", session=None):
    if not isinstance(message, dict) or message.get("role", message.get("type")) != "assistant":
        return None
    usage = message.get("tokens")
    if not isinstance(usage, dict):
        result.skipped += 1
        _warn(result, "OpenCode 助手记录缺少 token 用量，未按零推算。")
        return None
    cache = usage.get("cache") or {}
    counts = [_nat(usage.get("input")), _nat(usage.get("output")), _nat(usage.get("reasoning", 0)),
              _nat(cache.get("read", 0)), _nat(cache.get("write", 0))] if isinstance(cache, dict) else [None]
    if None in counts or len(counts) != 5:
        result.skipped += 1
        _warn(result, "OpenCode token 字段不完整或格式无效，未推测用量。")
        return None
    fresh, visible, reasoning, cached, write = counts
    if not sum(counts):
        result.skipped += 1
        _warn(result, "OpenCode 存在尚未记录用量的零值占位消息，未计为真实调用。")
        return None
    inp = fresh + cached + write
    supplied = _nat(usage.get("total"))
    if mode == "v2":
        out = visible + reasoning
    elif supplied == inp + visible:
        out = visible
    elif supplied == inp + visible + reasoning:
        out = visible + reasoning
    elif not reasoning:
        out = visible
    else:
        result.skipped += 1
        _warn(result, "旧版 OpenCode reasoning 的包含关系无法由 total 确认，已跳过该用量。")
        return None
    if reasoning > out or supplied is not None and supplied != inp + out:
        result.skipped += 1
        _warn(result, "OpenCode 分项计数与 total 不一致，已跳过该用量。")
        return None
    time = message.get("time") if isinstance(message.get("time"), dict) else {}
    stamp = _timestamp(time.get("created", message.get("time_created")))
    mid, sid = message.get("id"), message.get("sessionID", message.get("session_id"))
    if not stamp or not mid or not sid:
        result.skipped += 1
        _warn(result, "OpenCode 用量缺少稳定消息 ID、会话 ID 或有效时间，未导入。")
        return None
    session = session or {}
    session_time = session.get("time") if isinstance(session.get("time"), dict) else {}
    start = _timestamp(session.get("time_created", session_time.get("created")))
    if (session.get("fork_session_id") or session.get("parent_id") or session.get("parentID")) and start and stamp < start:
        # Forks copy previous assistant tokens into newly generated message IDs.
        # Those rows predate this session and did not consume new tokens.
        return None
    model = message.get("model") or {}
    if isinstance(model, dict):
        model = model.get("id")
    model = model or message.get("modelID")
    path = message.get("path") or {}
    project = path.get("cwd") if isinstance(path, dict) else None
    return _event(provider, "opencode:message:" + str(mid), sid, stamp, model,
                  project or session.get("directory"), inp, out, cached, write, reasoning)


def _source_signature(path):
    signature = []
    for p in (path, Path(str(path) + "-wal")):
        try:
            st = p.stat()
            signature.append((st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            signature.append(None)
    return signature


def _legacy_session(path, sid):
    # The former file store kept session metadata separately from message files.
    # Only look for this exact ID under the known adjacent storage/session tree.
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        return {}
    storage = next((parent for parent in path.parents if parent.name == "storage"), None)
    if storage is None:
        return {}
    candidates = (storage / "session").glob(f"*/{sid}.json")
    for metadata in candidates:
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeError):
            return {}
    return {}


@contextmanager
def _snapshot(path, result):
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix="tokenledger-opencode-") as directory:
            before = _source_signature(path)
            copy = Path(directory) / "opencode.db"
            shutil.copyfile(path, copy)
            wal = Path(str(path) + "-wal")
            if wal.is_file():
                shutil.copyfile(wal, str(copy) + "-wal")
            after = _source_signature(path)
            if before != after and attempt < 2:
                continue
            if before != after:
                _warn(result, "OpenCode 正在写入，当前为临时快照；下次扫描会继续核对。")
            with closing(sqlite3.connect(copy)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                yield connection
            return


def _opencode(path, provider):
    result = ParseResult()
    events = {}
    if path.suffix.lower() == ".db":
        with _snapshot(path, result) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            formats = [("session_message", "session_v2", "v2"), ("message", "session", "legacy")]
            found = False
            for table, session_table, mode in formats:
                if table not in tables:
                    continue
                found = True
                sessions = {}
                if session_table in tables:
                    sessions = {row["id"]: dict(row) for row in connection.execute(f"SELECT * FROM {session_table}")}
                for row in connection.execute(f"SELECT * FROM {table}"):
                    row = dict(row)
                    try:
                        message = json.loads(row.get("data") or "{}")
                        if not isinstance(message, dict):
                            raise ValueError
                        for key in ("id", "session_id", "type", "time_created"):
                            if key in row:
                                message.setdefault(key, row[key])
                        sid = row.get("session_id", message.get("sessionID"))
                        event = _opencode_event(message, provider, result, mode=mode, session=sessions.get(sid))
                        if event:
                            _merge(events, event)
                    except (ValueError, TypeError):
                        result.skipped += 1
                        _warn(result, "OpenCode 消息元数据损坏或尚未写完，已跳过。")
                if mode == "v2":
                    # Compaction/migration or older multi-step updates can leave
                    # session totals without their per-call timestamps/models.
                    # Report the exact coverage gap; never invent call dates.
                    gap = 0
                    for sid, session in sessions.items():
                        fields = ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")
                        if all(_nat(session.get(field)) is not None for field in fields):
                            recorded = sum(session[field] for field in fields)
                            retained = sum(event["total_tokens"] for event in events.values() if event["session_id"] == sid)
                            gap += max(0, recorded - retained)
                    if gap:
                        _warn(result, f"OpenCode 会话汇总比逐条记录多 {gap:,} token；差额缺少逐条时间/模型，未分配到统计，历史覆盖不完整。")
            if not found:
                _warn(result, "此 OpenCode 数据库没有受支持的消息表，无法精确读取。")
    else:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            messages = value if isinstance(value, list) else [value]
            for message in messages:
                sid = message.get("sessionID") if isinstance(message, dict) else None
                session = _legacy_session(path, sid)
                if not session and any(parent.name == "storage" for parent in path.parents):
                    result.skipped += 1
                    _warn(result, "OpenCode 旧消息缺少相邻会话元数据，暂未计入，以免把分叉继承的历史重复计算。")
                    continue
                event = _opencode_event(message, provider, result, session=session)
                if event:
                    _merge(events, event)
        except (ValueError, UnicodeError):
            result.skipped += 1
            _warn(result, "OpenCode JSON 记录损坏或尚未写完，已跳过。")
    result.events = list(events.values())
    return result


def _gemini_cli(path, provider):
    result = ParseResult()
    metadata = {}
    messages = []
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError
                    if "sessionId" in row:
                        metadata.update({k: row[k] for k in ("sessionId", "projectHash", "directories") if k in row})
                    if "$set" in row and isinstance(row["$set"], dict):
                        values = row["$set"]
                        metadata.update({k: values[k] for k in ("sessionId", "projectHash", "directories") if k in values})
                        if isinstance(values.get("messages"), list):
                            messages.extend(values["messages"])
                    if "messages" in row and isinstance(row["messages"], list):
                        messages.extend(row["messages"])
                    if row.get("type") == "gemini":
                        messages.append(row)
                    # Rewind/patch records alter display/history, not tokens already spent.
                except (ValueError, UnicodeError):
                    result.skipped += 1
                    _warn(result, "Gemini CLI JSONL 存在无效或尚未写完的记录，已跳过。")
    else:
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or not isinstance(metadata.get("messages"), list):
                raise ValueError
            messages = metadata["messages"]
        except (ValueError, UnicodeError):
            _warn(result, "Gemini CLI 会话 JSON 无效或尚未写完，无法读取用量。")
            result.skipped += 1
            return result
    events = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "gemini":
            continue
        usage = message.get("tokens")
        if not isinstance(usage, dict):
            result.skipped += 1
            continue
        counts = [_nat(usage.get(key, 0 if key in ("cached", "thoughts", "tool") else None))
                  for key in ("input", "output", "cached", "thoughts", "tool")]
        if None in counts:
            result.skipped += 1
            _warn(result, "Gemini CLI tokens 缺少完整 input/output，未推测用量。")
            continue
        prompt, visible, cached, thoughts, tool = counts
        inp, out = prompt + tool, visible + thoughts
        supplied = _nat(usage.get("total"))
        if cached > prompt or supplied is not None and supplied != inp + out:
            result.skipped += 1
            _warn(result, "Gemini CLI 分项与 total 不一致，已跳过以免重复计入推理。")
            continue
        mid, sid = message.get("id"), metadata.get("sessionId")
        stamp = _timestamp(message.get("timestamp"))
        if not mid or not sid or not stamp:
            result.skipped += 1
            _warn(result, "Gemini CLI 缺少稳定消息 ID、会话 ID 或有效时间，未导入。")
            continue
        directories = metadata.get("directories") or []
        project = directories[0] if isinstance(directories, list) and directories else metadata.get("projectHash", "Gemini CLI")
        event = _event(provider, "gemini-cli:message:" + str(mid), sid, stamp,
                       message.get("model"), project, inp, out, cached, 0, thoughts)
        _merge(events, event)
    result.events = list(events.values())
    if not events:
        _warn(result, "此 Gemini CLI 会话未保存可用 token 用量，不能由对话文本补算。")
    elif result.skipped:
        _warn(result, "部分 Gemini CLI 记录缺少用量字段，历史覆盖可能不完整。")
    return result


def parse_file(path: Path, kind: str, provider: str) -> ParseResult:
    path = Path(path)
    if kind == "opencode":
        return _opencode(path, provider)
    if kind == "gemini_cli":
        return _gemini_cli(path, provider)
    raise ValueError("Unsupported mainstream source")
