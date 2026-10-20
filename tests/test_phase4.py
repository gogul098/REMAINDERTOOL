from datetime import datetime, timedelta, timezone

import requests

from relay import relay
from tracker import alerts, backup, db

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


class Posts:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.fail:
            raise requests.ConnectionError("down")

        class R:
            def raise_for_status(self): pass  # noqa: E704
        return R()


def task(tid, hours, title="Título"):
    deadline = (NOW + timedelta(hours=hours)).astimezone(timezone(timedelta(hours=5, minutes=30)))
    return {"id": tid, "title": title, "url": "https://x.example/a b", "category": "c",
            "deadline_iso": deadline.isoformat()}


def test_relay_uses_same_sequence_ids_and_times_as_laptop():
    post = Posts()
    assert relay.run([task(7, 30)], "https://ntfy.example", "t" * 24, None, NOW, post) == 0
    urls = {u.rsplit("/", 1)[1]: kw["headers"]["At"] for u, kw in post.calls}
    assert urls == {alerts.seq_id(7, k): str(int((NOW + timedelta(hours=30) - lead).timestamp()))
                    for k, lead in alerts.LEAD.items()}


def test_relay_never_sends_immediately_and_skips_far_tasks():
    post = Posts()
    relay.run([task(1, 1), task(2, 24 * 20)], "https://ntfy.example", "t" * 24, None, NOW, post)
    seqs = [u.rsplit("/", 1)[1] for u, _ in post.calls]
    assert seqs == ["t1-overdue"]  # 24H/2H already passed for task 1; task 2 is outside the window
    assert all("At" in kw["headers"] for _, kw in post.calls)


def test_relay_reports_failures_and_skips_tasks_without_id():
    assert relay.run([task(1, 30)], "https://n.example", "t" * 24, None, NOW, Posts(fail=True)) == 3
    legacy = task(1, 30)
    del legacy["id"]
    post = Posts()
    assert relay.run([legacy], "https://n.example", "t" * 24, None, NOW, post) == 0 and not post.calls


def test_relay_request_is_latin1_safe_and_authenticated():
    url, headers, body = alerts.build_request("https://n.example/", "topic", 3, "2H", "Título ü", "https://x.example/a b",
                                              NOW + timedelta(hours=5), NOW, "tk_abc")
    assert url == "https://n.example/topic/t3-2h" and headers["Authorization"] == "Bearer tk_abc"
    for v in headers.values():
        v.encode("latin-1")
    assert "Título ü".encode() in body


def test_backup_creates_once_per_day_and_keeps_last_n(tmp_path):
    path = tmp_path / "courses.db"
    c = db.connect(path)
    db.upsert_bookmark(c, "g", "https://x.example", "X", "c")
    c.commit()
    c.close()
    for i in range(20):
        backup.backup_daily(path, datetime(2030, 1, 1) + timedelta(days=i), keep=14)
    files = sorted((tmp_path / "backups").glob("courses-*.db"))
    assert len(files) == 14 and files[-1].name == "courses-2030-01-20.db"
    assert backup.backup_daily(path, datetime(2030, 1, 20)) is None  # same day: no second backup
    restored = db.connect(files[-1])
    assert restored.execute("SELECT title FROM tasks").fetchone()[0] == "X"
    assert not list((tmp_path / "backups").glob("*.tmp"))
