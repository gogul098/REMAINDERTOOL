"""Load and validate config.json. Fails fast with a clear message."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    chrome_user_data_dir: Path
    chrome_profile: str
    chrome_exe: str | None
    bookmark_folders: list[str] | str  # list of folder paths, or "ALL"
    timezone: str
    db_path: Path
    wallpaper: dict
    mobile: dict
    ntfy: dict

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def bookmarks_files(self) -> list[Path]:
        """Local bookmarks plus account bookmarks (signed-in, non-synced profiles), whichever exist."""
        d = self.chrome_user_data_dir / self.chrome_profile
        return [p for p in (d / "Bookmarks", d / "AccountBookmarks") if p.exists()]


def _default_user_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.json"
    if not path.exists():
        raise ConfigError(f"{path} not found; copy config.example.json to config.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e

    folders = raw.get("bookmark_folders", "ALL")
    if folders != "ALL" and not (
        isinstance(folders, list) and folders and all(isinstance(f, str) and f for f in folders)
    ):
        raise ConfigError('bookmark_folders must be "ALL" or a non-empty list of folder paths')
    if isinstance(folders, list):
        folders = [f.strip("/") for f in folders]

    tz = raw.get("timezone", "Asia/Kolkata")
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as e:
        raise ConfigError(f"unknown timezone {tz!r} (is tzdata installed?)") from e

    profile = raw.get("chrome_profile")
    if not profile:
        raise ConfigError("chrome_profile is required (e.g. 'Profile 1')")

    udd = Path(raw.get("chrome_user_data_dir") or _default_user_data_dir())
    db_path = Path(raw.get("db_path") or "data/courses.db")
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    wp = {"base": "assets/base_wallpaper.jpg", "max_tasks": 6, "anchor_y": 0.35, **raw.get("wallpaper", {})}
    wp["base"] = Path(wp["base"]) if Path(wp["base"]).is_absolute() else ROOT / wp["base"]
    mobile = {"gist_filename": "deadlines.json", **raw.get("mobile", {})}
    ntfy = {"server": "https://ntfy.sh", "topic": None, **raw.get("ntfy", {})}
    topic = ntfy["topic"]
    if topic and (len(topic) < 20 or not re.fullmatch(r"[A-Za-z0-9_-]+", topic)):
        raise ConfigError("ntfy.topic must be 20+ chars of letters, digits, _ or - (it is the only thing keeping it private)")
    ntfy["server"] = ntfy["server"].rstrip("/")
    return Config(udd, profile, raw.get("chrome_exe"), folders, tz, db_path, wp, mobile, ntfy)
