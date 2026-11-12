#Requires -Version 5.1

# WS Skim Windows install
# Copyright (C) 2026 Jeff Millar, WA1HCO
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Always writes a log: scripts\windows\install-log.txt
# Finds or installs official CPython 3.12, creates .venv, installs
# requirements.txt, warns if jt9 is missing, and puts WS Skim on the
# Desktop and in the Start menu. Does not install UHD / B210.

<#
.SYNOPSIS
  Install WS Skim on Windows (Python, venv, shortcuts).

.NOTES
  Keep this file ASCII-only and CRLF. Windows PowerShell 5.1 reads UTF-8
  without BOM as the system ANSI code page.
#>
[CmdletBinding()]
param(
    [string] $RepoPath = "",
    [string] $LogPath = "",
    [switch] $SkipShortcut
)

$ErrorActionPreference = "Stop"
$script:LogFile = $null

function Log([string] $msg, [string] $color = "White") {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    try { Write-Host $line -ForegroundColor $color } catch { Write-Output $line }
    if ($script:LogFile) {
        try { Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8 } catch { }
    }
}
function Step($m) { Log "==> $m" "Cyan" }
function Ok($m)   { Log "    OK: $m" "Green" }
function Warn($m) { Log "    WARN: $m" "Yellow" }
function Fail($m) { Log "    FAIL: $m" "Red" }

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
    foreach ($extra in @(
        "${env:ProgramFiles}\Python312",
        "${env:ProgramFiles}\Python313",
        "${env:LocalAppData}\Programs\Python\Python312",
        "${env:LocalAppData}\Programs\Python\Python313",
        "${env:ProgramFiles}\Python311",
        "${env:ProgramFiles}\Python310"
    )) {
        if ((Test-Path $extra) -and ($env:Path -notlike "*$extra*")) {
            $env:Path = "$extra;$env:Path"
        }
    }
}

function Test-IsVendorPython([string] $Exe) {
    if (-not $Exe) { return $true }
    $n = $Exe.ToLowerInvariant()
    foreach ($frag in @(
        '\libreoffice\',
        '\windowsapps\',
        '\microsoft office\',
        '\osgeo',
        '\qgis',
        '\blender\',
        '\arcgis',
        '\gimp\',
        '\inkscape\',
        'python-core-'
    )) {
        if ($n.Contains($frag)) { return $true }
    }
    return $false
}

function Test-PythonExe([string] $Exe) {
    if (-not $Exe) { return $null }
    $Exe = $Exe.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return $null }
    $leaf = Split-Path -Leaf $Exe
    if ($leaf -notmatch '^(python|pythonw|python3)\.exe$') { return $null }
    if (Test-IsVendorPython $Exe) { return $null }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $code = "import sys; print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1])); print(sys.executable)"
        $output = & $Exe -c $code 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        $lines = @($output | ForEach-Object { "$_" } | Where-Object { $_ -and $_.Trim() })
        if (-not $lines -or $lines.Count -lt 1) { return $null }
        $ver = $lines[0].Trim()
        $parts = $ver.Split(".")
        if ($parts.Count -lt 2) { return $null }
        if ([int]$parts[0] -ne 3 -or [int]$parts[1] -lt 10 -or [int]$parts[1] -gt 12) { return $null }
        $resolved = $Exe
        if ($lines.Count -ge 2) {
            $cand = $lines[1].Trim().Trim('"')
            if ((Test-Path -LiteralPath $cand -PathType Leaf) -and -not (Test-IsVendorPython $cand)) {
                $resolved = $cand
            }
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { return $null }
        if (Test-IsVendorPython $resolved) { return $null }
        return @{ Exe = $resolved; Version = $ver }
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Find-Python {
    Update-SessionPath
    Log "    Searching for python.exe..."

    try {
        $pyCmd = Get-Command py -ErrorAction SilentlyContinue
        if ($pyCmd -and -not (Test-IsVendorPython $pyCmd.Source)) {
            $out = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $info = Test-PythonExe $out.Trim()
                if ($info) { Log "    found via py -3.12: $($info.Exe)"; return $info }
            }
            $out = & py -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $info = Test-PythonExe $out.Trim()
                if ($info) { Log "    found via py -3: $($info.Exe)"; return $info }
            }
        }
    } catch { }

    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $info = Test-PythonExe $cmd.Source
            if ($info) { Log "    found on PATH: $($info.Exe)"; return $info }
            Log "    skip PATH ${name}: $($cmd.Source)"
        }
    }

    $searchRoots = @(
        "$env:LocalAppData\Python",
        "$env:LocalAppData\Programs\Python",
        "$env:ProgramFiles\Python314",
        "$env:ProgramFiles\Python313",
        "$env:ProgramFiles\Python312",
        "$env:ProgramFiles\Python311",
        "$env:ProgramFiles\Python310",
        "${env:ProgramFiles(x86)}\Python312",
        "${env:ProgramFiles(x86)}\Python310"
    )
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "$env:LocalAppData\Programs")) {
        if (-not $base -or -not (Test-Path $base)) { continue }
        try {
            $searchRoots += @(Get-ChildItem -Path $base -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName })
        } catch { }
    }
    $found = @()
    foreach ($root in ($searchRoots | Select-Object -Unique)) {
        if (-not (Test-Path $root)) { continue }
        try {
            $found += Get-ChildItem -Path $root -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue -Depth 4 |
                Where-Object { $_.FullName -notmatch "\\Lib\\|WindowsApps|\\Scripts\\" } |
                ForEach-Object { $_.FullName }
        } catch { }
    }

    foreach ($hive in @(
        "HKLM:\SOFTWARE\Python\PythonCore",
        "HKCU:\SOFTWARE\Python\PythonCore",
        "HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore"
    )) {
        if (-not (Test-Path $hive)) { continue }
        Get-ChildItem $hive -ErrorAction SilentlyContinue | ForEach-Object {
            $ip = Join-Path $_.PSPath "InstallPath"
            try {
                $props = Get-ItemProperty -LiteralPath $ip -ErrorAction SilentlyContinue
                if ($props.ExecutablePath) { $found += $props.ExecutablePath }
                $dir = $props.'(default)'
                if ($dir) { $found += (Join-Path $dir "python.exe") }
            } catch { }
        }
    }

    $candidates = @($found | Where-Object { $_ } | Select-Object -Unique | Sort-Object -Descending)
    foreach ($c in $candidates) {
        if (Test-IsVendorPython $c) {
            Log "    skip vendor/embedded: $c"
            continue
        }
        $info = Test-PythonExe $c
        if ($info) { Log "    found on disk: $($info.Exe)"; return $info }
    }
    Log "    no suitable python.exe found"
    return $null
}

