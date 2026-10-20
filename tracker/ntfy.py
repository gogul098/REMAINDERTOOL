"""Phone alerts via ntfy, kept in line with the DB by reconcile().

reconcile() diffs what *should* be on ntfy (from ACTIVE tasks) against ntfy_schedule (what we did put there):
  - scheduled with the At header when the alert is inside ntfy's 3-day window (topped up on later runs),
  - sent immediately when that kind is already the current one and was never scheduled,
  - replaced by re-posting to the same sequence ID when a deadline changes,
  - cancelled with DELETE on the sequence ID when a task is completed, ignored or moved out of range.
It is idempotent, so a failed run is simply retried by the outbox.
"""
import logging

import requests

from . import alerts, db
from .alerts import seq_id
from .config import Config
from .credentials import get_secret
from .timeutil import from_iso, iso_to_local, to_iso

log = logging.getLogger("tracker.ntfy")


def desired(task, now) -> dict:
    """kind -> fire time. A fire time <= now means 'send immediately'."""
    return alerts.alert_times(from_iso(task["deadline_utc"]), now, alerts.due_kind(task["deadline_utc"], now))


def _auth() -> dict:
    token = get_secret("NTFY_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _publish(cfg: Config, http, task, kind: str, fire, now) -> None:
    when = iso_to_local(task["deadline_utc"], cfg.tz).strftime("%a %d %b, %I:%M %p")
    url, headers, body = alerts.build_request(
        cfg.ntfy["server"], cfg.ntfy["topic"], task["id"], kind, task["title"], task["url"], fire, now,
        get_secret("NTFY_TOKEN"), task["category"], when, cfg.ntfy.get("app_url"), cfg.ntfy.get("icon_url"))
    r = http.post(url, data=body, headers=headers, timeout=15)
    r.raise_for_status()


def _cancel(cfg: Config, http, task_id: int, kind: str) -> None:
    r = http.delete(f"{cfg.ntfy['server']}/{cfg.ntfy['topic']}/{seq_id(task_id, kind)}", headers=_auth(), timeout=15)
    if r.status_code != 404:
        r.raise_for_status()


def reconcile(cfg: Config, conn, now, http=requests) -> bool:
    """Returns False if ntfy isn't configured. Raises after trying everything if any call failed."""
    if not cfg.ntfy["topic"]:
        log.debug("ntfy reconcile skipped: no topic configured")
        return False

    rows = {(r["task_id"], r["kind"]): r for r in conn.execute("SELECT * FROM ntfy_schedule")}
    want = {}
    for t in db.list_tasks(conn, "ACTIVE", include_missing=True):
        for kind, fire in desired(t, now).items():
            want[(t["id"], kind)] = (t, fire)
    failures = []

    for key, row in rows.items():  # stale: task no longer wants this alert
        if key in want:
            continue  # unchanged, or replaced below via the same sequence ID
        try:
            if from_iso(row["fire_at"]) > now:  # still pending on the server; a sent alert is left alone
                _cancel(cfg, http, *key)
            with conn:
                conn.execute("DELETE FROM ntfy_schedule WHERE task_id=? AND kind=?", key)
        except requests.RequestException as e:
            failures.append(f"cancel {key}: {e}")

    for key, (task, fire) in want.items():
        row = rows.get(key)
        if row and row["deadline_utc"] == task["deadline_utc"]:
            continue
        try:
            _publish(cfg, http, task, key[1], fire, now)
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ntfy_schedule (task_id, kind, deadline_utc, fire_at) VALUES (?,?,?,?)",
                    (key[0], key[1], task["deadline_utc"], to_iso(fire)),
                )
        except requests.RequestException as e:
            failures.append(f"publish {key}: {e}")

    if failures:
        raise RuntimeError("; ".join(failures))
    return True
