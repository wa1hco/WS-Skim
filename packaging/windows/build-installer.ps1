# build-installer.ps1 - build dist\WS-Skim-Setup.exe with Inno Setup 6.
#
#     powershell -ExecutionPolicy Bypass -File packaging\windows\build-installer.ps1 [-Tag v0.1.4-test]
#
# Needs Python 3 (to stage the tree) and Inno Setup 6.  If ISCC.exe is not
# found it is installed with Chocolatey when available.
param(
    [string]$Tag = ''
)
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root

$Stage = Join-Path $Root 'build\stage-windows'
$stageArgs = @('tools\dev\stage_release.py', '--out', $Stage)
if ($Tag) { $stageArgs += @('--tag', $Tag) }
$Version = (& python @stageArgs | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw 'stage_release.py failed' }

function Find-Iscc {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, "$env:LOCALAPPDATA\Programs")) {
        if (-not $base) { continue }
        $p = Join-Path $base 'Inno Setup 6\ISCC.exe'
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

$Iscc = Find-Iscc
if (-not $Iscc) {
    if (Get-Command choco -ErrorAction SilentlyContinue) {
        Write-Host 'Installing Inno Setup with Chocolatey ...'
        choco install innosetup -y --no-progress | Out-Host
        $Iscc = Find-Iscc
    }
}
if (-not $Iscc) { throw 'Inno Setup 6 (ISCC.exe) not found. Install it from https://jrsoftware.org/isinfo.php' }

$Dist = Join-Path $Root 'dist'
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
& $Iscc "/DAppVersion=$Version" "/DSourceDir=$Stage" "/DOutputDir=$Dist" (Join-Path $PSScriptRoot 'ws-skim.iss')
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
Write-Host "Built $Dist\WS-Skim-Setup.exe (WS Skim $Version)"
