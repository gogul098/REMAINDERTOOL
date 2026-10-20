"""Alert rules shared by the laptop (ntfy.py, notify.py) and the GitHub Actions relay (relay/relay.py).

Standard library plus timeutil only, so the relay can import it without config or a database.
"""
from datetime import datetime, timedelta
from urllib.parse import quote

from .timeutil import from_iso

LEAD = {"24H": timedelta(hours=24), "2H": timedelta(hours=2), "OVERDUE": timedelta(0)}
TEXT = {"24H": "Due in 24 hours", "2H": "Due in 2 hours", "OVERDUE": "Overdue"}
PRIORITY = {"24H": "3", "2H": "4", "OVERDUE": "5"}
TAGS = {"24H": "calendar", "2H": "alarm_clock", "OVERDUE": "rotating_light"}
MAX_DELAY = timedelta(days=3) - timedelta(minutes=10)  # ntfy's maximum is 3 days; keep a margin
MIN_DELAY = timedelta(seconds=15)  # ntfy's minimum is 10 seconds


def due_kind(deadline_iso: str, now: datetime) -> str | None:
    """The single most urgent kind that currently applies, or None.

    Only the current window is considered, so waking the laptop with 1h left gives 2H, never a stale 24H.
    """
    remaining = from_iso(deadline_iso) - now
    if remaining <= timedelta(0):
        return "OVERDUE"
    if remaining <= timedelta(hours=2):
        return "2H"
    if remaining <= timedelta(hours=24):
        return "24H"
    return None


def seq_id(task_id: int, kind: str) -> str:
    return f"t{task_id}-{kind.lower()}"


def alert_times(deadline: datetime, now: datetime, current: str | None = None) -> dict:
    """kind -> fire time for alerts ntfy can schedule now (inside its window, in the future).

    If `current` names a kind whose time has passed, it maps to `now`, meaning 'send immediately'.
    The relay passes no `current`, so it only ever schedules and never sends immediate alerts.
    """
    out = {}
    for kind, lead in LEAD.items():
        fire = deadline - lead
        if fire - now > MIN_DELAY:
            if fire - now <= MAX_DELAY:
                out[kind] = fire
        elif kind == current:
            out[kind] = now
    return out


def build_request(server: str, topic: str, task_id: int, kind: str, title: str, url: str,
                  fire: datetime, now: datetime, token: str | None = None) -> tuple[str, dict, bytes]:
    """(url, headers, body) for one ntfy publish. Headers must be latin-1, so the title goes in the UTF-8 body."""
    headers = {
        "Title": "Course deadline",
        "Priority": PRIORITY[kind],
        "Tags": TAGS[kind],
        "Click": quote(url, safe=":/?&=#%+@!$'()*,;~[]"),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if fire > now:
        headers["At"] = str(int(fire.timestamp()))
    return (f"{server.rstrip('/')}/{topic}/{seq_id(task_id, kind)}", headers, f"{TEXT[kind]}: {title}".encode("utf-8"))
