"""Capture the rendered dashboard using the optional browser runtime."""

import json
from pathlib import Path
import subprocess
import threading


_CAPTURE_LOCK = threading.Lock()

# Each entry maps a settings option to an existing card within its overview section.
IMAGE_GROUPS = (
    ("host", "主机", (("host", "主机", None),)),
    ("compute", "处理器 · 内存 · 显卡", (
        ("cpu", "处理器", '[data-sheet="cpu"]'),
        ("memory", "内存", '[data-sheet="mem"]'),
        ("gpu", "显卡", '[data-ref="gpuCard"]'),
    )),
    ("storage", "存储", (
        ("pool", "存储池用量", ".pool"),
        ("disk", "硬盘盘位与健康状态", ".nas"),
        ("raid", "阵列", '[data-ref="raid"]'),
    )),
    ("network", "网络与流量", (
        ("interfaces", "网口与流量统计", '[data-sheet="net"]'),
        ("container_traffic", "容器流量排行", ".grid > .card:nth-child(2)"),
        ("daily_traffic", "每日流量图", ".chart-card"),
    )),
    ("thermal", "温度与散热", (
        ("temp", "温度", '[data-sheet="temp"]'),
        ("fan", "风扇与电压", ".g-thermal > .card:nth-child(2)"),
    )),
    ("energy", "能耗", (
        ("power", "实时功耗", ".energy-live"),
        ("power_cost", "用电与电费", ".stat-cells"),
        ("daily_power", "每日用电图", ".chart-card"),
    )),
    ("weather", "天气", (("weather", "天气", None),)),
)
_TEXT_TO_IMAGE = {
    "system": ("host",), "cpu": ("cpu",), "load": ("cpu",), "memory": ("memory",), "gpu": ("gpu",),
    "disk": ("disk",), "smart": ("disk",), "storage": ("pool", "raid"),
    "network": ("interfaces", "container_traffic", "daily_traffic"),
    "temp": ("temp",), "fan": ("fan",),
    "power": ("power", "power_cost", "daily_power"), "weather": ("weather",),
}


def image_selection(items):
    selected = {item for item in items if isinstance(item, str)}
    return [key for _, _, cards in IMAGE_GROUPS for key, _, _ in cards if key in selected]


def image_modules(cfg):
    saved = cfg.get("report_image_modules")
    if isinstance(saved, list):
        return image_selection(saved)
    return image_selection(
        key for module in cfg["report_modules"] for key in _TEXT_TO_IMAGE.get(module, ())
    )


def capture_png(html, modules, data, sampled_at):
    selected = set(modules)
    groups = [
        {"id": group, "hide": [selector for key, _, selector in cards if key not in selected]}
        for group, _, cards in IMAGE_GROUPS if any(key in selected for key, _, _ in cards)
    ]
    request = json.dumps({"html": html, "groups": groups, "data": data, "sampledAt": sampled_at})
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
