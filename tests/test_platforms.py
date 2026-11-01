import tempfile
import unittest
from pathlib import Path

from tokenlog.platforms import default_timezone_name, resolve_timezone, user_data_dir


class PlatformPathsTests(unittest.TestCase):
    def test_macos_location_is_user_application_support(self):
        self.assertEqual(user_data_dir("darwin", {}, "/sample/home"),
                         Path("/sample/home/Library/Application Support/TokenLedger"))

    def test_windows_honors_localappdata_and_has_fallback(self):
        self.assertEqual(user_data_dir("win32", {"LOCALAPPDATA": "/sample/local"}, "/sample/home"),
                         Path("/sample/local/TokenLedger"))
        self.assertEqual(user_data_dir("win32", {}, "/sample/home"),
                         Path("/sample/home/AppData/Local/TokenLedger"))

    def test_linux_honors_xdg_and_explicit_override(self):
        self.assertEqual(user_data_dir("linux", {"XDG_DATA_HOME": "/sample/xdg"}, "/sample/home"),
                         Path("/sample/xdg/tokenledger"))
        self.assertEqual(user_data_dir("darwin", {"TOKENLOG_DATA_DIR": "/chosen/data"}, "/sample/home"),
                         Path("/chosen/data"))

    def test_local_timezone_uses_rules_not_fixed_current_offset(self):
        self.assertEqual(default_timezone_name(), "local")
        self.assertIsNone(resolve_timezone("local"))
        self.assertEqual(str(resolve_timezone("UTC")), "UTC")


if __name__ == "__main__":
    unittest.main()
