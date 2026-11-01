"""Extensible, local-only source registry shared by desktop and CLI.

Built-ins understand a harness's on-disk layout. Other harnesses can export a
supported API usage format or the documented tokenlog.usage.v1 receipt format.
Selecting a generic source never enables network interception or API calls.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from .parsers import ParseResult

KINDS = ("claude", "codex", "agy", "opencode", "gemini_cli", "cline", "roo", "cursor_csv",
         "openai", "anthropic", "gemini", "tokenlog")
GENERIC_KINDS = ("openai", "anthropic", "gemini", "tokenlog")
KIND_LABELS = {
    "claude": "Claude Code 日志", "codex": "Codex 日志", "agy": "Antigravity 会话库",
    "opencode": "OpenCode 本机会话", "gemini_cli": "Gemini CLI 本机会话",
    "cline": "Cline VS Code 任务日志", "roo": "Roo Code 任务日志",
    "cursor_csv": "Cursor 官方用量 CSV",
    "openai": "OpenAI / 兼容 API usage", "anthropic": "Anthropic API usage",
    "gemini": "Gemini GenerateContent usageMetadata", "tokenlog": "Token Ledger 标准记录 v1",
}
_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BUILTIN = {"claude": "claude", "codex": "codex", "agy": "agy", "opencode": "opencode",
            "gemini-cli": "gemini_cli", "cline": "cline", "roo": "roo", "cursor": "cursor_csv"}


@dataclass
class SourceSpec:
    provider: str
    label: str
    kind: str
    roots: list[Path]
    enabled: bool = True

    def __post_init__(self):
        if not isinstance(self.provider, str) or not _PROVIDER.fullmatch(self.provider) or self.provider == "all":
            raise ValueError("来源 ID 需以小写字母开头，只能包含小写字母、数字、下划线或连字符，最长 64 字符。")
        if not isinstance(self.label, str) or not self.label.strip() or len(self.label) > 100:
            raise ValueError("来源名称需为 1–100 个字符。")
        self.label = self.label.strip()
        if self.kind not in KINDS:
            raise ValueError("不支持的记录格式。")
        if self.provider in _BUILTIN and self.kind != _BUILTIN[self.provider]:
            raise ValueError("内置来源 ID 不能用于其他记录格式，请使用新的来源 ID。")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled 必须是布尔值。")
        if not isinstance(self.roots, (list, tuple)) or not self.roots:
            raise ValueError("至少选择一个日志文件或文件夹。")
        if any(not isinstance(root, (str, Path)) or not str(root).strip() for root in self.roots):
            raise ValueError("日志路径必须是非空字符串。")
        self.roots = [Path(root).expanduser() for root in self.roots]
        if self.kind in (*GENERIC_KINDS, "cline", "roo", "cursor_csv"):
            for root in self.roots:
                full = root.resolve()
                home = Path.home().resolve()
                same_home = full == home
                try:
                    # macOS can preserve a differently cased spelling for the
                    # same home directory on its default case-insensitive disk.
                    same_home = same_home or full.samefile(home)
                except OSError:
                    pass
                if same_home or full == Path(full.anchor):
                    raise ValueError("请选择具体日志目录，不能递归扫描主目录或磁盘根目录。")

    def to_dict(self) -> dict:
        return {"provider": self.provider, "label": self.label, "kind": self.kind,
                "roots": [str(root) for root in self.roots], "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict) -> "SourceSpec":
        if not isinstance(data, dict):
            raise ValueError("来源配置必须是 JSON 对象。")
        try:
            return cls(data["provider"], data["label"], data["kind"], data["roots"], data.get("enabled", True))
        except KeyError as exc:
            raise ValueError(f"来源配置缺少字段：{exc.args[0]}") from None


class AdapterRegistry:
    def __init__(self, codex_home: Path | None = None, claude_home: Path | None = None,
                 extra_sources: list[SourceSpec | dict] | None = None, include_builtin: bool = True):
        specs = {}
        if include_builtin:
            codex = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
            claude = Path(claude_home or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
            configured = os.environ.get("TOKENLOG_AGY_ROOTS")
            agy_roots = ([Path(p) for p in configured.split(os.pathsep) if p] if configured is not None
                         else [Path.home() / ".gemini" / name for name in ("antigravity-cli", "antigravity")])
            # An intentionally empty agy override disables discovery, while a
            # SourceSpec always contains a reviewable path.
            if not agy_roots:
                agy_roots = [Path.home() / ".gemini" / "antigravity-cli"]
                agy_enabled = False
            else:
                agy_enabled = True
            specs = {
                "claude": SourceSpec("claude", "Claude Code", "claude", [claude]),
                "codex": SourceSpec("codex", "Codex", "codex", [codex]),
                "agy": SourceSpec("agy", "agy / Antigravity", "agy", agy_roots, agy_enabled),
            }
            from . import ide_imports, mainstream
            for provider, label, kind in (
                ("opencode", "OpenCode", "opencode"), ("gemini-cli", "Gemini CLI", "gemini_cli"),
                ("cline", "Cline", "cline"), ("roo", "Roo Code", "roo"),
                ("cursor", "Cursor（用量 CSV 导入）", "cursor_csv"),
            ):
                roots = mainstream.default_roots(kind) if kind in ("opencode", "gemini_cli") else ide_imports.default_roots(kind)
                specs[provider] = SourceSpec(provider, label, kind, roots, kind != "cursor_csv")
        seen = set()
        for source in extra_sources or []:
            spec = SourceSpec.from_dict(source) if isinstance(source, dict) else source
            if not isinstance(spec, SourceSpec):
                raise ValueError("无效的来源配置。")
            if spec.provider in seen:
                raise ValueError(f"来源 ID 重复：{spec.provider}")
            seen.add(spec.provider)
            specs[spec.provider] = spec
        self._sources = specs
        self._status = {}

    @property
    def labels(self):
        return {source.provider: source.label for source in self._sources.values()}

    def list_sources(self) -> list[SourceSpec]:
        return list(self._sources.values())

    def discover(self) -> list[tuple[str, Path]]:
        found = []
        statuses = {}
        owners = {}
        for source in self.list_sources():
            paths = set()
            errors = 0
            if source.enabled:
                for root in source.roots:
                    try:
                        if source.kind == "claude":
                            paths.update(p.resolve() for p in (root / "projects").rglob("*.jsonl") if p.is_file())
                        elif source.kind == "codex":
                            for name in ("sessions", "archived_sessions"):
                                paths.update(p.resolve() for p in (root / name).rglob("*.jsonl") if p.is_file())
                        elif source.kind == "agy":
                            paths.update(p.resolve() for p in (root / "conversations").glob("*.db") if p.is_file())
                        elif source.kind in ("opencode", "gemini_cli"):
                            from . import mainstream
                            paths.update(mainstream.discover_files(source.kind, [root]))
                        elif source.kind in ("cline", "roo", "cursor_csv"):
                            from . import ide_imports
                            paths.update(ide_imports.discover_files(source.kind, [root]))
                        elif root.is_file():
                            if root.suffix.lower() in (".json", ".jsonl"):
                                paths.add(root.resolve())
                        elif root.is_dir():
                            for extension in ("*.jsonl", "*.json"):
                                paths.update(p.resolve() for p in root.rglob(extension) if p.is_file())
                    except OSError:
                        errors += 1
            duplicates = {owners[path] for path in paths if path in owners}
            overlaps = sum(path in owners for path in paths)
            paths = {path for path in paths if path not in owners}
            for path in paths:
                owners[path] = source.provider
            state = "disabled" if not source.enabled else "ok" if paths else "missing"
            warning = "已停用采集；此前的历史统计仍保留。" if not source.enabled else (
                "未发现可读取的本机用量记录；缺少记录不代表没有使用。" if not paths else "")
            if errors:
                warning = f"{errors} 个目录无法访问；" + warning
            if source.kind == "cursor_csv" and not paths:
                warning = "请从 Cursor 官方 Usage 页面导出 CSV 后选择该文件；本程序不会自动获取账号用量。"
            if overlaps:
                warning = (f"跳过 {overlaps} 个重复日志文件；已由来源 {', '.join(sorted(duplicates))} 采集，避免重复统计。"
                           + (" " + warning if paths and warning else ""))
            statuses[source.provider] = {"label": source.label, "status": state, "files": len(paths), "warning": warning}
            found.extend((source.provider, path) for path in sorted(paths))
        self._status = statuses
        return found

    def status(self) -> dict:
        if not self._status:
            self.discover()
        return {key: dict(value) for key, value in self._status.items()}

    def parse_file(self, path: Path, provider: str) -> ParseResult:
        source = self._sources.get(provider)
        if source is None:
            raise ValueError("未知的数据来源。")
        if source.kind in ("claude", "codex"):
            from . import parsers
            result = parsers.parse_file(Path(path), source.kind)
            # An additional installation can use its own provider ID.
            for event in result.events:
                event["provider"] = provider
            return result
        if source.kind == "agy":
            from .agy import parse_file as parse_agy
            result = parse_agy(Path(path))
            for event in result.events:
                event["provider"] = provider
            return result
        if source.kind in ("opencode", "gemini_cli"):
            from . import mainstream
            return mainstream.parse_file(Path(path), source.kind, provider)
        if source.kind in ("cline", "roo", "cursor_csv"):
            from . import ide_imports
            return ide_imports.parse_file(Path(path), source.kind, provider)
        from .generic import parse_file
        return parse_file(Path(path), provider, source.kind)
