#Requires -Version 5.1
# Developer helper. Operators double-click Install-WsSkim.cmd.
# UHD / B210 is a separate optional step (tools\Install-UhdWindows.ps1).
[CmdletBinding()]
param(
    [string] $RepoPath = "",
    [string] $LogPath = ""
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$real = Join-Path $here "Install-WsSkim.ps1"
if (-not $RepoPath) { $RepoPath = (Resolve-Path -LiteralPath (Join-Path $here "..\..")).Path }
if (-not $LogPath) { $LogPath = Join-Path $here "install-log.txt" }
& powershell -NoProfile -ExecutionPolicy Bypass -File $real -RepoPath $RepoPath -LogPath $LogPath
exit $LASTEXITCODE
