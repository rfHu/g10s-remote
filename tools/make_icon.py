"""生成程序图标 app.ico（一个麦克风）。

纯标准库：手写 PNG（zlib）再封装成 ICO。同时输出 app-preview.png 便于肉眼检查。
用法：python tools/make_icon.py
"""

import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ICO = os.path.join(ROOT, "app.ico")
OUT_WHEEL_ICO = os.path.join(ROOT, "app-wheel.ico")
OUT_DIALOG_ICO = os.path.join(ROOT, "app-dialog.ico")
OUT_WATCH_ICO = os.path.join(ROOT, "app-watch.ico")
OUT_PNG = os.path.join(ROOT, "app-preview.png")
SIZES = [16, 32, 48, 64, 256]
SS = 4                      # 超采样倍数

BG = (0x2F, 0x6F, 0xEB)         # 蓝底 —— 程序图标（麦克风）
BG_WHEEL = (0xE0, 0x7A, 0x1E)   # 橙底 —— 旧滚轮图标
BG_DIALOG = (0x24, 0xA1, 0x5A)  # 绿底 —— 对话模式
BG_WATCH = (0x25, 0x63, 0xC7)   # 蓝底 —— 观看模式（比程序图标略深，便于区分）
FG = (0xFF, 0xFF, 0xFF)     # 白色前景


def in_round_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def dist_to_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def mic_alpha(x, y):
    """归一化坐标 (0..1)，返回该点是否属于麦克风图形。"""
    # 话筒头：竖直胶囊
    if dist_to_seg(x, y, 0.50, 0.26, 0.50, 0.50) <= 0.115:
        return True
    # 话筒环：下半圆环
    r = math.hypot(x - 0.50, y - 0.50)
    if y > 0.50 and 0.235 <= r <= 0.295:
        return True
    # 立杆
    if 0.775 <= y <= 0.855 and abs(x - 0.50) <= 0.030:
        return True
    # 底座
    if 0.855 <= y <= 0.900 and abs(x - 0.50) <= 0.135:
        return True
    return False


def arrows_alpha(x, y):
    """上下双箭头，用于「滚轮模式」图标。"""
    # 上箭头杆 + 头
    if 0.28 <= y <= 0.62 and abs(x - 0.50) <= 0.032:
        return True
    if y < 0.30 and abs(x - 0.50) <= 0.155 and abs(y - 0.30) + abs(x - 0.50) <= 0.155:
        return True
    # 下箭头杆 + 头
    if 0.38 <= y <= 0.72 and abs(x - 0.50) <= 0.032:
        return True
    if y > 0.70 and abs(x - 0.50) <= 0.155 and abs(y - 0.70) + abs(x - 0.50) <= 0.155:
        return True
    return False


def four_way_alpha(x, y):
    """十字四向箭头，用于「编辑模式」图标。

    杆要比箭头底边略窄一点点，缺口才不会变成明显的方块孔。
    """
    BAR = 0.076
    if abs(x - 0.50) <= BAR and 0.30 <= y <= 0.70:
        return True
    if abs(y - 0.50) <= BAR and 0.30 <= x <= 0.70:
        return True
    if 0.16 <= y <= 0.34 and abs(x - 0.50) <= 0.106 * (y - 0.16) / 0.18:
        return True
    if 0.66 <= y <= 0.84 and abs(x - 0.50) <= 0.106 * (0.84 - y) / 0.18:
        return True
    if 0.16 <= x <= 0.34 and abs(y - 0.50) <= 0.106 * (x - 0.16) / 0.18:
        return True
    if 0.66 <= x <= 0.84 and abs(y - 0.50) <= 0.106 * (0.84 - x) / 0.18:
        return True
    return False


