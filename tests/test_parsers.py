import json
from pathlib import Path
import tempfile
import unittest

from tokenlog.parsers import parse_file


def usage(i, o, cached=0, reasoning=0):
    return {"input_tokens": i, "output_tokens": o, "cached_input_tokens": cached,
            "reasoning_output_tokens": reasoning, "total_tokens": i + o}


def row(typ, payload, sec=1):
    return {"type": typ, "timestamp": f"2026-10-31T12:00:{sec:02d}.000Z", "payload": payload}


def context(turn="turn-a", model="model-a"):
    return row("turn_context", {"turn_id": turn, "model": model, "cwd": "/work/project"})


def counter(total, last=None, sec=1):
    return row("event_msg", {"type": "token_count", "info": {
        "total_token_usage": total, "last_token_usage": last or total}}, sec)


def response(ident, counts, turn="turn-a", sec=1, thread="thread-a"):
    return row("token_usage_record", {"thread_id": thread, "turn_id": turn,
        "response_id": ident, "usage": counts}, sec)


class ParserTests(unittest.TestCase):
    def parse(self, rows, provider="codex", tail=""):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)
            return parse_file(path, provider)

    def test_codex_responses_are_authoritative_over_lagging_counters(self):
        result = self.parse([
            row("session_meta", {"id": "thread-a", "cwd": "/work/project"}), context(),
            response("resp-1", usage(100, 20, 50, 4), sec=1),
            counter(usage(100, 20, 50, 4), sec=21),
            response("resp-2", usage(200, 30, 150, 8), sec=22),
            counter(usage(300, 50, 200, 12), usage(200, 30, 150, 8), sec=45),
        ])
        self.assertEqual(len(result.events), 2)
        self.assertEqual(sum(e["total_tokens"] for e in result.events), 350)
        self.assertEqual(sum(e["reasoning_tokens"] for e in result.events), 12)
        self.assertTrue(all(e["method"] == "response" for e in result.events))

    def test_legacy_counter_deltas_and_repeated_snapshots(self):
        result = self.parse([context(), counter(usage(100, 10, 70)),
            counter(usage(100, 10, 70), sec=2),
            counter(usage(250, 30, 190), usage(150, 20, 120), sec=3)])
        self.assertEqual([e["total_tokens"] for e in result.events], [110, 170])
        self.assertEqual(result.events[-1]["cached_input_tokens"], 120)

    def test_old_and_new_turns_can_coexist_without_double_counting(self):
        result = self.parse([context("old"), counter(usage(100, 10)),
            context("new", "model-b"), response("resp", usage(50, 5), turn="new", sec=2),
            counter(usage(150, 15), usage(50, 5), sec=3)])
        self.assertEqual(sum(e["total_tokens"] for e in result.events), 165)
        self.assertEqual({e["model"] for e in result.events}, {"model-a", "model-b"})

    def test_fork_does_not_bill_inherited_cumulative_history(self):
        result = self.parse([row("session_meta", {"id": "child", "forked_from_id": "parent"}),
            context(), counter(usage(10050, 1005), usage(50, 5)),
            counter(usage(10110, 1011), usage(60, 6), sec=2)])
        self.assertEqual(sum(e["total_tokens"] for e in result.events), 121)
        self.assertTrue(result.warnings)

    def test_fork_with_response_records_ignores_inherited_snapshot(self):
        result = self.parse([row("session_meta", {"id": "child", "forked_from_id": "parent"}),
            context(), response("resp", usage(50, 5)),
            counter(usage(10050, 1005), usage(0, 0), sec=2)])
        self.assertEqual(sum(e["total_tokens"] for e in result.events), 55)

    def test_counter_reset_uses_last_request(self):
        result = self.parse([context(), counter(usage(100, 10)),
            counter(usage(20, 2), usage(20, 2), sec=2),
            counter(usage(50, 5), usage(30, 3), sec=3)])
        self.assertEqual(sum(e["total_tokens"] for e in result.events), 165)
        self.assertTrue(result.warnings)

    def test_restored_guardian_counter_before_task_start_is_only_baseline(self):
        result = self.parse([row("session_meta", {"id": "guardian", "parent_thread_id": "parent"}),
            row("compacted", {}), context("previous-turn"),
            counter(usage(10000, 1000), usage(100, 10)),
            row("event_msg", {"type": "task_started", "turn_id": "new-turn"}),
            context("new-turn"), response("actual", usage(50, 5), turn="new-turn", thread="guardian", sec=2),
            counter(usage(10050, 1005), usage(50, 5), sec=3)])
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["total_tokens"], 55)
        self.assertTrue(result.warnings)

    def test_response_keys_survive_copies_and_preserve_child_thread(self):
        original = self.parse([context(), response("shared", usage(50, 5), thread="child")]).events[0]
        copied = self.parse([row("session_meta", {"id": "different"}), context(),
            response("shared", usage(50, 5), thread="child")]).events[0]
        self.assertEqual(original["event_key"], copied["event_key"])
        self.assertEqual(original["session_id"], "child")

    def test_embedded_child_usage_does_not_inherit_parent_model(self):
        result = self.parse([row("session_meta", {"id": "parent"}), context(),
            response("child-response", usage(50, 5), thread="child", turn="child-turn"),
            counter(usage(100, 10), sec=2)])
        child = next(e for e in result.events if e["session_id"] == "child")
        self.assertEqual(child["model"], "unknown")
        self.assertEqual(sum(e["total_tokens"] for e in result.events), 165)
        self.assertTrue(result.warnings)

    def test_child_transcript_can_contain_copied_parent_session_metadata(self):
        result = self.parse([
            row("session_meta", {"id": "child", "forked_from_id": "parent", "timestamp": "2026-10-31T12:00:02Z"}),
            row("session_meta", {"id": "parent", "timestamp": "2026-10-31T12:00:00Z"}),
            context("old-turn"), response("parent-response", usage(100, 10), turn="old-turn", thread="parent", sec=1),
            counter(usage(100, 10), sec=1), context("child-turn", "child-model"),
            response("child-response", usage(50, 5), turn="child-turn", thread="child", sec=3),
            counter(usage(150, 15), usage(50, 5), sec=4),
        ])
        self.assertEqual(len(result.events), 2)
        self.assertEqual(sum(e["total_tokens"] for e in result.events), 165)
        self.assertEqual(result.events[-1]["model"], "child-model")

    def test_claude_streaming_blocks_merge_maxima_and_include_cache(self):
        def message(output, second):
            return {"type": "assistant", "timestamp": f"2026-11-01T00:00:0{second}+11:00",
                "sessionId": "session", "cwd": "/work/claude", "requestId": "request",
                "message": {"id": "msg", "model": "claude-test", "usage": {
                    "input_tokens": 2, "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 20, "output_tokens": output,
                    "output_tokens_details": {"thinking_tokens": 3}}}}
        result = self.parse([message(5, 1), message(10, 2), message(10, 3)], "claude")
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual(event["input_tokens"], 122)
        self.assertEqual(event["total_tokens"], 132)
        self.assertEqual(event["reasoning_tokens"], 3)
        self.assertEqual(event["timestamp"], "2026-10-31T13:00:01.000Z")

    def test_missing_usage_is_not_a_zero_usage_request(self):
        result = self.parse([{"type": "assistant", "message": {"id": "x"},
            "timestamp": "2026-11-01T00:00:00Z"}], "claude", tail='{"type":')
        self.assertEqual(result.events, [])
        self.assertEqual(result.skipped, 2)
        self.assertEqual(len(result.warnings), 2)

    def test_invalid_timestamps_are_reported(self):
        r = response("x", usage(10, 1))
        r["timestamp"] = "2026-11-01T00:00:00"
        result = self.parse([r])
        self.assertEqual(result.events, [])
        self.assertEqual(result.skipped, 1)

    def test_synthetic_claude_message_is_not_a_call(self):
        result = self.parse([{"type": "assistant", "timestamp": "2026-11-01T00:00:00Z",
            "message": {"id": "synthetic", "model": "<synthetic>", "usage": usage(0, 0)}}], "claude")
        self.assertEqual(result.events, [])


if __name__ == "__main__":
    unittest.main()
