import json
import sqlite3
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from email.header import decode_header, make_header
from zoneinfo import ZoneInfo

import pytest

from relay import relay
from tracker import alerts, db, mobile, notify, ntfy
from tracker.alerts import Reminders
from tracker.config import ConfigError, load_config
from tracker.timeutil import to_iso
from tests.test_phase3 import FakeHttp, posts

IST = ZoneInfo("Asia/Kolkata")
REM = Reminders(IST)  # 09:00 reminders, 10-day horizon, study 45 min at 19:00
DEADLINE = datetime(2030, 1, 20, 23, 59, tzinfo=IST).astimezone(timezone.utc)  # Sun 20 Jan, 11:59 PM IST
D = lambda n: datetime(2030, 1, 20 - n, 9, 0, tzinfo=IST).astimezone(timezone.utc)  # noqa: E731  fire time of Dn


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def cfg():
    return replace(load_config(), ntfy={"server": "https://ntfy.example", "topic": "x" * 24},
                   reminders={"enabled": True, "horizon_days": 10, "time": "09:00", "study_time": "19:00",
                              "study_minutes": 45})


def add(conn, guid="a", deadline=DEADLINE):
    db.upsert_bookmark(conn, guid, f"https://x.example/{guid}", f"Course {guid}", "Bookmarks bar/Courses")
    tid = conn.execute("SELECT id FROM tasks WHERE chrome_guid=?", (guid,)).fetchone()[0]
    db.activate(conn, tid, to_iso(deadline))
    return tid


def test_fires_one_per_day_at_local_time_counted_from_the_deadline_date():
    f = REM.fires(DEADLINE)
    assert list(f) == [f"D{n}" for n in range(2, 11)]
    assert f["D10"] == D(10) and f["D2"] == D(2)
    assert f["D10"].astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2030-01-10 09:00"


def test_current_is_latest_passed_reminder_and_stops_inside_24h():
    assert REM.current(DEADLINE, D(10) - timedelta(minutes=1)) is None
    assert REM.current(DEADLINE, D(10) + timedelta(minutes=1)) == "D10"
    assert REM.current(DEADLINE, D(9) + timedelta(hours=5)) == "D9"
    assert REM.current(DEADLINE, D(2) + timedelta(hours=20)) == "D2"  # still >24h from the deadline
    assert REM.current(DEADLINE, DEADLINE - timedelta(hours=23)) is None  # the 24H alert takes over


def test_kickoff_then_one_toast_per_day_each_once(conn):
    add(conn)
    sent = []
    send = lambda t, k: sent.append(k) or True  # noqa: E731
    notify.evaluate(conn, D(10) - timedelta(hours=1), send, REM)
    assert sent == []
    for n in (10, 9, 8):
        notify.evaluate(conn, D(n) + timedelta(minutes=5), send, REM)
        notify.evaluate(conn, D(n) + timedelta(hours=6), send, REM)  # later the same day: nothing new
    assert sent == ["D10", "D9", "D8"]


def test_waking_after_days_off_sends_only_todays_reminder(conn):
    add(conn)
    sent = []
    notify.evaluate(conn, D(6) + timedelta(hours=2), lambda t, k: sent.append(k) or True, REM)
    assert sent == ["D6"]


def test_daily_toasts_hand_over_to_24h_and_changing_deadline_rearms(conn):
    tid = add(conn)
    sent = []
    send = lambda t, k: sent.append(k) or True  # noqa: E731
    notify.evaluate(conn, D(2) + timedelta(minutes=1), send, REM)
    notify.evaluate(conn, DEADLINE - timedelta(hours=20), send, REM)
    assert sent == ["D2", "24H"]
    db.activate(conn, tid, to_iso(DEADLINE + timedelta(days=1)))  # extended a day: now a D3 moment
    notify.evaluate(conn, D(2) + timedelta(minutes=2), send, REM)
    assert sent == ["D2", "24H", "D3"]


def test_disabled_reminders_change_nothing(conn):
    add(conn)
    sent = []
    notify.evaluate(conn, D(10) + timedelta(minutes=5), lambda t, k: sent.append(k) or True, None)
    assert sent == []


def test_wording_tells_you_how_much_and_when():
    head, body = alerts.describe("D10", "Course A", "Sun 20 Jan, 11:59 PM", "Bookmarks bar", REM)
    assert "10-day countdown starts" in head and "Course A" in head
    assert "1/10" in body and "45 min at 7:00 PM" in body and "Skim the whole syllabus" in body
    head, body = alerts.describe("D2", "Course A", "Sun 20 Jan, 11:59 PM", "", REM)
    assert head.startswith("📚 2 days left") and "1/2" in body and "buffer" in body
    assert alerts._tip(7, 10).startswith("Take one module")
    assert alerts._tip(4, 10).startswith("Work through")