function Install-PythonWinget {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Warn "winget not available"
        return $false
    }
    Step "Trying winget Python 3.12 install..."
    $ids = @("Python.Python.3.12")
    foreach ($id in $ids) {
        Log "    winget install -e --id $id"
        $args = @("install", "-e", "--id", $id, "--source", "winget", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
        if (Test-IsAdmin) { $args += @("--scope", "machine") }
        & winget @args 2>&1 | ForEach-Object { Log "      $_" }
        $code = $LASTEXITCODE
        Log "    winget exit code: $code"
        Update-SessionPath
        Start-Sleep -Seconds 3
        $py = Find-Python
        if ($py) { Ok "winget provided Python $($py.Version)"; return $true }
    }
    return $false
}

function Get-PythonInstaller {
    $ver = "3.12.8"
    $url = "https://www.python.org/ftp/python/$ver/python-$ver-amd64.exe"
    $tmp = Join-Path $env:TEMP "ws-skim-python-$ver-amd64.exe"
    if ((Test-Path $tmp) -and ((Get-Item $tmp).Length -gt 10MB)) {
        Log "    using cached installer: $tmp"
        return $tmp
    }
    Log "    downloading $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    } catch {
        Log "    Invoke-WebRequest failed: $_"
        Log "    trying bitsadmin..."
        & bitsadmin /transfer WsSkimPython /download /priority foreground $url $tmp
        if (-not (Test-Path $tmp)) { throw "Could not download Python installer: $_" }
    }
    if (-not (Test-Path $tmp) -or ((Get-Item $tmp).Length -lt 1MB)) {
        throw "Python installer download incomplete: $tmp"
    }
    Log "    downloaded $([math]::Round((Get-Item $tmp).Length/1MB,1)) MB"
    return $tmp
}

