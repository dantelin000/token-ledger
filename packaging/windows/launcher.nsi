# -*- coding: utf-8 -*-
Unicode true
Name "Token Ledger"
OutFile "${OUTPUT}"
Icon "${APP_ICON}"
RequestExecutionLevel user
ManifestDPIAware true
SilentInstall silent
AutoCloseWindow true
SetCompressor /SOLID lzma
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "Token Ledger"
VIAddVersionKey /LANG=1033 "FileDescription" "Token Ledger Desktop"
VIAddVersionKey /LANG=1033 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Token Ledger contributors"
!include "FileFunc.nsh"

Section
  SetOutPath "$EXEDIR"
  ${GetParameters} $0
  ClearErrors
  Exec '"$EXEDIR\runtime\pythonw.exe" -I -B "$EXEDIR\app\start.py" $0'
  IfErrors 0 done
  MessageBox MB_OK|MB_ICONSTOP "Could not start Token Ledger. Please reinstall to repair its components."
  SetErrorLevel 1
  done:
SectionEnd
