@echo off
REM run.bat — launch WS Skim from its venv (Windows).
REM Re-runnable; passes any args straight through to ws_skim.py.
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found.  Run scripts\windows\install.ps1 first. 1>&2
    exit /b 1
)
".venv\Scripts\python.exe" ws_skim.py %*
