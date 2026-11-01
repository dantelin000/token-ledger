import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tokenlog.adapters import SourceSpec
from tokenlog.settings import AppSettings, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_missing_file_uses_portable_builtin_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = load_settings(Path(directory) / "missing.json")
        self.assertEqual(settings.timezone, "local")
        self.assertEqual(settings.interval, 15)
        self.assertEqual(settings.language, "en")
        self.assertEqual({s.provider for s in settings.sources}, {"claude", "codex", "agy", "opencode", "gemini-cli", "cline", "roo", "cursor"})

    def test_legacy_preferences_gain_new_tools_without_reenabling_sources(self):
        original = SourceSpec("codex", "My Codex", "codex", [Path("/chosen/codex")], False)
        loaded = AppSettings.from_dict({"version": 1, "sources": [original.to_dict()]})
        codex = next(source for source in loaded.sources if source.provider == "codex")
        self.assertEqual(codex.to_dict(), original.to_dict())
        self.assertIn("opencode", {source.provider for source in loaded.sources})
        self.assertEqual(loaded.to_dict()["version"], 2)
        self.assertEqual(loaded.language, "en")

    def test_roundtrip_preserves_disabled_builtin_and_custom_source(self):
        settings = AppSettings(sources=[SourceSpec("codex", "Codex", "codex", [Path("/chosen/codex")], False),
            SourceSpec("custom", "我的 Harness", "openai", [Path("/chosen/logs")], True)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences" / "settings.json"
            save_settings(settings, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.to_dict(), settings.to_dict())
            self.assertFalse(loaded.sources[0].enabled)
            self.assertEqual(list(path.parent.glob(".settings-*")), [])

    def test_duplicate_source_and_bad_settings_are_rejected(self):
        source = SourceSpec("custom", "Custom", "tokenlog", [Path("/chosen/logs")]).to_dict()
        for value in ({"interval": True}, {"interval": 0}, {"timezone": ""}, {"sources": {}},
                      {"version": 99}, {"language": "invalid"}, {"language": []}, {"sources": [source, source]},
                      {"sources": [dict(source, kind="unknown")]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AppSettings.from_dict(value)

    def test_language_persists_without_changing_source_preferences(self):
        settings = AppSettings(language="zh_CN", interval=30)
        original_sources = [source.to_dict() for source in settings.sources]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(settings, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.language, "zh_CN")
            self.assertEqual(loaded.interval, 30)
            self.assertEqual([source.to_dict() for source in loaded.sources], original_sources)
        legacy = settings.to_dict()
        legacy.pop("language")
        self.assertEqual(AppSettings.from_dict(legacy).language, "en")

    def test_atomic_failure_preserves_existing_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(AppSettings(sources=[]), path)
            original = path.read_bytes()
            with patch("tokenlog.settings.os.replace", side_effect=OSError("failure")), self.assertRaises(OSError):
                save_settings(AppSettings(interval=30, sources=[]), path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".settings-*")), [])

    def test_malformed_settings_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{incomplete", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{incomplete")


if __name__ == "__main__":
    unittest.main()
