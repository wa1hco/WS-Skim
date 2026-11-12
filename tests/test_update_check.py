"""Tests for ws_skim/update_check.py — release check, download, installer hand-off."""
import hashlib
import io
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ws_skim import update_check as uc


# ── tag ordering ────────────────────────────────────────────────────────────

def test_tag_order():
    tags = ["v0.1.3-alpha", "v0.1.4-test.1", "v0.1.4-test.2", "v0.1.4-test.10",
            "v0.1.4", "v0.1.5-rc.1", "v0.1.5", "v0.2.0"]
    assert sorted(tags, key=uc.tag_key) == tags


def test_tag_key_rejects_non_versions():
    for bad in ["", "latest", "v1.2", "release-1", "v1.2.3-"]:
        assert uc.tag_key(bad) is None


def test_prerelease_tag():
    assert uc.is_prerelease_tag("v0.1.4-test.2")
    assert not uc.is_prerelease_tag("v0.1.4")


def test_installed_tag(tmp_path):
    assert uc.installed_tag(tmp_path) == ""
    (tmp_path / "RELEASE").write_text("v0.1.4-test.2\n")
    assert uc.installed_tag(tmp_path) == "v0.1.4-test.2"


# ── releases JSON ───────────────────────────────────────────────────────────

def _asset(name, size=10):
    return {"name": name, "size": size,
            "browser_download_url": f"https://example.invalid/{name}"}


def _rel(tag, pre=None, draft=False, assets=(uc.LINUX_ASSET, uc.WINDOWS_ASSET, uc.SUMS_ASSET)):
    return {"tag_name": tag, "name": tag, "draft": draft,
            "prerelease": ("-" in tag) if pre is None else pre,
            "html_url": f"https://github.com/x/releases/tag/{tag}", "body": "notes",
            "assets": [_asset(a) for a in assets]}


def test_parse_skips_drafts_and_releases_without_installers():
    data = [_rel("v0.1.5", draft=True),
            _rel("v0.1.4-test.3", assets=(uc.WINDOWS_ASSET, uc.SUMS_ASSET)),
            _rel("v0.1.4-test.2"),
            _rel("v0.1.3-alpha", assets=("map144-0.1.3-alpha.zip", "SHA256SUMS")),
            {"tag_name": "nightly", "assets": []}]
    got = uc.parse_releases(data, uc.LINUX_ASSET)
    assert [r.tag for r in got] == ["v0.1.4-test.2"]
    assert got[0].installer_url.endswith(uc.LINUX_ASSET)
    assert got[0].prerelease


def test_parse_requires_both_installers():
    for missing in (uc.LINUX_ASSET, uc.WINDOWS_ASSET, uc.SUMS_ASSET):
        assets = tuple(a for a in (uc.LINUX_ASSET, uc.WINDOWS_ASSET, uc.SUMS_ASSET) if a != missing)
        for platform_asset in (uc.LINUX_ASSET, uc.WINDOWS_ASSET):
            assert uc.parse_releases([_rel("v0.1.4-test.3", assets=assets)], platform_asset) == []


def test_pick_update_prerelease_rules():
    rels = uc.parse_releases([_rel("v0.1.4-test.3"), _rel("v0.1.4-test.2"),
                              _rel("v0.1.3")], uc.LINUX_ASSET)
    # A tester on a prerelease gets newer prereleases.
    assert uc.pick_update("v0.1.4-test.2", rels).tag == "v0.1.4-test.3"
    # Already newest: nothing.
    assert uc.pick_update("v0.1.4-test.3", rels) is None
    # A full-release user is not offered prereleases.
    assert uc.pick_update("v0.1.3", rels) is None
    assert uc.pick_update("v0.1.3", rels, include_prereleases=True).tag == "v0.1.4-test.3"
    # Unknown current version: never offer.
    assert uc.pick_update("", rels) is None


