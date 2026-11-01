#!/usr/bin/env python3
"""Check distributable filenames and text sources for accidental private data."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import zipfile

PRIVATE_PARTS = {".git", ".claude", ".codex", ".gemini", ".venv", ".venv-desktop",
                 "__pycache__", "data"}
PRIVATE_NAMES = {"auth.json", "credentials.json", ".credentials.json", ".env",
                 "token-usage.sqlite3", "sources.json", "settings.json"}
PRIVATE_SUFFIXES = {".jsonl", ".db", ".sqlite", ".sqlite3", ".eml"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".json", ".yml", ".yaml", ".command", ".bat", ".nsi", ".nsh"}


def check_name(name):
    path = PurePosixPath(name)
    sample = path.parts[-2:] == ("examples", "tokenlog-usage.sample.jsonl")
    return bool(PRIVATE_PARTS.intersection(path.parts) or path.name in PRIVATE_NAMES
                or (path.suffix.lower() in PRIVATE_SUFFIXES and not sample)
                or ".." in path.parts or path.is_absolute())


def audit(path: Path):
    errors = []
    count = 0
    home = str(Path.home()).encode("utf-8")
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                count += 1
                if check_name(info.filename):
                    errors.append(f"private-looking filename: {info.filename}")
                if (PurePosixPath(info.filename).suffix.lower() in TEXT_SUFFIXES
                    or info.filename.endswith("tokenlog-usage.sample.jsonl")) and home in archive.read(info):
                    errors.append(f"local home path in text: {info.filename}")
    elif path.is_dir():
        for item in path.rglob("*"):
            if not item.is_file() or item.is_symlink():
                continue
            count += 1
            name = item.relative_to(path).as_posix()
            if check_name(name):
                errors.append(f"private-looking filename: {name}")
    else:
        count = 1
    return count, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.artifacts:
        count, errors = audit(path)
        print(f"{path.name}: {count} files, {len(errors)} findings")
        for error in errors:
            print(f"  {error}")
        failed |= bool(errors)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
