"""Read Cursor usage exports and Cline/Roo task usage without guessing cache math.

Cursor exports contain four disjoint token categories. Roo's current task writer
stores normalized totalInputTokens in tokensIn. Cline's legacy task writer stores
provider-convention input; task_metadata.model_usage identifies that convention.
Unknown cached-input conventions are reported and skipped, never estimated.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .generic import ReceiptError, _int, _timestamp
from .parsers import ParseResult, _merge_event


def default_roots(kind: str) -> list[Path]:
    if kind == "cursor_csv":
        return [Path.home() / ".tokenledger-imports" / "cursor"]
    extension = "saoudrizwan.claude-dev" if kind == "cline" else "rooveterinaryinc.roo-cline"
    home = Path.home()
    if sys.platform == "darwin":
        application = home / "Library" / "Application Support"
    elif sys.platform == "win32":
        application = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
    else:
        application = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    roots = [application / editor / "User" / "globalStorage" / extension
             for editor in ("Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf")]
    if kind == "cline":
        roots.append(home / ".cline" / "data")
    return roots


def discover_files(kind: str, roots: list[Path]) -> list[Path]:
    found = set()
    for root in roots:
        root = Path(root).expanduser()
        if root.is_file():
            if kind == "cursor_csv" and root.suffix.lower() == ".csv":
                found.add(root.resolve())
            elif kind in ("cline", "roo") and root.name == "ui_messages.json":
                found.add(root.resolve())
        elif root.is_dir():
            if kind == "cursor_csv":
                found.update(p.resolve() for p in root.rglob("*.csv") if p.is_file())
            else:
                # Select only the known task receipt file, never broad JSON or
                # extension settings that may contain authentication material.
                found.update(p.resolve() for p in root.rglob("ui_messages.json") if p.is_file())
    return sorted(found)


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _csv_count(row: dict, names, required=False):
    for name in names:
        if name in row and str(row[name]).strip() != "":
            text = str(row[name]).strip().replace(",", "")
            if not text.isascii() or not text.isdigit():
                raise ReceiptError("CSV token 字段不是非负整数")
            return int(text)
    if required:
        raise ReceiptError("CSV 缺少必需 token 列")
    return 0


def parse_cursor(path: Path, provider="cursor") -> ParseResult:
    result, events, fingerprints = ParseResult(), {}, Counter()
    without_ids = without_sessions = 0
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = {_header(value) for value in reader.fieldnames or []}
        if not headers:
            result.warnings.append("Cursor CSV 没有可读取的表头。")
            return result
        split = "inputwocachewrite" in headers and "inputwcachewrite" in headers
        for source in reader:
            row = {_header(k): v for k, v in source.items() if k}
            try:
                # The two official input columns are mutually exclusive base
                # and cache-write categories, rather than input + its subset.
                if split:
                    write = _csv_count(row, ("inputwcachewrite",), True)
                    base = _csv_count(row, ("inputwocachewrite",), True)
                else:
                    base = _csv_count(row, ("inputtokens", "input"), True)
                    write = _csv_count(row, ("cachewrite", "cachewritetokens"), True)
                read = _csv_count(row, ("cacheread", "cachereadtokens"), True)
                output = _csv_count(row, ("outputtokens", "output"), True)
                input_tokens = base + read + write
                total = _csv_count(row, ("totaltokens", "total"), True)
                if total != input_tokens + output:
                    raise ReceiptError("CSV Total 与四类 token 合计不一致，未猜测缓存口径")
                stamp = _timestamp(row.get("date") or row.get("timestamp") or row.get("time"))
                model = row.get("model") or "unknown"
                identifier = row.get("requestid") or row.get("eventid") or row.get("usageeventid")
                if not identifier:
                    # Preserve identical rows inside one export while merging
                    # overlapping exports independently of their filenames.
                    # Official exports have used Type/Kind and User/Email
                    # labels for the same fields. Column spelling is not part
                    # of an event's identity across overlapping exports.
                    category = (row.get("type") or row.get("kind") or "").strip()
                    actor = (row.get("email") or row.get("user") or row.get("userid") or "").strip()
                    identity = [stamp, model, base, write, read, output, category, actor]
                    digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
                    occurrence = fingerprints[digest]
                    fingerprints[digest] += 1
                    identifier = f"fingerprint:{digest}:{occurrence}"
                    without_ids += 1
                session = row.get("conversationid") or row.get("sessionid")
                if not session:
                    session = "cursor:unassigned-export"
                    without_sessions += 1
                reasoning = _csv_count(row, ("reasoningtokens", "thinkingtokens"))
                if reasoning > output:
                    raise ReceiptError("推理子计数超过输出")
                event = {"provider": provider, "event_key": "cursor:export:" + identifier,
                         "session_id": session, "timestamp": stamp, "model": model,
                         "project": row.get("project") or row.get("workspace") or "Cursor 用量导出（未分组）",
                         "input_tokens": input_tokens, "cached_input_tokens": read,
                         "cache_write_tokens": write, "output_tokens": output,
                         "reasoning_tokens": reasoning, "total_tokens": total, "method": "export"}
                _merge_event(events, event)
            except ReceiptError as exc:
                result.skipped += 1
                message = str(exc)
                if message not in result.warnings:
                    result.warnings.append(message + "；未按零用量处理。")
    result.events = list(events.values())
    if without_ids:
        result.warnings.append("Cursor 导出缺少调用 ID，按时间/模型/四类 token 及重复行序号去重；同毫秒相同记录的跨导出身份存在限制。")
    if without_sessions:
        result.warnings.append("Cursor CSV 未导出会话 ID，归入未分组导出；显示的分组数不代表真实会话数。")
    result.warnings.append("Cursor 为导出快照；请定期导入新的官方 Usage CSV。导出行可能合并多次模型调用。")
    return result


def _read_metadata(path: Path):
    meta_path = path.parent / "task_metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        with meta_path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def _model_for(meta: dict, stamp_ms, usage: dict):
    entries = meta.get("model_usage", [])
    history = [value for value in (entries if isinstance(entries, list) else [])
               if isinstance(value, dict) and isinstance(value.get("ts"), (int, float)) and value["ts"] <= stamp_ms]
    chosen = max(history, key=lambda value: value["ts"]) if history else {}
    return (usage.get("model") or usage.get("modelId") or chosen.get("model_id") or "unknown",
            usage.get("apiProvider") or chosen.get("model_provider_id"))


def _task_counts(usage: dict, kind: str, api_provider):
    input_tokens = _int(usage.get("tokensIn"), True)
    output = _int(usage.get("tokensOut"), True)
    read, write = _int(usage.get("cacheReads")), _int(usage.get("cacheWrites"))
    if "totalInputTokens" in usage:
        input_tokens = _int(usage["totalInputTokens"], True)
    elif not read and not write:
        pass
    elif type(usage.get("cacheReadsAlreadyIncludedInInput")) is bool:
        if not usage["cacheReadsAlreadyIncludedInInput"]:
            input_tokens += read + write
    elif kind == "roo" and usage.get("apiProtocol") in ("anthropic", "openai"):
        # Roo Task.ts writes costResult.totalInputTokens to tokensIn for both
        # protocols. Adding cache again would double-count even Anthropic rows.
        pass
    elif kind == "cline":
        if api_provider in ("anthropic", "bedrock", "vertex") or usage.get("apiProtocol") == "anthropic":
            input_tokens += read + write
        elif api_provider in ("openai", "openai-native", "lmstudio", "ollama") or usage.get("apiProtocol") == "openai":
            pass
        else:
            raise ReceiptError("Cline 缓存输入口径未知：缺少可确认的 API provider/protocol")
    else:
        raise ReceiptError("旧 Roo 缓存输入口径未知：缺少已规范化输入的协议标记")
    reasoning = _int(usage.get("reasoningTokens"))
    if read + write > input_tokens or reasoning > output:
        raise ReceiptError("缓存或推理子计数超过所属总计")
    return {"input_tokens": input_tokens, "output_tokens": output,
            "cached_input_tokens": read, "cache_write_tokens": write,
            "reasoning_tokens": reasoning, "total_tokens": input_tokens + output}


def parse_task(path: Path, kind: str, provider: str) -> ParseResult:
    result = ParseResult()
    with Path(path).open("r", encoding="utf-8") as handle:
        try:
            rows = json.load(handle)
        except (ValueError, RecursionError):
            result.warnings.append("任务日志无效或尚未写完；未推测 token。")
            return result
    if not isinstance(rows, list):
        result.warnings.append("不支持的 Cline/Roo 任务日志结构；请选择 ui_messages.json。")
        return result
    metadata = _read_metadata(Path(path))
    events, warnings = {}, Counter()
    unknown = 0
    aggregates = 0
    for row in rows:
        if isinstance(row, dict) and row.get("say") in ("deleted_api_reqs", "subagent_usage"):
            aggregates += 1
        if not isinstance(row, dict) or row.get("type") != "say" or row.get("say") != "api_req_started":
            continue
        try:
            usage = json.loads(row["text"]) if isinstance(row.get("text"), str) else row.get("text")
            if not isinstance(usage, dict):
                raise ReceiptError("缺少结构化调用 usage")
            stamp_ms = row.get("ts")
            if type(stamp_ms) not in (int, float):
                raise ReceiptError("调用缺少时间戳")
            stamp = _timestamp(stamp_ms / 1000)
            model, api_provider = _model_for(metadata, stamp_ms, usage)
            if not isinstance(model, str):
                model = "unknown"
            counts = _task_counts(usage, kind, api_provider)
            identifier = usage.get("requestId") or usage.get("responseId") or row.get("id") or f"{Path(path).parent.name}:{stamp_ms}"
            project = usage.get("cwd") or metadata.get("cwd") or metadata.get("workspace") or "unknown"
            if not isinstance(project, str):
                project = "unknown"
            event = {"provider": provider, "event_key": f"{kind}:task-call:{identifier}",
                     "session_id": Path(path).parent.name, "timestamp": stamp,
                     "model": str(model), "project": str(project), "method": "task_usage", **counts}
            _merge_event(events, event)
            unknown += model == "unknown"
        except (ReceiptError, ValueError, KeyError) as exc:
            result.skipped += 1
            # Never retain request text or exceptions that embed source data.
            message = str(exc) if isinstance(exc, ReceiptError) else "缺少有效的调用 usage、身份或时间"
            warnings[message] += 1
    result.events = list(events.values())
    result.warnings.extend(f"{count} 条任务记录未导入：{message}；未按零用量处理。" for message, count in warnings.items())
    if unknown:
        result.warnings.append("部分任务调用未保存模型名称，归入 unknown。")
    if events and any(event["project"] == "unknown" for event in events.values()):
        result.warnings.append("任务日志未保存可确认的工作区路径，项目归入 unknown。")
    if aggregates:
        result.warnings.append("任务包含已删除请求或子代理的聚合摘要，未与逐条调用叠加以避免重复；该部分历史覆盖可能不完整。")
    return result


def parse_file(path: Path, kind: str, provider: str) -> ParseResult:
    if kind == "cursor_csv":
        return parse_cursor(path, provider)
    if kind in ("cline", "roo"):
        return parse_task(path, kind, provider)
    raise ValueError("不支持的 IDE 用量记录格式。")
