"""Portable application locations and timezone selection for desktop installs."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

APP_DIRECTORY = "TokenLedger"


def user_data_dir(platform_name=None, environ=None, home=None) -> Path:
    """Return a per-user writable location; never write inside an app bundle."""
    platform_name = sys.platform if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    override = environ.get("TOKENLOG_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / APP_DIRECTORY
    if platform_name in ("win32", "cygwin"):
        base = environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / APP_DIRECTORY
    base = environ.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / "tokenledger"


def default_timezone_name() -> str:
    """Use each machine's local timezone rather than a developer's timezone."""
    return "local"


def resolve_timezone(name="local"):
    """None means datetime.astimezone() local rules, including historical DST.

    Do not return datetime.now().astimezone().tzinfo for 'local': that value may
    be a fixed current offset and would misclassify historical summer/winter use.
    Frozen Windows builds include tzdata for explicit IANA zone names.
    """
    return None if not name or name == "local" else ZoneInfo(name)
