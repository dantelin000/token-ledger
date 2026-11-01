@echo off
cd /d "%~dp0"
if exist "dist\windows-x86_64\Token Ledger.exe" (
  start "" "dist\windows-x86_64\Token Ledger.exe"
  exit /b 0
)
if exist ".venv-desktop\Scripts\python.exe" (
  ".venv-desktop\Scripts\python.exe" -m tokenlog
  if not errorlevel 1 exit /b 0
)
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -m tokenlog
  if not errorlevel 1 exit /b 0
)
where python >nul 2>nul
if %errorlevel% equ 0 (
  python -m tokenlog
  if not errorlevel 1 exit /b 0
)
echo Use the packaged Token Ledger.exe, or install Python from python.org with Tcl/Tk enabled.
pause
