# Releasing WS Skim

Only a version tag changes what users install. Pushes to branches and
to `main` are backup and tracking; they never touch a release.

## What a tag does

Pushing a tag `v*` runs `.github/workflows/installers.yml`:

1. **Linux:** `packaging/linux/build-installer.sh` builds
   `ws-skim-linux-install.sh`, a single self-extracting installer
   (shell header plus a tar.gz of the tree). The job installs it on a
   clean Ubuntu 24.04 runner with the distro Python, checks the menu
   entry and desktop icon, launches the GUI headless for 30 s, then
   uninstalls.
2. **Windows:** `packaging/windows/build-installer.ps1` builds
   `WS-Skim-Setup.exe` with Inno Setup 6. The job installs it silently
   on a clean Windows runner, checks the desktop and Start-menu
   shortcuts and the Python environment, then uninstalls.
3. **Release:** if both pass, a GitHub release is created for the tag
   with both installers and `SHA256SUMS.txt`. Tags with a suffix
   (`v0.1.4-test`, `v0.2.0-rc.1`) are marked **prerelease**.

The same build and install tests run on pull requests that touch
`scripts/`, `packaging/` or the other installer inputs listed in the
workflow.

Both installer files have no version in their names, so these links
always point at the newest full (non-pre) release:

- `https://github.com/wa1hco/WS-Skim/releases/latest/download/WS-Skim-Setup.exe`
- `https://github.com/wa1hco/WS-Skim/releases/latest/download/ws-skim-linux-install.sh`

GitHub's "latest" skips prereleases, so these links return 404 until the
first full release exists.

## Version and hash

The version lives in `map144_app/__init__.py` and `pyproject.toml`
(`tools/dev/check-version.py` keeps them equal). The tag must be
`v<version>` or `v<version>-<suffix>`; `tools/dev/stage_release.py`
refuses anything else. The build writes the short git hash to a `BUILD`
file, so the installed app shows `0.1.4 (51b9fd9)` in its startup log line
and **Help → About**, and the release title carries the same pair.

## Cutting a release

1. Bump the version in both files if needed and update `CHANGELOG.md`.
   Merge to `main` with CI green.
2. Tag and push:

   ```bash
   git tag v0.1.4-test
   git push origin v0.1.4-test
   ```

3. Watch the **Installers** workflow. When it finishes, the release
   appears on the Releases page.
4. For a test release, install it on a clean machine and check that
   the desktop icon appears (and the Start-menu entry on Windows).

If a tag build fails, fix the problem, delete the tag
(`git push --delete origin <tag>` and `git tag -d <tag>`) and tag again.

## Building locally

- Linux: `packaging/linux/build-installer.sh` → `dist/ws-skim-linux-install.sh`
- Windows: `powershell -ExecutionPolicy Bypass -File packaging\windows\build-installer.ps1`
  → `dist\WS-Skim-Setup.exe` (needs Inno Setup 6)

Both build from the committed tree (`git archive HEAD`), so commit
first. `docs/`, `tests/` and other developer-only paths are left out
of the installers.

## Where things are installed

| | Linux | Windows |
|---|---|---|
| Program | `~/.local/share/ws-skim` | `%LOCALAPPDATA%\Programs\WS-Skim` |
| Launcher | `~/.local/share/applications/ws-skim.desktop` and `~/Desktop/ws-skim.desktop` | Start menu and desktop `WS Skim` shortcuts |
| Admin rights | not needed | not needed (the optional B210 UHD driver asks) |
| Launch | `scripts/run.sh` | `.venv\Scripts\pythonw.exe map144.py` (no console) |
| Settings | `~/.config/ws-skim` | `%APPDATA%\WS-Skim` |
| Logs | `~/.local/state/ws-skim/logs` | `%LOCALAPPDATA%\WS-Skim\logs` (plus `console.log` for pythonw output) |
| Python | distro `python3` (`python3-venv`) | found, or installed per-user by `install.ps1` (winget, else python.org 3.12.10) |
| Update | run a newer installer | run a newer installer |
| Uninstall | `bash ws-skim-linux-install.sh --uninstall` | Windows Settings → Apps |

Updates keep the Python environment (`.venv`), the B210 kit (`env`)
and recordings (`MSK144`). Settings and logs are outside the program
folder (`map144_app/user_paths.py`), so neither uninstaller touches them.
On first start, older settings (the `RadioIQ` QSettings store and
`~/.config/map144`) and in-tree `MSK144/logs` are copied over once.
