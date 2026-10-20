"""Render active deadlines onto the base wallpaper and apply it. Always renders from the clean base."""
import ctypes
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import db
from .config import ROOT, Config
from .timeutil import countdown, from_iso, iso_to_local, now_utc

log = logging.getLogger("tracker.wallpaper")

OUT_DIR = ROOT / "output"
STATE_DIR = ROOT / "data"
FONT_REG = r"C:\Windows\Fonts\segoeui.ttf"
FONT_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"

RED, AMBER, GREEN = (239, 68, 68), (245, 158, 11), (34, 197, 94)
SPI_SETDESKWALLPAPER, SPI_GETDESKWALLPAPER = 20, 0x73


def primary_resolution() -> tuple[int, int]:
    u = ctypes.windll.user32
    u.SetProcessDPIAware()  # otherwise scaled displays report a smaller size
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def fit_cover(img: Image.Image, size: tuple[int, int], anchor_y: float = 0.5) -> Image.Image:
    """Scale to cover `size`, then crop. anchor_y picks which vertical slice survives (0=top, 1=bottom)."""
    w, h = size
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left = (img.width - w) // 2
    top = round((img.height - h) * anchor_y)
    return img.crop((left, top, left + w, top + h))


def _font(path: str, px: int):
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        return ImageFont.load_default(px)


def _truncate(text: str, font, max_w: float) -> str:
    if font.getlength(text) <= max_w:
        return text
    while text and font.getlength(text + "…") > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def _color(deadline_iso: str, now: datetime):
    hours = (from_iso(deadline_iso) - now).total_seconds() / 3600
    return RED if hours < 24 else AMBER if hours < 72 else GREEN


def render(base: Image.Image, tasks, tz, now: datetime, size: tuple[int, int],
           max_tasks: int = 6, anchor_y: float = 0.35) -> Image.Image:
    """tasks: rows with title, category, deadline_utc, sorted by deadline. Returns an RGB image of `size`."""
    img = fit_cover(base.convert("RGB"), size, anchor_y).convert("RGBA")
    if not tasks:
        return img.convert("RGB")

    s = size[1] / 1080
    f_head, f_title = _font(FONT_BOLD, round(26 * s)), _font(FONT_BOLD, round(22 * s))
    f_sub, f_cd = _font(FONT_REG, round(15 * s)), _font(FONT_BOLD, round(22 * s))
    pad, gap, bar = round(24 * s), round(14 * s), round(6 * s)
    card_w = round(640 * s)
    shown, extra = tasks[:max_tasks], max(0, len(tasks) - max_tasks)

    row_h = round(22 * 1.35 * s) + round(15 * 1.5 * s)
    head_h = round(26 * 1.6 * s)
    more_h = round(15 * 2 * s) if extra else 0
    card_h = pad * 2 + head_h + len(shown) * (row_h + gap) - gap + more_h

    margin = round(size[0] * 0.04)
    x0, y0 = size[0] - card_w - margin, margin * 2
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x0, y0, x0 + card_w, y0 + card_h), radius=round(18 * s), fill=(18, 18, 24, 225))

    x, y = x0 + pad, y0 + pad
    d.text((x, y), "Upcoming deadlines", font=f_head, fill=(255, 255, 255, 255))
    y += head_h
    inner_w = card_w - 2 * pad - bar - round(12 * s)
    for t in shown:
        col = _color(t["deadline_utc"], now)
        cd = countdown(t["deadline_utc"], now)
        cd_w = f_cd.getlength(cd)
        d.rounded_rectangle((x, y, x + bar, y + row_h), radius=bar // 2, fill=col + (255,))
        tx = x + bar + round(12 * s)
        d.text((tx, y), _truncate(t["title"], f_title, inner_w - cd_w - gap), font=f_title, fill=(255, 255, 255, 255))
        d.text((x + card_w - 2 * pad - cd_w, y), cd, font=f_cd, fill=col + (255,))
        when = iso_to_local(t["deadline_utc"], tz).strftime("%a %d %b, %I:%M %p")
        sub = _truncate(f"{t['category'].split('/')[-1]} · {when}", f_sub, inner_w)
        d.text((tx, y + round(22 * 1.35 * s)), sub, font=f_sub, fill=(170, 170, 185, 255))
        y += row_h + gap
    if extra:
        d.text((x + bar + round(12 * s), y - gap + round(8 * s)), f"+{extra} more", font=f_sub, fill=(170, 170, 185, 255))
    return Image.alpha_composite(img, layer).convert("RGB")


# ---- applying ----
def _current_wallpaper() -> str:
    buf = ctypes.create_unicode_buffer(520)
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, len(buf), buf, 0)
    return buf.value


def apply(path: Path) -> bool:
    return bool(ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(path), 3))


def remember_original() -> None:
    """Save the user's own wallpaper path once, so uninstall can restore it."""
    f = STATE_DIR / "original_wallpaper.txt"
    cur = _current_wallpaper()
    if not f.exists() and cur and Path(cur).parent != OUT_DIR:
        STATE_DIR.mkdir(exist_ok=True)
        f.write_text(cur, encoding="utf-8")


def restore_original() -> bool:
    f = STATE_DIR / "original_wallpaper.txt"
    return f.exists() and apply(Path(f.read_text(encoding="utf-8")))


def _write_atomic(img: Image.Image) -> Path:
    """Alternate between two filenames so Windows never serves a cached copy of the same path."""
    OUT_DIR.mkdir(exist_ok=True)
    state = OUT_DIR / "current.txt"
    last = state.read_text().strip() if state.exists() else "b"
    name = "a" if last == "b" else "b"
    final = OUT_DIR / f"active_wallpaper_{name}.png"
    tmp = OUT_DIR / f"active_wallpaper_{name}.tmp.png"
    img.save(tmp, "PNG")
    os.replace(tmp, final)
    state.write_text(name)
    return final


def refresh(cfg: Config, conn, now: datetime | None = None, apply_it: bool = True) -> Path | None:
    """Re-render from the DB and apply. Skips applying if nothing visible changed."""
    now = now or now_utc()
    tasks = db.list_tasks(conn, "ACTIVE", include_missing=True)
    size = primary_resolution()
    base = Image.open(cfg.wallpaper["base"])
    img = render(base, tasks, cfg.tz, now, size, cfg.wallpaper["max_tasks"], cfg.wallpaper["anchor_y"])

    digest = hashlib.sha256(img.tobytes()).hexdigest()
    digest_file = OUT_DIR / "last_render.sha"
    OUT_DIR.mkdir(exist_ok=True)
    if digest_file.exists() and digest_file.read_text() == digest and OUT_DIR in Path(_current_wallpaper()).parents:
        return None
    if apply_it:
        remember_original()
    path = _write_atomic(img)
    ok = apply(path) if apply_it else True
    if ok:
        digest_file.write_text(digest)
    log.info("wallpaper: %d active tasks, applied=%s -> %s", len(tasks), ok, path.name)
    return path
