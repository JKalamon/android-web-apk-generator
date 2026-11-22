"""Local web UI for the APK generator (stdlib only)."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import adb as adbmod
from . import builder, toolchain

STATIC = Path(__file__).resolve().parent / "static"
_tc_lock = threading.Lock()
_tc: toolchain.Toolchain | None = None
_build_lock = threading.Lock()


def tc(refresh: bool = False) -> toolchain.Toolchain:
    global _tc
    with _tc_lock:
        if _tc is None or refresh:
            _tc = toolchain.detect()
        return _tc


def _apk_path(name: str) -> Path:
    p = (builder.OUTPUT_DIR / Path(unquote(name)).name).resolve()
    if p.parent != builder.OUTPUT_DIR.resolve() or p.suffix != ".apk" or not p.exists():
        raise FileNotFoundError(name)
    return p


def _apk_package(apk: Path) -> str:
    out = subprocess.run([tc().tool("aapt2"), "dump", "packagename", str(apk)],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "could not read package name")
    return out.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "apkgen"

    def log_message(self, fmt, *args):  # quieter console
        if args and "/api/" in str(args[0]):
            return
        super().log_message(fmt, *args)

    # -- helpers ---------------------------------------------------------
    def _json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _same_origin(self) -> bool:
        # Block cross-site POSTs from other web pages to this local server.
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == self.headers.get("Host")

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                body = (STATIC / "index.html").read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/env":
                self._json(tc(refresh=True).to_dict())
            elif path == "/api/devices":
                t = tc()
                self._json({"devices": adbmod.devices(t.adb) if t.adb else [], "adb": bool(t.adb)})
            elif path == "/api/apks":
                self._json({"apks": builder.list_outputs()})
            elif path.startswith("/download/"):
                apk = _apk_path(path[len("/download/"):])
                data = apk.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", f'attachment; filename="{apk.name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        if not self._same_origin():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/build":
                cfg = builder.AppConfig.from_json(self._body())
                with _build_lock:
                    try:
                        apk, log = builder.build(cfg, tc())
                    except builder.BuildError as e:
                        self._json({"ok": False, "error": str(e), "log": e.log})
                        return
                self._json({"ok": True, "apk": apk.name, "size": apk.stat().st_size, "log": log})
            elif path == "/api/install":
                d = self._body()
                t = tc()
                if not t.adb:
                    self._json({"error": "adb not found"}, HTTPStatus.BAD_REQUEST)
                    return
                apk = _apk_path(d["apk"])
                package = _apk_package(apk)
                serials = d.get("serials") or []
                with ThreadPoolExecutor(max_workers=max(1, len(serials))) as ex:
                    results = list(ex.map(lambda s: adbmod.install(
                        t.adb, s, str(apk), package, bool(d.get("uninstallFirst")), bool(d.get("launch"))), serials))
                self._json({"results": results, "package": package})
            elif path == "/api/connect":
                t = tc()
                address = str(self._body().get("address", ""))
                if not t.adb:
                    self._json({"ok": False, "output": "adb not found"})
                elif not re.fullmatch(r"[A-Za-z0-9\[][A-Za-z0-9.\-\[\]:]*", address.strip()):
                    self._json({"ok": False, "output": "Enter an IP address or hostname, e.g. 192.168.1.50"})
                else:
                    self._json(adbmod.connect(t.adb, address))
            elif path == "/api/clean":
                builder.clean_outputs()
                self._json({"ok": True})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except FileNotFoundError:
            self._json({"error": "APK not found"}, HTTPStatus.NOT_FOUND)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"APK Generator running at {url}  (Ctrl+C to stop)")
    t = tc()
    for p in t.problems:
        print("  ! " + p)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
