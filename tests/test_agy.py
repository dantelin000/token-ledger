from pathlib import Path
import csv
import io
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tokenlog.agy import parse_file


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def scalar(number, value):
    return varint(number << 3) + varint(value)


def blob(number, value):
    if isinstance(value, str):
        value = value.encode()
    return varint((number << 3) | 2) + varint(len(value)) + value


def usage(identity="response-1", input_tokens=100, cached=500, output=80):
    # Actual Antigravity ModelUsageStats schema fields, independent of parser.
    return (scalar(1, 1318) + scalar(2, input_tokens) + scalar(3, output)
            + scalar(4, 20) + scalar(5, cached) + scalar(9, 30)
            + blob(11, identity))


def make_database(path, wal=False):
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript("""
        CREATE TABLE trajectory_meta (trajectory_id TEXT, cascade_id TEXT);
        INSERT INTO trajectory_meta VALUES ('trajectory-1','conversation-1');
        CREATE TABLE trajectory_metadata_blob (id TEXT,data BLOB);
        CREATE TABLE steps (idx INTEGER,metadata BLOB);
        CREATE TABLE gen_metadata (idx INTEGER,data BLOB);
    """)
    workspace = blob(1, "file:///Users/example/My%20Project")
    connection.execute("INSERT INTO trajectory_metadata_blob VALUES ('main',?)", (blob(1, workspace),))
    return connection


def insert_generation(connection, retry=True):
    timestamp = scalar(1, 1790000000) + scalar(2, 500000000)
    stats = usage()
    metadata = blob(1, timestamp) + blob(9, stats)
    connection.execute("INSERT INTO steps VALUES (1,?)", (metadata,))
    chat = blob(4, stats) + blob(19, "gemini-example")
    if retry:
        # A retry_infos entry repeats the successful attempt; another attempt
        # has its own response ID and real billable usage.
        chat += blob(17, blob(2, stats))
        chat += blob(17, blob(2, usage("response-earlier", 10, 0, 50)))
    generator = blob(1, chat) + blob(2, varint(1))
    connection.execute("INSERT INTO gen_metadata VALUES (0,?)", (generator,))
    connection.commit()


