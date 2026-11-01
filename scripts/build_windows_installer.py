#!/usr/bin/env python3
"""Build an offline Windows x64 installation wizard with NSIS, on any host.

Only allowlisted application sources and hash-pinned public dependencies enter
the payload. This never scans usage logs or packages the developer's ledger.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "packaging" / "windows"
sys.path.insert(0, str(ROOT))
from tokenlog import __version__
from scripts.build_desktop import sha256, source_archive


def download(item: dict, destination: Path) -> Path:
    if destination.exists():
        if sha256(destination) != item["sha256"]:
            raise RuntimeError(f"Dependency checksum mismatch: {destination.name}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".download")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "TokenLedger-build"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        if sha256(partial) != item["sha256"]:
            raise RuntimeError(f"Downloaded dependency checksum mismatch: {destination.name}")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def safe_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or not path.parts:
        raise ValueError(f"Unsafe archive member: {name!r}")
    return path.parts


def extract_runtime(archive: Path, destination: Path):
    with tarfile.open(archive, "r:gz") as source:
        seen = set()
        for member in source:
            parts = safe_parts(member.name)
            if parts[0] != "python" or not (member.isfile() or member.isdir()):
                raise ValueError(f"Unexpected runtime archive member: {member.name!r}")
            target = destination.joinpath(*parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            identity = "/".join(parts[1:]).casefold()
            if identity in seen:
                raise ValueError(f"Duplicate runtime member: {member.name!r}")
            seen.add(identity)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)


def extract_tzdata(archive: Path, destination: Path):
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            parts = safe_parts(member.filename)
            if not (parts[0] == "tzdata" or parts[0].startswith("tzdata-") and parts[0].endswith(".dist-info")):
                raise ValueError(f"Unexpected timezone archive member: {member.filename!r}")
            if member.is_dir():
                continue
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(member))


def pe_machine(path: Path) -> int:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError(f"Not a Windows executable: {path.name}")
        stream.seek(0x3C)
        offset, = struct.unpack("<I", stream.read(4))
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError(f"Invalid PE signature: {path.name}")
        return struct.unpack("<H", stream.read(2))[0]


def write_icon(path: Path):
    """Small code-drawn ledger icon; no imaging dependencies in the build."""
    images = []
    for size in (16, 32, 48, 64):
        pixels = bytearray()
        for y in reversed(range(size)):
            for x in range(size):
                px, py = (x + .5) / size, (y + .5) / size
                inside = .06 <= px <= .94 and .06 <= py <= .94
                color = (30, 135, 120, 255) if inside else (0, 0, 0, 0)
                for left, right, top in ((.23, .35, .55), (.44, .56, .38), (.65, .77, .22)):
                    if left <= px <= right and top <= py <= .78:
                        color = (255, 255, 255, 255)
                pixels.extend((color[2], color[1], color[0], color[3]))
        mask = bytes(((size + 31) // 32) * 4 * size)
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
        images.append((size, header + pixels + mask))
    offset = 6 + 16 * len(images)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, contents in images:
        directory.extend(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(contents), offset))
        offset += len(contents)
    path.write_bytes(directory + b"".join(contents for _, contents in images))


def nsis_string(text: str) -> str:
    if "\n" in text or "\r" in text:
        raise ValueError("Newline in installer path")
    return text.replace("$", "$$").replace('"', '$\\"')


def payload_manifests(payload: Path, build: Path) -> tuple[Path, Path]:
    files = sorted(path for path in payload.rglob("*") if path.is_file())
    install = ["!macro InstallPayload"]
    uninstall = ["!macro UninstallPayload"]
    directories = set()
    previous = None
    for path in files:
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed in the installer payload")
        relative = path.relative_to(payload)
        parent = relative.parent.as_posix()
        if parent != previous:
            suffix = "" if parent == "." else "\\" + parent.replace("/", "\\")
            install.append(f'  SetOutPath "$INSTDIR{nsis_string(suffix)}"')
            previous = parent
        install.append(f'  File "{nsis_string(str(path))}"')
        name = nsis_string(str(relative).replace("/", "\\"))
        uninstall.append(f'  Delete "$INSTDIR\\{name}"')
        directories.update(p for p in relative.parents if p != Path("."))
    for directory in sorted(directories, key=lambda p: (-len(p.parts), str(p))):
        name = nsis_string(str(directory).replace("/", "\\"))
        uninstall.append(f'  RMDir "$INSTDIR\\{name}"')
    install.append("!macroend")
    uninstall.append("!macroend")
    install_file, uninstall_file = build / "install-files.nsh", build / "uninstall-files.nsh"
    install_file.write_text("\n".join(install) + "\n", encoding="utf-8-sig")
    uninstall_file.write_text("\n".join(uninstall) + "\n", encoding="utf-8-sig")
    return install_file, uninstall_file


def compiler_path(argument: str | None) -> str:
    if argument:
        path = Path(argument).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"makensis not found: {path}")
        return str(path)
    path = shutil.which("makensis")
    if path:
        return path
    if sys.platform == "win32":
        for variable in ("ProgramFiles(x86)", "ProgramFiles"):
            path = Path(os.environ.get(variable, "C:/Program Files")) / "NSIS" / "makensis.exe"
            if path.is_file():
                return str(path)
    raise RuntimeError("Install NSIS first (macOS: brew install makensis; Windows: choco install nsis).")


def compile_nsis(compiler: str, template: str, definitions: dict):
    prefix = "/" if sys.platform == "win32" else "-"
    command = [compiler, prefix + "V3", prefix + "WX", prefix + "INPUTCHARSET", "UTF8"]
    command.extend(prefix + "D" + key + "=" + nsis_string(str(value)) for key, value in definitions.items())
    command.append(str(TEMPLATES / template))
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--makensis", help="Path to the NSIS compiler")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--smoke-test", action="store_true", help="Windows only: install, self-test and uninstall in a temporary directory")
    args = parser.parse_args()
    if args.smoke_test and sys.platform != "win32":
        parser.error("The installer round-trip test must run on Windows.")
    compiler = compiler_path(args.makensis)
    dependencies = json.loads((TEMPLATES / "dependencies.json").read_text(encoding="utf-8"))
    build = ROOT / "build" / "windows-installer"
    build.mkdir(parents=True, exist_ok=True)
    runtime = download(dependencies["runtime"], build / "downloads" / "python-windows.tar.gz")
    timezone = download(dependencies["tzdata"], build / "downloads" / "tzdata.whl")
    payload = build / "payload"
    if payload.is_symlink():
        raise RuntimeError("Refusing to replace a symlinked payload directory")
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir()
    extract_runtime(runtime, payload / "runtime")
    for name in ("python.exe", "pythonw.exe", "python313.dll", "DLLs/_tkinter.pyd", "DLLs/_sqlite3.pyd"):
        if pe_machine(payload / "runtime" / name) != 0x8664:
            raise RuntimeError(f"Runtime component is not Windows x64: {name}")
    for name in ("Lib/tkinter/__init__.py", "tcl/tcl8.6/init.tcl", "tcl/tk8.6/tk.tcl"):
        if not (payload / "runtime" / name).is_file():
            raise RuntimeError(f"Runtime is missing its GUI components: {name}")
    app = payload / "app"
    (app / "tokenlog").mkdir(parents=True)
    for module in (ROOT / "tokenlog").glob("*.py"):
        if module.is_symlink():
            raise RuntimeError("Application sources must not be symlinks")
        shutil.copyfile(module, app / "tokenlog" / module.name)
    shutil.copyfile(TEMPLATES / "start.py", app / "start.py")
    extract_tzdata(timezone, app)
    shutil.copyfile(ROOT / "LICENSE", payload / "LICENSE")
    shutil.copytree(ROOT / "packaging/licenses", payload / "licenses")
    (payload / "THIRD-PARTY-NOTICES.txt").write_text(
        "Token Ledger includes CPython, Tcl/Tk and other runtime libraries from\n"
        "Astral python-build-standalone, plus the Python tzdata package.\n"
        "Their license texts are retained in licenses/, runtime/LICENSE* and package dist-info.\n"
        "https://github.com/astral-sh/python-build-standalone\n"
        "https://github.com/python/tzdata\n", encoding="utf-8")
    icon = build / "TokenLedger.ico"
    write_icon(icon)
    common = {"APP_VERSION": __version__, "APP_ICON": icon}
    compile_nsis(compiler, "launcher.nsi", {**common, "OUTPUT": payload / "Token Ledger.exe"})
    manifest = {"app": "TokenLedger", "version": __version__, "architecture": "x86_64",
                "dependencies": dependencies,
                "files": {p.relative_to(payload).as_posix(): sha256(p)
                          for p in sorted(payload.rglob("*")) if p.is_file()}}
    (payload / "install-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    install_files, uninstall_files = payload_manifests(payload, build)
    destination = args.output.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"TokenLedger-{__version__}-windows-x64-setup.exe"
    size = sum(p.stat().st_size for p in payload.rglob("*") if p.is_file())
    compile_nsis(compiler, "installer.nsi", {**common, "OUTPUT": output,
                 "INSTALL_FILES": install_files, "UNINSTALL_FILES": uninstall_files,
                 "INSTALLED_KIB": (size + 1023) // 1024})
    if pe_machine(output) != 0x14C:
        raise RuntimeError("Unexpected NSIS bootstrap architecture")
    # A 32-bit NSIS bootstrap launches the bundled 64-bit Windows runtime.
    source = source_archive(destination)
    subprocess.run([sys.executable, str(ROOT / "packaging/audit_artifacts.py"), str(source), str(app)], check=True)
    info = {"file": output.name, "version": __version__, "application_architecture": "x86_64",
            "default_language": "en", "languages": ["en", "zh_CN"],
            "installer_bootstrap": "x86", "minimum_windows": "10", "installer": "NSIS",
            "nsis_version": subprocess.check_output([compiler, "/VERSION" if sys.platform == "win32" else "-VERSION"], text=True).strip(),
            "offline": True, "per_user": True, "admin_required": False,
            "sha256": sha256(output), "bytes": output.stat().st_size, "dependencies": dependencies,
            "payload_files": len(manifest["files"]) + 1, "payload_bytes": size,
            "windows_install_self_test_uninstall": False, "signed_release": False,
            "build_host": sys.platform, "artifact_source_audit": "passed"}
    if args.smoke_test:
        subprocess.run([sys.executable, str(ROOT / "scripts/verify_windows_installer.py"), "--installer", str(output)], check=True)
        info["windows_install_self_test_uninstall"] = True
    (destination / "windows-installer-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    artifacts = sorted(p for p in destination.iterdir() if p.suffix in (".exe", ".zip", ".dmg") and p.is_file())
    (destination / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in artifacts), encoding="ascii")
    print(json.dumps({k: v for k, v in info.items() if k != "dependencies"}, indent=2))


if __name__ == "__main__":
    main()
