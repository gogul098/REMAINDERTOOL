"""Alert rules shared by the laptop (ntfy.py, notify.py) and the GitHub Actions relay (relay/relay.py).

Standard library plus timeutil only, so the relay can import it without config or a database.

Two families of alerts:
  - deadline alerts: 24H, 2H and OVERDUE, relative to the deadline;
  - countdown plan: from `horizon` days out, one reminder per day (kinds D10 ... D2, named by days left) at a
    fixed local time, saying how much to do today and when to study. D10 is the 10-day kickoff.
"""
import base64
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .timeutil import from_iso

LEAD = {"24H": timedelta(hours=24), "2H": timedelta(hours=2), "OVERDUE": timedelta(0)}
TEXT = {"24H": "Due in 24 hours", "2H": "Due in 2 hours", "OVERDUE": "Overdue"}
PRIORITY = {"24H": "3", "2H": "4", "OVERDUE": "5"}
EMOJI = {"24H": "📅", "2H": "⏰", "OVERDUE": "🚨"}
TAGS = {"24H": "calendar", "2H": "alarm_clock", "OVERDUE": "rotating_light"}
MAX_DELAY = timedelta(days=3) - timedelta(minutes=10)  # ntfy's maximum is 3 days; keep a margin
MIN_DELAY = timedelta(seconds=15)  # ntfy's minimum is 10 seconds


def _hhmm(value: str, name: str) -> time:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except ValueError as e:
        raise ValueError(f"{name} must look like '09:00', got {value!r}") from e


@dataclass(frozen=True)
class Reminders:
    """Settings for the daily countdown plan."""
    tz: ZoneInfo
    remind_at: time = time(9, 0)
    horizon: int = 10
    study_at: time = time(19, 0)
    study_minutes: int = 45

    @classmethod
    def from_dict(cls, d: dict, tz_name: str) -> "Reminders":
        d = d or {}
        horizon = int(d.get("horizon_days", 10))
        if not 2 <= horizon <= 30:
            raise ValueError("reminders.horizon_days must be between 2 and 30")
        return cls(ZoneInfo(tz_name), _hhmm(d.get("time", "09:00"), "reminders.time"), horizon,
                   _hhmm(d.get("study_time", "19:00"), "reminders.study_time"), int(d.get("study_minutes", 45)))

    def to_dict(self) -> dict:
        """What the Gist carries, so the relay applies the same schedule."""
        return {"timezone": self.tz.key, "time": self.remind_at.strftime("%H:%M"), "horizon_days": self.horizon,
                "study_time": self.study_at.strftime("%H:%M"), "study_minutes": self.study_minutes}

    @classmethod
    def from_payload(cls, d: dict) -> "Reminders":
        return cls.from_dict(d, d["timezone"])

    def fires(self, deadline: datetime) -> dict:
        """kind -> UTC fire time, for D<horizon> down to D2. Day counting uses the deadline's local calendar date."""
        date = deadline.astimezone(self.tz).date()
        return {
            f"D{n}": datetime.combine(date - timedelta(days=n), self.remind_at, tzinfo=self.tz).astimezone(timezone.utc)
            for n in range(2, self.horizon + 1)
        }

    def current(self, deadline: datetime, now: datetime) -> str | None:
        """The latest daily reminder whose time has passed, only while more than 24h remain (24H takes over)."""
        if deadline - now <= timedelta(hours=24):
            return None
        passed = [(fire, kind) for kind, fire in self.fires(deadline).items() if fire <= now]
        return max(passed)[1] if passed else None


def due_kind(deadline_iso: str, now: datetime) -> str | None:
    """The single most urgent deadline kind that currently applies, or None.

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


def current_kind(deadline_iso: str, now: datetime, rem: "Reminders | None" = None) -> str | None:
    """The one kind that is 'due right now': a deadline alert if one applies, else today's countdown reminder."""
    return due_kind(deadline_iso, now) or (rem.current(from_iso(deadline_iso), now) if rem else None)


def seq_id(task_id: int, kind: str) -> str:
    return f"t{task_id}-{kind.lower()}"


def alert_times(deadline: datetime, now: datetime, current: str | None = None,
                rem: "Reminders | None" = None) -> dict:
    """kind -> fire time for alerts ntfy can schedule now (inside its window, in the future).

    If `current` names a kind whose time has passed, it maps to `now`, meaning 'send immediately'.
    The relay passes no `current`, so it only ever schedules and never sends immediate alerts.
    """
    candidates = {kind: deadline - lead for kind, lead in LEAD.items()}
    if rem:
        candidates.update(rem.fires(deadline))
    out = {}
    for kind, fire in candidates.items():
        if fire - now > MIN_DELAY:
            if fire - now <= MAX_DELAY:
                out[kind] = fire
        elif kind == current:
            out[kind] = now
    return out


# ---- wording ----
def _tip(n: int, horizon: int) -> str:
    if n >= horizon:
        return "Skim the whole syllabus, list every module, and split them evenly across the days."
    if n >= 6:
        return "Take one module end to end, then write down 3 key takeaways."
    if n >= 3:
        return "Work through the modules in order, and start any quiz or project early."
    return "Finish the last modules today so tomorrow is a buffer for the final quiz or project."


def _clock(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def describe(kind: str, title: str, when_text: str, category: str, rem: "Reminders | None" = None
             ) -> tuple[str, str]:
    """(headline, markdown body) for any kind."""
    if kind in LEAD:
        body = "\n".join(x for x in (f"**{when_text}**" if when_text else "", category) if x) or title
        return f"{EMOJI[kind]} {TEXT[kind]}: {title}", body
    n = int(kind[1:])
    study = f"{rem.study_minutes} min at {_clock(rem.study_at)}" if rem else "a focused session today"
    horizon = rem.horizon if rem else 10
    headline = (f"🚀 {n}-day countdown starts: {title}" if n >= horizon else f"📚 {n} days left: {title}")
    lines = [
        f"**Today:** finish about 1/{n} of what's left of the course.",
        f"**When:** {study}.",
        f"**How:** {_tip(n, horizon)}",
        "",
        f"Due {when_text}" + (f" · {category}" if category else ""),
    ]
    return headline, "\n".join(lines)


def _mime(text: str) -> str:
    """RFC 2047 encoded-word, so any Unicode (emoji, accents) survives in an HTTP header."""
    return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def build_request(server: str, topic: str, task_id: int, kind: str, title: str, url: str,
                  fire: datetime, now: datetime, token: str | None = None, category: str = "",
                  when_text: str = "", app_url: str | None = None, icon_url: str | None = None,
                  rem: "Reminders | None" = None) -> tuple[str, dict, bytes]:
    """(url, headers, body) for one ntfy publish: emoji title, Markdown body, Open buttons, optional icon."""
    safe = ":/?&=#%+@!$'()*;~[]"  # no comma: it separates fields inside the Actions header
    actions = [f"view, Open course, {quote(url, safe=safe)}, clear=true"]
    if app_url:
        actions.append(f"view, Open tracker, {quote(app_url, safe=safe)}")
    headline, body = describe(kind, title, when_text, category, rem)
    headers = {
        "Title": _mime(headline[:120]),
        "Priority": PRIORITY.get(kind, "3"),
        "Tags": TAGS.get(kind, "books"),
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
    return (f"{server.rstrip('/')}/{topic}/{seq_id(task_id, kind)}", headers, body.encode("utf-8"))
