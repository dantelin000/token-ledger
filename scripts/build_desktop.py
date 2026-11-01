#!/usr/bin/env python3
"""Build on the target OS: macOS .app or Windows .exe, plus clean source ZIP.

This script never scans the user's home or packages local usage databases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
from pathlib import Path
import subprocess
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tokenlog import __version__
APP_NAME = "Token Ledger"


def source_archive(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "TokenLedger-source.zip"
    files = []
    for name in ("README.md", "README.zh-CN.md", "LICENSE", "pyproject.toml", ".gitignore", ".gitattributes", "启动用量统计.command", "启动用量统计.bat",
                 "examples/tokenlog-usage.sample.jsonl"):
        path = ROOT / name
        if path.is_file():
            files.append(path)
    for directory in ("tokenlog", "tests", "scripts", "packaging", "docs", ".github/workflows"):
        base = ROOT / directory
        if base.is_dir():
            files.extend(p for p in base.rglob("*") if p.is_file()
                         and "__pycache__" not in p.parts
                         and p.suffix in (".py", ".md", ".toml", ".json", ".yml", ".yaml", ".txt", ".spec", ".nsi", ".nsh"))
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(set(files)):
            z.write(path, "TokenLedger/" + path.relative_to(ROOT).as_posix())
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(destination):
    artifacts = sorted(p for p in destination.iterdir() if p.suffix in (".exe", ".zip", ".dmg") and p.is_file())
    (destination / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in artifacts), encoding="ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Run the packaged --self-test without scanning user logs")
    parser.add_argument("--target-arch", choices=("arm64",),
                        help="macOS only; all bundled binaries must support this architecture")
    args = parser.parse_args()
    destination = args.output.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    source = source_archive(destination)
    if args.source_only:
        subprocess.run([sys.executable, str(ROOT / "packaging" / "audit_artifacts.py"), str(source)], check=True)
        print(source)
        write_checksums(destination)
        return
    if sys.platform not in ("darwin", "win32"):
        parser.error("Build this desktop release on macOS or Windows. Use --source-only elsewhere.")
    if args.target_arch and sys.platform != "darwin":
        parser.error("--target-arch selects macOS architectures only; use the matching Windows Python runtime.")
    if sys.platform == "win32" and struct.calcsize("P") != 8:
        parser.error("Windows desktop builds require a 64-bit Python runtime.")
    try:
        import tkinter
        import PyInstaller
        import tzdata
    except ImportError as exc:
        parser.error(f"Missing build runtime: {exc}. Install .[build] with a Python that includes Tk.")
    entry = ROOT / "tokenlog" / "desktop_entry.py"
    if not entry.is_file():
        parser.error("tokenlog/desktop_entry.py is not present yet")
    tag = "macos" if sys.platform == "darwin" else "windows"
    architecture = "arm64" if sys.platform == "darwin" else platform.machine().lower()
    if architecture in ("amd64", "x64"):
        architecture = "x86_64"
    output = destination / f"{tag}-{architecture}"
    build = ROOT / "build" / f"{tag}-{architecture}"
    build.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--windowed", "--name", APP_NAME,
               "--distpath", str(output), "--workpath", str(build / "work"),
               "--specpath", str(build), "--paths", str(ROOT),
               "--collect-data", "tzdata", "--collect-submodules", "tokenlog",
               "--add-data", str(ROOT / "LICENSE") + os.pathsep + ".",
               "--exclude-module", "pytest", "--exclude-module", "unittest",
               "--exclude-module", "tokenlog.static"]
    if sys.platform == "darwin":
        command.extend(["--onedir", "--osx-bundle-identifier", "app.tokenledger.desktop"])
        command.extend(["--target-architecture", "arm64"])
    else:
        command.append("--onefile")
    command.append(str(entry))
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / ".pyinstaller-cache")
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    if sys.platform == "darwin":
        app = output / f"{APP_NAME}.app"
        plist = app / "Contents/Info.plist"
        metadata = plistlib.loads(plist.read_bytes())
        metadata.update(CFBundleShortVersionString=__version__, CFBundleVersion=__version__,
                        CFBundleDevelopmentRegion="en", CFBundleLocalizations=["en", "zh_CN"],
                        LSArchitecturePriority=["arm64"], LSRequiresNativeExecution=True)
        plist.write_bytes(plistlib.dumps(metadata, sort_keys=False))
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        archive = destination / f"TokenLedger-{tag}-{architecture}.zip"
        # ditto preserves .app symlinks and executable modes; zipfile does not.
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
        artifact = app
        checked = 0
        for binary in app.rglob("*"):
            if not binary.is_file() or binary.is_symlink():
                continue
            with binary.open("rb") as stream:
                magic = stream.read(4)
            if magic in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca',b'\xca\xfe\xba\xbf'):
                archs = subprocess.check_output(["lipo","-archs",str(binary)],text=True).split()
                if archs != ["arm64"]:
                    raise RuntimeError(f"Non-native component in Apple Silicon package: {binary.relative_to(app)} {archs}")
                checked += 1
        if not checked:
            raise RuntimeError("No native binaries verified")
    else:
        artifact = output / f"{APP_NAME}.exe"
        archive = destination / f"TokenLedger-{tag}-{architecture}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(artifact, artifact.name)
    if args.smoke_test:
        executable = artifact / "Contents" / "MacOS" / APP_NAME if sys.platform == "darwin" else artifact
        subprocess.run([str(executable), "--self-test"], check=True, timeout=30)
    subprocess.run([sys.executable, str(ROOT / "packaging" / "audit_artifacts.py"),
                    str(source), str(artifact)], check=True)
    info = {"platform": tag, "architecture": architecture, "version": __version__,
            "default_language": "en", "languages": ["en", "zh_CN"],
            "intel_components": False if sys.platform == "darwin" else None,
            "native_binary_count": checked if sys.platform == "darwin" else None,
            "python": platform.python_version(), "pyinstaller": PyInstaller.__version__,
            "artifact": str(artifact.relative_to(destination)),
            "archive": archive.name, "sha256": sha256(archive),
            "source_archive": source.name, "source_sha256": sha256(source),
            "signed_release": False, "notarized": False,
            "distribution_signature": "ad-hoc" if sys.platform == "darwin" else "unsigned",
            "packaged_self_test": bool(args.smoke_test), "artifact_filename_audit": "passed"}
    (output / "build-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    write_checksums(destination)
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
