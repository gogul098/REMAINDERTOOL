"""Publish deadlines.json to a secret GitHub Gist for the Android widget (KWGT / HTTP Request Widget).

Only titles and URLs go in the payload: secret Gists are unlisted, not private.
The widget computes the countdown on-device from deadline_iso, so nothing here goes stale.
"""
import hashlib
import json
import logging

import requests

from . import db
from .config import ROOT, Config
from .credentials import get_secret
from .timeutil import iso_to_local

log = logging.getLogger("tracker.mobile")
STATE_FILE = ROOT / "data" / "state.json"
API = "https://api.github.com"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def build_payload(conn, cfg: Config) -> str:
    rows = db.list_tasks(conn, "ACTIVE", include_missing=True)
    tasks = [
        {
            "id": r["id"],
            "title": r["title"],
            "category": r["category"],
            "url": r["url"],
            "deadline_iso": iso_to_local(r["deadline_utc"], cfg.tz).isoformat(),  # with +05:30 offset
        }
        for r in rows
    ]
    return json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False, sort_keys=True)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def create_gist(cfg: Config, token: str, http=requests) -> dict:
    name = cfg.mobile["gist_filename"]
    r = http.post(f"{API}/gists", headers=_headers(token), timeout=15, json={
        "description": "course deadlines", "public": False, "files": {name: {"content": '{"tasks": []}'}}})
    r.raise_for_status()
    return r.json()


def raw_url(gist: dict, filename: str) -> str:
    """Stable URL without the revision hash, so it always serves the latest content."""
    return f"https://gist.githubusercontent.com/{gist['owner']['login']}/{gist['id']}/raw/{filename}"


def sync(cfg: Config, conn, http=requests) -> bool:
    """PATCH the Gist only if the content changed. Returns False if not configured (not a failure)."""
    token, state = get_secret("GITHUB_TOKEN"), load_state()
    gist_id = state.get("gist_id")
    if not token or not gist_id:
        log.debug("gist sync skipped: token or gist_id missing (run setup_remote.py)")
        return False
    content = build_payload(conn, cfg)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if state.get("gist_hash") == digest:
        return True
    r = http.patch(f"{API}/gists/{gist_id}", headers=_headers(token), timeout=15,
                   json={"files": {cfg.mobile["gist_filename"]: {"content": content}}})
    r.raise_for_status()
    save_state({**load_state(), "gist_hash": digest})
    log.info("gist updated (%d bytes)", len(content))
    return True
