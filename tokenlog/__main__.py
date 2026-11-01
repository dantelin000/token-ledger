"""Desktop by default; --report offers a headless, local-only JSON report."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .platforms import user_data_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="Token Ledger · macOS / Windows 原生桌面用量账本")
    parser.add_argument("--data-dir", type=Path, default=user_data_dir())
    parser.add_argument("--db", type=Path)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--claude-home", type=Path)
    parser.add_argument("--timezone")
    parser.add_argument("--interval", type=int)
    parser.add_argument("--migrate-from", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", action="store_true", help="扫描一次并输出 JSON，不启动界面")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD")
    parser.add_argument("--provider", default="all", help="来源 ID，如 all / claude / codex / agy / opencode")
    args = parser.parse_args(argv)
    if not args.report:
        from .desktop import main as desktop_main
        desktop_args = ["--data-dir", str(args.data_dir)]
        for name in ("db", "timezone", "interval", "migrate_from"):
            value = getattr(args, name)
            if value is not None:
                desktop_args.extend(["--" + name.replace("_", "-"), str(value)])
        if args.self_test:
            desktop_args.append("--self-test")
        desktop_main(desktop_args)
        return
    from .settings import load_settings
    from .adapters import AdapterRegistry
    from .store import Ledger
    settings = load_settings(args.data_dir / "settings.json")
    for source in settings.sources:
        if source.provider == "codex" and args.codex_home:
            source.roots = [args.codex_home]
        if source.provider == "claude" and args.claude_home:
            source.roots = [args.claude_home]
    registry = AdapterRegistry(include_builtin=False, extra_sources=settings.sources)
    ledger = Ledger(args.db or args.data_dir / "token-usage.sqlite3",
        args.codex_home or Path.home()/".codex", args.claude_home or Path.home()/".claude",
        args.timezone or settings.timezone, registry)
    ledger.sync()
    print(json.dumps(ledger.report(args.start, args.end, args.provider), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
