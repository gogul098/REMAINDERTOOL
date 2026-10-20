import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tracker import db, extractor
from tracker.timeutil import countdown, format_local, from_iso, local_to_iso, to_iso

BOOKMARKS = {
    "roots": {
        "bookmark_bar": {
            "type": "folder", "name": "Bookmarks bar",
            "children": [
                {"type": "folder", "name": "Courses", "children": [
                    {"type": "url", "guid": "g1", "name": "Algo", "url": "https://a.example/algo"},
                    {"type": "folder", "name": "Math", "children": [
                        {"type": "url", "guid": "g2", "name": "Calc", "url": "https://a.example/calc"}]},
                ]},
                {"type": "url", "guid": "g3", "name": "News", "url": "https://news.example"},
                {"type": "url", "guid": "g4", "name": "JS", "url": "javascript:void(0)"},
            ],
        },
        "other": {"type": "folder", "name": "Other bookmarks", "children": []},
    }
}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def sync(conn, folders):
    seen = set()
    with conn:
        for guid, url, title, cat in extractor.iter_bookmarks(BOOKMARKS, folders):
            seen.add(guid)
            db.upsert_bookmark(conn, guid, url, title, cat)
        db.mark_missing(conn, seen)


def test_migration_idempotent(tmp_path):
    p = tmp_path / "m.db"
    db.connect(p).close()
    c = db.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_folder_filter_includes_subfolders_only_http():
    got = {g for g, *_ in extractor.iter_bookmarks(BOOKMARKS, ["Bookmarks bar/Courses"])}
    assert got == {"g1", "g2"}
    assert {g for g, *_ in extractor.iter_bookmarks(BOOKMARKS, "ALL")} == {"g1", "g2", "g3"}


def test_category_is_full_path():
    cats = {g: c for g, _, _, c in extractor.iter_bookmarks(BOOKMARKS, "ALL")}
    assert cats["g2"] == "Bookmarks bar/Courses/Math"


def test_resync_keeps_deadline_and_title_override(conn):
    sync(conn, "ALL")
    tid = db.list_tasks(conn, "UNSCHEDULED")[0]["id"]
    with conn:
        db.set_title(conn, tid, "My title")
        db.activate(conn, tid, "2030-01-01T00:00:00Z")
    BOOKMARKS["roots"]["bookmark_bar"]["children"][0]["children"][0]["name"] = "Renamed in Chrome"
    sync(conn, "ALL")
    row = db.get_task(conn, tid)
    assert row["status"] == "ACTIVE" and row["deadline_utc"] == "2030-01-01T00:00:00Z"
    assert row["title"] == "My title"
    BOOKMARKS["roots"]["bookmark_bar"]["children"][0]["children"][0]["name"] = "Algo"


def test_untouched_title_follows_chrome(conn):
    sync(conn, "ALL")
    db.upsert_bookmark(conn, "g1", "https://a.example/algo", "New name", "Bookmarks bar/Courses")
    assert conn.execute("SELECT title FROM tasks WHERE chrome_guid='g1'").fetchone()[0] == "New name"


def test_missing_bookmark_hidden_not_deleted_then_restored(conn):
    sync(conn, "ALL")
    sync(conn, ["Bookmarks bar/Courses"])  # g3 now out of scope
    assert len(db.list_tasks(conn, "UNSCHEDULED")) == 2
    assert len(db.list_tasks(conn, "UNSCHEDULED", include_missing=True)) == 3
    sync(conn, "ALL")
    assert len(db.list_tasks(conn, "UNSCHEDULED")) == 3


def test_recreated_bookmark_adopts_old_row(conn):
    db.upsert_bookmark(conn, "old", "https://x.example", "X", "c")
    db.mark_missing(conn, set())
    db.upsert_bookmark(conn, "new", "https://x.example", "X", "c")
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_extend_complete_restore(conn):
    db.upsert_bookmark(conn, "g", "https://x.example", "X", "c")
    tid = conn.execute("SELECT id FROM tasks").fetchone()[0]
    db.activate(conn, tid, "2030-01-01T00:00:00Z")
    db.extend(conn, tid, 1)
    assert db.get_task(conn, tid)["deadline_utc"] == "2030-01-02T00:00:00Z"
    db.complete(conn, tid)
    assert db.get_task(conn, tid)["completed_at"]
    db.restore(conn, tid)
    assert db.get_task(conn, tid)["status"] == "ACTIVE"
    db.ignore(conn, tid)
    db.unschedule(conn, tid)
    db.ignore(conn, tid)
    db.restore(conn, tid)
    assert db.get_task(conn, tid)["status"] == "UNSCHEDULED"


def test_status_check_constraint(conn):
    db.upsert_bookmark(conn, "g", "https://x.example", "X", "c")
    with pytest.raises(Exception):
        conn.execute("UPDATE tasks SET status='BOGUS'")


def test_timezone_roundtrip_ist():
    ist = ZoneInfo("Asia/Kolkata")
    iso = local_to_iso(datetime(2030, 6, 1, 23, 59), ist)
    assert iso == "2030-06-01T18:29:00Z"
    assert format_local(iso, ist).endswith("11:59 PM")


def test_dst_handling():
    ny = ZoneInfo("America/New_York")
    assert local_to_iso(datetime(2030, 1, 15, 12, 0), ny) == "2030-01-15T17:00:00Z"  # EST
    assert local_to_iso(datetime(2030, 7, 15, 12, 0), ny) == "2030-07-15T16:00:00Z"  # EDT


def test_countdown():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert countdown("2030-01-03T05:00:00Z", now) == "2d 5h"
    assert countdown("2030-01-01T02:30:00Z", now) == "2h 30m"
    assert countdown("2029-12-31T23:00:00Z", now) == "overdue 1h 0m"
    assert to_iso(from_iso("2030-01-01T00:00:00Z")) == "2030-01-01T00:00:00Z"


def test_read_bookmarks_file(tmp_path):
    p = tmp_path / "Bookmarks"
    p.write_text(json.dumps(BOOKMARKS), encoding="utf-8")
    assert extractor.read_bookmarks_file(p) == BOOKMARKS


def test_list_chrome_profiles(tmp_path):
    (tmp_path / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 1": {"name": "Me", "user_name": "me@x.com"}}}})
    )
    assert extractor.list_chrome_profiles(tmp_path) == [{"folder": "Profile 1", "name": "Me", "email": "me@x.com"}]
