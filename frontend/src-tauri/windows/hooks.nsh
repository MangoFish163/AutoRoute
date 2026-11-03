; Lifecycle extensions for the pinned Tauri NSIS template.
!macro NSIS_HOOK_PREINSTALL
  Call ValidateInstallLocation
  ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
  ReadRegStr $R1 SHCTX "${UNINSTKEY}" "UninstallString"
  ${If} $R1 != ""
    ExecWait '$R1 /S /UPDATE _?=$4' $0
    ${If} $0 != 0
      MessageBox MB_ICONSTOP "旧版本未能安全移除，升级已停止。配置和密钥已保留。" /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
    SetOutPath "$INSTDIR"
  ${EndIf}
  Call StopInstalledApp
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  Call un.StopInstalledApp
  nsExec::ExecToLog '"$INSTDIR\backend\router-backend.exe" --validate-tree "$INSTDIR"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "安装目录含有重解析点或无法安全检查，请查看卸载详情并手动处理后重试。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} $UpdateMode <> 1
    StrCpy $1 ""
    ${If} $DeleteAppDataCheckboxState = 1
      StrCpy $1 "--remove-data"
    ${EndIf}
    nsExec::ExecToLog '"$INSTDIR\backend\router-backend.exe" --cleanup $1'
    Pop $0
    ${If} $0 != 0
      MessageBox MB_ICONSTOP "数据或缓存清理失败。请查看卸载详情，关闭占用文件的程序后重试；尚未删除程序文件。" /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    ${If} $DeleteAppDataCheckboxState = 1
      DetailPrint "本应用管理的配置、密钥和历史记录已删除。"
    ${Else}
      DetailPrint "配置、密钥和历史记录已保留，重新安装后可继续使用。"
    ${EndIf}
    DeleteRegKey HKCU "${MANUPRODUCTKEY}"
    DeleteRegKey /ifempty HKCU "${MANUKEY}"
  ${EndIf}
!macroend

; Restart Manager is used only to check the exact installed executable.
; No process-name kills and no RmForceShutdown fallback.
!macro LMR_STOP_FUNCTION PREFIX
Function ${PREFIX}StopInstalledApp
  !insertmacro RestartManager_StartSession $R0
  ${If} $R0 == ""
    SetErrorLevel 1
    Abort "无法检查应用进程，请退出应用后重试。"
  ${EndIf}
  !insertmacro RestartManager_RegisterFile $R0 "$INSTDIR\${MAINBINARYNAME}.exe"
  StrCpy $9 0
  lmr_wait:
    System::Call 'RSTRTMGR::RmGetList(p R0, *i .r1, *i .r2, p 0, *i .r3) i .r0'
    ${If} $0 = 0
      !insertmacro RestartManager_EndSession $R0
      Return
    ${EndIf}
    ${If} $0 != ${ERROR_MORE_DATA}
      !insertmacro RestartManager_EndSession $R0
      SetErrorLevel 1
      Abort "无法检查应用文件占用，请退出应用后重试。"
    ${EndIf}
    ${If} $9 = 0
      nsExec::ExecToLog '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
      Pop $0
    ${EndIf}
    IntOp $9 $9 + 1
    ${If} $9 > 150
      !insertmacro RestartManager_EndSession $R0
      SetErrorLevel 1
      Abort "应用仍在退出或文件被其他程序占用，请稍后重试。"
    ${EndIf}
    Sleep 100
    Goto lmr_wait
FunctionEnd
!macroend
