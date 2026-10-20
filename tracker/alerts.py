"""Alert rules shared by the laptop (ntfy.py, notify.py) and the GitHub Actions relay (relay/relay.py).

Standard library plus timeutil only, so the relay can import it without config or a database.
"""
import base64
from datetime import datetime, timedelta
from urllib.parse import quote

from .timeutil import from_iso

LEAD = {"24H": timedelta(hours=24), "2H": timedelta(hours=2), "OVERDUE": timedelta(0)}
TEXT = {"24H": "Due in 24 hours", "2H": "Due in 2 hours", "OVERDUE": "Overdue"}
PRIORITY = {"24H": "3", "2H": "4", "OVERDUE": "5"}
EMOJI = {"24H": "📅", "2H": "⏰", "OVERDUE": "🚨"}
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


def _mime(text: str) -> str:
    """RFC 2047 encoded-word, so any Unicode (emoji, accents) survives in an HTTP header."""
    return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def build_request(server: str, topic: str, task_id: int, kind: str, title: str, url: str,
                  fire: datetime, now: datetime, token: str | None = None, category: str = "",
                  when_text: str = "", app_url: str | None = None, icon_url: str | None = None
                  ) -> tuple[str, dict, bytes]:
    """(url, headers, body) for one ntfy publish: emoji title, Markdown body, Open buttons, optional icon."""
    safe = ":/?&=#%+@!$'()*;~[]"  # no comma: it separates fields inside the Actions header
    actions = [f"view, Open course, {quote(url, safe=safe)}, clear=true"]
    if app_url:
        actions.append(f"view, Open tracker, {quote(app_url, safe=safe)}")
    headers = {
        "Title": _mime(f"{EMOJI[kind]} {TEXT[kind]}: {title}"[:120]),
        "Priority": PRIORITY[kind],
        "Tags": TAGS[kind],
        "Markdown": "yes",
        "Click": quote(url, safe=":/?&=#%+@!$'()*,;~[]"),
        "Actions": "; ".join(actions),
    }
    if icon_url:
        headers["Icon"] = icon_url
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if fire > now:
        headers["At"] = str(int(fire.timestamp()))
    body = "\n".join(x for x in (f"**{when_text}**" if when_text else "", category) if x) or title
    return (f"{server.rstrip('/')}/{topic}/{seq_id(task_id, kind)}", headers, body.encode("utf-8"))
