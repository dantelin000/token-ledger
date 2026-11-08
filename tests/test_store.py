import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tokenlog.store import Ledger


def event(key="one", timestamp="2026-10-31T13:00:00.000000+00:00", amount=100):
    return dict(provider="codex", event_key=key, session_id="session-1", timestamp=timestamp,
                model="model", project="=unsafe spreadsheet formula", input_tokens=amount,
                cached_input_tokens=50, cache_write_tokens=0, output_tokens=10,
                reasoning_tokens=3, total_tokens=amount + 10, method="response")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = Ledger(self.root / "ledger.db", self.root / "codex", self.root / "claude", tz="Australia/Melbourne")
        self.source = self.root / "one.jsonl"
        self.source.write_text("fixture\n")

    def tearDown(self):
        self.tmp.cleanup()

    def sync_events(self, events, paths=None, force=False):
        result = SimpleNamespace(events=events, warnings=[], skipped=0)
        paths = paths or [self.source]
        with patch.object(self.ledger, "discover", return_value=[("codex", p) for p in paths]), \
             patch("tokenlog.parsers.parse_file", return_value=result):
            self.assertTrue(self.ledger.sync(force))

    def test_project_meter_tracks_directories_and_selected_sessions_without_double_counting(self):
        first = {**event('first'), 'project': '/work/game', 'session_id': 'one'}
        child = {**event('child'), 'project': '/work/game/art', 'session_id': 'two'}
        sibling = {**event('sibling'), 'project': '/work/game-other', 'session_id': 'three'}
        manual = {**event('manual'), 'project': 'unknown', 'session_id': 'four'}
        self.sync_events([first, child, sibling, manual])
        pid = self.ledger.save_tracked_project('Game', ['/work/game'], [('codex', 'one'), ('codex', 'four')])
        report = self.ledger.report()
        project = report['tracked_projects'][0]
        self.assertEqual((project['total_tokens'], project['sessions_count'], project['requests']), (330, 3, 3))
        self.assertEqual(report['totals']['total_tokens'], 440)
        self.assertEqual(self.ledger.report(provider='claude')['tracked_projects'][0]['total_tokens'], 0)
        self.assertEqual(self.ledger.report('2026-11-02', '2026-11-02')['tracked_projects'][0]['total_tokens'], 0)
        future = {**event('future'), 'project': '/work/game/src', 'session_id': 'five'}
        self.source.write_text('changed\n')
        self.sync_events([first, child, sibling, manual, future])
        self.assertEqual(self.ledger.report()['tracked_projects'][0]['total_tokens'], 440)
        reopened = Ledger(self.root / 'ledger.db', self.root / 'codex', self.root / 'claude')
        self.assertEqual(reopened.tracked_projects()[0]['id'], pid)
        reopened.save_tracked_project('Renamed', [], [('codex', 'four')], pid)
        self.assertEqual(reopened.report()['tracked_projects'][0]['total_tokens'], 110)
        reopened.delete_tracked_project(pid)
        self.assertEqual(reopened.report()['tracked_projects'], [])
        self.assertEqual(reopened.report()['totals']['total_tokens'], 550)
        with self.assertRaises(ValueError):
            reopened.save_tracked_project('Invalid', ['unknown'], [])

    def test_idempotent_and_duplicate_copied_sources(self):
        self.sync_events([event()])
        self.sync_events([event()])
        copy = self.root / "copy.jsonl"
        copy.write_text("fixture\n")
        self.sync_events([event()], [self.source, copy])
        report = self.ledger.report()
        self.assertEqual(report["totals"]["total_tokens"], 110)
        self.assertEqual(report["totals"]["requests"], 1)
        self.assertEqual(self.source.read_text(), "fixture\n")

    def test_changed_streaming_snapshot_updates_and_missing_source_preserves(self):
        self.sync_events([event()])
        self.source.write_text("fixture has grown\n")
        self.sync_events([event(amount=200)])
        self.assertEqual(self.ledger.report()["totals"]["total_tokens"], 210)
        self.source.unlink()
        with patch.object(self.ledger, "discover", return_value=[]):
            self.ledger.sync()
        self.assertEqual(self.ledger.report()["totals"]["total_tokens"], 210)
        self.assertTrue(any("移走" in n for n in self.ledger.report()["coverage"]["notes"]))

    def test_local_date_filter_includes_midnight_but_excludes_next(self):
        self.sync_events([event("before", "2026-10-31T12:59:59.999999+00:00"),
                          event("start", "2026-10-31T13:00:00.000000+00:00"),
                          event("end", "2026-11-01T13:00:00.000000+00:00")])
        report = self.ledger.report("2026-11-01", "2026-11-01")
        self.assertEqual(report["totals"]["requests"], 1)
        self.assertEqual(report["daily"][0]["date"], "2026-11-01")
        self.assertEqual(self.ledger.report(provider="claude")["totals"]["total_tokens"], 0)
        with self.assertRaises(ValueError):
            self.ledger.report("2026-11-02", "2026-11-01")

    def test_csv_counts_reconcile_and_formulas_are_escaped(self):
        self.sync_events([event()])
        rows = list(csv.DictReader(io.StringIO(self.ledger.export_csv().lstrip("\ufeff"))))
        self.assertEqual(int(rows[0]["total_tokens"]), 110)
        self.assertTrue(rows[0]["project"].startswith("'="))

    def test_invalid_rewrite_keeps_old_ledger(self):
        self.sync_events([event()])
        self.source.write_text("different\n")
        with patch.object(self.ledger, "discover", return_value=[("codex", self.source)]), \
             patch("tokenlog.parsers.parse_file", side_effect=OSError("unreadable")):
            self.ledger.sync()
        report = self.ledger.report()
        self.assertEqual(report["totals"]["total_tokens"], 110)
        self.assertTrue(report["coverage"]["errors"])

    def test_truncated_source_does_not_erase_previously_recorded_usage(self):
        self.sync_events([event("first"), event("second")])
        self.source.write_text("compacted source\n")
        self.sync_events([event("second", amount=90)])
        self.assertEqual(self.ledger.report()["totals"]["total_tokens"], 220)

    def test_real_parser_propagates_unreadable_source(self):
        from tokenlog.parsers import parse_file
        with self.assertRaises(OSError):
            parse_file(self.root / "does-not-exist.jsonl", "codex")

    def test_reassigning_same_file_replaces_derived_usage_without_double_count(self):
        from tokenlog.adapters import AdapterRegistry, SourceSpec
        custom = self.root / "usage.jsonl"
        custom.write_text('{"schema":"tokenlog.usage.v1","event_id":"receipt-1","session_id":"s-1",'
            '"timestamp":"2026-11-01T00:00:00Z","model":"test-model","usage":'
            '{"input_tokens":100,"output_tokens":20,"total_tokens":120}}\n')
        self.ledger.registry = AdapterRegistry(include_builtin=False,
            extra_sources=[SourceSpec("first", "First", "tokenlog", [custom])])
        self.assertTrue(self.ledger.sync())
        self.assertEqual(self.ledger.report()["totals"]["total_tokens"], 120)
        self.ledger.registry = AdapterRegistry(include_builtin=False,
            extra_sources=[SourceSpec("second", "Second", "tokenlog", [custom])])
        self.assertTrue(self.ledger.sync())
        report = self.ledger.report()
        self.assertEqual(report["totals"]["total_tokens"], 120)
        self.assertEqual(report["sessions"][0]["provider"], "second")

    def test_files_without_recorded_usage_are_not_confirmed_zero(self):
        self.ledger.source_status["codex"] = {"status": "ok", "files": 1, "warning": ""}
        row = next(p for p in self.ledger.report()["providers"] if p["provider"] == "codex")
        self.assertEqual(row["status"], "no_usage")
        self.assertIn("不等于零", row["warning"])


if __name__ == "__main__":
    unittest.main()
