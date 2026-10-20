from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from tracker import db, notify, wallpaper
from tracker.timeutil import to_iso

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def add(conn, guid, hours_from_now):
    db.upsert_bookmark(conn, guid, f"https://x.example/{guid}", guid, "Bookmarks bar/Courses")
    tid = conn.execute("SELECT id FROM tasks WHERE chrome_guid=?", (guid,)).fetchone()[0]
    db.activate(conn, tid, to_iso(NOW + timedelta(hours=hours_from_now)))
    return tid


@pytest.mark.parametrize("hours,kind", [(48, None), (24.5, None), (24, "24H"), (3, "24H"),
                                         (2, "2H"), (0.5, "2H"), (0, "OVERDUE"), (-5, "OVERDUE")])
def test_due_kind_boundaries(hours, kind):
    assert notify.due_kind(to_iso(NOW + timedelta(hours=hours)), NOW) == kind


def test_each_kind_fires_once(conn):
    add(conn, "a", 20)
    sent = []
    send = lambda t, k: sent.append(k) or True  # noqa: E731
    notify.evaluate(conn, NOW, send)
    notify.evaluate(conn, NOW + timedelta(minutes=30), send)
    assert sent == ["24H"]
    notify.evaluate(conn, NOW + timedelta(hours=19), send)  # now 1h left
    notify.evaluate(conn, NOW + timedelta(hours=19, minutes=10), send)
    notify.evaluate(conn, NOW + timedelta(hours=21), send)  # overdue
    notify.evaluate(conn, NOW + timedelta(hours=22), send)
    assert sent == ["24H", "2H", "OVERDUE"]


def test_waking_late_sends_only_the_current_kind(conn):
    add(conn, "a", 1)  # laptop opened with 1h left
    sent = []
    notify.evaluate(conn, NOW, lambda t, k: sent.append(k) or True)
    assert sent == ["2H"]


def test_failed_send_retries_next_run(conn):
    add(conn, "a", 1)
    notify.evaluate(conn, NOW, lambda t, k: False)
    sent = []
    notify.evaluate(conn, NOW, lambda t, k: sent.append(k) or True)
    assert sent == ["2H"]


def test_changing_deadline_rearms(conn):
    tid = add(conn, "a", 1)
    sent = []
    send = lambda t, k: sent.append(k) or True  # noqa: E731
    notify.evaluate(conn, NOW, send)
    db.extend(conn, tid, 0)  # no-op keeps one alert
    notify.evaluate(conn, NOW, send)
    assert sent == ["2H"]
    db.activate(conn, tid, to_iso(NOW + timedelta(hours=1.5)))  # moved
    notify.evaluate(conn, NOW, send)
    assert sent == ["2H", "2H"]


def test_non_active_tasks_never_notify(conn):
    tid = add(conn, "a", 1)
    db.complete(conn, tid)
    sent = []
    notify.evaluate(conn, NOW, lambda t, k: sent.append(k) or True)
    assert sent == []


def test_fit_cover_exact_size_for_portrait_source():
    out = wallpaper.fit_cover(Image.new("RGB", (736, 1041)), (1920, 1080), 0.35)
    assert out.size == (1920, 1080)


def test_render_smoke_and_empty_returns_clean_base(conn):
    base = Image.new("RGB", (736, 1041), (200, 80, 90))
    for i, h in enumerate([-3, 5, 30, 100, 200, 300, 400, 500]):
        add(conn, f"t{i}" + "x" * 80, h)
    rows = db.list_tasks(conn, "ACTIVE")
    img = wallpaper.render(base, rows, IST, NOW, (1920, 1080))
    clean = wallpaper.render(base, [], IST, NOW, (1920, 1080))
    assert img.size == clean.size == (1920, 1080)
    assert img.tobytes() != clean.tobytes()
    assert clean.getpixel((10, 10)) == wallpaper.fit_cover(base, (1920, 1080), 0.35).getpixel((10, 10))


def test_colors_by_urgency():
    c = lambda h: wallpaper._color(to_iso(NOW + timedelta(hours=h)), NOW)  # noqa: E731
    assert c(-1) == c(23) == wallpaper.RED
    assert c(48) == wallpaper.AMBER
    assert c(100) == wallpaper.GREEN
