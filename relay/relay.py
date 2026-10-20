"""Stateless relay for GitHub Actions: keeps ntfy alerts scheduled while the laptop is off.

Reads deadlines.json from the Gist, and for every alert that has just come inside ntfy's 3-day scheduling
window, posts it with the same sequence ID the laptop uses. Re-posting an identical sequence ID replaces the
scheduled message, so running every hour (and alongside the laptop) never produces duplicates.
It never sends immediate alerts and never cancels; cancelling is the laptop's job when you complete a task.

Environment: DEADLINES_URL (raw Gist URL), NTFY_TOPIC, optional NTFY_SERVER and NTFY_TOKEN.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker import alerts  # noqa: E402  (stdlib-only module, no config or DB needed)


def run(tasks: list[dict], server: str, topic: str, token: str | None, now: datetime, post=requests.post,
        app_url: str | None = None) -> int:
    """Returns the number of failed posts."""
    failures = 0
    for t in tasks:
        if "id" not in t:
            print(f"skip {t.get('title')!r}: no id yet (laptop hasn't pushed the new format)")
            continue
        deadline = datetime.fromisoformat(t["deadline_iso"]).astimezone(timezone.utc)
        for kind, fire in alerts.alert_times(deadline, now).items():
            url, headers, body = alerts.build_request(
                server, topic, t["id"], kind, t["title"], t["url"], fire, now, token,
                t.get("category", ""), t.get("deadline_text", ""), app_url)
            try:
                post(url, data=body, headers=headers, timeout=15).raise_for_status()
                print(f"scheduled {kind} for task {t['id']} at {fire.isoformat()}")
            except requests.RequestException as e:
                failures += 1
                print(f"FAILED {kind} for task {t['id']}: {e}")
    return failures


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()  # pasted secrets often carry a trailing newline


def main() -> int:
    r = requests.get(_env("DEADLINES_URL"), timeout=15)
    r.raise_for_status()
    failures = run(r.json()["tasks"], _env("NTFY_SERVER") or "https://ntfy.sh",
                   _env("NTFY_TOPIC"), _env("NTFY_TOKEN") or None, datetime.now(timezone.utc),
                   app_url=_env("APP_URL") or None)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
