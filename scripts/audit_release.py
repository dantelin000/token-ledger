#!/usr/bin/env python3
"""Audit publishable source, Git history and source ZIPs without printing secrets.

Reports finding categories, filenames and line numbers only. Binary runtime
dependencies have their own pinned hashes and licenses; this scans our sources.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    "credential_token": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,})\b"),
    "inline_secret": re.compile(r'''(?im)\b(?:api_key|password|client_secret|access_token|refresh_token)\s*[:=]\s*["'][^"'\n]{16,}["']'''),
}
PRIVATE_DIRS = {"private", "data", "data-desktop", "data-v21", "__pycache__", ".claude", ".codex", ".gemini"}
PRIVATE_NAMES = {"auth.json", "credentials.json", "settings.json", "sources.json", ".env"}
PRIVATE_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".log", ".eml", ".mbox", ".pem", ".key", ".pyc", ".jsonl"}


def scan(name: str, contents: bytes, *, home: str | None = None):
    path = PurePosixPath(name)
    findings = []
    sample = path.parts[-2:] == ("examples", "tokenlog-usage.sample.jsonl")
    if (path.is_absolute() or ".." in path.parts or "\\" in name or
            PRIVATE_DIRS.intersection(path.parts) or path.name in PRIVATE_NAMES or
            any(part.startswith(".venv") for part in path.parts) or
            path.suffix.lower() in PRIVATE_SUFFIXES and not sample):
        findings.append({"path": name, "kind": "private_or_unsafe_filename"})
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        return findings + [{"path": name, "kind": "unexpected_source_binary"}]
    if "\0" in text:
        return findings + [{"path": name, "kind": "unexpected_source_binary"}]
    for kind, pattern in PATTERNS.items():
        for matched in pattern.finditer(text):
            findings.append({"path": name, "line": text.count("\n", 0, matched.start()) + 1, "kind": kind})
    personal = home or str(Path.home())
    for variant in {personal, personal.replace("\\", "/"), personal.replace("\\", "\\\\")}:
        if variant and variant in text:
            findings.append({"path": name, "kind": "developer_home_path"})
            break
    return findings


def git(root, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=root)


def audit(root: Path, archives=(), history=False):
    root = root.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise RuntimeError("Initialize a separate project repository before auditing; refusing the parent repository.")
    names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    names = sorted({name.decode() for name in names if name})
    findings = []
    for name in names:
        path = root / name
        if path.is_symlink():
            findings.append({"path": name, "kind": "source_symlink"})
        else:
            findings.extend(scan(name, path.read_bytes()))
    commits = []
    if history:
        commits = git(root, "rev-list", "--all").decode().splitlines()
        for commit in commits:
            metadata = git(root, "show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", commit)
            findings.extend(scan(f"git-metadata/{commit[:12]}", metadata))
            for raw in git(root, "ls-tree", "-r", "--name-only", "-z", commit).split(b"\0"):
                if raw:
                    name = raw.decode()
                    findings.extend(scan(f"git-history/{commit[:12]}/{name}", git(root, "show", f"{commit}:{name}")))
    members = 0
    for archive in archives:
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                if not member.is_dir():
                    members += 1
                    findings.extend(scan(f"{archive.name}/{member.filename}", source.read(member)))
    return {"source_files": len(names), "git_commits": len(commits),
            "archive_members": members, "findings": findings, "passed": not findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("archives", nargs="*", type=Path)
    args = parser.parse_args()
    result = audit(args.root, args.archives, args.history)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
