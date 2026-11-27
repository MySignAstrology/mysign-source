"""
Turn scans of Hevelius's zodiac plates into linework the app can tint.

    python3 tools/make_plates.py <folder of scans> app/static/plates

Each scan is a photograph of a printed page: grey paper, uneven light, black
ink. The app wants only the ink, as an alpha mask it can colour with one CSS
token and lay faintly behind a sign. So for every pixel this asks "how much
darker is this than the paper around it?" and writes that as opacity.

Sources and licence are in docs/ATTRIBUTION.md. Standard library only; the one
outside tool is macOS `sips`, used to decode the JPEG. Run it on a Mac.
"""
import os
import struct
import subprocess
import sys
import tempfile
import zlib

SIGNS = ["aries", "taurus", "gemini", "cancer", "leo", "virgo", "libra", "scorpio",
         "sagittarius", "capricorn", "aquarius", "pisces"]
BLOCK = 28        # the paper level is measured per block of this many pixels
FLOOR = 20        # this much darker than paper is still paper (grain, show-through)
SPAN = 78         # this much darker again is solid ink
TRIM = 0.035      # the photographed page edge, cut from every side
LEVELS = 12       # opacity steps kept; fewer steps, smaller files


def read_bmp(path):
    """24- or 32-bit uncompressed BMP -> (width, height, rows of luminance bytes)."""
    with open(path, "rb") as f:
        b = f.read()
    off, = struct.unpack_from("<I", b, 10)
    w, h, _, bpp = struct.unpack_from("<iiHH", b, 18)
    step, flip = bpp // 8, h > 0
    h = abs(h)
    stride = (w * step + 3) & ~3
    rows = []
    for y in range(h):
        row = b[off + y * stride: off + y * stride + w * step]
        rows.append(bytes((row[i] * 29 + row[i + 1] * 150 + row[i + 2] * 77) >> 8
                          for i in range(0, w * step, step)))
    if flip:
        rows.reverse()
    return w, h, rows


def paper_levels(w, h, rows):
    """The paper's brightness around each block: the 80th percentile, so ink in
    the block does not drag the estimate down."""
    out = []
    for by in range(0, h, BLOCK):
        line = []
        for bx in range(0, w, BLOCK):
            px = sorted(v for y in range(by, min(by + BLOCK, h)) for v in rows[y][bx:bx + BLOCK])
            line.append(px[int(len(px) * 0.8)])
        out.append(line)
    return out


def ink_alpha(w, h, rows):
    paper = paper_levels(w, h, rows)
    gh, gw = len(paper), len(paper[0])
    step = 255 // (LEVELS - 1)
    out = []
    for y in range(h):
        # Blend between block centres, so block edges never show as steps.
        fy = min(max((y - BLOCK / 2) / BLOCK, 0), gh - 1)
        y0 = int(fy); y1 = min(y0 + 1, gh - 1); ty = fy - y0
        line = bytearray(w)
        for x in range(w):
            fx = min(max((x - BLOCK / 2) / BLOCK, 0), gw - 1)
            x0 = int(fx); x1 = min(x0 + 1, gw - 1); tx = fx - x0
            p = ((paper[y0][x0] * (1 - tx) + paper[y0][x1] * tx) * (1 - ty)
                 + (paper[y1][x0] * (1 - tx) + paper[y1][x1] * tx) * ty)
            a = (p - FLOOR - rows[y][x]) / SPAN
            a = 0 if a <= 0 else 255 if a >= 1 else int(a * 255)
            line[x] = min(255, round(a / step) * step)
        out.append(line)
    return out


def write_png(path, w, h, alpha):
    """Grey + alpha, grey fixed at white: only the alpha channel carries anything."""
    raw = bytearray()
    for line in alpha:
        raw.append(0)
        for a in line:
            raw += bytes((255, a))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 4, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main(src, dst):
    os.makedirs(dst, exist_ok=True)
    for sign in SIGNS:
        scan = os.path.join(src, sign + ".jpg")
        if not os.path.exists(scan) or os.path.getsize(scan) < 20000:
            print(f"{sign:12} missing")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            bmp = os.path.join(tmp, "p.bmp")
            subprocess.run(["sips", "-s", "format", "bmp", scan, "--out", bmp],
                           check=True, capture_output=True)
            w, h, rows = read_bmp(bmp)
        cx, cy = int(w * TRIM), int(h * TRIM)
        rows = [r[cx:w - cx] for r in rows[cy:h - cy]]
        w, h = w - 2 * cx, h - 2 * cy
        out = os.path.join(dst, sign + ".png")
        write_png(out, w, h, ink_alpha(w, h, rows))
        print(f"{sign:12} {w}x{h}  {os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
