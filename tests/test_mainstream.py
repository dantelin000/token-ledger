from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tokenlog.mainstream import discover_files, parse_file


class MainstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_json(self, value, name="message.json"):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def message(self, **updates):
        value = dict(id="msg-1", sessionID="ses-1", role="assistant", time={"created": 1792192503768},
            modelID="model", path={"cwd": "/project"},
            tokens={"input": 10, "output": 20, "reasoning": 5, "cache": {"read": 30, "write": 2}, "total": 62})
        value.update(updates)
        return value

    def test_legacy_opencode_reasoning_inclusive_and_copies_deduplicate(self):
        path = self.write_json([self.message(), self.message()])
        result = parse_file(path, "opencode", "opencode")
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual(event["input_tokens"], 42)
        self.assertEqual(event["output_tokens"], 20)
        self.assertEqual(event["total_tokens"], 62)
        self.assertEqual(event["reasoning_tokens"], 5)

    def test_legacy_ambiguous_reasoning_without_total_is_warned(self):
        value = self.message()
        del value["tokens"]["total"]
        result = parse_file(self.write_json(value), "opencode", "opencode")
        self.assertFalse(result.events)
        self.assertTrue(result.warnings)

    def test_current_opencode_v2_and_wal_snapshot_never_write_source(self):
        path = self.root / "opencode.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("CREATE TABLE session_v2(id TEXT,directory TEXT,time_created INTEGER,fork_session_id TEXT)")
            connection.execute("CREATE TABLE session_message(id TEXT,session_id TEXT,type TEXT,time_created INTEGER,data TEXT)")
            connection.execute("INSERT INTO session_v2 VALUES('ses-1','/project',1792192500000,NULL)")
            value = self.message()
            del value["tokens"]["total"]
            del value["role"]
            connection.execute("INSERT INTO session_message VALUES(?,?,?,?,?)",
                ("msg-1", "ses-1", "assistant", 1792192503768, json.dumps(value)))
            connection.commit()
            originals = {p: p.read_bytes() for p in self.root.glob("opencode.db*")}
            result = parse_file(path, "opencode", "opencode")
            self.assertEqual(len(result.events), 1)
            self.assertEqual(result.events[0]["output_tokens"], 25)
            self.assertEqual(result.events[0]["total_tokens"], 67)
            for p, original in originals.items():
                self.assertEqual(p.read_bytes(), original)

    def test_legacy_sqlite_message_table_reads_metadata(self):
        path = self.root / "opencode.db"
        with closing(sqlite3.connect(path)) as c:
            c.execute("CREATE TABLE message(id TEXT,session_id TEXT,data TEXT)")
            c.execute("CREATE TABLE session(id TEXT,directory TEXT,time_created INTEGER,parent_id TEXT)")
            c.execute("INSERT INTO session VALUES('ses-1','/project',1792192500000,NULL)")
            c.execute("INSERT INTO message VALUES(?,?,?)", ("msg-1", "ses-1", json.dumps(self.message())))
            c.commit()
        result = parse_file(path, "opencode", "opencode")
        self.assertEqual(result.events[0]["total_tokens"], 62)

    def test_fork_copied_opencode_history_is_not_new_usage(self):
        path = self.root / "opencode.db"
        with closing(sqlite3.connect(path)) as c:
            c.execute("CREATE TABLE session_v2(id TEXT,directory TEXT,time_created INTEGER,fork_session_id TEXT)")
            c.execute("CREATE TABLE session_message(id TEXT,session_id TEXT,type TEXT,time_created INTEGER,data TEXT)")
            c.execute("INSERT INTO session_v2 VALUES('ses-1','/project',1792192600000,'old-session')")
            value = self.message()
            del value["tokens"]["total"]
            c.execute("INSERT INTO session_message VALUES(?,?,?,?,?)", ("msg-1", "ses-1", "assistant",1792192503768,json.dumps(value)))
            c.commit()
        self.assertFalse(parse_file(path, "opencode", "opencode").events)

    def test_opencode_session_aggregate_gap_is_explicit_not_fabricated(self):
        path = self.root / "opencode.db"
        with closing(sqlite3.connect(path)) as c:
            c.execute("CREATE TABLE session_v2(id TEXT,tokens_input INTEGER,tokens_output INTEGER,tokens_reasoning INTEGER,tokens_cache_read INTEGER,tokens_cache_write INTEGER)")
            c.execute("CREATE TABLE session_message(id TEXT,session_id TEXT,type TEXT,time_created INTEGER,data TEXT)")
            c.execute("INSERT INTO session_v2 VALUES('s1',100,20,5,30,0)")
            c.commit()
        result = parse_file(path, "opencode", "opencode")
        self.assertFalse(result.events)
        self.assertTrue(any("155" in warning and "历史覆盖不完整" in warning for warning in result.warnings))

    def test_legacy_json_fork_checks_adjacent_session_metadata(self):
        message = self.root / "storage/message/ses-1/msg-1.json"
        metadata = self.root / "storage/session/project/ses-1.json"
        message.parent.mkdir(parents=True)
        metadata.parent.mkdir(parents=True)
        message.write_text(json.dumps(self.message()))
        metadata.write_text(json.dumps({"id": "ses-1", "parentID": "parent", "time": {"created": 1792192600000}}))
        self.assertFalse(parse_file(message, "opencode", "opencode").events)

    def test_late_legacy_metadata_does_not_initially_count_inherited_usage(self):
        from tokenlog.store import Ledger
        message = self.root / "storage/message/ses-1/msg-1.json"
        metadata = self.root / "storage/session/project/ses-1.json"
        message.parent.mkdir(parents=True)
        message.write_text(json.dumps(self.message()))
        before = Ledger.signature(message)
        result = parse_file(message, "opencode", "opencode")
        self.assertFalse(result.events)
        self.assertTrue(result.warnings)
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({"id": "ses-1", "time": {"created": 1792192500000}}))
        self.assertNotEqual(Ledger.signature(message), before)
        self.assertEqual(parse_file(message, "opencode", "opencode").events[0]["total_tokens"], 62)

    def test_gemini_jsonl_snapshots_include_tool_and_thoughts_once(self):
        path = self.root / "session-test.jsonl"
        metadata = {"sessionId": "gemini-session", "projectHash": "hash", "directories": ["/project"]}
        message = {"id": "g1", "type": "gemini", "timestamp": "2026-10-20T12:00:00Z", "model": "gemini",
                   "tokens": {"input": 100, "output": 20, "cached": 60, "thoughts": 30, "tool": 5, "total": 155}}
        path.write_text("\n".join(json.dumps(row) for row in [metadata, message, message, {"$rewindTo": "g1"}]))
        result = parse_file(path, "gemini_cli", "gemini-cli")
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual((event["input_tokens"], event["output_tokens"], event["total_tokens"]), (105, 50, 155))
        self.assertEqual(event["cached_input_tokens"], 60)
        self.assertEqual(event["reasoning_tokens"], 30)

    def test_gemini_json_and_inconsistent_total_and_missing_usage(self):
        messages = [{"id": "g1", "type": "gemini", "timestamp": "2026-10-20T12:00:00Z", "tokens": {"input": 100,"output":20,"thoughts":30,"total":120}}]
        path = self.write_json({"sessionId": "s1", "messages": messages}, "session-test.json")
        result = parse_file(path, "gemini_cli", "gemini-cli")
        self.assertFalse(result.events)
        self.assertTrue(result.warnings)
        messages[0].pop("tokens")
        result = parse_file(self.write_json({"sessionId": "s1", "messages": messages}), "gemini_cli", "gemini-cli")
        self.assertFalse(result.events)
        self.assertTrue(result.warnings)

    def test_discovery_only_uses_history_locations(self):
        chats = self.root / "tmp/project/chats"
        chats.mkdir(parents=True)
        (chats / "session-one.jsonl").write_text("{}")
        (self.root / "oauth_creds.json").write_text("{}")
        files = discover_files("gemini_cli", [self.root])
        self.assertEqual([p.name for p in files], ["session-one.jsonl"])


if __name__ == "__main__":
    unittest.main()
