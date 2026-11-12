# Plan: Installer and release

Status: documentation only. This PR does not change installers, CI,
or `main`. It records the installer and release plan for
`wa1hco/ws-skimmer`.

Related: [docs/plan/ft8-ft4-wims-udp.md](ft8-ft4-wims-udp.md).

## Goal

A visitor to [github.com/wa1hco/ws-skimmer](https://github.com/wa1hco/ws-skimmer)
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
  (Windows). The double-click desktop installer is still planned.
- Developer scripts from the repo root now live under `tools/dev/`.
- `README-Windows.md`, `README-Linux.md`, and `README_GUI.md` document
  `MAP144_*` environment variables and CLI flags.
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
- First ws-skimmer release, with FT8/FT4, is proposed as `v0.2.0`.

## Tasks (in order)

1. ~~Move launchers to `scripts/` and developer scripts to `tools/dev/`.~~ Done in PR 2 (this layout change). The desktop installer is still later.
2. Add GUI settings and desktop / Start-menu launchers.
3. Rename remaining map144 references.
4. Move the old READMEs (`README-Windows.md`, `README-Linux.md`,
   `README_GUI.md`) into `docs/` and remove environment-variable, CLI
   flag, and `python -m` instructions from user-facing docs.
5. Add the release workflow (tag builds installers and attaches them
   to a GitHub release).
6. Add CI that tests the installers on clean Windows and Linux
   runners.
7. Once CI exists, require status checks on `main`. `main` currently
   blocks force-push and deletion only.
8. ~~Generate multi-size `.ico` (16–256) and Linux PNG sizes from the
   1024 px sources in `assets/icons/` with a dev script in
   `tools/dev/`.~~ Done in PR 2 (`tools/dev/make_ws_skim_icons.py`).
9. Cut a test prerelease (for example `v0.1.4-test`) and verify a
   clean install on a clean machine creates the desktop icon, and the
   Start-menu entry on Windows.

## Prerequisite

Add a GPLv3 `LICENSE` file and the missing attribution headers before
the first public release. The repo has no `LICENSE` file, even though
the README claims GPLv3.

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
