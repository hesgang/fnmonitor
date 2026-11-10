"""Report choices refer to the cards rendered by the overview page."""

import threading

from .report_chromium import capture


_CAPTURE_LOCK = threading.Lock()

# Group IDs follow the overview layout. The hero is always before that layout.
IMAGE_GROUPS = (
    ("hero", "页首", (
        ("overview", "总览", ".ring-wrap, .hero-text"),
        ("clock_weather", "时钟和天气", ".clock-card"),
    )),
    ("metrics", "核心指标", (
        ("metric_cpu", "处理器", '[data-m="cpu"]'),
        ("metric_mem", "内存", '[data-m="mem"]'),
        ("metric_disk", "存储", '[data-m="disk"]'),
        ("metric_net", "网络", '[data-m="net"]'),
        ("metric_temp", "温度", '[data-m="temp"]'),
        ("metric_power", "功耗", '[data-m="pwr"]'),
    )),
    ("storage", "存储", (
        ("pool", "存储池用量", ".pool"),
        ("disk", "硬盘盘位", ".nas"),
        ("raid", "阵列", '[data-ref="raid"]'),
    )),
    ("trends", "趋势", tuple(
        ("trend_" + key, title, f'.chart-card:has([data-ref="ch-{key}"])')
        for key, title in (("cpumem", "CPU 与内存"), ("net", "网络"), ("io", "磁盘读写"),
                           ("temp", "温度"), ("power", "功耗趋势"), ("fan", "风扇转速"))
    )),
    ("compute", "处理器 · 内存 · 显卡", (
        ("cpu", "每核使用率", '[data-sheet="cpu"]'),
        ("memory", "内存构成", '[data-sheet="mem"]'),
        ("gpu", "显卡", '[data-ref="gpuCard"]'),
    )),
    ("thermal", "温度与散热", (
        ("temp", "处理器与主板 / 硬盘", ".g-thermal > .card:first-child"),
        ("fan", "风扇与电压", ".g-thermal > .card:nth-child(2)"),
    )),
    ("energy", "能耗", (
        ("power", "实时功耗", ".energy-live"),
        ("power_cost", "用电与电费", ".stat-cells"),
        ("daily_power", "每日用电", ".chart-card"),
    )),
    ("network", "网络与流量", (
        ("interfaces", "网口与流量统计", '[data-sheet="net"]'),
        ("container_traffic", "容器流量排行", ".grid > .card:nth-child(2)"),
        ("daily_traffic", "每日流量", ".chart-card"),
    )),
    ("procs", "进程", (("processes", "进程", ".mod-body > .card"),)),
    ("monthly", "上月汇总", (
        ("monthly_summary", "上月回顾", ".mod-body .card"),
        ("monthly_stats", "上月指标", ".stat-cells"),
    )),
    ("host", "主机", (
        ("host_system", "系统信息", '[data-ref="a"]'),
        ("host_hardware", "硬件与网口", '[data-ref="b"]'),
        ("host_memory", "内存插槽", '[data-ref="c"]'),
    )),
)

_OLD_KEYS = {"host": "overview", "weather": "clock_weather"}
_TEXT_TO_IMAGE = {
    "system": ("overview",), "cpu": ("metric_cpu",), "load": ("metric_cpu",),
    "memory": ("metric_mem",), "gpu": ("gpu",), "disk": ("disk",), "smart": ("disk",),
    "storage": ("pool", "raid"), "network": ("interfaces", "container_traffic", "daily_traffic"),
    "temp": ("metric_temp",), "fan": ("fan",), "power": ("power", "power_cost", "daily_power"),
    "weather": ("clock_weather",),
}


def image_selection(items):
    selected = {_OLD_KEYS.get(item, item) for item in items if isinstance(item, str)}
    return [key for _, _, cards in IMAGE_GROUPS for key, _, _ in cards if key in selected]


def image_modules(cfg):
    saved = cfg.get("report_image_modules")
    if isinstance(saved, list):
        return image_selection(saved)
    return image_selection(
        key for module in cfg["report_modules"] for key in _TEXT_TO_IMAGE.get(module, ())
    )


def image_groups(modules):
    selected = set(modules)
    return [
        {"id": group, "cards": [selector for _, _, selector in cards],
         "keep": [selector for key, _, selector in cards if key in selected]}
        for group, _, cards in IMAGE_GROUPS if any(key in selected for key, _, _ in cards)
    ]


def capture_png(html, groups, data):
    if not _CAPTURE_LOCK.acquire(blocking=False):
        raise RuntimeError("正在生成日报图，请稍后重试")
    try:
        return capture(html, groups, data)
    finally:
        _CAPTURE_LOCK.release()
