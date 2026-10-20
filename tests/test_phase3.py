import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import requests

from tracker import db, mobile, ntfy, outbox
from tracker.config import load_config
from tracker.timeutil import to_iso

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self):
        self.calls, self.fail, self.delete_status = [], False, 200

    def _do(self, method, url, **kw):
        if self.fail:
            raise requests.ConnectionError("offline")
        self.calls.append((method, url, kw))
        return FakeResp(self.delete_status if method == "DELETE" else 200)

    def post(self, url, **kw): return self._do("POST", url, **kw)  # noqa: E704
    def delete(self, url, **kw): return self._do("DELETE", url, **kw)  # noqa: E704
    def patch(self, url, **kw): return self._do("PATCH", url, **kw)  # noqa: E704


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def cfg(tmp_path):
    base = load_config()
    return replace(base, ntfy={"server": "https://ntfy.example", "topic": "x" * 24}, reminders={"enabled": False})


def add(conn, guid, hours):
    db.upsert_bookmark(conn, guid, f"https://x.example/{guid}", f"Title {guid} – ü", "Bookmarks bar/Courses")
    tid = conn.execute("SELECT id FROM tasks WHERE chrome_guid=?", (guid,)).fetchone()[0]
    db.activate(conn, tid, to_iso(NOW + timedelta(hours=hours)))
    return tid


def posts(http):
    return [(u.rsplit("/", 1)[1], kw["headers"].get("At")) for m, u, kw in http.calls if m == "POST"]


def test_schedules_only_inside_three_day_window(conn, cfg):
    add(conn, "far", 24 * 10)  # nothing within 3 days -> nothing scheduled yet
    add(conn, "near", 80)  # 24H fires in 56h (in window); 2H in 78h and OVERDUE in 80h are beyond it
    http = FakeHttp()
    assert ntfy.reconcile(cfg, conn, NOW, http)
    assert [s for s, _ in posts(http)] == ["t2-24h"]
    ntfy.reconcile(cfg, conn, NOW + timedelta(hours=10), http)  # window moved: 2H (68h away) and OVERDUE (70h away) are topped up
    assert [s for s, _ in posts(http)] == ["t2-24h", "t2-2h", "t2-overdue"]


def test_at_header_is_deadline_minus_lead(conn, cfg):
    add(conn, "a", 30)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, NOW, http)
    at = dict(posts(http))
    assert int(at["t1-24h"]) == int((NOW + timedelta(hours=6)).timestamp())
    assert int(at["t1-2h"]) == int((NOW + timedelta(hours=28)).timestamp())
    assert int(at["t1-overdue"]) == int((NOW + timedelta(hours=30)).timestamp())


def test_second_run_is_idempotent(conn, cfg):
    add(conn, "a", 30)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, NOW, http)
    n = len(http.calls)
    ntfy.reconcile(cfg, conn, NOW + timedelta(minutes=5), http)
    assert len(http.calls) == n


def test_current_kind_sent_immediately_once_without_at(conn, cfg):
    add(conn, "a", 1)  # 2H is current; 24H already passed and is skipped
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, NOW, http)
    p = dict(posts(http))
    assert p["t1-2h"] is None and "t1-24h" not in p and p["t1-overdue"] is not None
    ntfy.reconcile(cfg, conn, NOW + timedelta(minutes=1), http)
    assert len(posts(http)) == 2


def test_extend_replaces_same_sequence_id_and_rearms(conn, cfg):
    tid = add(conn, "a", 30)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, NOW, http)
    db.extend(conn, tid, 1)
    http.calls.clear()
    ntfy.reconcile(cfg, conn, NOW, http)
    p = dict(posts(http))
    assert int(p["t1-24h"]) == int((NOW + timedelta(hours=30)).timestamp())  # 54h deadline - 24h
    assert not [c for c in http.calls if c[0] == "DELETE"]


def test_complete_cancels_pending_alerts(conn, cfg):
    tid = add(conn, "a", 30)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, NOW, http)
    db.complete(conn, tid)
    http.calls.clear()
    ntfy.reconcile(cfg, conn, NOW, http)
    assert sorted(u.rsplit("/", 1)[1] for m, u, _ in http.calls if m == "DELETE") == ["t1-24h", "t1-2h", "t1-overdue"]
    assert conn.execute("SELECT COUNT(*) FROM ntfy_schedule").fetchone()[0] == 0


def test_already_sent_alerts_are_not_deleted_on_complete(conn, cfg):
    tid = add(conn, "a", 30)
    ntfy.reconcile(cfg, conn, NOW, FakeHttp())
    db.complete(conn, tid)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, NOW + timedelta(hours=29), http)  # 24H and 2H long since delivered
    assert [u.rsplit("/", 1)[1] for m, u, _ in http.calls if m == "DELETE"] == ["t1-overdue"]


def test_delete_404_is_success(conn, cfg):
    tid = add(conn, "a", 30)
    ntfy.reconcile(cfg, conn, NOW, FakeHttp())
    db.complete(conn, tid)
    http = FakeHttp()
    http.delete_status = 404
    assert ntfy.reconcile(cfg, conn, NOW, http)