def test_ntfy_schedules_next_three_days_of_reminders_and_tops_up(conn, cfg):
    add(conn)
    now = D(10) - timedelta(hours=1)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, now, http)
    p = dict(posts(http))
    assert set(p) == {"t1-d10", "t1-d9", "t1-d8"}  # D7 is 73h away, beyond the 3-day window
    assert int(p["t1-d10"]) == int(D(10).timestamp())
    n = len(http.calls)
    ntfy.reconcile(cfg, conn, now + timedelta(minutes=10), http)
    assert len(http.calls) == n  # idempotent
    ntfy.reconcile(cfg, conn, now + timedelta(days=1), http)  # window moved on a day
    assert "t1-d7" in dict(posts(http))


def test_ntfy_sends_todays_reminder_immediately_once(conn, cfg):
    add(conn)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, D(8) + timedelta(hours=3), http)
    assert dict(posts(http))["t1-d8"] is None  # no At header: immediate
    ntfy.reconcile(cfg, conn, D(8) + timedelta(hours=4), http)
    assert [s for s, _ in posts(http)].count("t1-d8") == 1


def test_ntfy_reminder_message_is_rich_and_ascii_safe(conn, cfg):
    add(conn)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, D(8) + timedelta(hours=3), http)
    _, _, kw = next(c for c in http.calls if c[1].endswith("t1-d8"))
    for v in kw["headers"].values():
        v.encode("latin-1")
    assert str(make_header(decode_header(kw["headers"]["Title"]))).startswith("📚 8 days left: Course a")
    body = kw["data"].decode()
    assert "1/8" in body and "45 min at 7:00 PM" in body and "Sun 20 Jan" in body


def test_completing_cancels_pending_reminders(conn, cfg):
    tid = add(conn)
    http = FakeHttp()
    now = D(10) - timedelta(hours=1)
    ntfy.reconcile(cfg, conn, now, http)
    db.complete(conn, tid)
    http.calls.clear()
    ntfy.reconcile(cfg, conn, now, http)
    gone = sorted(u.rsplit("/", 1)[1] for m, u, _ in http.calls if m == "DELETE")
    assert {"t1-d10", "t1-d9", "t1-d8"} <= set(gone)


def test_relay_follows_the_same_plan_from_the_gist_settings(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(mobile, "get_secret", lambda n: None)
    add(conn)
    payload = json.loads(mobile.build_payload(conn, cfg))
    assert payload["settings"]["time"] == "09:00" and payload["settings"]["horizon_days"] == 10
    now = D(10) - timedelta(hours=1)
    posted = []
    rem = Reminders.from_payload(payload["settings"])
    relay.run(payload["tasks"], "https://n.example", "t" * 24, None, now,
              lambda url, **kw: posted.append((url.rsplit("/", 1)[1], kw["headers"]["At"])) or type(
                  "R", (), {"raise_for_status": lambda self: None})(), rem=rem)
    laptop = FakeHttp()
    ntfy.reconcile(cfg, conn, now, laptop)
    assert sorted(posted) == sorted(posts(laptop))


def test_payload_omits_settings_when_disabled(conn, cfg):
    off = replace(cfg, reminders={"enabled": False})
    add(conn)
    assert "settings" not in json.loads(mobile.build_payload(conn, off))


def test_config_validation(tmp_path):
    base = json.loads(open("config.example.json", encoding="utf-8").read())
    for bad in ({"time": "9am"}, {"horizon_days": 1}, {"study_time": "x"}):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({**base, "reminders": bad}))
        with pytest.raises(ConfigError):
            load_config(p)


def test_migration_v3_keeps_old_rows_and_accepts_daily_kinds(tmp_path):
    c = sqlite3.connect(tmp_path / "old.db")
    for i in range(2):  # build a v2 database the way an existing install would have it
        c.executescript("BEGIN;\n" + db.MIGRATIONS[i] + f"\nPRAGMA user_version={i + 1};\nCOMMIT;")
    c.execute("INSERT INTO tasks (url,title,created_at,updated_at) VALUES ('u','t','x','x')")
    c.execute("INSERT INTO notifications (task_id,kind,deadline_utc_at_send,sent_at) VALUES (1,'24H','d','s')")
    c.execute("INSERT INTO ntfy_schedule VALUES (1,'2H','d','f')")
    c.commit()
    db.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert c.execute("SELECT kind FROM notifications").fetchall() == [("24H",)]
    assert c.execute("SELECT kind FROM ntfy_schedule").fetchall() == [("2H",)]
    c.execute("INSERT INTO notifications (task_id,kind,deadline_utc_at_send,sent_at) VALUES (1,'D7','d','s')")
    c.execute("INSERT INTO ntfy_schedule VALUES (1,'D7','d','f')")
