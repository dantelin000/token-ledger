# -*- coding: utf-8 -*-
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "${INSTALL_FILES}"
!include "${UNINSTALL_FILES}"

!define APP_NAME "Token Ledger"
!define APP_KEY "Software\TokenLedger"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\TokenLedger"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Token Ledger"
RequestExecutionLevel user
ManifestDPIAware true
ManifestSupportedOS all
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUninstDetails show
BrandingText "Token Ledger ${APP_VERSION}"
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "Token Ledger"
VIAddVersionKey /LANG=1033 "FileDescription" "Token Ledger Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Token Ledger contributors"
Var AppMutex

!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Welcome to Token Ledger Setup"
!define MUI_WELCOMEPAGE_TEXT "Track local token usage by client and view its estimated API value.$\r$\n$\r$\nAll required components are included. You do not need to install Python.$\r$\n$\r$\nPlease close Token Ledger before continuing."
!define MUI_COMPONENTSPAGE_TEXT_TOP "Choose whether to create a desktop shortcut. Start Menu shortcuts are included automatically."
!define MUI_FINISHPAGE_TITLE "Token Ledger is installed"
!define MUI_FINISHPAGE_TEXT "You can now open Token Ledger from the Start Menu.$\r$\n$\r$\nTo use Chinese, open Settings and choose Language / 语言.$\r$\n$\r$\nYour usage ledger and settings are preserved when updating or uninstalling."
!define MUI_FINISHPAGE_RUN "$INSTDIR\Token Ledger.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch Token Ledger"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "Remove Token Ledger program files and shortcuts.$\r$\n$\r$\nYour usage ledger, settings and original client logs will be preserved.$\r$\n$\r$\nPlease close Token Ledger before continuing."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

!macro AcquireAppMutex PREFIX
Function ${PREFIX}AcquireAppMutex
  retry:
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\TokenLedger.Desktop") p.r0 ?e'
  Pop $1
  ${If} $0 P= 0
    MessageBox MB_OK|MB_ICONSTOP "Could not check whether Token Ledger is running. Please try again." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ${If} $1 == 183
    System::Call 'kernel32::CloseHandle(p r0)'
    IfSilent cancelled
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Please close Token Ledger, then click Retry." IDRETRY retry
    cancelled:
    SetErrorLevel 2
    Abort
  ${EndIf}
  StrCpy $AppMutex $0
FunctionEnd
!macroend
!insertmacro AcquireAppMutex ""
!insertmacro AcquireAppMutex "un."

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ; Respect an explicit /D target, otherwise reuse the registered user install.
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} "$0" "/D=" $1
  ${If} ${Errors}
    ReadRegStr $1 HKCU "${APP_KEY}" "InstallLocation"
    ${If} $1 != ""
      StrCpy $INSTDIR $1
    ${EndIf}
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "This package requires 64-bit Windows 10 or Windows 11." /SD IDOK
    SetErrorLevel 3
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "This package requires Windows 10 or Windows 11." /SD IDOK
    SetErrorLevel 3
    Abort
  ${EndIf}
FunctionEnd

Function .onVerifyInstDir
  GetFullPathName $0 "$INSTDIR"
  ${GetRoot} "$0" $1
  ${If} $0 == "$1\"
  ${OrIf} $0 == "$PROFILE"
  ${OrIf} $0 == "$LOCALAPPDATA"
  ${OrIf} $0 == "$LOCALAPPDATA\TokenLedger"
  ${OrIf} $0 == "$WINDIR"
  ${OrIf} $0 == "$PROGRAMFILES64"
  ${OrIf} $0 == "$PROGRAMFILES32"
  ${OrIf} $0 == "$DESKTOP"
  ${OrIf} $0 == "$DOCUMENTS"
  ${OrIf} $0 == "$TEMP"
    Abort
  ${EndIf}
FunctionEnd

Section "Program files (required)" MainFiles
  SectionIn RO
  Call AcquireAppMutex
  SetOverwrite on
  !insertmacro InstallPayload
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\Token Ledger"
  CreateShortcut "$SMPROGRAMS\Token Ledger\Token Ledger.lnk" "$INSTDIR\Token Ledger.exe" "" "$INSTDIR\Token Ledger.exe"
  Delete "$SMPROGRAMS\Token Ledger\卸载 Token Ledger.lnk"
  CreateShortcut "$SMPROGRAMS\Token Ledger\Uninstall Token Ledger.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APP_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "Token Ledger"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Token Ledger.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Token Ledger contributors"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${INSTALLED_KIB}
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Create desktop shortcut" DesktopShortcut
  CreateShortcut "$DESKTOP\Token Ledger.lnk" "$INSTDIR\Token Ledger.exe" "" "$INSTDIR\Token Ledger.exe"
SectionEnd

Function .onInstSuccess
  System::Call 'kernel32::CloseHandle(p $AppMutex)'
FunctionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Section "Uninstall"
  Call un.AcquireAppMutex
  ; Exact build manifest only: never recursively remove the installation folder.
  !insertmacro UninstallPayload
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\Token Ledger.lnk"
  Delete "$SMPROGRAMS\Token Ledger\Token Ledger.lnk"
  Delete "$SMPROGRAMS\Token Ledger\卸载 Token Ledger.lnk"
  Delete "$SMPROGRAMS\Token Ledger\Uninstall Token Ledger.lnk"
  RMDir "$SMPROGRAMS\Token Ledger"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegValue HKCU "${APP_KEY}" "InstallLocation"
  DeleteRegKey /ifempty HKCU "${APP_KEY}"
SectionEnd