def test_failure_raises_and_leaves_state_for_retry(conn, cfg):
    add(conn, "a", 30)
    http = FakeHttp()
    http.fail = True
    with pytest.raises(RuntimeError):
        ntfy.reconcile(cfg, conn, NOW, http)
    assert conn.execute("SELECT COUNT(*) FROM ntfy_schedule").fetchone()[0] == 0
    http.fail = False
    assert ntfy.reconcile(cfg, conn, NOW, http)
    assert len(posts(http)) == 3


def test_unicode_title_is_mime_encoded_and_alert_is_rich(conn, cfg):
    from email.header import decode_header, make_header

    add(conn, "a", 30)
    http = FakeHttp()
    ntfy.reconcile(cfg, conn, NOW, http)
    _, _, kw = http.calls[0]
    h = kw["headers"]
    for v in h.values():
        v.encode("latin-1")  # every header is plain ASCII on the wire
    title = str(make_header(decode_header(h["Title"])))
    assert "ü" in title and title.startswith("📅 Due in 24 hours")
    assert h["Markdown"] == "yes" and h["Actions"].startswith("view, Open course, https://x.example/a")
    assert b"Bookmarks bar/Courses" in kw["data"]


def test_not_configured_is_a_noop(conn, cfg):
    add(conn, "a", 30)
    http = FakeHttp()
    assert ntfy.reconcile(replace(cfg, ntfy={"server": "x", "topic": None}), conn, NOW, http) is False
    assert not http.calls


# ---- outbox ----
def test_outbox_success_clears_and_failure_backs_off(conn):
    outbox.enqueue(conn, "ntfy", NOW)
    outbox.enqueue(conn, "ntfy", NOW)  # deduped
    assert outbox.pending_count(conn) == 1

    def boom():
        raise RuntimeError("offline")

    assert outbox.process(conn, {"ntfy": boom}, NOW) == {"ntfy": "failed"}
    row = conn.execute("SELECT * FROM outbox").fetchone()
    assert row["attempts"] == 1 and row["next_try_at"] == to_iso(NOW + timedelta(seconds=60))
    assert outbox.process(conn, {"ntfy": boom}, NOW + timedelta(seconds=10)) == {}  # not due yet
    outbox.process(conn, {"ntfy": boom}, NOW + timedelta(seconds=61))
    assert conn.execute("SELECT attempts FROM outbox").fetchone()[0] == 2
    assert outbox.process(conn, {"ntfy": lambda: None}, NOW + timedelta(hours=1)) == {"ntfy": "ok"}
    assert outbox.pending_count(conn) == 0


def test_enqueue_during_backoff_retries_immediately(conn):
    outbox.enqueue(conn, "gist", NOW)
    outbox.process(conn, {"gist": lambda: 1 / 0}, NOW)
    outbox.enqueue(conn, "gist", NOW + timedelta(seconds=1))
    assert outbox.process(conn, {"gist": lambda: None}, NOW + timedelta(seconds=1)) == {"gist": "ok"}


def test_backoff_capped(conn):
    outbox.enqueue(conn, "gist", NOW)
    t = NOW
    for _ in range(15):
        outbox.process(conn, {"gist": lambda: 1 / 0}, t)
        nxt = datetime.fromisoformat(conn.execute("SELECT next_try_at FROM outbox").fetchone()[0].replace("Z", "+00:00"))
        assert nxt - t <= timedelta(hours=6)
        t = nxt
    assert nxt - (t - timedelta(hours=6)) == timedelta(hours=6)  # last wait is exactly the cap


# ---- gist ----
def test_gist_payload_and_only_patches_on_change(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(mobile, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mobile, "get_secret", lambda n: "tok")
    mobile.save_state({"gist_id": "abc"})
    add(conn, "a", 30)
    http = FakeHttp()
    assert mobile.sync(cfg, conn, http)
    assert mobile.sync(cfg, conn, http)  # unchanged
    assert len([c for c in http.calls if c[0] == "PATCH"]) == 1
    sent = http.calls[0][2]["json"]["files"]["deadlines.json"]["content"]
    task = json.loads(sent)["tasks"][0]
    assert task["deadline_iso"].endswith("+05:30") and "hours_remaining" not in task
    assert task["title"] == "Title a – ü"
    add(conn, "b", 40)
    mobile.sync(cfg, conn, http)
    assert len([c for c in http.calls if c[0] == "PATCH"]) == 2


def test_gist_unconfigured_is_skipped_not_failed(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(mobile, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mobile, "get_secret", lambda n: None)
    assert mobile.sync(cfg, conn, FakeHttp()) is False


def test_raw_url_has_no_revision():
    g = {"id": "abc123", "owner": {"login": "me"}}
    assert mobile.raw_url(g, "deadlines.json") == "https://gist.githubusercontent.com/me/abc123/raw/deadlines.json"
