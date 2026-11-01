import csv
from decimal import Decimal
import io
from pathlib import Path
import tempfile
import unittest

from tokenlog.pricing import PriceBook, empty_total, add_total, finish_total
from tokenlog.generic import normalize_usage
from tokenlog.adapters import AdapterRegistry, SourceSpec
from tokenlog.store import Ledger


def receipt(**extra):
    return {"model": "claude-opus-5-5", "input_tokens": 1_000_000, "output_tokens": 100_000,
        "cached_input_tokens": 400_000, "cache_write_tokens": 200_000,
        "reasoning_tokens": 50_000, **extra}


class PricingTests(unittest.TestCase):
    def test_ttl_unknown_is_range_and_write_is_not_counted_twice(self):
        q = PriceBook().quote(receipt())
        # Ordinary 400k*.4? rates are 4/1M, read .20, write5..8, output20.
        self.assertEqual(Decimal(q["low_usd"]), Decimal("4.68"))
        self.assertEqual(Decimal(q["high_usd"]), Decimal("5.28"))
        self.assertEqual(q["ordinary_input_tokens"], 400000)

    def test_known_mixed_ttls_have_one_value_reasoning_already_in_output(self):
        q = PriceBook().quote(receipt(cache_write_short_tokens=50000, cache_write_long_tokens=150000))
        self.assertEqual(Decimal(q["low_usd"]), Decimal("5.13"))
        self.assertEqual(q["low_usd"], q["high_usd"])
        self.assertEqual(q["components_low"]["output"], "2")

    def test_openai_context_threshold_is_per_call_including_cached_input(self):
        book = PriceBook()
        a = book.quote(receipt(model="gpt-6.1-sol", input_tokens=272000, output_tokens=100,
                              cached_input_tokens=200000, cache_write_tokens=0))
        b = book.quote(receipt(model="gpt-6.1-sol", input_tokens=272001, output_tokens=100,
                              cached_input_tokens=200000, cache_write_tokens=0))
        self.assertEqual(Decimal(a["low_usd"]), Decimal(".165"))
        self.assertEqual(Decimal(b["low_usd"]), Decimal(".329504"))

    def test_haiku_threshold_counts_all_input(self):
        q = PriceBook().quote(receipt(model="claude-haiku-5-5", input_tokens=100001,
            cached_input_tokens=90000, cache_write_tokens=0, output_tokens=10))
        self.assertEqual(q["rates_low"]["input"], "0.50")

    def test_unknown_proxy_models_are_not_guessed_or_zeroed(self):
        q = PriceBook().quote(receipt(model="gemini-3.8-flash-n"))
        self.assertIsNone(q["low_usd"])
        q = PriceBook().quote(receipt(model="codex-auto-review"))
        self.assertIsNone(q["low_usd"])

    def test_legacy_combined_delta_gets_context_range(self):
        q = PriceBook().quote(receipt(model="gpt-6-sol", method="counter_delta"))
        self.assertLess(Decimal(q["low_usd"]), Decimal(q["high_usd"]))

    def test_import_preserves_anthropic_and_openai_write_ttl_details(self):
        a = normalize_usage({"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30,
            "cache_creation": {"ephemeral_5m_input_tokens": 5, "ephemeral_1h_input_tokens": 25}}, "anthropic")
        b = normalize_usage({"input_tokens": 40, "output_tokens": 20, "input_tokens_details": {
            "cache_write_tokens": 30, "cache_write_tokens_details": {"ttl_30m_tokens": 5, "ttl_12h_tokens": 25}}}, "openai")
        for x in (a,b):
            self.assertEqual((x["input_tokens"],x["cache_write_short_tokens"],x["cache_write_long_tokens"]),(40,5,25))

    def test_override_roundtrip_and_invalid_prices_do_not_overwrite_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"prices.json"
            book = PriceBook(path)
            values = {"input":"2", "output":"4", "read":".1", "write_short":"2.5", "write_long":"5"}
            book.save_override("custom-model", values)
            self.assertIsNotNone(PriceBook(path).quote(receipt(model="custom-model"))["low_usd"])
            before = path.read_bytes()
            with self.assertRaises((ValueError, ArithmeticError)):
                book.save_override("custom-model", dict(values,input="NaN"))
            self.assertEqual(path.read_bytes(), before)

    def test_inconsistent_counts_are_excluded(self):
        self.assertIsNone(PriceBook().quote(receipt(cache_write_short_tokens=300000))["low_usd"])

    def test_group_sum_and_unknown_coverage(self):
        total = empty_total()
        add_total(total, PriceBook().quote(receipt()), 1_100_000)
        add_total(total, PriceBook().quote(receipt(model="unknown")), 1_100_000)
        q = finish_total(total)
        self.assertEqual((q["priced_records"], q["unpriced_records"], q["unpriced_tokens"]),(1,1,1100000))
        self.assertEqual(Decimal(q["low_usd"]), Decimal("4.68"))

    def test_full_receipt_csv_and_report_estimates_reconcile(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            f=root/"receipt.jsonl"
            f.write_text(json.dumps({"schema":"tokenlog.usage.v1","event_id":"sample-r1", "session_id":"s1",
                "timestamp":"2026-11-01T00:00:00Z", "model":"claude-opus-5-5", "usage":receipt()})+'\n')
            registry=AdapterRegistry(include_builtin=False,extra_sources=[SourceSpec("sample","Sample","tokenlog",[f])])
            ledger=Ledger(root/"ledger.db",root/"codex",root/"claude",registry=registry)
            self.assertTrue(ledger.sync())
            report=ledger.report(include_calls=True)
            cost=report["totals"]["api_estimate"]
            for group in ("sessions","models","daily","providers"):
                self.assertEqual(sum(Decimal(x["api_estimate"]["low_usd"]) for x in report[group]),Decimal(cost["low_usd"]))
            rows=list(csv.DictReader(io.StringIO(ledger.export_csv().lstrip('\ufeff'))))
            self.assertEqual(Decimal(rows[0]["api_equivalent_low_usd"]),Decimal(cost["low_usd"]))
            self.assertEqual(report["calls"][0]["api_estimate"]["low_usd"], cost["low_usd"])


if __name__ == "__main__":
    unittest.main()
