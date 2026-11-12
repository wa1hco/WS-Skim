@echo off
REM Developer helper. Operators double-click the WS Skim desktop icon.
cd /d "%~dp0"
if not exist "%~dp0Start-WsSkim.vbs" (
  echo Missing Start-WsSkim.vbs
  exit /b 1
)
wscript //nologo "%~dp0Start-WsSkim.vbs"