def test_pick_update_full_release_beats_its_prereleases():
    rels = uc.parse_releases([_rel("v0.1.4"), _rel("v0.1.4-test.9")], uc.LINUX_ASSET)
    assert uc.pick_update("v0.1.4-test.2", rels).tag == "v0.1.4"


def test_platform_asset():
    assert uc.platform_asset("win32") == uc.WINDOWS_ASSET
    assert uc.platform_asset("linux") == uc.LINUX_ASSET
    assert uc.platform_asset("darwin") is None


def test_check_for_update_without_release_file(tmp_path, monkeypatch):
    monkeypatch.setattr(uc, "fetch_releases",
                        lambda *a, **k: pytest.fail("must not query GitHub"))
    assert uc.check_for_update(tmp_path) is None


# ── download ────────────────────────────────────────────────────────────────

class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _fake_release():
    return uc.parse_releases([_rel("v0.1.4-test.3")], uc.LINUX_ASSET)[0]


def _serve(monkeypatch, payload, sums):
    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith(uc.SUMS_ASSET):
            return _Resp(sums.encode())
        return _Resp(payload)
    monkeypatch.setattr(uc, "urlopen", fake_urlopen)


def test_expected_sha256():
    text = "abc123  ws-skim-linux-install.sh\nDEF456 *WS-Skim-Setup.exe\n"
    assert uc.expected_sha256(text, uc.LINUX_ASSET) == "abc123"
    assert uc.expected_sha256(text, uc.WINDOWS_ASSET) == "def456"
    assert uc.expected_sha256(text, "other") is None


def test_download_verifies_checksum(tmp_path, monkeypatch):
    payload = b"#!/bin/bash\necho installer\n" * 1000
    good = hashlib.sha256(payload).hexdigest()
    _serve(monkeypatch, payload, f"{good}  {uc.LINUX_ASSET}\n")
    seen = []
    path = uc.download(_fake_release(), tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert path.read_bytes() == payload
    assert seen[-1] == (len(payload), len(payload))


def test_download_rejects_bad_checksum(tmp_path, monkeypatch):
    _serve(monkeypatch, b"tampered", f"{'0' * 64}  {uc.LINUX_ASSET}\n")
    with pytest.raises(ValueError, match="checksum"):
        uc.download(_fake_release(), tmp_path)
    assert not (tmp_path / uc.LINUX_ASSET).exists()


def test_download_cancel_removes_partial_file(tmp_path, monkeypatch):
    payload = b"x" * (1024 * 1024)
    _serve(monkeypatch, payload, f"{hashlib.sha256(payload).hexdigest()}  {uc.LINUX_ASSET}\n")
    calls = []

    def progress(d, t):
        calls.append(d)

    with pytest.raises(uc.Cancelled):
        uc.download(_fake_release(), tmp_path, progress=progress,
                    cancelled=lambda: len(calls) >= 1)
    assert len(calls) == 1                   # stopped after the first chunk
    assert not (tmp_path / uc.LINUX_ASSET).exists()


def test_download_rejects_missing_checksum(tmp_path, monkeypatch):
    _serve(monkeypatch, b"x", f"{'0' * 64}  something-else\n")
    with pytest.raises(ValueError, match="no entry"):
        uc.download(_fake_release(), tmp_path)


# ── installer hand-off ──────────────────────────────────────────────────────

def test_windows_script_is_ascii_and_checks_the_installer():
    s = uc._WINDOWS_SCRIPT
    s.encode("ascii")                         # cmd.exe reads .cmd in the OEM code page
    assert "PID eq %WSS_PID%" in s
    assert '"%WSS_INSTALLER%" /SILENT' in s
    assert s.index("/SILENT") < s.index("if errorlevel 1 goto failed") < s.index("%WSS_MAIN%")


def test_helper_env_carries_paths(tmp_path):
    root = tmp_path / "Jos\u00e9" / "WS-Skim"
    env = uc._helper_env(tmp_path / "s.exe", root, 7, tmp_path / "u.log", "win32")
    assert env["WSS_PID"] == "7"
    assert env["WSS_MAIN"] == str(root / "ws_skim.py")
    assert env["WSS_PYTHONW"].endswith("pythonw.exe")


def _linux_fixture(tmp_path, installer_exit=0):
    root = tmp_path / "app d\u00e9j\u00e0"
    (root / "scripts").mkdir(parents=True)
    marker = tmp_path / "events"
    run = root / "scripts" / "run.sh"
    run.write_text(f"#!/bin/bash\necho started >> '{marker}'\n")
    run.chmod(0o755)
    inst_dir = tmp_path / "dl"
    inst_dir.mkdir()
    installer = inst_dir / uc.LINUX_ASSET
    installer.write_text(f"#!/bin/bash\necho \"installed $*\" >> '{marker}'\nexit {installer_exit}\n")
    return root, marker, installer


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="bash hand-off")
def test_linux_hand_off_runs_installer_then_restarts(tmp_path):
    root, marker, installer = _linux_fixture(tmp_path)
    sleeper = subprocess.Popen(["sleep", "1"])      # stands in for the running app
    uc.start_install(installer, root=root, pid=sleeper.pid, platform="linux")
    time.sleep(0.3)
    assert not marker.exists()                       # still waiting for the app to exit
    sleeper.wait()
    for _ in range(50):
        if marker.exists() and "started" in marker.read_text():
            break
        time.sleep(0.1)
    lines = marker.read_text().splitlines()
    assert lines == [f"installed --dest {root}", "started"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="bash hand-off")
