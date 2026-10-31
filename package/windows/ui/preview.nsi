; A read-only harness for the real installer surface. No files are installed, no service
; or registry key is touched, and no application is launched. Only temporary UI assets.
Unicode true
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor lzma
Name "Solaris setup preview"
OutFile "${OUTPUT_FILE}"
InstallDir "$PROGRAMFILES64\Solaris"
Icon "${APP_ICON}"
XPStyle on
Page instfiles
LoadLanguageFile "${NSISDIR}\Contrib\Language files\English.nlf"
LoadLanguageFile "${NSISDIR}\Contrib\Language files\SimpChinese.nlf"
!include LogicLib.nsh
!include FileFunc.nsh
!include "${INSTALLER_UI_INCLUDE}"
Var Mode
Var Theme
Var Outcome
Var Decision

Function .onInit
  ${GetParameters} $0
  ${GetOptions} $0 "/MODE=" $Mode
  ${GetOptions} $0 "/THEME=" $Theme
  ${GetOptions} $0 "/OUTCOME=" $Outcome
  ${GetOptions} $0 "/LANG=" $1
  StrCpy $LANGUAGE ${LANG_ENGLISH}
  ${If} $1 == "zh"
    StrCpy $LANGUAGE ${LANG_SIMPCHINESE}
  ${EndIf}
  !insertmacro UiAssets
  SolarisSetupUI::Begin "preview:$Mode" "$LANGUAGE" "${APP_VERSION}" "$INSTDIR" "$PLUGINSDIR" "$Theme"
  Pop $Decision
  ${If} $Decision == "cancel"
    SetErrorLevel 1223
    Quit
  ${ElseIf} $Decision == "error"
    SetErrorLevel 1603
    Quit
  ${EndIf}
  StrCpy $UiEnabled 1
  SetSilent silent
FunctionEnd

Section
  ${If} $LANGUAGE == ${LANG_SIMPCHINESE}
    !insertmacro UiStatus "正在准备应用文件…"
    !insertmacro UiLog "预览模式：未修改应用、服务或用户数据。"
  ${Else}
    !insertmacro UiStatus "Preparing application files…"
    !insertmacro UiLog "Preview mode: no application, service or user data was changed."
  ${EndIf}
  Sleep 6000
  ${If} $Outcome == "failure"
    SolarisSetupUI::Finish "failure" "Could not start the background service.$\r$\nError 5: Access is denied."
  ${Else}
    SolarisSetupUI::Finish "success" "Operation completed."
  ${EndIf}
  Pop $0
  ${If} $0 == "open"
    SetErrorLevel 10
  ${ElseIf} $Decision == "purge"
    SetErrorLevel 20
  ${Else}
    SetErrorLevel 0
  ${EndIf}
SectionEnd
