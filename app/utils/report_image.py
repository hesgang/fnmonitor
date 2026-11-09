"""Render a phone-sized overview from the existing report payload."""

import io
import math
import os
import time


def _num(value, unit="", digits=1):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"{value:.{digits}f}{unit}"
    return "—"


def _rate(value):
    if not isinstance(value, (int, float)):
        return "—"
    if value >= 1024 * 1024:
        return _num(value / (1024 * 1024), " MB/s")
    return _num(value / 1024, " KB/s")


def _rows(items, format_row, limit=4):
    rows = [format_row(item) for item in items[:limit]]
    if len(items) > limit:
        rows.append(f"另有 {len(items) - limit} 项")
    return rows


def _card(key, p, detail):
    n = _num
    if key == "system":
        return "fnOS " + str(p.get("fnos_version") or "—"), ["已运行 " + str(p.get("uptime") or "—")], None
    if key == "cpu":
        return n(p.get("percent"), "%"), [f'{p.get("cores", 0)} 核 · 负载 {n(p.get("load_1"), digits=2)}'], p.get("percent")
    if key == "memory":
        return n(p.get("percent"), "%"), [f'{n(p.get("used_gb"))} / {n(p.get("total_gb"), " GB")}'], p.get("percent")
    if key == "disk":
        pct = p.get("total_percent") if p.get("size_tb") else None
        rows = [f'{n(p.get("used_tb"), digits=2)} / {n(p.get("size_tb"), " TB", 2)} · {p.get("count", 0)} 块硬盘'] if pct is not None else [f'{p.get("count", 0)} 块硬盘 · 未读取到存储用量']
        if detail:
            rows += _rows(p.get("disks", []), lambda d: f'{d["name"]} · {d["power"]} · {n(d.get("temp"), " °C", 0)}')
        return n(pct, "%"), rows, pct
    if key == "network":
        items = p.get("interfaces", [])
        rx = sum(x.get("rx_rate", 0) for x in items)
        tx = sum(x.get("tx_rate", 0) for x in items)
        rows = ["↑ " + _rate(tx)] if items else []
        if detail:
            rows += _rows(items, lambda x: f'{x["name"]} · ↓ {_rate(x.get("rx_rate"))}')
        return "↓ " + _rate(rx) if items else "无数据", rows, None
    if key == "power":
        value = n(p.get("watts"), " W") if p.get("ok") else "不可用"
        rows = [str(p.get("source") or "—")]
        for label, period in (("今日", "today"), ("本月", "month")):
            energy = p.get(period + "_kwh")
            if energy is not None:
                rows.append(f'{label} {n(energy, " kWh", 3)} · ¥{n(p.get(period + "_yuan"), digits=2)}')
        return value, rows, None
    if key == "load":
        return n(p.get("1m"), digits=2), [f'5 分钟 {n(p.get("5m"), digits=2)} · 15 分钟 {n(p.get("15m"), digits=2)}'], None
    if key == "temp":
        return n(p.get("cpu"), " °C"), ["系统 " + n(p.get("system"), " °C")], None
    if key == "smart":
        rows = [f'已检查 {p.get("total", 0)} 块硬盘']
        if detail:
            rows += _rows(p.get("bad_list", []), lambda d: f'{d["name"]} · {d["health"]}')
        return f'{p.get("bad", 0)} 块需留意' if p.get("total") else "无数据", rows, None
    if key == "storage":
        return f'{p.get("pools", 0)} 个存储池', [f'{len(p.get("raid", []))} 项阵列记录'], None
    if key in ("docker", "vms"):
        items = p.get("containers" if key == "docker" else "vms", [])
        rows = _rows(items, lambda x: f'{x["name"]} · {x["state"]}') if detail else []
        return f'{p.get("running", 0)} / {p.get("total", 0)} 运行', rows, None
    if key == "gpu":
        rows = _rows(p.get("gpus", []), lambda x: f'{x["name"]} · {n(x.get("util"), "%")}') if detail else []
        return f'{p.get("count", 0)} 块显卡' if p.get("count") else "无数据", rows, None
    if key == "fan":
        rows = _rows(p.get("fans", []), lambda x: f'{x["name"]} · {n(x.get("rpm"), " RPM", 0)}') if detail else []
        return f'{p.get("count", 0)} 个风扇' if p.get("count") else "无数据", rows, None
    if key == "idrac":
        return "已配置" if p.get("configured") else "未配置", [], None
    if key == "weather":
        return n(p.get("temp"), " °C", 0), [f'{p.get("city", "—")} · {p.get("text", "—")}'], None
    return "无数据", [], None


