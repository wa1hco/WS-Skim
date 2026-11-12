@echo off

REM WS Skim
REM Copyright (C) 2026 Jeff Millar, WA1HCO
REM
REM SPDX-License-Identifier: GPL-3.0-or-later

setlocal EnableExtensions
REM Double-click install with reliable UAC elevation.

cd /d "%~dp0"
set "HERE=%~dp0"
set "PS1=%~dp0Install-WsSkim.ps1"
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
set "LOG=%~dp0install-log.txt"
set "ELEV=%TEMP%\ws-skim-elevate-install.ps1"

echo.
echo  WS Skim Windows install
echo  ======================
echo  Folder: %REPO%
echo  Script: %PS1%
echo  Log:    %LOG%
echo.

if not exist "%REPO%\map144.py" (
  echo  ERROR: This does not look like a WS Skim folder:
  echo    %REPO%
  echo  Extract the download to C:\WS-Skim and try again.
  echo.
  pause
  exit /b 1
)

if not exist "%PS1%" (
  echo  ERROR: Missing Install-WsSkim.ps1
  pause
  exit /b 1
)

echo [%DATE% %TIME%] Install-WsSkim.cmd started > "%LOG%"
echo Repo=%REPO%>> "%LOG%"
echo User=%USERNAME%>> "%LOG%"

net session >nul 2>&1
if not errorlevel 1 (
  echo  Already Administrator - running install...
  echo Already admin >> "%LOG%"
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -RepoPath "%REPO%" -LogPath "%LOG%"
  goto :show
)

echo  Administrator permission is requested so Python can be installed for this PC.
echo  A prompt should appear - click Yes.
echo.
echo Waiting for UAC / elevated install... >> "%LOG%"

(
  echo $ErrorActionPreference = 'Stop'
  echo $ps1  = '%PS1%'
  echo $repo = '%REPO%'
  echo $log  = '%LOG%'
  echo "Elevated helper starting" ^| Out-File -FilePath $log -Append -Encoding utf8
  echo "  ps1=$ps1" ^| Out-File -FilePath $log -Append -Encoding utf8
  echo "  repo=$repo" ^| Out-File -FilePath $log -Append -Encoding utf8
  echo if (-not ^(Test-Path -LiteralPath $ps1^)^) { "MISSING ps1" ^| Out-File $log -Append; exit 2 }
  echo $out = Join-Path $env:TEMP 'ws-skim-elev-ps1-out.txt'
  echo $err = Join-Path $env:TEMP 'ws-skim-elev-ps1-err.txt'
  echo $p = Start-Process -FilePath powershell.exe -Wait -PassThru -NoNewWindow -RedirectStandardOutput $out -RedirectStandardError $err -ArgumentList @^('-NoProfile','-ExecutionPolicy','Bypass','-File',$ps1,'-RepoPath',$repo,'-LogPath',$log^)
  echo $code = $p.ExitCode
  echo if ^(Test-Path $out^) { Get-Content $out -ErrorAction SilentlyContinue ^| ForEach-Object { $_ ^| Out-File -FilePath $log -Append -Encoding utf8 } }
  echo if ^(Test-Path $err^) { Get-Content $err -ErrorAction SilentlyContinue ^| ForEach-Object { "STDERR: $_" ^| Out-File -FilePath $log -Append -Encoding utf8 } }
  echo "Elevated helper exit=$code" ^| Out-File -FilePath $log -Append -Encoding utf8
  echo exit $code
) > "%ELEV%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%ELEV%'); 'Start-Process exit=' + $p.ExitCode | Out-File -FilePath '%LOG%' -Append -Encoding utf8; exit $p.ExitCode } catch { $_ | Out-File -FilePath '%LOG%' -Append -Encoding utf8; 'UAC_DENIED_OR_ERROR' | Out-File -FilePath '%LOG%' -Append -Encoding utf8; exit 1 }"

set ELEV_ERR=%ERRORLEVEL%
echo Elevation/start exit code: %ELEV_ERR% >> "%LOG%"

:show
echo.
echo  ---------- install-log.txt ----------
if exist "%LOG%" (
  type "%LOG%"
) else (
  echo  ^(log missing - unexpected^)
)
echo  -------------------------------------
echo.

findstr /C:"SUCCESS" "%LOG%" >nul 2>&1
if not errorlevel 1 (
  echo  RESULT: SUCCESS
  if exist "%~dp0python-path.txt" (
    echo  Python:
    type "%~dp0python-path.txt"
  )
  echo  Next: double-click WS Skim on the Desktop or in the Start menu
  goto :done
)

findstr /C:"UAC_DENIED_OR_ERROR" "%LOG%" >nul 2>&1
if not errorlevel 1 (
  echo  RESULT: Permission was denied or elevation failed.
  echo  Right-click Install-WsSkim.cmd -^> Run as administrator
  goto :done
)

findstr /C:"Elevated helper starting" "%LOG%" >nul 2>&1
if errorlevel 1 (
  if %ELEV_ERR% neq 0 (
    echo  RESULT: Elevated process did not start ^(cancelled?^).
    echo  Right-click Install-WsSkim.cmd -^> Run as administrator
    goto :done
  )
)

echo  RESULT: Install did not finish with SUCCESS.
echo  Scroll up for FAIL lines in the log.
echo  Or right-click Install-WsSkim.cmd -^> Run as administrator

:done
echo.
echo  Full log: %LOG%
echo.
pause
exit /b 0