def test_linux_hand_off_does_not_restart_after_failed_install(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")       # no zenity/kdialog popups in CI
    root, marker, installer = _linux_fixture(tmp_path, installer_exit=3)
    log = tmp_path / "update.log"
    script = uc.start_install(installer, root=root, pid=999999, log=log, platform="linux")
    for _ in range(50):
        if log.exists() and "did not finish" in log.read_text():
            break
        time.sleep(0.1)
    time.sleep(0.3)
    assert marker.read_text().splitlines() == [f"installed --dest {root}"]
    assert "did not finish" in log.read_text()
    script.read_text(encoding="ascii")


def test_stage_writes_release_file(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "dev"))
    try:
        import stage_release
    finally:
        sys.path.pop(0)
    out = tmp_path / "stage"
    stage_release.stage(out, tag="v0.1.4-test.9")
    assert (out / "RELEASE").read_text().strip() == "v0.1.4-test.9"
    stage_release.stage(out)
    assert not (out / "RELEASE").exists()


def test_help_menu_gets_check_for_updates(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    from ws_skim import update_ui
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(uc, "installed_tag", lambda root=uc.ROOT: "")
    win = QtWidgets.QMainWindow()
    menu = win.menuBar().addMenu("&Help")
    update_ui.setup_update_check(win, menu)
    assert [a.text() for a in menu.actions()] == ["Check for Updates…"]
    win.deleteLater()
    app.processEvents()


def _wait_for(app, cond, timeout=5.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


@pytest.mark.parametrize("fail", [False, True])
def test_download_completion_reaches_hand_off_or_error(monkeypatch, tmp_path, fail):
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    from ws_skim import update_ui
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    installer = tmp_path / uc.LINUX_ASSET

    def fake_download(release, progress=None, cancelled=None, **kw):
        progress(5, 10)
        if fail:
            raise ValueError("checksum mismatch")
        return installer

    seen = []
    monkeypatch.setattr(uc, "download", fake_download)
    monkeypatch.setattr(update_ui, "_hand_off", lambda win, rel, path: seen.append(("ok", path)))
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: seen.append(("error", a[2]))))
    win = QtWidgets.QMainWindow()
    update_ui._download_and_install(win, _fake_release())
    assert _wait_for(app, lambda: bool(seen))
    if fail:
        assert seen[0][0] == "error" and "checksum mismatch" in seen[0][1]
    else:
        assert seen == [("ok", installer)]
    win.deleteLater()
    app.processEvents()
