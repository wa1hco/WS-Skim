# Installing WS Skim

This page is the one set of install steps. The other documents link here.

WS Skim needs Python 3.10, 3.11, or 3.12 (the Windows installer looks for official Python 3.12) and [WSJT-X](https://wsjt.sourceforge.io/) for its jt9 decoder. It does **not** install radio drivers. USRP B210 / UHD support is a separate optional step, described at the end.

## Linux

1. Download the Linux package from [GitHub Releases](https://github.com/wa1hco/WS-Skim/releases) when a release is available, or use a copy of the WS Skim folder you already have.
2. Unpack it anywhere. Downloads is fine.
3. Open the unpacked folder, then open **scripts**.
4. Double-click **Install WS Skim**.

WS Skim copies itself to `~/.local/share/ws-skim` in your home folder, sets up its Python environment, and puts **WS Skim** on the desktop and in the app menu. A message says so, and says you can delete the download. Delete the unpacked folder, then double-click **WS Skim**.

The file manager may ask once before **Install WS Skim** runs:

- **Ubuntu and other GNOME desktops:** If a message says the file is not trusted, right-click **Install WS Skim** and click **Allow Launching**. Then double-click it again.
- **KDE:** If a window asks whether to open or run the file, click **Execute**.
- **XFCE:** If a window asks whether to run the file, click **Execute**.
- **Cinnamon:** If a window asks, click **Run**. If it says the file is not trusted, right-click it and click **Allow Launching**, then double-click it again.

If the file opens as a page of text, right-click it, open **Properties**, allow it to run as a program, and double-click it again.

If Python is missing, a message names what to install. On Ubuntu and Debian the package is `python3`. Install that, then double-click **Install WS Skim** again.

If WSJT-X is not installed yet, the installer warns you and still finishes. Install WSJT-X from [wsjt.sourceforge.io](https://wsjt.sourceforge.io/), then start WS Skim again when you want its jt9 decoder.

**Updating.** Double-click **Install WS Skim** again, or install a newer download the same way. That replaces the copy in `~/.local/share/ws-skim`. Your settings and logs stay where they are.

**Only one copy.** The folder is always `~/.local/share/ws-skim`, with no version in its name. The copy an update replaced is kept at `~/.local/share/ws-skim.previous`. The next update replaces that previous copy, so old copies do not pile up.

**A git clone** is not copied. Its **Install WS Skim** puts an icon on the desktop that runs the clone where it is, and the clone updates with git.

## Windows

1. Download the Windows zip from [GitHub Releases](https://github.com/wa1hco/WS-Skim/releases) when a release is available, or use a copy of the WS Skim folder you already have.
2. Extract it to **`C:\WS-Skim`**.
3. Open `C:\WS-Skim\scripts\windows` and double-click **Install-WsSkim.cmd**. Click **Yes** when Windows asks for permission.
4. Double-click **WS Skim** on the Desktop or in the Start menu.

The installer finds or installs official Python 3.12, sets up the program, and puts **WS Skim** on the Desktop and in the Start menu. Its log is `scripts\windows\install-log.txt`.

The scripts are not signed yet, so Windows may warn the first time:

- **"Windows protected your PC"** (SmartScreen): click **More info**, then **Run anyway**, if you trust [wa1hco/WS-Skim](https://github.com/wa1hco/WS-Skim).
- **The file is blocked:** right-click the `.zip` before extracting (or `Install-WsSkim.cmd`), choose **Properties**, check **Unblock**, and click **OK**.

To put the icons back later, double-click `C:\WS-Skim\scripts\windows\Add WS Skim to Desktop.bat`.

**Updating.** Extract a newer zip over `C:\WS-Skim`, then double-click **Install-WsSkim.cmd** again. Your settings and logs stay where they are.

If WSJT-X is not installed yet, the installer warns you and still finishes. Install WSJT-X, then start WS Skim again when you want its jt9 decoder.

## Where your settings live

Settings and logs are not in the program folder, so installing or updating keeps them.

| | Linux | Windows |
|--|--|--|
| Settings (window layout, radio, reporting) | `~/.config/ws-skim/` | `%APPDATA%\WS-Skim\` |
| Logs | `~/.local/state/ws-skim/logs/` | `%LOCALAPPDATA%\WS-Skim\logs\` |

If you already used an older copy of the program, WS Skim copies those settings and logs into the new places the first time it runs.

## After installing

Double-click **WS Skim**. Radio source and sample rate are chosen in the app under the **Source** menu. Reporting is set up under **View → Reporting**.

## USRP B210 (optional)

The default install does not include Ettus UHD or B210 drivers. If you use a B210, do that as a separate step after WS Skim itself is installed:

- Linux: install `python3-uhd` (and the usual UHD packages) from your distribution, then double-click **Install WS Skim** again so the program can see the system package.
- Windows: use `tools\Install-UhdWindows.ps1` and, when you have the offline B210 kit, `tools\install-b210.bat`. Those helpers are described in [tools/kit-README.txt](tools/kit-README.txt).

## Developer

Typed commands are for people changing WS Skim or installing from a shell.

```bash
git clone https://github.com/wa1hco/WS-Skim.git
cd WS-Skim
# Debian or Ubuntu:
sudo apt install -y python3
scripts/install-linux.sh    # same as double-clicking Install WS Skim; a clone stays in place
```

On Windows, `scripts\windows\Install-WsSkim.cmd` is the install entry. `scripts\windows\install.ps1` runs the same PowerShell install. Optional GPU extras (`requirements-gpu.txt`) are not part of the default install.

A git clone can also be started with `scripts/run.sh` or the **WS Skim** desktop icon after install.
