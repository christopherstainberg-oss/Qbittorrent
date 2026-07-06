#!/usr/bin/env python
"""Generate the PWA icon set for qBittorrent Auto-Sorter.

Draws a "download into tray" mark (a blue->green gradient arrow descending
into an open tray) on the app's dark panel background, and writes every PNG
size the manifest needs plus a matching SVG favicon.

One-time/dev tool only — the app does not import Pillow at runtime. Re-run
after tweaking the design:

    python tools/make_icons.py

Outputs to qbit_sorter/static/.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "qbit_sorter" / "static"

# Brand palette (matches the web UI CSS variables).
BG_TOP = (29, 43, 61)       # #1D2B3D
BG_BOTTOM = (15, 23, 32)    # #0F1720
BORDER = (42, 59, 77)       # #2A3B4D
ARROW_TOP = (108, 184, 236)  # #6CB8EC (blue)
ARROW_BOTTOM = (52, 165, 106)  # #34A56A (green)
TRAY = (74, 163, 223)       # #4AA3DF

SS = 4  # supersample factor for anti-aliasing


def vgrad(w: int, h: int, c1, c2, y0: float | None = None, y1: float | None = None) -> Image.Image:
    """Vertical gradient c1->c2. If y0/y1 given, the ramp is confined to that
    band (clamped to c1 above y0 and c2 below y1)."""
    if y0 is None:
        y0, y1 = 0, h - 1
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = 0.0 if y <= y0 else 1.0 if y >= y1 else (y - y0) / (y1 - y0)
        col.putpixel((0, y), tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    return col.resize((w, h))


def _motif(sz: int, scale: float) -> Image.Image:
    """The download-into-tray glyph, scaled about centre, on a transparent
    layer of size `sz`. Coordinates are authored in a 1000x1000 design space."""
    cx = cy = sz / 2

    def T(x, y):
        return (cx + (x - 500) * scale * sz / 1000, cy + (y - 500) * scale * sz / 1000)

    def box(x0, y0, x1, y1):
        p0, p1 = T(x0, y0), T(x1, y1)
        return [p0[0], p0[1], p1[0], p1[1]]

    def r(v):
        return v * scale * sz / 1000

    # Arrow (shaft + head) as a mask, then filled with a blue->green gradient.
    mask = Image.new("L", (sz, sz), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle(box(440, 205, 560, 500), radius=r(60), fill=255)
    md.polygon([T(312, 468), T(688, 468), T(500, 712)], fill=255)
    ay0, ay1 = T(500, 205)[1], T(500, 712)[1]
    arrow = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    arrow.paste(vgrad(sz, sz, ARROW_TOP, ARROW_BOTTOM, ay0, ay1), (0, 0), mask)

    # Open tray (U shape) the arrow drops into.
    tray = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    td = ImageDraw.Draw(tray)
    for b in (box(300, 612, 372, 844), box(628, 612, 700, 844), box(300, 780, 700, 844)):
        td.rounded_rectangle(b, radius=r(30), fill=TRAY + (255,))

    return Image.alpha_composite(tray, arrow)


def make(px: int, variant: str) -> Image.Image:
    """variant: 'any' (rounded squircle, transparent corners),
    'maskable' (full-bleed, motif inside the safe zone),
    'opaque' (full-bleed, for Apple touch icon)."""
    sz = px * SS
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    grad = vgrad(sz, sz, BG_TOP, BG_BOTTOM)

    if variant == "any":
        mask = Image.new("L", (sz, sz), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, sz - 1, sz - 1], radius=int(sz * 0.22), fill=255)
        img.paste(grad, (0, 0), mask)
        bd = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        inset = sz * 0.012
        ImageDraw.Draw(bd).rounded_rectangle(
            [inset, inset, sz - 1 - inset, sz - 1 - inset],
            radius=int(sz * 0.205), outline=BORDER + (255,), width=max(2, int(sz * 0.012)))
        img = Image.alpha_composite(img, bd)
        motif_scale = 0.84
    else:
        img.paste(grad, (0, 0))
        motif_scale = 0.62 if variant == "maskable" else 0.76

    img = Image.alpha_composite(img, _motif(sz, motif_scale))
    if variant == "opaque":  # Apple touch icons must be fully opaque
        flat = Image.new("RGBA", (sz, sz), BG_BOTTOM + (255,))
        img = Image.alpha_composite(flat, img)
    return img.resize((px, px), Image.LANCZOS)


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1D2B3D"/><stop offset="1" stop-color="#0F1720"/>
    </linearGradient>
    <linearGradient id="ar" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#6CB8EC"/><stop offset="1" stop-color="#34A56A"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="984" height="984" rx="220" fill="url(#bg)" stroke="#2A3B4D" stroke-width="12"/>
  <g transform="translate(500 500) scale(0.84) translate(-500 -500)" fill="#4AA3DF">
    <rect x="300" y="612" width="72" height="232" rx="30"/>
    <rect x="628" y="612" width="72" height="232" rx="30"/>
    <rect x="300" y="780" width="400" height="64" rx="30"/>
    <g fill="url(#ar)">
      <rect x="440" y="205" width="120" height="295" rx="60"/>
      <polygon points="312,468 688,468 500,712"/>
    </g>
  </g>
</svg>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, "any"),
        ("icon-512.png", 512, "any"),
        ("icon-maskable-192.png", 192, "maskable"),
        ("icon-maskable-512.png", 512, "maskable"),
        ("apple-touch-icon.png", 180, "opaque"),
        ("favicon-32.png", 32, "any"),
    ]
    for name, px, variant in jobs:
        make(px, variant).save(OUT / name)
        print("wrote", name)
    (OUT / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")
    print("wrote favicon.svg")


if __name__ == "__main__":
    main()
