"""Language switching must leave accounting values and user content intact."""
import copy
from pathlib import PurePosixPath, PureWindowsPath
from string import Formatter
import unittest

from tokenlog.i18n import Translator
from tokenlog.pricing import PriceBook
from tokenlog.translations import EN


class LocalizationTests(unittest.TestCase):
    def test_catalog_preserves_format_arguments(self):
        formatter = Formatter()
        for source, english in EN.items():
            with self.subTest(source=source):
                before = sorted((field, spec, conversion) for _, field, spec, conversion in formatter.parse(source) if field is not None)
                after = sorted((field, spec, conversion) for _, field, spec, conversion in formatter.parse(english) if field is not None)
                self.assertEqual(before, after)
                self.assertNotRegex(english, r"[\u3400-\u9fff]")

    def test_prices_and_call_details_use_locale_without_changing_counts(self):
        from tokenlog.price_ui import detail_text
        event = {"provider": "sample", "model": "claude-opus-5-5", "session_id": "我的会话",
                 "project": "我的项目", "event_key": "sample-call", "timestamp": "2026-11-01T00:00:00Z",
                 "input_tokens": 1000, "output_tokens": 200, "cached_input_tokens": 400,
                 "cache_write_tokens": 200, "reasoning_tokens": 50, "total_tokens": 1200,
                 "cache_write_short_tokens": 0, "cache_write_long_tokens": 0, "method": "message"}
        event["api_estimate"] = PriceBook().quote(event)
        original = copy.deepcopy(event)
        english = detail_text(event)
        chinese = detail_text(event, language="zh_CN")
        self.assertIn("Cache write", english)
        self.assertIn("5 minutes to 1 hour", english)
        self.assertIn("缓存写入", chinese)
        for text in (english, chinese):
            self.assertIn("1,000", text)
            self.assertIn("1,200", text)
            self.assertIn("我的项目", text)
            self.assertIn("我的会话", text)
        self.assertNotRegex(english.replace("我的项目", "").replace("我的会话", ""), r"[\u3400-\u9fff]")
        self.assertEqual(event, original)

    def test_persisted_and_composed_diagnostics_translate(self):
        t = Translator()
        messages = ["2 条记录未导入：缺少 usage；未按零用量处理。",
                    "codex / rollout.jsonl: 跳过 3 条无效或尚未写完的 JSON 记录 · 检测到 1 次累计计数重置，边界使用 last_token_usage",
                    "2 个目录无法访问；已停用采集；此前的历史统计仍保留。",
                    "OpenCode 会话汇总比逐条记录多 1,234 token；差额缺少逐条时间/模型，未分配到统计，历史覆盖不完整。"]
        for message in messages:
            with self.subTest(message=message):
                self.assertNotRegex(t.message(message), r"[\u3400-\u9fff]")
                self.assertEqual(Translator("zh_CN").message(message), message)
        self.assertIn("1,234", t.message(messages[-1]))

    def test_unknown_content_and_substitution_values_are_preserved(self):
        t = Translator()
        for path, expected in ((PurePosixPath("/我的目录/账本.sqlite3"), "Ledger: /我的目录/账本.sqlite3"),
                               (PureWindowsPath("C:/我的目录/账本.sqlite3"), "Ledger: C:\\我的目录\\账本.sqlite3")):
            self.assertEqual(t("账本路径：{0}", path), expected)
        self.assertEqual(t.source_label("custom", "我的工具"), "我的工具")
        self.assertEqual(t.source_label("cursor", "我的 Cursor"), "我的 Cursor")
        self.assertEqual(t.message("用户写的任意内容"), "用户写的任意内容")
        self.assertEqual(t.money({"low_usd": "1.5", "high_usd": "1.5", "unpriced_records": 2}), "$1.50 (partial)")
        self.assertEqual(Translator("zh_CN").money(None), "未定价")


if __name__ == "__main__":
    unittest.main()
