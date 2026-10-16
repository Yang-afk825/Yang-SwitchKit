"""生成程序图标（纯 Python，不依赖 Pillow）。

画一个交换机机身 + 端口的简洁图标，输出多尺寸 ICO。
用法：python tools/make_icon.py
"""
from __future__ import annotations

import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parents[1] / "assets" / "app.ico"

BG = (26, 34, 50, 255)        # 深底
BODY = (56, 74, 99, 255)      # 机身
PORT = (59, 130, 246, 255)    # 端口蓝
LINK = (125, 211, 252, 255)   # 链接浅蓝


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png(size: int, rows: list) -> bytes:
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def _in_round_rect(x, y, x0, y0, x1, y1, r) -> bool:
    if not (x0 <= x < x1 and y0 <= y < y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def _paint(canvas, x0, y0, x1, y1, color, r=0.0) -> None:
    h = len(canvas)
    for y in range(int(y0), int(y1) + 1):
        if not 0 <= y < h:
            continue
        for x in range(int(x0), int(x1) + 1):
            if not 0 <= x < len(canvas[y]):
                continue
            if r <= 0 or _in_round_rect(x, y, x0, y0, x1 + 1, y1 + 1, r):
                canvas[y][x] = color


def draw(size: int) -> list:
    canvas = [[(0, 0, 0, 0)] * size for _ in range(size)]
    s = float(size)

    _paint(canvas, 0, 0, size - 1, size - 1, BG, r=s * 0.21)

    # 机身
    bx0, bx1 = s * 0.15, s * 0.85
    by0, by1 = s * 0.35, s * 0.65
    _paint(canvas, bx0, by0, bx1, by1, BODY, r=max(1.0, s * 0.05))

    # 端口
    count = 4 if size >= 32 else 2
    span = bx1 - bx0
    step = span / (count * 2 + 1)
    pw = step * 0.5
    py0 = by0 + s * 0.095
    py1 = by1 - s * 0.095
    for i in range(count):
        px0 = bx0 + step * (i * 2 + 1) + (step - pw) / 2
        _paint(canvas, px0, py0, px0 + pw, py1, PORT, r=max(0.0, s * 0.008))

    # 上方的链路示意
    if size >= 32:
        ly = s * 0.22
        _paint(canvas, s * 0.30, ly, s * 0.70, ly + max(1.0, s * 0.045),
               LINK, r=s * 0.02)
        ly2 = s * 0.76
        _paint(canvas, s * 0.30, ly2, s * 0.70, ly2 + max(1.0, s * 0.045),
               LINK, r=s * 0.02)
    return canvas


def build_ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    images = [(n, _png(n, draw(n))) for n in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for n, data in images:
        dim = 0 if n >= 256 else n
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build_ico())
    print(f"icon written: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
