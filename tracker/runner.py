"""One daemon pass, shared by daemon.py (scheduled) and the dashboard (after each save)."""
import logging
import msvcrt
import time
from contextlib import contextmanager

from . import backup, db, mobile, notify, ntfy, outbox, wallpaper
from .config import ROOT, Config
from .timeutil import now_utc

log = logging.getLogger("tracker.runner")
LOCK_FILE = ROOT / "data" / "daemon.lock"


@contextmanager
def single_instance(wait_seconds: float = 0):
    """Cross-process lock. Yields True if acquired, False if another run holds it past the wait."""
    LOCK_FILE.parent.mkdir(exist_ok=True)
    f = open(LOCK_FILE, "a+")
    got = False
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            got = True
            break
        except OSError:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
    try:
        yield got
    finally:
        if got:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


def run_once(cfg: Config, wait_seconds: float = 0) -> bool:
    """Evaluate notifications, then re-render the wallpaper. Never raises."""
    with single_instance(wait_seconds) as got:
        if not got:
            log.info("another run is active; skipping")
            return False
        conn = db.connect(cfg.db_path)
        try:
            now = now_utc()
            for step, fn in (
                ("backup", lambda: backup.backup_daily(cfg.db_path, now.astimezone(cfg.tz))),
                ("notifications", lambda: notify.evaluate(conn, now, notify.make_toast_sender(cfg, now), cfg.reminder_settings)),
                ("wallpaper", lambda: wallpaper.refresh(cfg, conn, now)),
                # Network last, so a slow connection never delays the desktop. Queueing both targets on every
                # pass also tops up ntfy's 3-day scheduling window.
                ("outbox", lambda: _sync_network(cfg, conn, now)),
            ):
                try:
                    fn()
                except Exception:  # noqa: BLE001 - one failing step must not stop the others
                    log.exception("%s step failed", step)
        finally:
            conn.close()
    return True


def _sync_network(cfg: Config, conn, now) -> None:
    for target in ("gist", "ntfy"):
        outbox.enqueue(conn, target, now)
    outbox.process(conn, {
        "gist": lambda: mobile.sync(cfg, conn),
        "ntfy": lambda: ntfy.reconcile(cfg, conn, now),
        "wallpaper": lambda: None,
    }, now)
