"""Presentation-only English / Simplified Chinese localization.

User names, paths, IDs and accounting records are never rewritten. Persisted
diagnostics from earlier releases are translated only when displayed.
"""
from __future__ import annotations

from functools import lru_cache
import re
from string import Formatter

from .translations import EN

LANGUAGES = {"en": "English", "zh_CN": "简体中文"}
DEFAULT_LANGUAGE = "en"
_FORMATTER = Formatter()


@lru_cache(maxsize=1)
def _message_patterns():
    patterns = []
    for source, target in EN.items():
        fields, pieces = [], []
        for literal, field, _spec, _conversion in _FORMATTER.parse(source):
            pieces.append(re.escape(literal))
            if field is not None:
                fields.append(field)
                pieces.append("(.*?)")
        if fields:
            patterns.append((sum(len(p) for p in pieces) - len(fields) * 5,
                             re.compile("".join(pieces), re.DOTALL), fields, target))
    return sorted(patterns, key=lambda item: item[0], reverse=True)


class Translator:
    def __init__(self, language=DEFAULT_LANGUAGE):
        if language not in LANGUAGES:
            raise ValueError("Unsupported language")
        self.language = language

    def __call__(self, source, *values, **named):
        text = EN.get(source, source) if self.language == "en" else source
        return text.format(*values, **named) if values or named else text

    def message(self, text, _depth=0):
        """Localize a known diagnostic, keeping all unrecognized content intact."""
        if not isinstance(text, str) or self.language != "en" or _depth > 8:
            return text
        if text in EN:
            return EN[text]
        if not re.search(r"[\u3400-\u9fff]", text):
            return text
        # A report warning may contain several diagnostics or a source prefix.
        if " · " in text:
            return " · ".join(self.message(part, _depth + 1) for part in text.split(" · "))
        for _, pattern, fields, target in _message_patterns():
            matched = pattern.fullmatch(text)
            if matched:
                captured = dict(zip(fields, matched.groups()))
                result = []
                for literal, field, _spec, _conversion in _FORMATTER.parse(target):
                    result.append(literal)
                    if field is not None:
                        # Captured numbers are already formatted (e.g. 1,000).
                        result.append(self.message(captured[field], _depth + 1))
                return "".join(result)
        prefix, separator, diagnostic = text.partition(": ")
        if separator:
            return prefix + separator + self.message(diagnostic, _depth + 1)
        return text

    def money(self, estimate, places=2):
        from .pricing import money
        text = money(estimate, places)
        if text == "未定价":
            return self(text)
        if text.endswith("（部分）"):
            return text.removesuffix("（部分）") + self("（部分）")
        return text

    def source_label(self, provider, label):
        if provider == "cursor" and label == "Cursor（用量 CSV 导入）":
            return self(label)
        return label
