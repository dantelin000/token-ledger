#!/usr/bin/env python3
"""Windows CI only: install, exercise bundled Tk, then verify safe uninstall.

Refuses to run over an existing installation. No client logs are scanned.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This end-to-end verification requires Windows.")
    import winreg
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TokenLedger"

    def installed():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return winreg.QueryValueEx(key, "InstallLocation")[0]
        except FileNotFoundError:
            return None

    if installed():
        raise RuntimeError("An existing Token Ledger installation must not be modified by this test.")
    with tempfile.TemporaryDirectory(prefix="TokenLedger-installer-test-") as temporary:
        base = Path(temporary)
        target = base / "安装 测试" / "Token Ledger"
        data = Path(os.environ["LOCALAPPDATA"]) / "TokenLedger"
        data_existed = data.exists()
        data.mkdir(parents=True, exist_ok=True)
        sentinel = data / f"installer-preservation-{uuid.uuid4().hex}.txt"
        sentinel.write_text("keep ledger data", encoding="utf-8")
        setup = args.installer.resolve()
        try:
            # NSIS requires /D last and unquoted, including when it contains spaces.
            command = f'"{setup}" /S /D={target}'
            subprocess.run(command, check=True, timeout=180)
            assert Path(installed()) == target, "Installation was not registered"
            assert (target / "Token Ledger.exe").is_file()
            assert (target / "Uninstall.exe").is_file()
            assert "MIT License" in (target / "LICENSE").read_text(encoding="utf-8")
            assert (target / "licenses/PYTHON.txt").is_file()
            start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Token Ledger"
            assert (start_menu / "Token Ledger.lnk").is_file(), "Missing Start Menu shortcut"
            executable = target / "runtime/python.exe"
            start = target / "app/start.py"
            result = subprocess.run([str(executable), "-I", "-B", str(start), "--self-test", "--data-dir", str(base / "isolated-data")],
                                    check=True, capture_output=True, text=True, encoding="utf-8", timeout=45)
            report = json.loads(result.stdout)
            assert report["platform"] == "win32" and report["native_ui"] == "tkinter", report
            assert report["default_language"] == "en" and "zh_CN" in report["languages"], report
            # Validate the copied payload against the build manifest, not merely existence.
            import hashlib
            manifest = json.loads((target / "install-manifest.json").read_text(encoding="utf-8"))
            for relative, expected in manifest["files"].items():
                with (target / relative).open("rb") as stream:
                    assert hashlib.file_digest(stream, "sha256").hexdigest() == expected, relative
            assert report["version"] == manifest["version"]
            # Keep an unrelated file in the installation directory: uninstall must spare it.
            extra = target / "my-notes.txt"
            extra.write_text("keep unrelated files", encoding="utf-8")
            # Run a copy in the temp folder so uninstaller completion is observable.
            import shutil
            uninstaller = base / "uninstall-test.exe"
            shutil.copyfile(target / "Uninstall.exe", uninstaller)
            subprocess.run(f'"{uninstaller}" /S _?={target}', check=True, timeout=120)
            assert installed() is None, "Uninstall registration was left behind"
            assert not (target / "Token Ledger.exe").exists()
            assert not (start_menu / "Token Ledger.lnk").exists()
            assert sentinel.read_text(encoding="utf-8") == "keep ledger data"
            assert extra.read_text(encoding="utf-8") == "keep unrelated files"
            remaining = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
            assert remaining == {"my-notes.txt"}, remaining
            print(json.dumps({"installed_native_tk": report, "manifest_verified": True,
                              "uninstalled": True, "ledger_preserved": True,
                              "unrelated_install_file_preserved": True}))
        finally:
            # Only our uniquely named sentinel is cleaned up; user data stays intact.
            sentinel.unlink(missing_ok=True)
            if not data_existed:
                try:
                    data.rmdir()
                except OSError:
                    pass


if __name__ == "__main__":
    main()
