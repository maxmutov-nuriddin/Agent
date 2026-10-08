"""PWA ikonkalarini yaratadi (PIL kerak emas). Ishlatish: python scripts/make_icons.py"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "aicompany" / "webui"
BG, LIME, DARK = (10, 12, 14), (199, 242, 58), (20, 24, 28)


def smooth(edge0, edge1, x):
    t = min(max((x - edge0) / (edge1 - edge0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render(size: int) -> bytes:
    rows = []
    c = size / 2
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx, dy = x + 0.5 - c, y + 0.5 - c
            d = math.hypot(dx, dy) / size  # 0..~0.7
            col = BG
            # tashqi yorug'lik
            col = mix(col, LIME, 0.18 * (1 - smooth(0.18, 0.42, d)))
            # halqa
            ring = smooth(0.255, 0.265, d) * (1 - smooth(0.285, 0.295, d))
            col = mix(col, LIME, ring)
            # ichki shar
            core = 1 - smooth(0.10, 0.115, d)
            col = mix(col, DARK, 1 - smooth(0.24, 0.25, d) if d < 0.25 else 0)
            col = mix(col, LIME, core)
            row += bytes(col)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


for name, size in {"icon-192.png": 192, "icon-512.png": 512, "apple-touch-icon.png": 180}.items():
    (OUT / name).write_bytes(render(size))
    print("yozildi", name)
