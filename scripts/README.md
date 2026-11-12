# scripts/

Operator launchers and installers for WS Skim. Lab benches and maintainer tools live under `tools/` and `tools/dev/`.

## Linux

| File | Purpose |
|--|--|
| `Install WS Skim.desktop` | Double-click to install or update, and to put **WS Skim** on the desktop and in the app menu. |
| `add-ws-skim-to-desktop.sh` | The work the desktop file runs. You do not need to start this yourself. |
| `start-ws-skim.sh` | Starts the app. The **WS Skim** desktop icon runs this. |
| `install-linux.sh` | Developer entry that does the same work as **Install WS Skim**. |
| `install.sh` | Thin wrapper around `install-linux.sh`, kept so older notes still work. |
| `run.sh` | Starts the app the same way as `start-ws-skim.sh`. |
| `run-router.sh` | Starts the optional router window. |
| `_say.sh`, `_resolve-python.sh` | Helpers used by the scripts above. Not double-clicked. |
| `assets/ws-skim*.png` | Icons for the desktop and app-menu entries. |

## Windows

| File | Purpose |
|--|--|
| `windows/Install-WsSkim.cmd` | Double-click to install or update. Click **Yes** if Windows asks for permission. |
| `windows/Install-WsSkim.ps1` | The install work the `.cmd` file runs. |
| `windows/Install-WsSkimDesktopShortcut.ps1` | Writes the Desktop and Start-menu **WS Skim** icons. |
| `windows/Add WS Skim to Desktop.bat` | Puts the icons back later. |
| `windows/Start-WsSkim.vbs` | Starts the app without a console window. The desktop icon runs this. |
| `windows/Start-WsSkim.cmd` | Console fallback to start the app. |
| `windows/install.ps1` | Thin wrapper around `Install-WsSkim.ps1`, kept so older notes still work. |
| `windows/run.bat` | Starts the app the same way as the desktop icon. |
| `windows/run-router.bat` | Starts the optional router window. |
| `windows/run-b210.bat` | Optional B210 launch helper; UHD is not part of the default install. |
| `windows/assets/ws-skim.ico` | Icon for the Desktop and Start-menu shortcuts. |

The Windows installer writes `windows/install-log.txt`.
