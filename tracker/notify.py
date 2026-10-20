"""Notification state machine + Windows toasts. (ntfy push is added in Phase 3.)"""
import logging
import subprocess
import sys
from datetime import datetime

from . import db
from .alerts import due_kind
from .config import ROOT, Config
from .timeutil import countdown, from_iso, now_utc, to_iso

log = logging.getLogger("tracker.notify")

DETACHED = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW


def evaluate(conn, now: datetime, send) -> list[tuple[int, str]]:
    """Fire each (task, kind, deadline) at most once. `send(task, kind)` returns True on success.

    The unique key includes the deadline, so changing a deadline re-arms every alert.
    """
    fired = []
    for t in db.list_tasks(conn, "ACTIVE", include_missing=True):
        kind = due_kind(t["deadline_utc"], now)
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


def toast_title(task, kind: str, now: datetime) -> str:
    if kind == "OVERDUE":
        return "Overdue"
    return f"Due in {countdown(task['deadline_utc'], now)}"


def make_toast_sender(cfg: Config, now: datetime):
    """Spawn a detached worker per toast so the daemon never blocks waiting for a click."""

    def send(task, kind: str) -> bool:
        args = [sys.executable, "-m", "tracker.toast_worker", toast_title(task, kind, now),
                f"{task['title']}\n{task['category']}", task["url"], cfg.chrome_profile, cfg.chrome_exe or ""]
        try:
            subprocess.Popen(args, cwd=ROOT, creationflags=DETACHED, close_fds=True)
            return True
        except OSError:
            log.exception("could not start toast worker")
            return False

    return send
