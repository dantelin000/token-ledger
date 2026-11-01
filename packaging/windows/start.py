"""Isolated entry point for the bundled Windows runtime (pythonw -I -B)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from pathlib import Path


def main():
    # -I deliberately excludes the script directory. Add only our own app files.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    mutex = None
    kernel = None
    if sys.platform == "win32" and "--self-test" not in sys.argv[1:]:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        mutex = kernel.CreateMutexW(None, False, "Local\\TokenLedger.Desktop")
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel.CloseHandle(mutex)
            from tokenlog.i18n import Translator
            from tokenlog.platforms import user_data_dir
            from tokenlog.settings import load_settings
            try:
                t = Translator(load_settings(user_data_dir() / "settings.json").language)
            except (ValueError, OSError):
                t = Translator()
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
            user.MessageBoxW(None, t("Token Ledger 已在运行，或正在安装／卸载。请稍后再打开。"), "Token Ledger", 0x40)
            return
    try:
        from tokenlog.desktop import main as desktop_main
        desktop_main()
    finally:
        if mutex is not None:
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
