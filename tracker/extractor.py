"""Chrome profile discovery and bookmark -> DB sync. Makes no network calls."""
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

from . import db
from .config import Config

log = logging.getLogger("tracker.extractor")

ROOT_NAMES = {"bookmark_bar": "Bookmarks bar", "other": "Other bookmarks", "synced": "Mobile bookmarks"}


def list_chrome_profiles(user_data_dir: Path) -> list[dict]:
    """Return [{folder, name, email}] from Local State's profile.info_cache."""
    state = json.loads((user_data_dir / "Local State").read_text(encoding="utf-8"))
    cache = state.get("profile", {}).get("info_cache", {})
    return [
        {"folder": folder, "name": info.get("name", ""), "email": info.get("user_name", "")}
        for folder, info in sorted(cache.items())
    ]


def read_bookmarks_file(path: Path, retries: int = 5) -> dict:
    """Copy first (Chrome rewrites the file while browsing), retry on torn JSON."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dst = Path(tmp) / "Bookmarks"
                shutil.copyfile(path, dst)
                return json.loads(dst.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            last = e
            time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(f"could not read bookmarks from {path}: {last}")


def _matches(category: str, folders: list[str] | str) -> bool:
    if folders == "ALL":
        return True
    return any(category == f or category.startswith(f + "/") for f in folders)


def iter_bookmarks(data: dict, folders: list[str] | str):
    """Yield (guid, url, title, category) for URL nodes inside the selected folders."""

    def walk(node: dict, path: str):
        if node.get("type") == "folder":
            here = f"{path}/{node.get('name', '')}" if path else node.get("name", "")
            for child in node.get("children", []):
                yield from walk(child, here)
        elif node.get("type") == "url" and _matches(path, folders):
            url = node.get("url", "")
            if url.startswith(("http://", "https://")):
                yield node.get("guid") or node.get("id"), url, node.get("name") or url, path

    for key, root in data.get("roots", {}).items():
        if not isinstance(root, dict):
            continue
        name = ROOT_NAMES.get(key, root.get("name", key))
        for child in root.get("children", []):
            yield from walk(child, name)


def list_folders(*datas: dict) -> list[str]:
    """All folder paths across the given bookmark files, for the dashboard's folder picker."""
    out: list[str] = []
    for data in datas:
        out.extend(_folders(data))
    return sorted(set(out))


def _folders(data: dict) -> list[str]:
    out: list[str] = []

    def walk(node, path):
        if node.get("type") == "folder":
            here = f"{path}/{node['name']}" if path else node["name"]
            out.append(here)
            for c in node.get("children", []):
                walk(c, here)

    for key, root in data.get("roots", {}).items():
        if isinstance(root, dict):
            name = ROOT_NAMES.get(key, root.get("name", key))
            out.append(name)
            for c in root.get("children", []):
                walk(c, name)
    return out


def sync_bookmarks(conn, cfg: Config) -> dict:
    files = cfg.bookmarks_files
    if not files:
        raise RuntimeError(f"no Bookmarks or AccountBookmarks file in {cfg.chrome_user_data_dir / cfg.chrome_profile}")
    datas = [read_bookmarks_file(f) for f in files]  # read all first so a failure changes nothing
    seen: set[str] = set()
    with conn:  # one transaction: a failure leaves the DB untouched
        for data in datas:
            for guid, url, title, category in iter_bookmarks(data, cfg.bookmark_folders):
                seen.add(guid)
                db.upsert_bookmark(conn, guid, url, title, category)
        missing = db.mark_missing(conn, seen)
    log.info("sync: %d bookmarks seen, %d newly missing", len(seen), missing)
    return {"seen": len(seen), "missing": missing}
