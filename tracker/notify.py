"""Notification state machine + Windows toasts."""
import logging
import subprocess
import sys
from datetime import datetime

from . import db
from .alerts import Reminders, current_kind, describe, due_kind  # noqa: F401  (due_kind re-exported)
from .config import ROOT, Config
from .timeutil import from_iso, iso_to_local, now_utc, to_iso

log = logging.getLogger("tracker.notify")

DETACHED = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW


def evaluate(conn, now: datetime, send, rem: Reminders | None = None) -> list[tuple[int, str]]:
    """Fire each (task, kind, deadline) at most once. `send(task, kind)` returns True on success.

    With `rem`, the daily countdown reminders are included. The unique key includes the deadline, so changing a
    deadline re-arms every alert.
    """
    fired = []
    for t in db.list_tasks(conn, "ACTIVE", include_missing=True):
        kind = current_kind(t["deadline_utc"], now, rem)
        if kind is None:
            continue
        seen = conn.execute(
            "SELECT 1 FROM notifications WHERE task_id=? AND kind=? AND deadline_utc_at_send=?",
            (t["id"], kind, t["deadline_utc"]),
        ).fetchone()
        if seen:
            continue
        if send(t, kind):
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO notifications (task_id, kind, deadline_utc_at_send, sent_at) VALUES (?,?,?,?)",
                    (t["id"], kind, t["deadline_utc"], to_iso(now_utc())),
                )
            fired.append((t["id"], kind))
        else:
            log.warning("toast for task %s (%s) failed; will retry next run", t["id"], kind)
    return fired


def toast_text(cfg: Config, task, kind: str) -> tuple[str, str]:
    """(title, plain body) for a Windows toast."""
    when = iso_to_local(task["deadline_utc"], cfg.tz).strftime("%a %d %b, %I:%M %p")
    headline, body = describe(kind, task["title"], when, task["category"], cfg.reminder_settings)
    if kind in ("24H", "2H", "OVERDUE"):  # the headline already says it; show the course name then the details
        return headline.split(": ", 1)[0], f"{task['title']}\n{body.replace('**', '')}"
    return headline, body.replace("**", "")


def make_toast_sender(cfg: Config, now: datetime):
    """Spawn a detached worker per toast so the daemon never blocks waiting for a click."""

    def send(task, kind: str) -> bool:
        title, body = toast_text(cfg, task, kind)
        args = [sys.executable, "-m", "tracker.toast_worker", title, body, task["url"], cfg.chrome_profile,
                cfg.chrome_exe or ""]
        try:
            subprocess.Popen(args, cwd=ROOT, creationflags=DETACHED, close_fds=True)
            return True
        except OSError:
            log.exception("could not start toast worker")
            return False

    return send
