; A custom Win32 surface over NSIS's silent engine. UI callbacks never execute installer
; code: Begin/Finish return a decision to this script, which remains the sole executor.
!addplugindir /x86-unicode "${INSTALLER_UI_DIR}"
Var UiEnabled

!macro UiAssets
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=solaris.ico "${APP_ICON}"
  File /oname=Inter-Regular.ttf "${INSTALLER_FONTS}\inter\Inter-Regular.ttf"
  File /oname=Inter-Medium.ttf "${INSTALLER_FONTS}\inter\Inter-Medium.ttf"
  File /oname=Inter-SemiBold.ttf "${INSTALLER_FONTS}\inter\Inter-SemiBold.ttf"
  File /oname=Inter-OFL.txt "${INSTALLER_FONTS}\inter\OFL.txt"
  File /oname=JetBrainsMono-Regular.ttf "${INSTALLER_FONTS}\jetbrains-mono\JetBrainsMono-Regular.ttf"
  File /oname=JetBrainsMono-OFL.txt "${INSTALLER_FONTS}\jetbrains-mono\OFL.txt"
  File /oname=LICENSE.txt "${INSTALLER_LICENSE}"
!macroend

; Only used from .onInit / un.onInit, where SetSilent is valid. $0 retains the choice
; (proceed/purge) so un.onInit can set its optional data-removal section.
!macro UiBegin MODE
  !insertmacro UiAssets
  SolarisSetupUI::Begin "${MODE}" "$LANGUAGE" "${APP_VERSION}" "$INSTDIR" "$PLUGINSDIR" "auto" "${APP_ARCH}"
  Pop $0
  ${If} $0 == "cancel"
    SetErrorLevel 1223
    Quit
  ${EndIf}
  ${If} $0 == "error"
    MessageBox MB_OK|MB_ICONSTOP "$(InterfaceFailed)"
    SetErrorLevel 1603
    Quit
  ${EndIf}
  SolarisSetupUI::GetDirectory
  Pop $INSTDIR
  StrCpy $UiEnabled 1
  SetSilent silent
!macroend

!macro UiStatus TEXT
  DetailPrint "${TEXT}"
  ${If} $UiEnabled == 1
    SolarisSetupUI::Status "${TEXT}"
  ${EndIf}
!macroend

!macro UiLog TEXT
  ${If} $UiEnabled == 1
    SolarisSetupUI::Log "${TEXT}"
  ${EndIf}
!macroend
