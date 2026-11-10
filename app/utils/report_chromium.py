"""Run the packaged browser only while rendering a report."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


RUNTIME = Path(__file__).resolve().parents[1] / "report-runtime"


def capture(html, groups, data):
    runtime = Path(os.environ.get("FNMONITOR_REPORT_RUNTIME") or RUNTIME).resolve()
    suffix = ".exe" if os.name == "nt" else ""
    driver = runtime / ("chromedriver" + suffix)
    browser = runtime / ("chrome-headless-shell" + suffix)
    if not driver.is_file() or not browser.is_file():
        raise RuntimeError("日报图需要包含 Chromium 的安装包")
    html = html.replace("</head>", "<script>window.fnmonitorReport = true;</script></head>", 1)
    token = secrets.token_hex(24)
    payload = {path: json.dumps(value).encode() for path, value in data.items()}

    class SnapshotHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
                self.send_error(403)
                return
            is_document = self.path == "/" + token
            if is_document:
                body = html.encode("utf-8")
                mime = "text/html; charset=utf-8"
            elif self.headers.get("Cookie") == "report=" + token and self.path in payload:
                body = payload[self.path]
                mime = "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if is_document:
                self.send_header("Set-Cookie", "report=" + token + "; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; script-src 'unsafe-inline'; "
                                 "style-src 'unsafe-inline'; connect-src 'self'; "
                                 "img-src 'self' data: blob:; font-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

    with tempfile.TemporaryDirectory(prefix="fnmonitor-report-") as directory:
        work = Path(directory)
        options = {}
        args = ["--headless", "--disable-background-networking", "--no-proxy-server",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "--window-size=1440,1000",
                "--force-prefers-reduced-motion", "--disable-dev-shm-usage"]
        if os.name == "posix" and os.geteuid() == 0:
            import pwd
            account = pwd.getpwnam("nobody")
            os.chown(work, account.pw_uid, account.pw_gid)
            options.update(user=account.pw_uid, group=account.pw_gid, extra_groups=[])
            args.append("--no-sandbox")
        elif os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        env = dict(os.environ, HOME=directory, TMPDIR=directory)
        with ThreadingHTTPServer(("127.0.0.1", 0), SnapshotHandler) as server:
            server.origin = "http://127.0.0.1:" + str(server.server_port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                return _render(driver, browser, args, env, options, work,
                               server.origin + "/" + token, groups)
            finally:
                server.shutdown()
                thread.join()


def _render(driver, browser, args, env, options, work, url, groups):
    log = work / "driver.log"
    with log.open("wb") as output:
        try:
            process = subprocess.Popen([str(driver), "--port=0"], stdout=output,
                                       stderr=subprocess.STDOUT, env=env, **options)
        except OSError as exc:
            raise RuntimeError("无法启动安装包内的 Chromium 驱动：" + str(exc)) from exc
        session = None
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process.poll() is None:
                match = re.search(r"started successfully on port (\d+)", log.read_text(errors="replace"))
                if match:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("Chromium 驱动启动失败：" + log.read_text(errors="replace")[-400:])
            origin = "http://127.0.0.1:" + match[1]
            opener = build_opener(ProxyHandler({}))

            def command(path, body=None, method="POST", timeout=20):
                data = None if body is None else json.dumps(body).encode()
                request = Request(origin + path, data=data, method=method,
                                  headers={"Content-Type": "application/json"})
                try:
                    with opener.open(request, timeout=timeout) as response:
                        return json.load(response)["value"]
                except HTTPError as exc:
                    detail = json.load(exc)["value"]["message"].splitlines()[0]
                    raise RuntimeError("日报浏览器：" + detail) from exc

            session = "/session/" + command("/session", {
                "capabilities": {"alwaysMatch": {
                    "browserName": "chrome",
                    "goog:chromeOptions": {"binary": str(browser), "args": args},
                    "timeouts": {"pageLoad": 12000, "script": 14000},
                }},
            })["sessionId"]
            command(session + "/url", {"url": url + "#/overview"})
            script = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")
            bounds = command(session + "/execute/async", {
                "script": script, "args": [groups],
            })
            if "error" in bounds:
                raise RuntimeError(bounds["error"])
            result = command(session + "/goog/cdp/execute", {
                "cmd": "Page.captureScreenshot",
                "params": {"format": "png", "captureBeyondViewport": True,
                           "clip": {**bounds, "scale": 1}},
            })
            return base64.b64decode(result["data"], validate=True)
        except (OSError, ValueError) as exc:
            raise RuntimeError("日报浏览器通信失败：" + str(exc)) from exc
        finally:
            if session:
                try:
                    command(session, method="DELETE", timeout=3)
                except (OSError, RuntimeError, ValueError):
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
