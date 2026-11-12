#Requires -Version 5.1
# WS Skim - create/refresh Desktop and Start-menu "WS Skim" shortcuts.
# Targets wscript.exe + Start-WsSkim.vbs so Explorer keeps the custom icon
# and the GUI starts without a console window.
param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs = Join-Path $here "Start-WsSkim.vbs"
$ico = Join-Path $here "assets\ws-skim.ico"
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"

if (-not (Test-Path -LiteralPath $vbs)) {
    Write-Host "ERROR: Start-WsSkim.vbs missing next to this script." -ForegroundColor Red
    if (-not $NoPause) { pause }
    exit 1
}
if (-not (Test-Path -LiteralPath $ico)) {
    Write-Host "ERROR: assets\ws-skim.ico missing." -ForegroundColor Red
    if (-not $NoPause) { pause }
    exit 1
}

$desk = [Environment]::GetFolderPath("Desktop")
if (-not $desk -or -not (Test-Path -LiteralPath $desk)) {
    $desk = Join-Path $env:USERPROFILE "Desktop"
}
if (-not (Test-Path -LiteralPath $desk)) {
    $desk = Join-Path $env:USERPROFILE "OneDrive\Desktop"
}
$lnkPath = Join-Path $desk "WS Skim.lnk"
$icoAbs = (Resolve-Path -LiteralPath $ico).Path
$vbsAbs = (Resolve-Path -LiteralPath $vbs).Path

$wsh = New-Object -ComObject WScript.Shell
$s = $wsh.CreateShortcut($lnkPath)
$s.TargetPath = $wscript
$s.Arguments = "//nologo `"$vbsAbs`""
$s.WorkingDirectory = $here
$s.WindowStyle = 1
$s.Description = "WS Skim meteor-scatter decoder"
$s.IconLocation = "$icoAbs,0"
$s.Save()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null

$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
if (-not (Test-Path -LiteralPath $startMenu)) {
    New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
}
$startLnk = Join-Path $startMenu "WS Skim.lnk"
$wsh2 = New-Object -ComObject WScript.Shell
$sm = $wsh2.CreateShortcut($startLnk)
$sm.TargetPath = $wscript
$sm.Arguments = "//nologo `"$vbsAbs`""
$sm.WorkingDirectory = $here
$sm.WindowStyle = 1
$sm.Description = "WS Skim meteor-scatter decoder"
$sm.IconLocation = "$icoAbs,0"
$sm.Save()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh2) | Out-Null

Write-Host ""
Write-Host " Desktop shortcut ready: WS Skim"
Write-Host "  $lnkPath"
Write-Host " Start menu: $startLnk"
Write-Host "  Icon: $icoAbs"
Write-Host " Double-click WS Skim."
Write-Host ""

if (-not $NoPause) { pause }
exit 0