function Install-PythonOfficial {
    Step "Installing Python 3.12.8 (official silent installer)..."
    $tmp = Get-PythonInstaller
    $targetDir = Join-Path $env:ProgramFiles "Python312"
    if (-not (Test-IsAdmin)) {
        $targetDir = Join-Path $env:LocalAppData "Programs\Python\Python312"
    }
    $arg = @(
        "/quiet",
        "InstallAllUsers=$(if (Test-IsAdmin) { '1' } else { '0' })",
        "PrependPath=1",
        "Include_launcher=1",
        "Include_test=0",
        "Include_pip=1",
        "Include_doc=0",
        "SimpleInstall=1",
        "TargetDir=`"$targetDir`""
    ) -join " "
    Log "    installer: $tmp"
    Log "    args: $arg"
    Log "    target: $targetDir"
    $p = Start-Process -FilePath $tmp -ArgumentList $arg -Wait -PassThru
    Log "    installer exit code: $($p.ExitCode)"
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010 -and $p.ExitCode -ne 1638) {
        Fail "Official installer failed with code $($p.ExitCode)"
        return $false
    }
    if ($p.ExitCode -eq 1638) {
        Warn "Installer exit 1638 (product already installed) - will search for existing python.exe"
    }
    Update-SessionPath
    Start-Sleep -Seconds 3
    $direct = Join-Path $targetDir "python.exe"
    $info = Test-PythonExe $direct
    if ($info) { Ok "Python at $direct"; return $true }
    $info = Find-Python
    if ($info) { Ok "Python at $($info.Exe)"; return $true }
    Fail "Installer reported success but python.exe not found under $targetDir"
    return $false
}

function Ensure-Python {
    Step "Python prerequisite"
    $py = Find-Python
    if ($py) {
        Ok "Already installed: $($py.Exe) ($($py.Version))"
        return $py
    }
    Warn "Python not found - installing official CPython 3.12..."
    if (Install-PythonWinget) {
        $py = Find-Python
        if ($py) { return $py }
    }
    if (Install-PythonOfficial) {
        $py = Find-Python
        if ($py) { return $py }
        foreach ($p in @(
            "$env:ProgramFiles\Python312\python.exe",
            "$env:LocalAppData\Programs\Python\Python312\python.exe"
        )) {
            $info = Test-PythonExe $p
            if ($info) { return $info }
        }
    }
    throw @"
Python could not be installed automatically.

Try manually:
  1. Download https://www.python.org/downloads/windows/
  2. Run the official 3.12 installer, and check:
       - Add python.exe to PATH
  3. Open a new window and double-click Install-WsSkim.cmd again

Log: $script:LogFile
"@
}

function Ensure-Venv([string] $Repo, $py) {
    Step "Virtual environment and packages"
    $venvDir = Join-Path $Repo ".venv"
    $venvPy = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Log "    Creating .venv with system site packages..."
        & $py.Exe -m venv --system-site-packages $venvDir
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    } else {
        $cfg = Join-Path $venvDir "pyvenv.cfg"
        if (Test-Path -LiteralPath $cfg) {
            $txt = Get-Content -LiteralPath $cfg -Raw
            if ($txt -match "include-system-site-packages = false") {
                $txt = $txt -replace "include-system-site-packages = false", "include-system-site-packages = true"
                Set-Content -LiteralPath $cfg -Value $txt -Encoding ASCII
            }
        }
        Ok "Reusing existing .venv"
    }
    if (-not (Test-Path -LiteralPath $venvPy)) { throw "venv python missing: $venvPy" }
    Log "    Upgrading pip..."
    & $venvPy -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    $req = Join-Path $Repo "requirements.txt"
    if (-not (Test-Path -LiteralPath $req)) { throw "requirements.txt missing in $Repo" }
    Log "    Installing requirements.txt..."
    & $venvPy -m pip install -r $req
    if ($LASTEXITCODE -ne 0) { throw "pip install requirements failed" }
    Ok "Packages installed"
    return $venvPy
}

function Check-Jt9([string] $Repo, [string] $VenvPy) {
    Step "Looking for WSJT-X (jt9)"
    $env:PYTHONPATH = $Repo
    $out = & $VenvPy -c "from map144_app.detection import find_jt9; print(find_jt9() or '')" 2>&1
    if ($LASTEXITCODE -eq 0 -and "$out".Trim()) {
        Ok "jt9: $($out.ToString().Trim())"
        return
    }
    Warn "WSJT-X was not found (it provides the jt9 decoder)."
    Warn "Install WSJT-X from https://wsjt.sourceforge.io/. You can still open WS Skim now."
}

# ========== main ==========
if (-not $RepoPath) {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoPath = (Resolve-Path -LiteralPath (Join-Path $here "..\..")).Path
}
if (-not $LogPath) {
    $LogPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "install-log.txt"
}
$script:LogFile = $LogPath
Log "WS Skim Windows install starting"
Log "Repo=$RepoPath"
Log "User=$env:USERNAME"

if (-not (Test-Path -LiteralPath (Join-Path $RepoPath "map144.py"))) {
    throw "Not a WS Skim folder: $RepoPath (map144.py missing)"
}

$py = Ensure-Python
$pyPathFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "python-path.txt"
Set-Content -LiteralPath $pyPathFile -Value $py.Exe -Encoding ASCII
$venvPy = Ensure-Venv $RepoPath $py
Check-Jt9 $RepoPath $venvPy

if (-not $SkipShortcut) {
    Step "Desktop and Start menu shortcuts"
    $shortcut = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "Install-WsSkimDesktopShortcut.ps1"
    if (-not (Test-Path -LiteralPath $shortcut)) { throw "Missing Install-WsSkimDesktopShortcut.ps1" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $shortcut -NoPause
    if ($LASTEXITCODE -ne 0) { throw "Shortcut install failed" }
    Ok "Shortcuts ready"
}

Log "SUCCESS"
Log "Next: double-click WS Skim on the Desktop or in the Start menu."
exit 0