class AntigravityTests(unittest.TestCase):
    def conversation(self, path, cascade, root, model, response):
        connection = make_database(path)
        connection.execute("UPDATE trajectory_meta SET cascade_id=?", (cascade,))
        connection.execute("UPDATE trajectory_metadata_blob SET data=?",
                           (blob(6, root) if root is not None else b"",))
        stats = usage(response)
        when = blob(1, scalar(1, 1790000000))
        connection.execute("INSERT INTO steps VALUES (1,?)", (when + blob(9, stats),))
        connection.execute("INSERT INTO gen_metadata VALUES (0,?)",
                           (blob(1, blob(4, stats) + blob(19, model)) + blob(2, varint(1)),))
        connection.commit()
        connection.close()

    def test_children_use_explicit_root_and_keep_distinct_call_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root, child = Path(directory)/"root.db", Path(directory)/"child.db"
            self.conversation(root,"root-one","root-one","claude-opus-5-5","root-response")
            self.conversation(child,"child-one","root-one","gpt-6-sol","child-response")
            a, b = parse_file(root).events[0], parse_file(child).events[0]
            self.assertEqual(a["session_id"],b["session_id"])
            self.assertEqual(a["session_id"],"root-one")
            self.assertNotEqual(a["event_key"],b["event_key"])
            self.assertNotEqual(a["model"],b["model"])

    def test_missing_empty_or_invalid_root_keeps_cascade_identity(self):
        from tokenlog.agy import _session_id
        for root in (None,b"",b"  ",b"\xff",b"bad\x00id",42):
            with self.subTest(root=root):
                metadata={} if root is None else {6:[root]}
                self.assertEqual(_session_id(metadata,"standalone"),"standalone")

    def test_upgrade_merges_children_and_snapshots_without_changing_accounting(self):
        from tokenlog.adapters import AdapterRegistry, SourceSpec
        from tokenlog.store import Ledger, COUNTERS
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            sources=base/"conversations"
            sources.mkdir()
            for name,cascade,root,model,response in (
                ("root","root-one","root-one","claude-opus-5-5","call-1"),
                ("child-a","child-one","root-one","gpt-6-sol","call-2"),
                ("child-b","child-two","root-one","gpt-6-sol","call-3"),
                ("other","root-two","root-two","claude-opus-5-5","call-4"),
                ("legacy","standalone",None,"claude-opus-5-5","call-5")):
                self.conversation(sources/(name+".db"),cascade,root,model,response)
            shutil.copyfile(sources/"child-a.db",sources/"snapshot.db")
            original={p.name:p.read_bytes() for p in sources.iterdir()}
            registry=AdapterRegistry(include_builtin=False,
                extra_sources=[SourceSpec("agy","agy","agy",[base])])
            ledger=Ledger(base/"ledger.db",base/"codex",base/"claude",registry=registry)
            with patch("tokenlog.agy._session_id",side_effect=lambda metadata,cascade:cascade):
                self.assertTrue(ledger.sync())
            before=ledger.report(include_calls=True)
            self.assertEqual(before["totals"]["sessions"],5)
            with ledger.connect() as c:
                c.execute("UPDATE metadata SET value='previous-parser' WHERE key='parser_signature'")
            self.assertTrue(ledger.sync())
            after=ledger.report(include_calls=True)
            self.assertEqual(after["totals"]["sessions"],3)
            self.assertEqual(after["totals"]["requests"],5)
            grouped={r["session_id"]:r for r in after["sessions"]}
            self.assertEqual(set(grouped),{"root-one","root-two","standalone"})
            self.assertEqual(grouped["root-one"]["requests"],3)
            self.assertIn("claude-opus-5-5",grouped["root-one"]["model"])
            self.assertIn("gpt-6-sol",grouped["root-one"]["model"])
            for key in (*COUNTERS,"requests","api_estimate"):
                self.assertEqual(before["totals"][key],after["totals"][key],key)
            self.assertEqual(before["models"],after["models"])
            self.assertEqual(before["daily"],after["daily"])
            self.assertEqual({r["event_key"]:r["api_estimate"] for r in before["calls"]},
                             {r["event_key"]:r["api_estimate"] for r in after["calls"]})
            csv_rows=list(csv.DictReader(io.StringIO(ledger.export_csv())))
            self.assertEqual(len(csv_rows),5)
            self.assertEqual(sum(int(r["total_tokens"]) for r in csv_rows),after["totals"]["total_tokens"])
            self.assertEqual(sum(r["total_tokens"] for r in after["sessions"]),after["totals"]["total_tokens"])
            # Force repeated snapshot parsing to verify that deduplication survives.
            self.assertTrue(ledger.sync(force=True))
            self.assertEqual(ledger.report()["sessions"],after["sessions"])
            self.assertEqual({p.name:p.read_bytes() for p in sources.iterdir()},original)

    def test_generation_step_retry_dedup_and_subcategories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.db"
            connection = make_database(path)
            insert_generation(connection)
            connection.close()
            before = path.read_bytes()
            result = parse_file(path)
            self.assertEqual(result.warnings, [])
            self.assertEqual(len(result.events), 2)
            event = next(e for e in result.events if e["event_key"].endswith("response-1"))
            self.assertEqual(event["input_tokens"], 620)
            self.assertEqual(event["output_tokens"], 80)
            self.assertEqual(event["reasoning_tokens"], 30)
            self.assertEqual(event["total_tokens"], 700)
            self.assertEqual(event["project"], "/Users/example/My Project")
            self.assertEqual(event["model"], "gemini-example")
            self.assertEqual(event["session_id"], "conversation-1")
            self.assertEqual(event["timestamp"], "2026-09-21T14:13:20.500Z")
            self.assertEqual(path.read_bytes(), before)

    def test_uncheckpointed_wal_is_included_without_writing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.db"
            connection = make_database(path, wal=True)
            insert_generation(connection, retry=False)
            self.assertTrue(Path(str(path) + "-wal").exists())
            originals = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            result = parse_file(path)
            self.assertEqual(len(result.events), 1)
            self.assertEqual(result.events[0]["total_tokens"], 700)
            self.assertEqual({p.name: p.read_bytes() for p in Path(directory).iterdir()}, originals)
            connection.close()

    def test_missing_usage_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.db"
            connection = make_database(path)
            connection.execute("INSERT INTO steps VALUES (1,?)", (blob(1, scalar(1, 1790000000)),))
            connection.commit()
            connection.close()
            result = parse_file(path)
            self.assertEqual(result.events, [])
            self.assertTrue(any("未记录" in warning for warning in result.warnings))

    def test_malformed_metadata_does_not_fabricate_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.db"
            connection = make_database(path)
            connection.execute("INSERT INTO gen_metadata VALUES (1,?)", (b"\x0a\x80",))
            connection.commit()
            connection.close()
            result = parse_file(path)
            self.assertEqual(result.events, [])
            self.assertTrue(any("解析失败" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
