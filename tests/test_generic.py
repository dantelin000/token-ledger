import json
import tempfile
import unittest
from pathlib import Path

from tokenlog.adapters import AdapterRegistry, SourceSpec
from tokenlog.generic import ReceiptError, normalize_usage, parse_file


def receipt(**changes):
    row = {"schema": "tokenlog.usage.v1", "event_id": "receipt-1", "session_id": "session-1",
           "timestamp": "2026-11-01T10:00:00+11:00", "model": "test-model", "project": "test-project",
           "usage": {"input_tokens": 100, "cached_input_tokens": 50, "cache_write_tokens": 10,
                     "output_tokens": 20, "reasoning_tokens": 5, "total_tokens": 120}}
    row.update(changes)
    return row


class GenericTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, records, name="usage.jsonl"):
        path = self.root / name
        path.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
        return path

    def test_canonical_receipts_deduplicate_copied_streaming_metadata(self):
        updated = receipt(usage={"input_tokens": 100, "cached_input_tokens": 50, "cache_write_tokens": 10,
                                 "output_tokens": 30, "reasoning_tokens": 10, "total_tokens": 130})
        path = self.write([receipt(), updated, updated])
        before = path.read_bytes()
        result = parse_file(path, "cursor", "tokenlog")
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual(event["timestamp"], "2026-10-31T23:00:00.000Z")
        self.assertEqual(event["total_tokens"], 130)
        self.assertEqual(event["provider"], "cursor")
        copy = self.write([updated], "copied.jsonl")
        self.assertEqual(parse_file(copy, "cursor", "tokenlog").events[0]["event_key"], event["event_key"])
        self.assertEqual(path.read_bytes(), before)

    def test_openai_chat_and_responses_share_exact_cached_semantics(self):
        chat = normalize_usage({"prompt_tokens": 100, "completion_tokens": 20,
                                "prompt_tokens_details": {"cached_tokens": 60},
                                "completion_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 120}, "openai")
        response = normalize_usage({"input_tokens": 100, "output_tokens": 20,
                                    "input_tokens_details": {"cached_tokens": 60},
                                    "output_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 120}, "openai")
        self.assertEqual(chat, response)
        self.assertEqual(chat["input_tokens"], 100)

    def test_anthropic_input_adds_cache_and_thinking_is_subset(self):
        counts = normalize_usage({"input_tokens": 2, "output_tokens": 20,
                                  "cache_read_input_tokens": 100, "cache_creation_input_tokens": 30,
                                  "output_tokens_details": {"thinking_tokens": 10}}, "anthropic")
        self.assertEqual(counts["input_tokens"], 132)
        self.assertEqual(counts["total_tokens"], 152)

    def test_gemini_includes_tool_input_and_thought_output_without_double_cache(self):
        counts = normalize_usage({"promptTokenCount": 100, "cachedContentTokenCount": 60,
                                  "toolUsePromptTokenCount": 15, "candidatesTokenCount": 20,
                                  "thoughtsTokenCount": 10, "totalTokenCount": 145}, "gemini")
        self.assertEqual(counts["input_tokens"], 115)
        self.assertEqual(counts["output_tokens"], 30)
        self.assertEqual(counts["total_tokens"], 145)
        with self.assertRaises(ReceiptError):
            normalize_usage({"promptTokenCount": 100, "responseTokenCount": 20, "totalTokenCount": 120}, "gemini")

    def test_missing_usage_ids_timezone_and_inconsistent_totals_are_not_zero_calls(self):
        rows = [receipt(usage=None), receipt(event_id=None), receipt(session_id=None),
                receipt(timestamp="2026-11-01T10:00:00"),
                receipt(usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 999})]
        result = parse_file(self.write(rows), "custom", "tokenlog")
        self.assertEqual(result.events, [])
        self.assertEqual(result.skipped, 5)
        self.assertTrue(result.warnings)

    def test_wrapper_and_json_array_import(self):
        row = {"session_id": "session", "timestamp": "2026-11-01T00:00:00Z", "project": "project",
               "response": {"id": "response", "model": "model",
                            "usage": {"input_tokens": 10, "output_tokens": 5}}}
        path = self.root / "usage.json"
        path.write_text(json.dumps([row]), encoding="utf-8")
        result = parse_file(path, "my-agent", "openai")
        self.assertEqual(result.events[0]["total_tokens"], 15)
        self.assertFalse(result.warnings)

    def test_anthropic_complementary_stream_fragments_share_one_record(self):
        start = {"request_id": "req-1", "session_id": "session", "timestamp": "2026-11-01T00:00:00Z",
                 "message": {"id": "msg-1", "model": "model", "usage": {"input_tokens": 10, "output_tokens": 0}}}
        delta = {"request_id": "req-1", "usage": {"output_tokens": 25}}
        result = parse_file(self.write([start, delta, delta]), "my-harness", "anthropic")
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["input_tokens"], 10)
        self.assertEqual(result.events[0]["output_tokens"], 25)
        self.assertEqual(result.events[0]["total_tokens"], 35)

    def test_custom_registry_discovery_disable_and_config_roundtrip(self):
        source = SourceSpec("my-agent", "我的 Agent", "tokenlog", [self.root])
        self.assertEqual(SourceSpec.from_dict(source.to_dict()), source)
        path = self.write([receipt()])
        registry = AdapterRegistry(extra_sources=[source], include_builtin=False)
        self.assertEqual(registry.discover(), [("my-agent", path.resolve())])
        self.assertEqual(registry.parse_file(path, "my-agent").events[0]["total_tokens"], 120)
        self.assertEqual(registry.labels, {"my-agent": "我的 Agent"})
        source.enabled = False
        self.assertEqual(registry.discover(), [])
        self.assertEqual(registry.status()["my-agent"]["status"], "disabled")

    def test_scoped_paths_and_provider_ids_are_validated(self):
        for provider in ["all", "Invalid", "../agent", ""]:
            with self.assertRaises(ValueError):
                SourceSpec(provider, "Agent", "tokenlog", [self.root])
        for root in [Path.home(), Path(Path.home().anchor)]:
            with self.assertRaises(ValueError):
                SourceSpec("agent", "Agent", "tokenlog", [root])
        with self.assertRaises(ValueError):
            SourceSpec("claude", "Agent", "tokenlog", [self.root])

    def test_overlapping_sources_have_one_deterministic_file_owner(self):
        path = self.write([receipt()])
        first = SourceSpec("one", "One", "tokenlog", [self.root])
        second = SourceSpec("two", "Two", "tokenlog", [path])
        registry = AdapterRegistry(include_builtin=False, extra_sources=[first, second])
        self.assertEqual(registry.discover(), [("one", path.resolve())])
        self.assertEqual(registry.status()["two"]["files"], 0)
        self.assertIn("避免重复统计", registry.status()["two"]["warning"])
        first.enabled = False
        self.assertEqual(registry.discover(), [("two", path.resolve())])

    def test_negative_float_boolean_or_bad_subcounts_are_rejected(self):
        for usage in [{"input_tokens": -1, "output_tokens": 0},
                      {"input_tokens": 1.5, "output_tokens": 0},
                      {"input_tokens": True, "output_tokens": 0},
                      {"input_tokens": 1, "output_tokens": 0, "cached_input_tokens": 2}]:
            with self.assertRaises(ReceiptError):
                normalize_usage(usage, "tokenlog")


if __name__ == "__main__":
    unittest.main()
