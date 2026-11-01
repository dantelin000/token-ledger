# Desktop builds

The runtime UI uses native Tk widgets and does not open a browser or start an HTTP server.

The macOS app and optional Windows portable PyInstaller executable are built on
their target operating systems with a Python that includes Tcl/Tk. The Windows
installation wizard below can also be cross-compiled with NSIS.

macOS:

```sh
python3 -m venv .venv-desktop
.venv-desktop/bin/python -m pip install ".[build]"
.venv-desktop/bin/python scripts/build_desktop.py
```

Windows PowerShell:

```powershell
py -3.13 -m venv .venv-desktop
.venv-desktop\Scripts\python.exe -m pip install ".[build]"
.venv-desktop\Scripts\python.exe scripts\build_desktop.py
```

Use the virtual environment's Python for all commands after creating it. On macOS the official python.org installer includes Tk; some Homebrew Python installations omit it. Verify `python -c "import tkinter"` before building. The packaged app includes Python, Tcl/Tk and the timezone database, so users do not need Python installed.

Outputs:

- macOS: `dist/macos-<architecture>/Token Ledger.app`, plus a ZIP preserving executable permissions and symlinks.
- Windows installation wizard: `dist/TokenLedger-2.2.1-windows-x64-setup.exe`.
- Optional Windows portable PyInstaller build: `dist/windows-<architecture>/Token Ledger.exe`, plus a ZIP.
- `dist/TokenLedger-source.zip`: clean cross-platform source, with no logs, ledger, credentials, local config or virtual environment.

`python scripts/build_desktop.py --source-only` creates only the clean source ZIP.

The application MIT license and upstream runtime notices are bundled in both
platforms. `packaging/licenses/UPSTREAM_LICENSES.json` records the upstream license sources
and hashes. PyInstaller also copies tzdata's distribution metadata and licenses.

Classic drag-to-Applications installer, after building the Apple Silicon app:

```sh
python -m pip install '.[dmg]'
python scripts/build_dmg.py
```

This produces `dist/TokenLedger-2.2.1-macos-arm64.dmg`, with the app on the left,
an `/Applications` folder link on the right, an arrow background and fixed Finder
icon positions. The script verifies the image checksum, mounts it read-only,
checks the layout and signature, and launches a temporary relocated copy's
self-test. It does not install into or modify the user's actual Applications folder.

`--smoke-test` additionally launches the packaged self-test: it initializes and closes Tk without scanning private logs or calling any model.

`python packaging/audit_artifacts.py dist/TokenLedger-source.zip` checks the source archive for private log/database filenames and developer home paths in text. The same check accepts a built `.app` folder. It supplements the allowlisted source packaging; it is not a full binary security audit.

macOS 仅构建 Apple Silicon arm64。打包脚本对每个 Mach-O 检查架构，拒绝包含 Intel 切片的组件。

## Windows installation wizard

Install the [NSIS compiler](https://nsis.sourceforge.io/Docs/Chapter3.html)
(`brew install makensis` on macOS, or `choco install nsis` on Windows), then run:

```sh
python scripts/build_windows_installer.py
```

`--makensis PATH` selects a compiler outside PATH. A relocated compiler may need
`NSISDIR` set to its `share/nsis` directory. The build uses only Python's standard
library and NSIS; it does not require a host Tk installation or PyInstaller.
Dependencies download once into `build/windows-installer/downloads` and are
verified against `packaging/windows/dependencies.json` before extraction:

- CPython 3.13.18, Windows x64, from Astral's
  [python-build-standalone](https://docs.astral.sh/python-build-standalone/distributions/),
  release 20261026, with Tcl/Tk, SQLite and upstream licenses retained.
- The pure-Python `tzdata` 2026.6 wheel, including its license.

The offline English wizard has five pages: welcome, install location, optional
desktop shortcut, installation progress, and finish with an optional launch.
It installs for the current user, defaults to `%LOCALAPPDATA%\Programs\Token Ledger`,
and registers Start Menu shortcuts and the Windows uninstall entry. It requires
Windows 10/11 x64; the small NSIS bootstrap is x86 and launches an x64 runtime.
No Python installation, network download, administrator access, scheduled task
or startup item is required. The launcher uses `-I -B` with the bundled runtime.

The app and installer share a Windows mutex so an upgrade/uninstall requests
that the user close the app before changing its files. Uninstall deletes only
the compiled manifest's exact file list, removes empty program folders, and
preserves both `%LOCALAPPDATA%\TokenLedger` and unrelated files in the program
directory. It never recursively deletes the installation directory.

On a **clean Windows test account**, run:

```powershell
python scripts/build_windows_installer.py --smoke-test
```

This installs to a temporary path containing spaces and Chinese characters,
checks the installed payload hashes and native Tk self-test, uninstalls, and
checks that a data sentinel and an unrelated installation-directory file both
survive. It refuses to overwrite an existing registered installation. This is
an automated runtime check; manual visual acceptance of the wizard is separate.

`dist/windows-installer-info.json` records hashes, dependency versions, build
host and whether the Windows round trip actually ran. Cross-compilation on
macOS does not verify Windows runtime behavior; inspect this field for the
particular release artifact.

The checked-in GitHub Actions workflow builds macOS ARM and the Windows x64
wizard independently, including native GUI tests and the Windows round trip.
A prepared workflow alone does not confirm a passing run; release verification
metadata records the actual results.

Local macOS builds use PyInstaller's ad-hoc signature, without a Developer ID or notarization; Windows builds are unsigned. Production distribution requires the owner's signing setup. Source paths, usage databases, API keys and account credentials are not included in the app.

PyInstaller builds are specific to their OS and interpreter architecture; see its [official runtime documentation](https://pyinstaller.org/en/stable/operating-mode.html).

## Interface languages

Version 2.2 defaults to English on both platforms. Choose **Settings → Display & refresh → Language / 语言 → 简体中文 → Save** to switch immediately. The preference persists in `settings.json` as `language: "en"` or `"zh_CN"`. Older settings without this field default to English. Changing language preserves filters, token counts, user names / paths, and unsaved edits in an open price editor. The installer is English; language selection is inside the application.
