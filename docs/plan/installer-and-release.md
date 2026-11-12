# Plan: Installer and release

Status: in progress. Tasks 1, 2 (launchers), 4, 5, 6 and 8 are done;
see the task list and [releasing.md](../dev/releasing.md).
Repository: `wa1hco/WS-Skim`.

Related: [docs/plan/ft8-ft4-wims-udp.md](ft8-ft4-wims-udp.md).

## Goal

A visitor to [github.com/wa1hco/WS-Skim](https://github.com/wa1hco/WS-Skim)
should see the installer immediately. The top of the README is a short
intro, then two large links, **Install on Windows** and **Install on
Linux**, pointing at the latest release downloads, each with a few
plain steps. There is no macOS installer for now, and that is
acceptable.

Users never set CLI flags, environment variables, or run `python -m`.
The install ends with a desktop or Start-menu launcher that opens the
GUI, with settings inside the app (zero knowledge, zero memory).

## Current state

Verified from the repo root:

- **Windows:** `scripts/windows/install.ps1`, `run.bat`,
  `run-router.bat`, `run-b210.bat`.
- **Linux:** `scripts/install.sh`, `run.sh`, `run-router.sh`.
  (`install_gui_deps.sh` was removed; it targeted a long-gone
  `flex_gui.py`.)
- **macOS:** none.
- Launchers live under `scripts/` (Linux) and `scripts/windows/`
  (Windows).
- **Installers:** `packaging/windows/ws-skim.iss` (Inno Setup,
  `WS-Skim-Setup.exe`, per-user, desktop and Start-menu shortcuts) and
  `packaging/linux/` (self-extracting `ws-skim-linux-install.sh`;
  `scripts/install-desktop.sh` adds the menu entry and desktop icon).
- `.github/workflows/installers.yml` builds both, installs them on clean
  runners, and on a `v*` tag publishes a GitHub release.
- Settings and logs live in the user profile (`ws_skim/user_paths.py`,
  from PR #6), so reinstalling never touches them. The Windows installer
  installs Python 3.12 per-user when none is found, and its shortcuts use
  `pythonw.exe` (no console).
- Developer scripts from the repo root now live under `tools/dev/`.
- The old `README-Windows.md` and `README-Linux.md` are now developer
  notes in `docs/dev/`; the user-facing README has no CLI flags or
  environment variables. `README_GUI.md` described the removed
  `flex_gui.py`; its FlexRadio setup steps live on in
  `docs/flexradio-setup.md`.
- The install scripts and the window title / About box say WS Skim.
  The Python package is `ws_skim` and the entry point `ws_skim.py`;
  `WS_SKIM_*` variables are read first, with `MAP144_*` as a fallback.
- B210/UHD installers live under `tools/`.
- CI runs pytest on Python 3.10 and 3.12 (added in PR 1).

## Push policy

Pushes happen for two reasons: backup and tracking, and release. Only
a release push updates what users install.

- Backup and tracking pushes go to branches or to `main` and do not
  change the install.
- A version tag triggers a workflow that builds the installers and
  attaches them to a GitHub release.
- The README install buttons always point at the latest release, so
  only a tag changes what users get.

## Versioning

Use a human version that also records the git hash, for example
`v0.2.0 (a3f9c1)`. Show both in the release notes and in the app's
About screen.

Continue from the existing `v0.1.3-alpha` tag:

- Tag the MSK144-only state before generalization. Proposed name:
  `v0.1.4` (to be confirmed).
- First WS Skim release, with FT8/FT4, is proposed as `v0.2.0`.

## Tasks (in order)

1. ~~Move launchers to `scripts/` and developer scripts to `tools/dev/`.~~ Done in PR 2 (this layout change). The desktop installer is still later.
2. ~~Add desktop / Start-menu launchers.~~ Done (installer PR). GUI
   settings already cover source and reporting; anything still needing
   a flag or variable moves into the GUI as it comes up.
3. ~~Rename remaining map144 references.~~ Done: `map144_app` became
   `ws_skim`, `map144.py` became `ws_skim.py`, log files are
   `ws-skim_*.log`, and variables are `WS_SKIM_*` (with `MAP144_*` as a
   fallback). Kept on purpose: the WSJT-X UDP client id and
   PSKReporter software name `map144`, the PulseAudio sink names
   (`MAP144-WSJT-X`, `map144.RF0.rx`) and the router's QSettings key,
   because station setups refer to them.
4. ~~Move the old READMEs (`README-Windows.md`, `README-Linux.md`,
   `README_GUI.md`) into `docs/` and remove environment-variable, CLI
   flag, and `python -m` instructions from user-facing docs.~~ Done:
   `docs/dev/linux.md`, `docs/dev/windows.md`, `docs/flexradio-setup.md`.
5. ~~Add the release workflow (tag builds installers and attaches them
   to a GitHub release).~~ Done: `.github/workflows/installers.yml`.
6. ~~Add CI that tests the installers on clean Windows and Linux
   runners.~~ Done: same workflow, on pull requests that touch
   installer inputs.
7. Once CI exists, require status checks on `main`. `main` currently
   blocks force-push and deletion only.
8. ~~Generate multi-size `.ico` (16–256) and Linux PNG sizes from the
   1024 px sources in `assets/icons/` with a dev script in
   `tools/dev/`.~~ Done in PR 2 (`tools/dev/make_ws_skim_icons.py`).
9. Cut a test prerelease (for example `v0.1.4-test`) and verify a
   clean install on a clean machine creates the desktop icon, and the
   Start-menu entry on Windows.

## Prerequisite

~~Add a GPLv3 `LICENSE` file and the missing attribution headers before
the first public release.~~ Done in PR #4.

## Icons

The wa1hco WS series icons use a shared look: a rounded square in
WIMS blue (RGB 11, 92, 173) with white letters only. The "WS"
lettering is identical in size and position on every icon. The second
line names the tool, for example SKIM or HUB.

Source artwork lives at `assets/icons/ws-skim.png` and
`assets/icons/ws-hub.png` (1024 px). A dev script in `tools/dev/`
generates the sizes the installers need: a multi-size Windows `.ico`
(16–256 px) and the PNG sizes used by a Linux `.desktop` file.

The installer must create a desktop icon, plus a Start-menu entry on
Windows, using the WS SKIM icon. The test release is a prerelease
(for example `v0.1.4-test`). It is a clean install on a clean
machine — there is no migration path, because nobody runs map144 —
and it must verify that the desktop icon appears.

WS HUB is the proposed name for the WIMS console and is not final.
The HUB icon is included so the series rule can be checked across
more than one tool.
