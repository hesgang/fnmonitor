"""Capture the rendered dashboard using the optional browser runtime."""

import json
from pathlib import Path
import subprocess
import threading


_CAPTURE_LOCK = threading.Lock()


def capture_png(html, modules, data, sampled_at):
    request = json.dumps({"html": html, "modules": modules, "data": data, "sampledAt": sampled_at})
    script = Path(__file__).with_suffix(".cjs")
    if not _CAPTURE_LOCK.acquire(blocking=False):
        raise RuntimeError("正在生成日报图，请稍后重试")
    try:
        try:
            result = subprocess.run(
                ["node", str(script)], input=request.encode("utf-8"),
                capture_output=True, timeout=50, check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("日报图需要 Node.js 和 Playwright Chromium") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("首页截图超时，请稍后重试") from exc
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or "首页截图失败")
        if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("浏览器未返回有效的 PNG")
        return result.stdout
    finally:
        _CAPTURE_LOCK.release()