def bubbles_alpha(x, y):
    """两个叠放的对话气泡，用于「对话模式」图标。

    小气泡外加了一圈「间隙」（gap），把大气泡在同一区域挖掉 —— 否则两个
    气泡都是纯白，叠在一起会糊成一块，没有层次。
    """
    big = in_round_rect(x, y, 0.13, 0.15, 0.65, 0.57, 0.09)
    gap = in_round_rect(x, y, 0.40, 0.42, 0.92, 0.86, 0.11)
    small = in_round_rect(x, y, 0.45, 0.47, 0.87, 0.81, 0.08)
    if small:
        return True
    if big and not gap:
        return True
    return False


def _in_circle(x, y, cx, cy, r):
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def eye_alpha(x, y):
    """眼睛（杏仁形轮廓 + 瞳孔），用于「观看模式」图标。"""
    R, d = 0.40, 0.17
    outer = _in_circle(x, y, 0.5, 0.5 - d, R) and _in_circle(x, y, 0.5, 0.5 + d, R)
    inner = (_in_circle(x, y, 0.5, 0.5 - d, R - 0.085)
             and _in_circle(x, y, 0.5, 0.5 + d, R - 0.085))
    if outer and not inner:
        return True
    return (x - 0.5) ** 2 + (y - 0.5) ** 2 <= 0.105 ** 2


def render(size, bg=BG, glyph=mic_alpha):
    """返回 RGBA 字节（size*size*4）。"""
    px = bytearray(size * size * 4)
    inv = 1.0 / (size * SS)
    for yy in range(size):
        for xx in range(size):
            bg_hits = fg_hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    nx = (xx * SS + sx + 0.5) * inv
                    ny = (yy * SS + sy + 0.5) * inv
                    if in_round_rect(nx, ny, 0.02, 0.02, 0.98, 0.98, 0.22):
                        bg_hits += 1
                        if glyph(nx, ny):
                            fg_hits += 1
            total = SS * SS
            a_bg = bg_hits / total
            a_fg = fg_hits / total
            if a_bg <= 0 and a_fg <= 0:
                continue
            # 前景覆盖在背景上
            a = a_bg
            if a_fg > 0:
                fg_w = a_fg / a_bg if a_bg else 0
                fg_w = min(1.0, fg_w)
                r = bg[0] * (1 - fg_w) + FG[0] * fg_w
                g = bg[1] * (1 - fg_w) + FG[1] * fg_w
                b = bg[2] * (1 - fg_w) + FG[2] * fg_w
            else:
                r, g, b = bg
            i = (yy * size + xx) * 4
            px[i] = int(r)
            px[i + 1] = int(g)
            px[i + 2] = int(b)
            px[i + 3] = int(a * 255)
    return bytes(px)


def png_bytes(size, rgba):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)                       # filter type 0
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def write_ico(path, images):
    """images: [(size, png_bytes)]"""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    offset = 6 + 16 * len(images)
    payload = b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII",
                               0 if size >= 256 else size,
                               0 if size >= 256 else size,
                               0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + entries + payload)


def build(path, bg, glyph, preview=None):
    images = []
    for size in SIZES:
        rgba = render(size, bg, glyph)
        data = png_bytes(size, rgba)
        images.append((size, data))
        if size == 256 and preview:
            with open(preview, "wb") as f:
                f.write(data)
        print("  %dx%d  (%d 字节)" % (size, size, len(data)))
    write_ico(path, images)
    print("已写入 %s  (%d 字节)" % (path, os.path.getsize(path)))


def main():
    print("程序图标（麦克风，蓝）—— 仅用作 exe 图标")
    build(OUT_ICO, BG, mic_alpha, OUT_PNG)
    print("对话模式图标（两个气泡，绿）")
    build(OUT_DIALOG_ICO, BG_DIALOG, bubbles_alpha, OUT_PNG.replace("preview", "preview-dialog"))
    print("观看模式图标（眼睛，蓝）")
    build(OUT_WATCH_ICO, BG_WATCH, eye_alpha, OUT_PNG.replace("preview", "preview-watch"))
    print("旧滚轮图标（上下箭头，橙）—— 已不用，保留生成")
    build(OUT_WHEEL_ICO, BG_WHEEL, arrows_alpha, OUT_PNG.replace("preview", "preview-wheel"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
