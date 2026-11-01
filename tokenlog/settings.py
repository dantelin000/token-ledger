"""Portable application preferences. Source logs and credentials never belong here."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile

from .adapters import AdapterRegistry, SourceSpec
from .i18n import DEFAULT_LANGUAGE, LANGUAGES


def _default_sources():
    return AdapterRegistry().list_sources()


@dataclass
class AppSettings:
    timezone: str = "local"
    interval: int = 15
    sources: list[SourceSpec] = field(default_factory=_default_sources)
    language: str = DEFAULT_LANGUAGE

    def to_dict(self):
        return {"version": 2, "timezone": self.timezone, "interval": self.interval, "language": self.language,
                "sources": [source.to_dict() for source in self.sources]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get("version", 1) not in (1, 2):
            raise ValueError("设置文件版本不受支持")
        timezone = value.get("timezone", "local")
        if not isinstance(timezone, str) or not timezone.strip() or len(timezone) > 128:
            raise ValueError("时区应为 local 或有效的 IANA 时区名称")
        interval = value.get("interval", 15)
        if isinstance(interval, bool) or not isinstance(interval, int) or not 5 <= interval <= 3600:
            raise ValueError("刷新间隔应为 5–3600 秒")
        language = value.get("language", DEFAULT_LANGUAGE)
        if not isinstance(language, str) or language not in LANGUAGES:
            raise ValueError("语言设置无效")
        raw_sources = value.get("sources")
        if raw_sources is None:
            sources = _default_sources()
        elif isinstance(raw_sources, list):
            sources = [SourceSpec.from_dict(source) for source in raw_sources]
            if value.get("version", 1) == 1:
                # Upgrade the earlier three-tool desktop preferences without
                # undoing a user's root overrides or enabled/disabled choices.
                existing = {source.provider for source in sources}
                sources.extend(source for source in _default_sources() if source.provider not in existing)
        else:
            raise ValueError("数据源应为列表")
        names = [source.provider for source in sources]
        if len(names) != len(set(names)):
            raise ValueError("数据源 ID 不能重复")
        return cls(timezone.strip(), interval, sources, language)


def load_settings(path: Path) -> AppSettings:
    path = Path(path)
    if not path.exists():
        return AppSettings()
    try:
        return AppSettings.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("设置文件不是有效的 UTF-8 JSON；请保留原文件并修正或更名。") from exc


def save_settings(settings: AppSettings, path: Path) -> None:
    """Validate and atomically replace our own preferences, preserving originals on failure."""
    settings = AppSettings.from_dict(settings.to_dict())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".settings-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
