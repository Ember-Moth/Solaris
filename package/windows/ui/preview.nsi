; A read-only harness for the real installer surface. No files are installed, no service
; or registry key is touched, and no application is launched. Only temporary UI assets.
; It reports the same steps and text as installer.nsi, a few seconds per step, so a test can
; capture each one: /MODE=install|upgrade|uninstall /OUTCOME=success|failure (at the second
; step, with the message that step fails with) /LANG=en|zh /THEME=auto|light|dark.
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
!define PRODUCT "Solaris"
!include /CHARSET=UTF8 "${__FILEDIR__}\..\messages.nsh"
Var Mode
Var Theme
Var Outcome
Var Decision
Var Failure
Var Failed

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
  ; A sample of the registered version an update replaces.
  StrCpy $2 ""
  ${If} $Mode == "upgrade"
    StrCpy $2 "0.9.4"
  ${EndIf}
  !insertmacro UiAssets
  SolarisSetupUI::Begin "preview:$Mode" "$LANGUAGE" "${APP_VERSION}" "$INSTDIR" "$PLUGINSDIR" "$Theme" "${APP_ARCH}" "$2"
  Pop $Decision
  ${If} $Decision == "cancel"
    SetErrorLevel 1223
    Quit
  ${ElseIf} $Decision == "error"
    SetErrorLevel 1603
    Quit
  ${EndIf}
  SolarisSetupUI::GetDirectory
  Pop $INSTDIR
  StrCpy $UiEnabled 1
  SetSilent silent
FunctionEnd

; installer.nsi reports these steps; the second one fails when asked.
!macro PreviewStep INDEX TEXT
  ${If} $Failed == 0
    !insertmacro UiStep ${INDEX}
    !insertmacro UiStatus "${TEXT}"
    Sleep 2500
    ${If} $Outcome == "failure"
    ${AndIf} ${INDEX} == 1
      SolarisSetupUI::Finish "failure" "$Failure"
      Pop $0
      StrCpy $Failed 1
    ${EndIf}
  ${EndIf}
!macroend

Section
  StrCpy $Failed 0
  ${If} $LANGUAGE == ${LANG_SIMPCHINESE}
    !insertmacro UiLog "预览模式：未修改应用、服务或用户数据。"
  ${Else}
    !insertmacro UiLog "Preview mode: no application, service or user data was changed."
  ${EndIf}
  ${If} $Mode == "uninstall"
    StrCpy $Failure "$(UninstallRemovalFailed) $INSTDIR\solaris-service.exe"
    !insertmacro PreviewStep 0 "$(ClosingUI)"
    !insertmacro PreviewStep 1 "$(RemovingService)"
    !insertmacro PreviewStep 2 "$(RemovingFiles)"
    ${If} $Decision == "purge"
      !insertmacro PreviewStep 3 "$(ClearingData)"
    ${EndIf}
  ${Else}
    StrCpy $Failure "$(CopyFailed)"
    ${If} $Mode == "upgrade"
      !insertmacro PreviewStep 0 "$(StoppingService)"
      !insertmacro PreviewStep 1 "$(RemovingOld)"
    ${Else}
      !insertmacro PreviewStep 0 "$(CheckingDirectory)"
      !insertmacro PreviewStep 1 "$(CopyingFiles)"
    ${EndIf}
    !insertmacro PreviewStep 2 "$(RegisteringApp)"
    !insertmacro PreviewStep 3 "$(StartingService)"
  ${EndIf}
  ${If} $Failed == 0
    SolarisSetupUI::Finish "success" ""
    Pop $0
  ${EndIf}
  ${If} $0 == "open"
    SetErrorLevel 10
  ${ElseIf} $Decision == "purge"
    SetErrorLevel 20
  ${Else}
    SetErrorLevel 0
  ${EndIf}
SectionEnd
