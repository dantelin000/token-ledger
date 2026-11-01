import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from tokenlog.adapters import AdapterRegistry, SourceSpec
from tokenlog.ide_imports import discover_files, parse_file


class IdeImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def csv_file(self, rows, name="cursor.csv"):
        path = self.root / name
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        path.write_text(output.getvalue(), encoding="utf-8-sig")
        return path

    def task(self, usage, metadata=None):
        path = self.root / "task-1" / "ui_messages.json"
        path.parent.mkdir(exist_ok=True)
        row = {"ts": 1793487600000, "type": "say", "say": "api_req_started", "text": json.dumps(usage)}
        path.write_text(json.dumps([row]), encoding="utf-8")
        if metadata:
            (path.parent / "task_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return path

    def test_cursor_split_columns_and_overlapping_export_identity(self):
        row = {"Date": "2026-11-01T00:00:00Z", "Model": "model", "Type": "Included",
               "Input (w/o Cache Write)": "10", "Input (w/ Cache Write)": "20",
               "Cache Read": "100", "Output Tokens": "30", "Total Tokens": "160"}
        path = self.csv_file([row, row])
        before = path.read_bytes()
        result = parse_file(path, "cursor_csv", "cursor")
        self.assertEqual(len(result.events), 2)
        self.assertEqual(sum(event["total_tokens"] for event in result.events), 320)
        self.assertEqual(result.events[0]["input_tokens"], 130)
        copy = self.csv_file([row, row], "copied.csv")
        self.assertEqual([e["event_key"] for e in result.events], [e["event_key"] for e in parse_file(copy, "cursor_csv", "cursor").events])
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(any("会话 ID" in warning for warning in result.warnings))

    def test_cursor_standard_columns_total_mismatch_is_not_estimated(self):
        row = {"Date": "2026-11-01T00:00:00Z", "Model": "model", "Input Tokens": "100",
               "Cache Write": "10", "Cache Read": "50", "Output Tokens": "20", "Total Tokens": "120"}
        result = parse_file(self.csv_file([row]), "cursor_csv", "cursor")
        self.assertEqual(result.events, [])
        self.assertEqual(result.skipped, 1)
        self.assertTrue(any("合计不一致" in warning for warning in result.warnings))

    def test_cursor_header_aliases_do_not_duplicate_overlapping_exports(self):
        base = {"Date": "2026-11-01T00:00:00Z", "Model": "model",
                "Input (w/o Cache Write)": "10", "Input (w/ Cache Write)": "20",
                "Cache Read": "100", "Output Tokens": "30", "Total Tokens": "160"}
        old = self.csv_file([{**base, "Type": "Included", "User": "person@example.invalid"}], "old.csv")
        new = self.csv_file([{**base, "Kind": "Included", "Email": "person@example.invalid"}], "new.csv")
        first = parse_file(old, "cursor_csv", "cursor").events[0]
        second = parse_file(new, "cursor_csv", "cursor").events[0]
        self.assertEqual(first["event_key"], second["event_key"])
        self.assertEqual(first["total_tokens"], 160)

    def test_cline_provider_history_distinguishes_cache_conventions(self):
        usage = {"tokensIn": 100, "tokensOut": 20, "cacheReads": 50, "cacheWrites": 10}
        metadata = {"cwd": "project", "model_usage": [{"ts": 1793487500000, "model_id": "model", "model_provider_id": "anthropic"}]}
        path = self.task(usage, metadata)
        events = parse_file(path, "cline", "cline").events
        self.assertEqual(events[0]["input_tokens"], 160)
        metadata["model_usage"][0]["model_provider_id"] = "openai"
        path = self.task(usage, metadata)
        events = parse_file(path, "cline", "cline").events
        self.assertEqual(events[0]["input_tokens"], 100)

    def test_unknown_cline_cache_semantics_are_reported_not_guessed(self):
        path = self.task({"tokensIn": 100, "tokensOut": 20, "cacheReads": 50})
        result = parse_file(path, "cline", "cline")
        self.assertEqual(result.events, [])
        self.assertTrue(any("缓存输入口径未知" in warning for warning in result.warnings))

    def test_roo_current_tokens_in_already_includes_cache_for_anthropic(self):
        path = self.task({"tokensIn": 160, "tokensOut": 20, "cacheReads": 50,
                          "cacheWrites": 10, "apiProtocol": "anthropic"})
        result = parse_file(path, "roo", "roo")
        self.assertEqual(result.events[0]["input_tokens"], 160)
        self.assertEqual(result.events[0]["total_tokens"], 180)

    def test_zero_cache_legacy_task_is_safe_and_only_known_receipt_file_is_discovered(self):
        path = self.task({"tokensIn": 100, "tokensOut": 20})
        (path.parent / "credentials.json").write_text("private", encoding="utf-8")
        result = parse_file(path, "cline", "cline")
        self.assertEqual(result.events[0]["total_tokens"], 120)
        self.assertEqual(discover_files("cline", [self.root]), [path.resolve()])

    def test_malformed_task_text_does_not_include_source_in_warning(self):
        path = self.task({})
        path.write_text(json.dumps([{"ts": 1, "type": "say", "say": "api_req_started", "text": "PRIVATE malformed request"}]))
        result = parse_file(path, "cline", "cline")
        self.assertEqual(result.events, [])
        self.assertNotIn("PRIVATE", "".join(result.warnings))

    def test_registry_includes_mainstream_and_cursor_stays_explicit_import(self):
        registry = AdapterRegistry()
        sources = {source.provider: source for source in registry.list_sources()}
        self.assertTrue({"claude", "codex", "agy", "opencode", "gemini-cli", "cline", "roo", "cursor"} <= sources.keys())
        self.assertFalse(sources["cursor"].enabled)
        self.assertEqual(sources["cursor"].kind, "cursor_csv")


if __name__ == "__main__":
    unittest.main()
