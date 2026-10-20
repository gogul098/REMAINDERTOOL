"""Retry queue. Each target syncs idempotently from the DB, so one pending row per target is enough."""
import logging
from datetime import timedelta

from .timeutil import from_iso, now_utc, to_iso

log = logging.getLogger("tracker.outbox")
MAX_BACKOFF = 6 * 3600


def enqueue(conn, target: str, now=None) -> None:
    """Mark a target as needing a sync. A target already waiting in backoff is retried right away."""
    now_s = to_iso(now or now_utc())
    with conn:
        n = conn.execute("UPDATE outbox SET next_try_at=? WHERE target=?", (now_s, target)).rowcount
        if not n:
            conn.execute("INSERT INTO outbox (target, next_try_at) VALUES (?,?)", (target, now_s))


def pending_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


def process(conn, handlers: dict, now=None) -> dict:
    """Run due targets. Success removes the row; failure backs off exponentially (1m, 2m, 4m ... 6h)."""
    now = now or now_utc()
    results = {}
    for row in conn.execute("SELECT * FROM outbox WHERE next_try_at<=? ORDER BY id", (to_iso(now),)).fetchall():
        try:
            handlers[row["target"]]()
            with conn:
                conn.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
            results[row["target"]] = "ok"
        except Exception as e:  # noqa: BLE001 - any failure just schedules a retry
            attempts = row["attempts"] + 1
            wait = min(60 * 2 ** (attempts - 1), MAX_BACKOFF)
            with conn:
                conn.execute("UPDATE outbox SET attempts=?, next_try_at=? WHERE id=?",
                             (attempts, to_iso(now + timedelta(seconds=wait)), row["id"]))
            log.warning("outbox %s failed (attempt %d, retry in %ds): %s", row["target"], attempts, wait, e)
            results[row["target"]] = "failed"
    return results
