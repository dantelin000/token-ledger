"""Read-only source scanning and a private, persistent SQLite usage ledger."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROVIDERS = {"claude": "Claude Code", "codex": "Codex", "agy": "agy"}
COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens",
            "reasoning_tokens", "total_tokens", "cache_write_short_tokens", "cache_write_long_tokens")
FIELDS = ("provider", "event_key", "session_id", "timestamp", "model", "project", *COUNTERS, "method")


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def blank():
    return {**dict.fromkeys(COUNTERS, 0), "requests": 0, "sessions": 0}


class Ledger:
    def __init__(self, db: Path, codex_home: Path, claude_home: Path,
                 tz: str = "local", registry=None):
        self.db = Path(db).resolve()
        self.codex_home = Path(codex_home).expanduser().resolve()
        self.claude_home = Path(claude_home).expanduser().resolve()
        self.timezone_name = tz
        self.tz = None if tz == "local" else ZoneInfo(tz)
        from .adapters import AdapterRegistry
        self.registry = registry or AdapterRegistry(codex_home=self.codex_home, claude_home=self.claude_home)
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.state = {"last_sync": None, "scanning": False, "error": None}
        self.source_status = {}
        from .pricing import PriceBook
        self.pricebook = PriceBook(self.db.parent / "price-overrides.json")
        self.db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._init_db()

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def _init_db(self):
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (
                    path TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    scanned_at TEXT NOT NULL, events INTEGER NOT NULL,
                    warning TEXT NOT NULL DEFAULT '', present INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                    source_path TEXT NOT NULL, provider TEXT NOT NULL,
                    event_key TEXT NOT NULL, session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL, model TEXT NOT NULL, project TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL, cached_input_tokens INTEGER NOT NULL,
                    cache_write_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL,
                    method TEXT NOT NULL, PRIMARY KEY(source_path, provider, event_key)
                );
                CREATE TABLE IF NOT EXISTS usage (
                    provider TEXT NOT NULL, event_key TEXT NOT NULL,
                    session_id TEXT NOT NULL, timestamp TEXT NOT NULL,
                    model TEXT NOT NULL, project TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL, cached_input_tokens INTEGER NOT NULL,
                    cache_write_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL,
                    method TEXT NOT NULL, PRIMARY KEY(provider, event_key)
                );
                CREATE INDEX IF NOT EXISTS usage_time ON usage(timestamp,provider);
                CREATE INDEX IF NOT EXISTS obs_key ON observations(provider,event_key);
            """)
            for table in ("observations", "usage"):
                existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                for column in ("cache_write_short_tokens", "cache_write_long_tokens"):
                    if column not in existing:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        try:
            self.db.chmod(0o600)
        except OSError:
            pass

    def discover(self):
        found = self.registry.discover()
        self.source_status = self.registry.status()
        return found

    def sync(self, force=False):
        if not self.scan_lock.acquire(blocking=False):
            return False
        self.state.update(scanning=True, error=None)
        try:
            files = self.discover()
            from . import __version__
            formats = json.dumps([(s.provider, s.kind) for s in self.registry.list_sources()], separators=(",", ":")).encode()
            signature = hashlib.sha256(__version__.encode() + formats + b"".join(
                p.read_bytes() for p in (Path(__file__).parent / name for name in ("parsers.py", "agy.py", "generic.py", "adapters.py", "mainstream.py", "ide_imports.py")) if p.exists()
            )).hexdigest()
            with self.connect() as c:
                previous = {r["path"]: r for r in c.execute("SELECT * FROM sources")}
                old_signature = c.execute("SELECT value FROM metadata WHERE key='parser_signature'").fetchone()
            parser_changed = not old_signature or old_signature[0] != signature
            force = force or parser_changed
            current = {str(p) for _, p in files}
            changed = False
            for provider, path in files:
                old = previous.get(str(path))
                try:
                    size, mtime_ns = self.signature(path)
                    if not force and old and old["provider"] == provider and old["size"] == size and old["mtime_ns"] == mtime_ns:
                        continue
                    result = self.registry.parse_file(path, provider)
                    # Store only allowlisted usage metadata, never messages, prompts or credentials.
                    rows = []
                    for ev in result.events:
                        if ev.get("provider") != provider:
                            continue
                        values = []
                        for f in FIELDS:
                            v = ev.get(f, "unknown" if f == "model" else "")
                            if f in COUNTERS:
                                v = max(0, int(v or 0))
                            values.append(v)
                        rows.append((str(path), *values))
                    with self.connect() as c:
                        # A parser update re-evaluates current source records.
                        # Routine log truncation/compaction must not erase past calls.
                        if parser_changed:
                            c.execute("DELETE FROM observations WHERE source_path=?", (str(path),))
                        update = ",".join(f"{f}=excluded.{f}" for f in FIELDS if f not in ("provider", "event_key", "model"))
                        update += ",model=CASE WHEN excluded.model='unknown' THEN observations.model ELSE excluded.model END"
                        c.executemany(f"""INSERT INTO observations(source_path,{','.join(FIELDS)})
                            VALUES ({','.join('?' for _ in range(len(FIELDS)+1))})
                            ON CONFLICT(source_path,provider,event_key) DO UPDATE SET {update}
                            WHERE excluded.total_tokens>=observations.total_tokens""", rows)
                        c.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?,1)",
                                  (str(path), provider, size, mtime_ns,
                                   utcnow(), len(rows), " · ".join(result.warnings[:5])))
                    changed = True
                except (OSError, ValueError, sqlite3.Error) as exc:
                    # Do not expose exception text that could contain source lines.
                    with self.connect() as c:
                        c.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?,1)",
                                  (str(path), provider, -1, -1, utcnow(), old["events"] if old else 0,
                                   f"读取失败：{type(exc).__name__}；已保留之前的统计。"))
            with self.connect() as c:
                for path in previous:
                    c.execute("UPDATE sources SET present=? WHERE path=?", (int(path in current), path))
                if changed:
                    c.execute("DELETE FROM usage")
                    fields = ",".join(FIELDS)
                    c.execute(f"""INSERT INTO usage({fields})
                        SELECT {fields} FROM (
                            SELECT *, ROW_NUMBER() OVER (
                                PARTITION BY provider,event_key
                                ORDER BY total_tokens DESC,CASE WHEN model='unknown' THEN 1 ELSE 0 END,timestamp,source_path
                            ) AS rank FROM observations
                        ) WHERE rank=1""")
                c.execute("INSERT OR REPLACE INTO metadata VALUES('parser_signature',?)", (signature,))
            self.state["last_sync"] = utcnow()
            return True
        except Exception as exc:
            self.state["error"] = f"扫描失败：{type(exc).__name__}；可重试刷新。"
            return False
        finally:
            self.state["scanning"] = False
            self.scan_lock.release()

    @staticmethod
    def signature(path):
        stat = path.stat()
        size, mtime_ns = stat.st_size, stat.st_mtime_ns
        # SQLite may append usage to WAL before the main database changes.
        if path.suffix == ".db":
            wal = Path(str(path) + "-wal")
            if wal.is_file():
                wal_stat = wal.stat()
                size += wal_stat.st_size
                mtime_ns = max(mtime_ns, wal_stat.st_mtime_ns)
        if path.name == "ui_messages.json":
            metadata = path.with_name("task_metadata.json")
            if metadata.is_file():
                info = metadata.stat()
                size += info.st_size
                mtime_ns = max(mtime_ns, info.st_mtime_ns)
        if path.suffix == ".json" and path.parent.parent.name == "message" and path.parent.parent.parent.name == "storage":
            for metadata in (path.parent.parent.parent / "session").glob(f"*/{path.parent.name}.json"):
                info = metadata.stat()
                size += info.st_size
                mtime_ns = max(mtime_ns, info.st_mtime_ns)
        return size, mtime_ns

    def _bounds(self, start, end):
        # Dates use the user's timezone, including DST, rather than log file names.
        first = date.fromisoformat(start) if start else None
        last = date.fromisoformat(end) if end else None
        if first and last and first > last:
            raise ValueError("开始日期不能晚于结束日期")
        # tz=None uses the OS timezone for each historical date, retaining DST.
        lo = datetime.combine(first, dtime.min, self.tz).astimezone(timezone.utc).isoformat() if first else None
        hi = datetime.combine(last + timedelta(days=1), dtime.min, self.tz).astimezone(timezone.utc).isoformat() if last else None
        return lo, hi

    def events(self, start=None, end=None, provider="all"):
        lo, hi = self._bounds(start, end)
        if provider != "all" and provider not in self.registry.labels:
            with self.connect() as c:
                if not c.execute("SELECT 1 FROM usage WHERE provider=? LIMIT 1", (provider,)).fetchone():
                    raise ValueError("未知数据源")
        clauses, params = [], []
        if lo:
            clauses.append("timestamp>=?")
            params.append(lo)
        if hi:
            clauses.append("timestamp<?")
            params.append(hi)
        if provider != "all":
            clauses.append("provider=?")
            params.append(provider)
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM usage" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY timestamp DESC", params)]

    def report(self, start=None, end=None, provider="all", include_calls=False):
        from .pricing import empty_total, add_total, finish_total
        rows = self.events(start, end, provider)
        labels = dict(self.registry.labels)
        for row in rows:
            labels.setdefault(row["provider"], row["provider"])
        totals, providers = blank(), {p: blank() for p in labels}
        daily, models, sessions = {}, {}, {}
        unique_sessions = set()
        provider_sessions = defaultdict(set)
        cost_groups = {}
        missing_models = defaultdict(int)
        calls = []
        for row in rows:
            p, sid = row["provider"], row["session_id"]
            day = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).astimezone(self.tz).date().isoformat()
            d = daily.setdefault((day, p), {"date": day, "provider": p, **blank()})
            m = models.setdefault((p, row["model"]), {"provider": p, "model": row["model"], **blank()})
            s = sessions.setdefault((p, sid), {"provider": p, "session_id": sid,
                "project": row["project"], "model": row["model"], "last_seen": row["timestamp"], **blank()})
            if s["model"] != row["model"] and row["model"] not in s["model"].split(", "):
                s["model"] += ", " + row["model"]
            quote = self.pricebook.quote(row)
            if quote["low_usd"] is None:
                missing_models[row["model"]] += 1
            if include_calls:
                calls.append({**row, "api_estimate": quote})
            for group in (totals, providers[p], d, m, s):
                for f in COUNTERS:
                    group[f] += row[f]
                group["requests"] += 1
                cost = cost_groups.setdefault(id(group), (group, empty_total()))[1]
                add_total(cost, quote, row["total_tokens"])
            unique_sessions.add((p, sid))
            provider_sessions[p].add(sid)
        totals["sessions"] = len(unique_sessions)
        for p in labels:
            providers[p]["sessions"] = len(provider_sessions[p])
        with self.connect() as c:
            coverage = dict(c.execute("SELECT MIN(timestamp) first_seen, MAX(timestamp) last_seen FROM usage").fetchone())
            sources = [dict(r) for r in c.execute("SELECT provider,path,present,warning FROM sources")]
            recorded = dict(c.execute("SELECT provider,COUNT(*) FROM usage GROUP BY provider"))
        errors = [f"{r['provider']} / {Path(r['path']).name}: {r['warning']}" for r in sources if r["warning"]]
        missing = sum(not r["present"] for r in sources)
        notes = [
            "本机日志中的已记录用量；不包含其他设备、普通网页聊天或未保存日志的调用，也不等同于订阅额度或账单。",
            "输入包含缓存读取和缓存写入；推理 token 已包含在输出中，请勿再次相加。",
            "Claude 按消息 ID 去重；Codex 优先使用响应记录，旧记录按累计计数求差值；其他数据源按所选格式解析。",
            "调用记录数可能包含旧日志的合并增量，不能据此精确计算 API 请求次数。",
        ]
        if missing:
            notes.append(f"{missing} 个原日志已移走或删除，账本保留此前已采集的用量。")
        coverage.update(files=len(sources), errors=errors, notes=notes)
        for group, cost in cost_groups.values():
            group["api_estimate"] = finish_total(cost)
        pricing = self.pricebook.info()
        pricing["unpriced_models"] = dict(missing_models)
        provider_rows = []
        for p, label in labels.items():
            status = dict(self.source_status.get(p, {"status": "missing", "files": 0, "warning": "等待扫描。"}))
            if status["status"] == "ok" and not recorded.get(p, 0):
                status.update(status="no_usage", warning="找到了日志，但没有可确认的 token 记录；缺少用量不等于零消耗。")
            provider_rows.append({"provider": p, "label": label, **providers[p], **status})
        return {
            "generated_at": utcnow(), "timezone": self.timezone_name, "range": {"start": start, "end": end},
            "totals": totals,
            "providers": provider_rows,
            "daily": sorted(daily.values(), key=lambda r: (r["date"], r["provider"])),
            "models": sorted(models.values(), key=lambda r: -r["total_tokens"]),
            "sessions": sorted(sessions.values(), key=lambda r: r["last_seen"], reverse=True),
            "coverage": coverage, "sync": dict(self.state),
            "pricing": pricing, **({"calls": calls} if include_calls else {}),
        }

    def export_csv(self, start=None, end=None, provider="all"):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        extra = ("api_equivalent_low_usd", "api_equivalent_high_usd", "pricing_currency", "pricing_as_of", "pricing_note")
        writer.writerow((*FIELDS, *extra))
        for row in self.events(start, end, provider):
            values = []
            for f in FIELDS:
                value = row[f]
                # Treat source metadata as spreadsheet text, never formulas.
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                    value = "'" + value
                values.append(value)
            quote = self.pricebook.quote(row)
            extras=[quote["low_usd"], quote["high_usd"], "USD", quote.get("verified_at", ""), " · ".join(quote["notes"])]
            for index,value in enumerate(extras):
                if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@","\t","\r")):
                    extras[index]="'"+value
            writer.writerow((*values, *extras))
        return "\ufeff" + output.getvalue()
