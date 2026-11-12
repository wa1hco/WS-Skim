\
@echo off
REM Starts WS Skim without being the Desktop shortcut target.
REM The shortcut uses Start-WsSkim.vbs so there is no console window.
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo WS Skim is not installed yet.
  echo Double-click Install-WsSkim.cmd, then try again.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" map144.py
