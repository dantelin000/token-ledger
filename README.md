# Token Ledger

English | [简体中文](README.zh-CN.md)

A local-first desktop token ledger for **macOS and Windows**. Track Claude Code,
Codex, agy / Antigravity and other AI coding clients separately, with daily
charts, session details, cache breakdowns, CSV export and estimated API values.

The app uses native Tk windows and reads local logs directly. It does not need a
browser, an account connection or API credentials, and does not upload your usage.

## Download and install

Download the installers from [Releases](https://github.com/dantelin000/token-ledger/releases/latest).

| Platform | Installer | Installation |
| --- | --- | --- |
| macOS, Apple Silicon | `TokenLedger-2.3.0-macos-arm64.dmg` | Open the DMG and drag Token Ledger to Applications. |
| Windows 10 / 11, x64 | `TokenLedger-2.3.0-windows-x64-setup.exe` | Follow the setup wizard; a per-user install needs no administrator access. |

Both installers include Python, Tcl/Tk and timezone data. Close the app before
updating. Updates and uninstall preserve your ledger and preferences. The macOS
build supports Apple Silicon only.

These builds are unsigned / ad-hoc signed and macOS builds are not notarized.
Build and verification details are recorded alongside the release downloads.

## Language

The default interface is **English**. To switch, open **Settings → Display &
refresh → Language / 语言 → 简体中文 → Save**. The change applies immediately and
persists across launches. Filters, usage records and user-entered names are
preserved.

## 2.2.1 fix

Antigravity child agents now use the recorded root conversation ID, so one
conversation stays together in Sessions across agent databases and models.
Per-call tokens, model totals and API estimates are preserved. Existing ledgers
are regrouped on their next scan; older logs without a root ID stay separate.

## Features

- Separate tool totals and stable chart colors, with legends and hover details.
- Today, last seven days, this month, all time, or custom date ranges.
- Search sessions and calls by project, model, time or ID.
- Project meters group recorded directories (including subdirectories) and manually selected sessions across tools, with filtered token and API-equivalent totals. Later matching records are included automatically. A record counts once per project but may belong to multiple projects. Editing a project shows its included sessions; deleting one keeps the underlying usage.
- Input, output, cache read, cache write and included reasoning token counts.
- API-equivalent USD estimates for a period, session, model or individual record.
- Explicit short/long cache-write prices; missing duration produces a range.
- Editable model prices; unknown models remain unpriced rather than being guessed.
- Read-only source access, a persistent local SQLite ledger, and CSV export.

## Supported sources

| Client / harness | Source |
| --- | --- |
| Claude Code | Local JSONL message usage |
| Codex CLI / desktop | Local session and archived-session usage |
| agy / Antigravity | Read-only snapshots of local session databases |
| OpenCode | Local SQLite / JSON message usage |
| Gemini CLI | Local session JSON / JSONL usage |
| Cline / Roo Code | Local VS Code task logs |
| Cursor | Official Usage CSV import; opt-in |
| Other clients | OpenAI-compatible, Anthropic, Gemini or Token Ledger v1 usage records |

Configure source paths or add an import in **Settings**. Support depends on the
client version and its recorded usage fields; exact historical tokens cannot be
reconstructed from chat text. See [source coverage](docs/SUPPORTED_HARNESSES.md).

## Counting and price estimates

Input already includes cache reads and writes; output already includes reasoning.
These components must not be added a second time. Legacy counter deltas can
combine multiple requests, so record counts are not exact API request counts.

API values use the **2026-11-01 price snapshot** documented in
[API pricing](docs/API_PRICING.md). They are reference estimates, not subscription
charges, historical bills, remaining quotas or account-wide usage. Other devices,
web chats without local logs, tools, taxes, regional/service-tier premiums and
explicit cache storage duration are outside the estimate's coverage.

## Local data and privacy

| Platform | Ledger and preferences |
| --- | --- |
| macOS | `~/Library/Application Support/TokenLedger/` |
| Windows | `%LOCALAPPDATA%\TokenLedger\` |

The ledger stores counts, timestamps, model IDs, session IDs, project metadata
and source paths. It does not store chat bodies or credentials. Source logs are
read-only. Removing or disabling a source preserves already collected history.

This repository and its source archives contain synthetic test data only;
personal logs, databases, settings, credentials and build environments are excluded.

## Run from source

Use Python 3.10+ with Tcl/Tk (the python.org installers include Tk):

```sh
python -m pip install .
python -m tokenlog
```

For a one-off command-line report:

```sh
python -m tokenlog --report --provider codex --start 2026-11-01 --end 2026-11-01
```

## Development and builds

```sh
python -W error::ResourceWarning -m unittest discover -s tests -v
python scripts/build_desktop.py --source-only
```

Native GUI tests are opt-in with `TOKENLOG_GUI_TEST=1` and require a graphical
session. [Build instructions](packaging/README.md) cover the macOS app and DMG,
Windows NSIS wizard, installer round-trip verification, and artifact audits.

## License

[MIT](LICENSE), copyright © 2026 dantelin000. Bundled third-party runtimes retain
their own license notices.
