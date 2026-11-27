"""
Generate the app icons. Run once; the PNGs are committed.

Pure Python — no Pillow, no design tool. The mark is a crescent built from two
circles with a supersampled edge, which is the one shape this product can
legitimately claim and the one that survives being 40px on a home screen.

    python tools/make_icons.py
"""
import math
import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).parent.parent / "app" / "static" / "icons"

INK = (14, 16, 32)          # --ground dark
BRASS = (217, 169, 78)      # --accent dark
SS = 3                      # supersampling factor per axis


def crescent_alpha(x, y, size):
    """Coverage in [0,1] for one pixel, supersampled."""
    r = size * 0.34
    cx, cy = size / 2, size / 2
    # the bite: a second circle offset up-right, slightly larger
    bx, by, br = cx + size * 0.155, cy - size * 0.115, r * 1.02
    hits = 0
    for sy in range(SS):
        for sx in range(SS):
            px = x + (sx + 0.5) / SS
            py = y + (sy + 0.5) / SS
            in_moon = (px - cx) ** 2 + (py - cy) ** 2 <= r * r
            in_bite = (px - bx) ** 2 + (py - by) ** 2 <= br * br
            hits += in_moon and not in_bite
    return hits / (SS * SS)


def png(path, size, bg=INK, fg=BRASS, radius_frac=0.0):
    rows = []
    for y in range(size):
        row = bytearray([0])                       # filter type 0
        for x in range(size):
            a = crescent_alpha(x, y, size)
            # rounded corners for the maskable/apple icon variants
            if radius_frac:
                rr = size * radius_frac
                dx = max(0, rr - x, x - (size - rr))
                dy = max(0, rr - y, y - (size - rr))
                if dx and dy and math.hypot(dx, dy) > rr:
                    row += bytes((0, 0, 0, 0))
                    continue
            row += bytes(tuple(round(bg[i] + (fg[i] - bg[i]) * a) for i in range(3)) + (255,))
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b""))
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for size, name, radius in [(192, "icon-192.png", 0.0), (512, "icon-512.png", 0.0),
                               (180, "apple-touch-icon.png", 0.0),
                               (512, "maskable-512.png", 0.0)]:
        p = png(OUT / name, size, radius_frac=radius)
        print(f"  {p.name:24} {p.stat().st_size:>7,} bytes")
