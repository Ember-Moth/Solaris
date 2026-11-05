; Delete only files named by the installed payload's manifest. A locked or read-only
; old extension module must not be silently retained during an architecture upgrade.
; The including script supplies LogicLib, the Fail macro and a RemovalFailed macro, which
; fails with a message naming PATH.
!ifndef SOLARIS_REMOVE_FILES_INCLUDED
!define SOLARIS_REMOVE_FILES_INCLUDED

!macro RemoveInstalledFile PATH
  Push "${PATH}"
  Call un.RemoveInstalledFile
!macroend

Function un.RemoveInstalledFile
  Exch $R0
  Push $R1
  StrCpy $R1 0
  retry:
    ; NSIS Delete handles read-only attributes and treats an absent file as success.
    ; Keep its attribute handling intact, including restoration if removal fails.
    ClearErrors
    Delete "$R0"
    IfErrors failed
    IfFileExists "$R0" failed done
  failed:
    IntOp $R1 $R1 + 1
    ${If} $R1 < 6
      Sleep 500
      Goto retry
    ${EndIf}
    !insertmacro RemovalFailed "$R0"
  done:
    Pop $R1
    Pop $R0
FunctionEnd

!endif
