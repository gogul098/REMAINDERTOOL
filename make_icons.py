"""Generates the app icons in docs/ (run once; output is committed)."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "docs"
CORAL, YELLOW, DARK = (240, 96, 107), (253, 224, 71), (24, 18, 22)


def draw(size: int, safe: float) -> Image.Image:
    """safe < 1 shrinks the artwork inside a full-bleed background, for maskable icons."""
    s = size * 4  # draw large, then downsample for smooth edges
    img = Image.new("RGB", (s, s), CORAL)
    d = ImageDraw.Draw(img)
    for y in range(s):  # vertical gradient coral -> deeper coral
        t = y / s
        d.line([(0, y), (s, y)], fill=(round(240 - 30 * t), round(96 - 30 * t), round(107 - 20 * t)))
    c, r = s / 2, s * 0.30 * safe / 0.8 * 0.8
    d.ellipse((c - r, c - r, c + r, c + r), fill=YELLOW)
    r2 = r * 0.82
    d.ellipse((c - r2, c - r2, c + r2, c + r2), fill=DARK)
    w = max(4, int(s * 0.035))
    d.line((c, c, c, c - r2 * 0.62), fill=YELLOW, width=w)          # minute hand
    d.line((c, c, c + r2 * 0.42, c + r2 * 0.18), fill=YELLOW, width=w)  # hour hand
    d.ellipse((c - w, c - w, c + w, c + w), fill=YELLOW)
    return img.resize((size, size), Image.LANCZOS)


OUT.mkdir(exist_ok=True)
draw(192, 1).save(OUT / "icon-192.png")
draw(512, 1).save(OUT / "icon-512.png")
draw(512, 0.8).save(OUT / "icon-maskable-512.png")
