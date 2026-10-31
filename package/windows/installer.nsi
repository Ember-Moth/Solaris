; The Windows installer and uninstaller (docs/architecture.md §9). scripts/build_windows.py
; compiles it with these definitions:
;   APP_DIRECTORY    the install directory as built: Solaris.exe, solaris-service.exe, geodata\
;   APP_VERSION      pyproject.toml's version, X.Y.Z
;   OUTPUT_FILE      Solaris-<version>-windows-x64-setup.exe
;   SETUP_SCRIPT     package/windows/setup.ps1, which closes the UI and handles the service
;   UNINSTALL_FILES  Delete and RMDir lines for exactly the files in APP_DIRECTORY
;
; It is the only place Solaris asks for elevation: the installer and the uninstaller run as
; administrator, the UI never does. Custom directories are verified and protected before
; files are written: the service accepts the UI only from its own directory.
;
; Command line: /S is silent; fresh installs also accept /D=<absolute folder> last, unquoted.
; The uninstaller also takes /PURGE (silent: delete the user data
; too) and /UPGRADE (an installer's call, below: remove the installed files and nothing else).

Unicode true
ManifestDPIAware true
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!include LogicLib.nsh
!include FileFunc.nsh
!include Sections.nsh
!include WinVer.nsh
!include x64.nsh
!include "${INSTALLER_UI_INCLUDE}"

!define PRODUCT "Solaris"
!define CLIENT "Solaris.exe"
!define SERVICE_EXECUTABLE "solaris-service.exe"
!define UNINSTALLER "uninstall.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT}"
; One-click import (D6, docs/release.md §7): the link is the UI's first argument.
!define LINK_COMMAND '"$INSTDIR\${CLIENT}" "%1"'
!define ERROR_LOG "$TEMP\${PRODUCT}-setup-error.log"
; 64-bit PowerShell: the installer itself is a 32-bit process.
!define POWERSHELL "$WINDIR\SysNative\WindowsPowerShell\v1.0\powershell.exe"

Name "${PRODUCT}"
OutFile "${OUTPUT_FILE}"
InstallDir "$PROGRAMFILES64\${PRODUCT}"
BrandingText "${PRODUCT} ${APP_VERSION}"
ShowInstDetails show
ShowUninstDetails show

Var Upgrade      ; the installer: a previous installation is in $INSTDIR
Var UpgradeCall  ; the uninstaller: called by a newer installer with /UPGRADE

; NSIS's built-in pages remain hidden: interactive runs switch to the silent engine
; after the custom UI confirms the operation. /S never creates a UI window.
Icon "${APP_ICON}"
UninstallIcon "${APP_ICON}"
XPStyle on
Page instfiles
UninstPage instfiles

; The first language is the fallback; NSIS picks the one matching the system's display
; language at startup.
LoadLanguageFile "${NSISDIR}\Contrib\Language files\English.nlf"
LoadLanguageFile "${NSISDIR}\Contrib\Language files\SimpChinese.nlf"

LangString InterfaceFailed ${LANG_ENGLISH} "The setup window could not be opened. Please run the installer again."
LangString InterfaceFailed ${LANG_SIMPCHINESE} "无法打开安装界面，请重新运行安装程序。"
LangString CopyingFiles ${LANG_ENGLISH} "Copying application files…"
LangString CopyingFiles ${LANG_SIMPCHINESE} "正在复制应用文件…"
LangString CheckingDirectory ${LANG_ENGLISH} "Checking the install folder…"
LangString CheckingDirectory ${LANG_SIMPCHINESE} "正在检查安装文件夹…"
LangString RegisteringApp ${LANG_ENGLISH} "Registering Solaris…"
LangString RegisteringApp ${LANG_SIMPCHINESE} "正在注册 Solaris…"
LangString RemovingFiles ${LANG_ENGLISH} "Removing application files…"
LangString RemovingFiles ${LANG_SIMPCHINESE} "正在移除应用文件…"
LangString ClearingData ${LANG_ENGLISH} "Clearing selected application data…"
LangString ClearingData ${LANG_SIMPCHINESE} "正在清除所选应用数据…"

LangString NeedsWindows ${LANG_ENGLISH} "${PRODUCT} needs 64-bit Windows 10 or later."
LangString NeedsWindows ${LANG_SIMPCHINESE} "${PRODUCT} 需要 64 位的 Windows 10 或更高版本。"
LangString SetupFailed ${LANG_ENGLISH} "${PRODUCT} could not complete the operation."
LangString SetupFailed ${LANG_SIMPCHINESE} "${PRODUCT} 未能完成操作。"
LangString UninstallerFailed ${LANG_ENGLISH} "The installed version of ${PRODUCT} could not be removed (result: $0). Uninstall it in Settings > Apps, then run this installer again."
LangString UninstallerFailed ${LANG_SIMPCHINESE} "无法移除已安装的 ${PRODUCT}（结果：$0）。请先在“设置 > 应用”中卸载，再运行本安装程序。"
LangString ClosingUI ${LANG_ENGLISH} "Closing ${PRODUCT}…"
LangString ClosingUI ${LANG_SIMPCHINESE} "正在关闭 ${PRODUCT}…"
LangString StoppingService ${LANG_ENGLISH} "Stopping the ${PRODUCT} service…"
LangString StoppingService ${LANG_SIMPCHINESE} "正在停止 ${PRODUCT} 服务…"
LangString RemovingOld ${LANG_ENGLISH} "Removing the installed version…"
LangString RemovingOld ${LANG_SIMPCHINESE} "正在移除已安装的版本…"
LangString StartingService ${LANG_ENGLISH} "Starting the ${PRODUCT} service…"
LangString StartingService ${LANG_SIMPCHINESE} "正在启动 ${PRODUCT} 服务…"
LangString RemovingService ${LANG_ENGLISH} "Removing the ${PRODUCT} service…"
LangString RemovingService ${LANG_SIMPCHINESE} "正在移除 ${PRODUCT} 服务…"
LangString DataSection ${LANG_ENGLISH} "Subscriptions, settings and logs"
LangString DataSection ${LANG_SIMPCHINESE} "订阅、设置和日志"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductName" "${PRODUCT}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileDescription" "${PRODUCT} Setup"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "LegalCopyright" "GPL-3.0-only"

; Stops with MESSAGE and exit code CODE (Windows Installer's codes, as other installers use
; them). Silent runs show nothing, so the message also goes to ERROR_LOG.
!macro Fail MESSAGE CODE
  FileOpen $9 "${ERROR_LOG}" w
  FileWriteUTF16LE /BOM $9 "${PRODUCT} ${APP_VERSION}: ${MESSAGE}$\r$\n"
  FileClose $9
  ${If} $UiEnabled == 1
    SolarisSetupUI::Finish "failure" "${MESSAGE}"
    Pop $9
    StrCpy $UiEnabled 0
  ${Else}
    MessageBox MB_OK|MB_ICONSTOP "${MESSAGE}" /SD IDOK
  ${EndIf}
  SetErrorLevel ${CODE}
  Abort
!macroend

; Runs one of setup.ps1's operations; it prints why it failed, which the message shows as it is.
!macro Setup OPERATION
  FileOpen $8 "$PLUGINSDIR\install-directory.txt" w
  FileWriteUTF16LE /BOM $8 "$INSTDIR"
  FileClose $8
  nsExec::ExecToStack /TIMEOUT=180000 '"${POWERSHELL}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\setup.ps1" -Operation ${OPERATION} -InstallDirectoryFile "$PLUGINSDIR\install-directory.txt"'
  Pop $0
  Pop $1
  ${If} $1 != ""
    DetailPrint "$1"
    !insertmacro UiLog "$1"
  ${EndIf}
  ${If} $0 != 0
    !insertmacro Fail "$(SetupFailed)$\r$\n$\r$\n$1" 1603
  ${EndIf}
!macroend

!macro RegisterScheme SCHEME
  WriteRegStr HKLM "Software\Classes\${SCHEME}" "" "URL:${PRODUCT}"
  WriteRegStr HKLM "Software\Classes\${SCHEME}" "URL Protocol" ""
  WriteRegStr HKLM "Software\Classes\${SCHEME}\DefaultIcon" "" '"$INSTDIR\${CLIENT}",0'
  WriteRegStr HKLM "Software\Classes\${SCHEME}\shell\open\command" "" '${LINK_COMMAND}'
!macroend

; Another client may have registered clash: since; leave the scheme alone unless it still
; opens this installation.
!macro UnregisterScheme SCHEME
  ReadRegStr $0 HKLM "Software\Classes\${SCHEME}\shell\open\command" ""
  ${If} $0 == '${LINK_COMMAND}'
    DeleteRegKey HKLM "Software\Classes\${SCHEME}"
  ${EndIf}
!macroend

Function .onInit
  StrCpy $UiEnabled 0
  Delete "${ERROR_LOG}"
  ${IfNot} ${RunningX64}
  ${OrIfNot} ${AtLeastWin10}
    !insertmacro Fail "$(NeedsWindows)" 1633
  ${EndIf}
  SetRegView 64
  SetShellVarContext all
  StrCpy $Upgrade 0
  ReadRegStr $0 HKLM "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $0 != ""
    ; An update always uses the registered installation, including a custom location.
    StrCpy $INSTDIR $0
    StrCpy $Upgrade 1
  ${EndIf}
  IfSilent done
  ${If} $Upgrade == 1
    !insertmacro UiBegin "upgrade"
  ${Else}
    !insertmacro UiBegin "install"
  ${EndIf}
  done:
FunctionEnd

Section
  InitPluginsDir
  File "/oname=$PLUGINSDIR\setup.ps1" "${SETUP_SCRIPT}"
  !insertmacro UiStatus "$(CheckingDirectory)"
  !insertmacro Setup prepare-directory
  ; setup.ps1 asks the UI to quit, and ends it when it does not; the tunnel runs in the service.
  !insertmacro UiStatus "$(ClosingUI)"
  !insertmacro Setup close-ui
  !insertmacro UiStatus "$(StoppingService)"
  !insertmacro Setup stop-service
  ; The previous uninstaller removes exactly the files it installed, so files a newer
  ; version no longer ships do not stay behind. Without it, the new files go over the old.
  ${If} $Upgrade == 1
  ${AndIf} ${FileExists} "$INSTDIR\${UNINSTALLER}"
    !insertmacro UiStatus "$(RemovingOld)"
    ClearErrors
    ExecWait '"$INSTDIR\${UNINSTALLER}" /S /UPGRADE _?=$INSTDIR' $0
    ${If} ${Errors}
    ${OrIf} $0 != 0
      !insertmacro Fail "$(UninstallerFailed)" 1603
    ${EndIf}
  ${EndIf}

  !insertmacro UiStatus "$(CopyingFiles)"
  SetOutPath "$INSTDIR"
  File /r "${APP_DIRECTORY}\*"
  WriteUninstaller "$INSTDIR\${UNINSTALLER}"

  !insertmacro UiStatus "$(RegisteringApp)"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayIcon" '"$INSTDIR\${CLIENT}",0'
  WriteRegStr HKLM "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\${UNINSTALLER}"'
  WriteRegStr HKLM "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\${UNINSTALLER}" /S'
  WriteRegStr HKLM "${UNINSTALL_KEY}" "URLInfoAbout" "https://github.com/Ember-Moth/Solaris"
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "EstimatedSize" $0
  ; In HKLM: the installer runs as an administrator who may not be the user. A clash: key in
  ; a user's own HKCU, from another client, still wins for that user.
  !insertmacro RegisterScheme solaris
  !insertmacro RegisterScheme clash
  CreateShortcut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\${CLIENT}"

  ; Created, or pointed at this installation again, and started; it restores the tunnel
  ; that was running before the upgrade.
  !insertmacro UiStatus "$(StartingService)"
  !insertmacro Setup install-service
SectionEnd

; The finish page's "Open Solaris". Started from this elevated process, the UI would run as
; administrator; Explorer starts it as the signed-in user instead.
Function OpenUI
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\${CLIENT}"'
FunctionEnd

Function .onInstSuccess
  ${If} $UiEnabled == 1
    SolarisSetupUI::Finish "success" ""
    Pop $0
    StrCpy $UiEnabled 0
    ${If} $0 == "open"
      Call OpenUI
    ${EndIf}
  ${EndIf}
FunctionEnd

Function .onInstFailed
  ${If} $UiEnabled == 1
    SolarisSetupUI::Finish "failure" "$(SetupFailed)"
    Pop $0
    StrCpy $UiEnabled 0
  ${EndIf}
FunctionEnd

Function un.onUninstSuccess
  ${If} $UiEnabled == 1
    SolarisSetupUI::Finish "success" ""
    Pop $0
    StrCpy $UiEnabled 0
  ${EndIf}
FunctionEnd

Function un.onUninstFailed
  ${If} $UiEnabled == 1
    SolarisSetupUI::Finish "failure" "$(SetupFailed)"
    Pop $0
    StrCpy $UiEnabled 0
  ${EndIf}
FunctionEnd

Function un.RemoveFiles
  !include "${UNINSTALL_FILES}"
  Delete "$INSTDIR\.solaris-install-root"
FunctionEnd

Section "!un.${PRODUCT}" UninstallProgram
  SectionIn RO
  ${If} $UpgradeCall == 1
    ; The installer has closed the UI and stopped the service; the service, the login items
    ; and the registrations stay for the new version.
    Call un.RemoveFiles
    Return
  ${EndIf}
  InitPluginsDir
  File "/oname=$PLUGINSDIR\setup.ps1" "${SETUP_SCRIPT}"
  !insertmacro UiStatus "$(ClosingUI)"
  !insertmacro Setup close-ui
  !insertmacro UiStatus "$(RemovingService)"
  !insertmacro Setup remove-service
  ; Every user's "Open at sign-in" task (src/solaris/desktop/login.py).
  !insertmacro Setup remove-tasks
  !insertmacro UnregisterScheme solaris
  !insertmacro UnregisterScheme clash
  Delete "$SMPROGRAMS\${PRODUCT}.lnk"
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  !insertmacro UiStatus "$(RemovingFiles)"
  Call un.RemoveFiles
  Delete "$INSTDIR\${UNINSTALLER}"
  ; Only when empty: files this package did not install stay.
  RMDir "$INSTDIR"
SectionEnd

; Unchecked unless asked for. The service's data is every user's (their configuration,
; overrides and caches under ProgramData); the UI's is the user's who runs the uninstaller.
; Other users' UI data and the credential store (the WARP account) are not reachable from
; here and stay.
Section /o "un.$(DataSection)" UninstallData
  ${If} $UpgradeCall == 1
    Return
  ${EndIf}
  !insertmacro UiStatus "$(ClearingData)"
  RMDir /r "$APPDATA\${PRODUCT}"
  SetShellVarContext current
  RMDir /r "$LOCALAPPDATA\${PRODUCT}"
  SetShellVarContext all
SectionEnd

; After the sections: /PURGE selects the data section by its index.
Function un.onInit
  StrCpy $UiEnabled 0
  SetRegView 64
  SetShellVarContext all
  StrCpy $UpgradeCall 0
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/UPGRADE" $1
  ${IfNot} ${Errors}
    StrCpy $UpgradeCall 1
  ${EndIf}
  ClearErrors
  ${GetOptions} $0 "/PURGE" $1
  ${IfNot} ${Errors}
  ${AndIf} $UpgradeCall == 0
    !insertmacro SelectSection ${UninstallData}
  ${EndIf}
  ${If} $UpgradeCall == 1
    SetSilent silent
    Return
  ${EndIf}
  IfSilent done
  ${If} ${SectionIsSelected} ${UninstallData}
    !insertmacro UiBegin "uninstall-purge"
  ${Else}
    !insertmacro UiBegin "uninstall"
  ${EndIf}
  ${If} $0 == "purge"
    !insertmacro SelectSection ${UninstallData}
  ${Else}
    !insertmacro UnselectSection ${UninstallData}
  ${EndIf}
  done:
FunctionEnd
