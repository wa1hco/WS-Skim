# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
""""WSJT-X for WS Skim": a separate WSJT-X that works stations on WS Skim's slice.

The Flex window's **Set up WSJT-X** button calls :func:`set_up`.  It writes a
WSJT-X settings file of its own (rig name ``Skim``, so the file is
``WSJT-X - Skim.ini`` and nothing of the operator's other WSJT-X copies is
touched) and a menu / desktop launcher "WSJT-X for WS Skim".

How that WSJT-X reaches the radio (Linux, measured on a FLEX-8600M):

* Rig control: the nCAT helper (kc2g-flex-tools) running for WS Skim's slice
  letter, through WSJT-X's "Hamlib NET rigctl" on ``localhost:<port>``.  nCAT
  finds its slice by letter once, when it starts.
* Audio: the nDAX helper for that slice letter (its PulseAudio/PipeWire
  source, e.g. ``flex.sliceB.rx``) and the TX sink an nDAX provides
  (e.g. ``flex.tx``).  WS Skim also puts nDAX's DAX channel on its slice.
* UDP: the same multicast group as the operator's WSJT-X (normally
  224.0.0.73:2237), so WS Hub, GridTracker and WS Skim all hear it.  WS Skim
  joins that group to read its decodes and status and sends Reply /
  Configure to the port it sends from.

Settings are only written while that WSJT-X is not running (WSJT-X rewrites
its file on exit).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

RIG_NAME = "Skim"
APP_ID = f"WSJT-X - {RIG_NAME}"          # WSJT-X's UDP id for --rig-name Skim
LAUNCHER_NAME = "WSJT-X for WS Skim"
LAUNCHER_FILE = "ws-skim-wsjtx.desktop"
RECORD_FILE = "skim_wsjtx.json"

DEFAULT_UDP_GROUP = "224.0.0.73"
DEFAULT_UDP_PORT = 2237

# Literal QSettings values, as WSJT-X writes them.
PTT_CAT = r"@Variant(\0\0\0\x7f\0\0\0\x1eTransceiverFactory::PTTMethod\0\0\0\0\xfPTT_method_CAT\0)"
DATA_MODE_DATA = r"@Variant(\0\0\0\x7f\0\0\0\x18\x43onfiguration::DataMode\0\0\0\0\xf\x64\x61ta_mode_data\0)"


class SetupError(RuntimeError):
    """Set up WSJT-X could not finish; the message says what to do."""


# ── where WSJT-X keeps things ───────────────────────────────────────────────

def wsjtx_config_dir(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / ".config"
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    return Path(xdg) if xdg else Path.home() / ".config"


def wsjtx_data_dir(rig: str | None = None, home: Path | None = None) -> Path:
    home = Path(home or Path.home())
    name = "WSJT-X" + (f" - {rig}" if rig else "")
    return home / ".local" / "share" / name


def ini_path(rig: str = RIG_NAME, home: Path | None = None) -> Path:
    return wsjtx_config_dir(home) / f"WSJT-X - {rig}.ini"


# ── processes (Linux /proc) ─────────────────────────────────────────────────

def _proc_cmdlines(proc: Path = Path("/proc")):
    try:
        entries = list(proc.iterdir())
    except OSError:
        return
    for d in entries:
        if not d.name.isdigit():
            continue
        try:
            raw = (d / "cmdline").read_bytes()
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        if args:
            yield int(d.name), args


def _flags(args: list[str]) -> dict[str, str]:
    """Go-style flags: ``-name value``, ``-name=value``, ``--name value``."""
    out: dict[str, str] = {}
    i = 1
    while i < len(args):
        a = args[i]
        if a.startswith("-") and len(a) > 1:
            key = a.lstrip("-")
            if "=" in key:
                k, _, v = key.partition("=")
                out[k] = v
            elif i + 1 < len(args) and not args[i + 1].startswith("-"):
                out[key] = args[i + 1]
                i += 1
            else:
                out[key] = "true"
        i += 1
    return out


@dataclass
class Helpers:
    """What runs for one slice letter: nCAT port and nDAX audio names."""
    letter: str
    cat_port: int | None = None
    dax_channel: int | None = None
    rx_source: str | None = None
    tx_sink: str | None = None
    found: list[str] = field(default_factory=list)


def find_helpers(letter: str, cmdlines=None) -> Helpers:
    """Find the nCAT / nDAX helpers serving slice ``letter``."""
    letter = (letter or "").upper()
    h = Helpers(letter=letter)
    tx_sinks: list[tuple[bool, str]] = []
    for _pid, args in (cmdlines if cmdlines is not None else _proc_cmdlines()):
        exe = Path(args[0]).name.lower()
        f = _flags(args)
        sl = (f.get("slice") or "A").upper()
        # nCAT (kc2g-flex-tools), not nmap's "ncat": it takes -station/-slice.
        is_ncat = exe == "ncat" and (Path(args[0]).name == "nCAT" or "station" in f or "slice" in f)
        if is_ncat:
            if sl == letter and h.cat_port is None:
                m = re.search(r"(\d+)$", f.get("listen", ":4532"))
                h.cat_port = int(m.group(1)) if m else 4532
                h.found.append(f"nCAT slice {sl} on port {h.cat_port}")
        elif exe == "ndax":
            tx_on = str(f.get("tx", "true")).lower() not in ("false", "0")
            if tx_on:
                tx_sinks.append((sl == letter, f.get("sink", "flexdax.tx")))
            if sl == letter and h.rx_source is None:
                h.rx_source = f.get("source", "flexdax.rx")
                try:
                    h.dax_channel = int(f.get("daxch", "1"))
                except ValueError:
                    h.dax_channel = None
                h.found.append(f"nDAX slice {sl} audio {h.rx_source} (DAX channel {h.dax_channel})")
    if tx_sinks:
        tx_sinks.sort(key=lambda t: not t[0])     # our letter's own sink first
        h.tx_sink = tx_sinks[0][1]
    return h


def wsjtx_running(rig: str = RIG_NAME, cmdlines=None) -> bool:
    """True when a WSJT-X started with this rig name is running."""
    for _pid, args in (cmdlines if cmdlines is not None else _proc_cmdlines()):
        if Path(args[0]).name.lower() not in ("wsjtx", "wsjtx.exe"):
            continue
        f = _flags(args)
        if f.get("rig-name") == rig or f.get("r") == rig:
            return True
    return False


def find_wsjtx_program() -> str | None:
    """The wsjtx program the operator normally runs."""
    cands = [Path.home() / ".local" / "bin" / "wsjtx"]
    w = shutil.which("wsjtx")
    if w:
        cands.append(Path(w))
    cands += [Path("/usr/local/bin/wsjtx"), Path("/usr/bin/wsjtx")]
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


# ── QSettings .ini editing (keeps every other line exactly as it was) ───────

def ini_set(text: str, section: str, values: dict[str, str]) -> str:
    """Set ``key=value`` lines inside ``[section]``; add the section if missing."""
    lines = text.splitlines()
    out: list[str] = []
    cur = None
    done: set[str] = set()
    seen_section = False

    def _flush_missing():
        for k, v in values.items():
            if k not in done:
                out.append(f"{k}={v}")
                done.add(k)

    for line in lines:
        m = re.match(r"^\[(.+)\]\s*$", line)
        if m:
            if cur == section:
                _flush_missing()
            cur = m.group(1)
            if cur == section:
                seen_section = True
            out.append(line)
            continue
        if cur == section and "=" in line:
            k = line.split("=", 1)[0]
            if k in values:
                if k not in done:
                    out.append(f"{k}={values[k]}")
                    done.add(k)
                continue
        out.append(line)
    if cur == section:
        _flush_missing()
    if not seen_section:
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section}]")
        _flush_missing()
    return "\n".join(out) + "\n"


def ini_get(text: str, section: str, key: str) -> str | None:
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\[(.+)\]\s*$", line)
        if m:
            cur = m.group(1)
            continue
        if cur == section and line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


def find_template(cfg_dir: Path | None = None) -> Path | None:
    """The operator's Flex WSJT-X settings to start from (read only).

    Prefers ``WSJT-X - flex.ini``, then any WSJT-X file using "Hamlib NET
    rigctl" (nCAT), then the default ``WSJT-X.ini``.
    """
    d = Path(cfg_dir or wsjtx_config_dir())
    pref = d / "WSJT-X - flex.ini"
    if pref.is_file():
        return pref
    others = sorted(p for p in d.glob("WSJT-X*.ini") if p.name != f"WSJT-X - {RIG_NAME}.ini")
    for p in others:
        try:
            if "Rig=Hamlib NET rigctl" in p.read_text(encoding="utf-8", errors="replace"):
                return p
        except OSError:
            continue
    plain = d / "WSJT-X.ini"
    return plain if plain.is_file() else None


@dataclass
class SetupPlan:
    letter: str
    cat_port: int
    dax_channel: int | None
    rx_source: str
    tx_sink: str
    dial_hz: int
    mode: str
    udp_group: str
    udp_port: int
    udp_interface: str
    udp_ttl: str
    my_call: str = ""
    my_grid: str = ""
    template: str | None = None


def udp_from_template(text: str) -> tuple[str, int, str, str]:
    """(group, port, interface list, TTL): the template's multicast settings,
    else 224.0.0.73:2237 on the loopback interface."""
    server = (ini_get(text, "Configuration", "UDPServer") or "").strip()
    try:
        port = int(ini_get(text, "Configuration", "UDPServerPort") or DEFAULT_UDP_PORT)
    except ValueError:
        port = DEFAULT_UDP_PORT
    iface = (ini_get(text, "Configuration", "UDPInterface") or "").strip()
    ttl = (ini_get(text, "Configuration", "UDPTTL") or "1").strip()
    first = server.split(".")[0] if server else ""
    if first.isdigit() and 224 <= int(first) <= 239:
        if not iface or iface.startswith("@Invalid"):
            iface = "lo"
        return server, port, iface, ttl
    return DEFAULT_UDP_GROUP, DEFAULT_UDP_PORT, "lo", "1"


def build_ini(template_text: str, plan: SetupPlan, data_dir: Path) -> str:
    text = template_text or "[General]\n"
    conf = {
        "Rig": "Hamlib NET rigctl",
        "CATNetworkPort": f"localhost:{plan.cat_port}",
        "PTTMethod": PTT_CAT,
        "DataMode": DATA_MODE_DATA,
        "Polling": "1",
        "SoundInName": plan.rx_source,
        "SoundOutName": plan.tx_sink,
        "AudioInputChannel": "Mono",
        "AudioOutputChannel": "Mono",
        "UDPServer": plan.udp_group,
        "UDPServerPort": str(plan.udp_port),
        "UDPInterface": plan.udp_interface,
        "UDPTTL": plan.udp_ttl,
        "AcceptUDPRequests": "true",
        "AzElDir": str(data_dir),
    }
    if plan.my_call and not ini_get(text, "Configuration", "MyCall"):
        conf["MyCall"] = plan.my_call
    if plan.my_grid and not ini_get(text, "Configuration", "MyGrid"):
        conf["MyGrid"] = plan.my_grid
    text = ini_set(text, "Configuration", conf)
    text = ini_set(text, "Common", {"Mode": plan.mode, "DialFreq": str(int(plan.dial_hz))})
    return text


# ── the record WS Skim keeps (click-to-work reads it) ───────────────────────

def record_path() -> Path:
    from .user_paths import config_dir
    return config_dir() / RECORD_FILE


def load_record(path: Path | None = None) -> dict | None:
    p = Path(path or record_path())
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def save_record(plan: SetupPlan, path: Path | None = None) -> None:
    p = Path(path or record_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(plan), indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ── launcher ────────────────────────────────────────────────────────────────

def launcher_text(program: str, icon: str | None = None) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={LAUNCHER_NAME}",
        "Comment=WSJT-X on WS Skim's slice, for click-to-work from the WS Skim roster",
        f'Exec="{program}" --rig-name {RIG_NAME}',
        "Terminal=false",
        "Categories=Network;HamRadio;",
    ]
    if icon:
        lines.insert(5, f"Icon={icon}")
    return "\n".join(lines) + "\n"


def _wsjtx_icon() -> str | None:
    for p in ("/usr/local/share/pixmaps/wsjtx_icon.png", "/usr/share/pixmaps/wsjtx_icon.png"):
        if Path(p).is_file():
            return p
    return None


def write_launchers(program: str, home: Path | None = None) -> list[Path]:
    home = Path(home or Path.home())
    text = launcher_text(program, _wsjtx_icon())
    written = []
    for d in (home / ".local" / "share" / "applications", home / "Desktop"):
        if d.name == "Desktop" and not d.is_dir():
            continue
        d.mkdir(parents=True, exist_ok=True)
        p = d / LAUNCHER_FILE
        p.write_text(text, encoding="utf-8")
        p.chmod(0o755)
        written.append(p)
        if d.name == "Desktop" and shutil.which("gio"):
            try:
                import subprocess
                subprocess.run(["gio", "set", str(p), "metadata::trusted", "true"],
                               check=False, capture_output=True, timeout=5)
            except Exception:
                pass
    return written


def link_log(template: Path | None, data_dir: Path) -> str | None:
    """Share the operator's WSJT-X log: if the template's data folder has a
    symlinked ``wsjtx_log.adi``, make the same link (never replaces a file)."""
    if template is None:
        return None
    rig = template.stem.replace("WSJT-X", "").lstrip(" -") or None
    src = wsjtx_data_dir(rig, template.parents[1]) / "wsjtx_log.adi"
    dest = data_dir / "wsjtx_log.adi"
    if dest.exists() or dest.is_symlink():
        return None
    if src.is_symlink():
        target = os.readlink(src)
        data_dir.mkdir(parents=True, exist_ok=True)
        os.symlink(target, dest)
        return str(target)
    return None


# ── the button ──────────────────────────────────────────────────────────────

def plan_setup(letter: str | None, dial_hz: int, mode: str = "FT8", *,
               cmdlines=None, template: Path | None = None,
               my_call: str = "", my_grid: str = "") -> SetupPlan:
    """Work out the settings, or raise SetupError with what to do."""
    if not sys.platform.startswith("linux"):
        raise SetupError("Set up WSJT-X works on Linux with nCAT and nDAX for now.")
    if not letter:
        raise SetupError("WS Skim has no slice of its own yet.  Tick \"WS Skim makes its "
                         "own panadapter\" and wait until it shows on the radio.")
    h = find_helpers(letter, cmdlines)
    missing = []
    if h.cat_port is None:
        missing.append(f"nCAT for slice {letter}")
    if not h.rx_source:
        missing.append(f"nDAX for slice {letter}")
    if not h.tx_sink:
        missing.append("an nDAX with transmit audio")
    if missing:
        raise SetupError(
            f"WS Skim's slice is {letter}, but these Flex helpers are not running: "
            + ", ".join(missing) + ".  Start them for slice " + letter + " (the same way "
            "you start nCAT and nDAX for your other slices), then press Set up WSJT-X again.")
    tpl = template if template is not None else find_template()
    ttext = ""
    if tpl is not None:
        try:
            ttext = tpl.read_text(encoding="utf-8", errors="replace")
        except OSError:
            ttext = ""
    group, port, iface, ttl = udp_from_template(ttext)
    return SetupPlan(letter=letter, cat_port=int(h.cat_port), dax_channel=h.dax_channel,
                     rx_source=h.rx_source, tx_sink=h.tx_sink, dial_hz=int(dial_hz),
                     mode=mode or "FT8", udp_group=group, udp_port=port,
                     udp_interface=iface, udp_ttl=ttl, my_call=my_call, my_grid=my_grid,
                     template=str(tpl) if tpl else None)


def set_up(plan: SetupPlan, *, home: Path | None = None, cmdlines=None,
           program: str | None = None, record: Path | None = None) -> dict:
    """Write the settings file, launcher and record.  Returns what was done."""
    if wsjtx_running(RIG_NAME, cmdlines):
        raise SetupError(f"{LAUNCHER_NAME} is running.  Close it first (it saves its own "
                         "settings when it closes), then press Set up WSJT-X again.")
    prog = program or find_wsjtx_program()
    if not prog:
        raise SetupError("WSJT-X is not installed (WS Skim could not find the wsjtx program).")
    ini = ini_path(RIG_NAME, home)
    data_dir = wsjtx_data_dir(RIG_NAME, home)
    tpl = Path(plan.template) if plan.template else None
    ttext = ""
    if tpl is not None and tpl.resolve() != ini.resolve():
        try:
            ttext = tpl.read_text(encoding="utf-8", errors="replace")
        except OSError:
            ttext = ""
    if ini.is_file():
        # Keep the operator's later changes to this copy (colours, macros...).
        ttext = ini.read_text(encoding="utf-8", errors="replace")
    ini.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = ini.with_suffix(".ini.tmp")
    tmp.write_text(build_ini(ttext, plan, data_dir), encoding="utf-8")
    os.replace(tmp, ini)
    launchers = write_launchers(prog, home)
    log_target = link_log(tpl, data_dir)
    save_record(plan, record)
    return {"ini": str(ini), "launchers": [str(p) for p in launchers],
            "log": log_target, "program": prog}


def summary(plan: SetupPlan, done: dict) -> str:
    """Plain words for the message box."""
    lines = [
        f"{LAUNCHER_NAME} is set up for WS Skim's slice {plan.letter}.",
        "",
        f"Rig control: nCAT for slice {plan.letter} (port {plan.cat_port}).",
        f"Audio in: {plan.rx_source}" + (f" (DAX channel {plan.dax_channel})" if plan.dax_channel else "")
        + f".  Audio out: {plan.tx_sink}.",
        f"Network: {plan.udp_group} port {plan.udp_port}, the same as your other WSJT-X copies.",
    ]
    if done.get("log"):
        lines.append(f"Log: shared with your other WSJT-X ({done['log']}).")
    lines += [
        "",
        f"Start it from the \"{LAUNCHER_NAME}\" launcher in your menu"
        + (" or on the desktop." if any("Desktop" in p for p in done.get("launchers", [])) else "."),
        "Then double-click a station in the WS Skim roster to work it.",
    ]
    return "\n".join(lines)
