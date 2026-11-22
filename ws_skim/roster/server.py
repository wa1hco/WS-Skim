# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


"""Local stdlib HTTP + SSE server for the roster page."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ws_skim.roster.model import RosterSession

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORTS = (8788, 8791, 8792, 8793, 8794, 8795, 8796, 8797, 8798, 8799)
_CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class RosterServer:
    """Serve the roster on 127.0.0.1 and push live state over ``/events``."""

    def __init__(self, session: RosterSession, *, host: str = "127.0.0.1",
                 port: int | None = None, choose_log=None):
        self.session = session
        self.host = host
        self.choose_log = choose_log  # callable, may be a Qt signal emit
        self._httpd: _QuietHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._clients = 0
        self._clients_lock = threading.Lock()
        self.port = self._bind(port)

    @property
    def client_count(self) -> int:
        """Roster pages connected to ``/events`` right now.

        A roster window left open from before an upgrade or restart reconnects
        on its own, so WS Skim checks this before opening another window.
        """
        with self._clients_lock:
            return self._clients

    def _client_delta(self, d: int) -> None:
        with self._clients_lock:
            self._clients = max(0, self._clients + d)

    def _bind(self, port: int | None) -> int:
        tried = []
        if port == 0:
            tried = [0]
        else:
            if port:
                tried.append(int(port))
            tried.extend(p for p in DEFAULT_PORTS if p not in tried)
        for p in tried:
            try:
                httpd = _QuietHTTPServer((self.host, p), self._handler())
                httpd.roster_server = self
                self._httpd = httpd
                return int(httpd.server_port)
            except OSError:
                continue
        # Last resort: ephemeral port.
        httpd = _QuietHTTPServer((self.host, 0), self._handler())
        httpd.roster_server = self
        self._httpd = httpd
        return int(httpd.server_port)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> str:
        if self._thread is not None and self._thread.is_alive():
            return self.url
        if self._httpd is None:
            raise RuntimeError("roster server has no socket")
        t = threading.Thread(target=self._httpd.serve_forever, name="ws-skim-roster", daemon=True)
        t.start()
        self._thread = t
        return self.url

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is not None:
            # shutdown() waits for serve_forever() to finish, so it would block
            # forever if the server was bound but never started.
            if thread is not None and thread.is_alive():
                try:
                    httpd.shutdown()
                except Exception:
                    pass
            try:
                httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None

    def snapshot(self) -> dict:
        with self._lock:
            return self.session.snapshot()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # noqa: A003
                return

            def _send(self, code: int, body: bytes, content_type: str,
                      extra: list[tuple[str, str]] | None = None) -> None:
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    for k, v in extra or []:
                        self.send_header(k, v)
                    self.end_headers()
                    if body:
                        self.wfile.write(body)
                except _CLIENT_GONE:
                    return

            def _json(self, obj, code: int = 200) -> None:
                body = json.dumps(obj, default=_json_default).encode("utf-8")
                self._send(code, body, "application/json; charset=utf-8")

            def _read_json(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if not raw:
                    return {}
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    return {}
                return data if isinstance(data, dict) else {}

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path or "/"
                if path == "/events":
                    self._events()
                    return
                if path == "/api/state":
                    self._json(server.snapshot())
                    return
                if path in {"/", "/roster", "/index.html"}:
                    path = "/roster.html"
                rel = path.lstrip("/")
                dest = (STATIC / rel).resolve()
                try:
                    dest.relative_to(STATIC.resolve())
                except ValueError:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                if not dest.is_file():
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                data = dest.read_bytes()
                ctype = _MIME.get(dest.suffix.lower(), "application/octet-stream")
                self._send(200, data, ctype)

            def do_POST(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path or "/"
                body = self._read_json()
                if path == "/api/log":
                    action = str(body.get("action") or "").strip().lower()
                    if action == "choose":
                        cb = server.choose_log
                        if cb is None:
                            self._json({"ok": False, "error": "no file dialog"}, 400)
                            return
                        try:
                            cb()
                        except Exception as exc:
                            self._json({"ok": False, "error": str(exc)}, 500)
                            return
                        self._json({"ok": True, "log": server.session.log.public()})
                        return
                    raw = body.get("path") or body.get("log")
                    if not raw:
                        self._json({"ok": False, "error": "path required"}, 400)
                        return
                    ok = server.session.set_log(str(raw), title=body.get("title"))
                    self._json({"ok": ok, "log": server.session.log.public()})
                    return
                if path == "/api/checklist":
                    items = body.get("items") or body
                    try:
                        server.session.set_checklist(items)
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 400)
                        return
                    self._json({"ok": True, "checklist": server.session.rules.public()})
                    return
                self._send(404, b"not found", "text/plain; charset=utf-8")

            def _events(self) -> None:
                server._client_delta(+1)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    while True:
                        payload = json.dumps(server.snapshot(), default=_json_default)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(1.0)
                except _CLIENT_GONE:
                    return
                except Exception:
                    return
                finally:
                    server._client_delta(-1)

        return Handler


class _QuietHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16

    def handle_error(self, request, client_address):
        # Client gone mid-request is normal (refresh, close).
        return


def _json_default(obj):
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"not json serializable: {type(obj)!r}")
