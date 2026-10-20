"""All times are stored as UTC ISO 8601 strings and displayed in the configured zone."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local_to_iso(naive_local: datetime, tz: ZoneInfo) -> str:
    """Interpret a naive wall-clock datetime in tz and return UTC ISO."""
    return to_iso(naive_local.replace(tzinfo=tz))


def iso_to_local(s: str, tz: ZoneInfo) -> datetime:
    return from_iso(s).astimezone(tz)


def format_local(s: str, tz: ZoneInfo) -> str:
    return iso_to_local(s, tz).strftime("%a %d %b %Y, %I:%M %p")


def countdown(s: str, now: datetime | None = None) -> str:
    secs = int((from_iso(s) - (now or now_utc())).total_seconds())
    sign = "overdue " if secs < 0 else ""
    secs = abs(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{sign}{d}d {h}h"
    if h:
        return f"{sign}{h}h {m}m"
    return f"{sign}{m}m"