def _font_path():
    options = (
        os.environ.get("FNMONITOR_REPORT_FONT"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msyh.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    )
    return next((path for path in options if path and os.path.isfile(path)), None)


def render_png(payload, modules, labels, now, detail=True):
    """Return PNG bytes. Optional renderer dependencies stay out of normal startup."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("日报图需要 Pillow") from exc
    font_path = _font_path()
    if not font_path:
        raise RuntimeError("未找到中文字体，可通过 FNMONITOR_REPORT_FONT 指定")
    fonts = {size: ImageFont.truetype(font_path, size) for size in (22, 25, 28, 40, 50)}
    cards = [(labels[k], *_card(k, payload[k], detail)) for k in modules if k in labels and k in payload]
    if not cards:
        raise ValueError("请至少选择一个报告模块")

    width, margin, gap, top = 960, 36, 18, 190
    groups = [cards[i:i + 2] for i in range(0, len(cards), 2)]
    heights = [max(170 + 33 * len(rows) + (20 if pct is not None else 0)
                   for _, _, rows, pct in group) for group in groups]
    image = Image.new("RGB", (width, top + sum(heights) + gap * len(groups) + 48), "#f4f6fa")
    draw = ImageDraw.Draw(image)

    def text(x, y, value, size, fill, max_width):
        value = " ".join(str(value).split())[:180]
        font = fonts[size]
        if draw.textlength(value, font=font) > max_width:
            while value and draw.textlength(value + "…", font=font) > max_width:
                value = value[:-1]
            value += "…"
        draw.text((x, y), value, font=font, fill=fill, anchor="lt")

    draw.rounded_rectangle((margin, 32, width - margin, 168), radius=24, fill="#14223b")
    text(64, 54, "飞牛监控 · 每日报告", 50, "#ffffff", 820)
    text(66, 119, time.strftime("%Y-%m-%d  %H:%M  ·  采集快照", time.localtime(now)), 22, "#bfcee8", 820)
    y = top
    for group, height in zip(groups, heights):
        for col, (label, value, rows, pct) in enumerate(group):
            card_width = (width - 2 * margin - gap) // 2 if len(group) == 2 else width - 2 * margin
            x = margin + col * (card_width + gap)
            draw.rounded_rectangle((x, y, x + card_width, y + height), radius=22, fill="#ffffff", outline="#dce3ee", width=2)
            text(x + 24, y + 23, label, 28, "#475674", card_width - 48)
            text(x + 24, y + 70, value, 40, "#14223b", card_width - 48)
            for i, row in enumerate(rows):
                text(x + 24, y + 130 + i * 33, row, 25, "#53617c", card_width - 48)
            if isinstance(pct, (int, float)) and math.isfinite(pct):
                left, bar_y = x + 24, y + height - 30
                span = card_width - 48
                draw.rounded_rectangle((left, bar_y, left + span, bar_y + 8), radius=4, fill="#e5ebf5")
                filled = int(span * max(0, min(100, pct)) / 100)
                if filled:
                    draw.rounded_rectangle((left, bar_y, left + filled, bar_y + 8), radius=4, fill="#3c6cdf")
        y += height + gap
    text(margin, y + 5, f"fnmonitor  ·  {len(cards)} 个已选模块", 22, "#53617c", width - 2 * margin)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
