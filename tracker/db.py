"""SQLite access: WAL, busy_timeout, versioned migrations, CRUD."""
import sqlite3
from datetime import timedelta
from pathlib import Path

from .timeutil import from_iso, now_utc, to_iso

MIGRATIONS = [
    # v1
    """
    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY,
        chrome_guid TEXT UNIQUE,
        url TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL,
        title_overridden INTEGER NOT NULL DEFAULT 0,
        deadline_utc TEXT,
        status TEXT NOT NULL DEFAULT 'UNSCHEDULED'
            CHECK (status IN ('UNSCHEDULED','ACTIVE','COMPLETED','IGNORED')),
        source_missing INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE INDEX idx_tasks_url ON tasks(url);
    CREATE INDEX idx_tasks_status ON tasks(status);

    CREATE TABLE notifications (
        id INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('24H','2H','OVERDUE')),
        deadline_utc_at_send TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        UNIQUE (task_id, kind, deadline_utc_at_send)
    );

    CREATE TABLE outbox (
        id INTEGER PRIMARY KEY,
        target TEXT NOT NULL CHECK (target IN ('gist','ntfy','wallpaper')),
        payload TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_try_at TEXT NOT NULL
    );
    """,
    # v2: what has been scheduled/sent on ntfy, so reconcile can diff, cancel and re-arm
    """
    CREATE TABLE ntfy_schedule (
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('24H','2H','OVERDUE')),
        deadline_utc TEXT NOT NULL,
        fire_at TEXT NOT NULL,
        PRIMARY KEY (task_id, kind)
    );
    """,
    # v3: daily countdown reminders add kinds D2..D30, so drop the fixed list of kinds on both tables
    """
    CREATE TABLE notifications_new (
        id INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        deadline_utc_at_send TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        UNIQUE (task_id, kind, deadline_utc_at_send)
    );
    INSERT INTO notifications_new SELECT id, task_id, kind, deadline_utc_at_send, sent_at FROM notifications;
    DROP TABLE notifications;
    ALTER TABLE notifications_new RENAME TO notifications;

    CREATE TABLE ntfy_schedule_new (
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        deadline_utc TEXT NOT NULL,
        fire_at TEXT NOT NULL,
        PRIMARY KEY (task_id, kind)
    );
    INSERT INTO ntfy_schedule_new SELECT task_id, kind, deadline_utc, fire_at FROM ntfy_schedule;
    DROP TABLE ntfy_schedule;
    ALTER TABLE ntfy_schedule_new RENAME TO ntfy_schedule;
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i in range(version, len(MIGRATIONS)):
        conn.executescript("BEGIN;\n" + MIGRATIONS[i] + f"\nPRAGMA user_version={i + 1};\nCOMMIT;")


def _now() -> str:
    return to_iso(now_utc())


def upsert_bookmark(conn, guid: str, url: str, title: str, category: str) -> None:
    """Category and URL always follow Chrome; title follows unless overridden."""
    now = _now()
    row = conn.execute("SELECT id FROM tasks WHERE chrome_guid=?", (guid,)).fetchone()
    if row is None:
        # A re-created bookmark may carry a new guid; adopt an orphaned row with the same URL.
        row = conn.execute(
            "SELECT id FROM tasks WHERE url=? AND source_missing=1 ORDER BY id LIMIT 1", (url,)
        ).fetchone()
        if row is not None:
            conn.execute("UPDATE tasks SET chrome_guid=? WHERE id=?", (guid, row["id"]))
    if row is None:
        conn.execute(
            "INSERT INTO tasks (chrome_guid,url,category,title,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (guid, url, category, title, now, now),
        )
        return
    conn.execute(
        """UPDATE tasks SET url=?, category=?, source_missing=0, updated_at=?,
           title = CASE WHEN title_overridden=1 THEN title ELSE ? END
           WHERE id=?""",
        (url, category, now, title, row["id"]),
    )


def mark_missing(conn, seen_guids: set[str]) -> int:
    rows = conn.execute("SELECT id, chrome_guid FROM tasks WHERE source_missing=0").fetchall()
    gone = [r["id"] for r in rows if r["chrome_guid"] not in seen_guids]
    conn.executemany("UPDATE tasks SET source_missing=1, updated_at=? WHERE id=?", [(_now(), i) for i in gone])
    return len(gone)


def list_tasks(conn, status: str, include_missing: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM tasks WHERE status=?"
    if not include_missing:
        q += " AND source_missing=0"
    q += " ORDER BY deadline_utc IS NULL, deadline_utc, category, title"
    return conn.execute(q, (status,)).fetchall()


def get_task(conn, task_id: int):
    return conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def set_title(conn, task_id: int, title: str) -> None:
    cur = conn.execute("SELECT title FROM tasks WHERE id=?", (task_id,)).fetchone()
    if cur is None or cur["title"] == title:
        return
    conn.execute(
        "UPDATE tasks SET title=?, title_overridden=1, updated_at=? WHERE id=?", (title, _now(), task_id)
    )


def activate(conn, task_id: int, deadline_utc: str) -> None:
    conn.execute(
        "UPDATE tasks SET status='ACTIVE', deadline_utc=?, completed_at=NULL, updated_at=? WHERE id=?",
        (deadline_utc, _now(), task_id),
    )


def extend(conn, task_id: int, days: int = 1) -> None:
    row = get_task(conn, task_id)
    if row is None or row["deadline_utc"] is None:
        return
    new = to_iso(from_iso(row["deadline_utc"]) + timedelta(days=days))
    conn.execute("UPDATE tasks SET deadline_utc=?, updated_at=? WHERE id=?", (new, _now(), task_id))


def complete(conn, task_id: int) -> None:
    now = _now()
    conn.execute(
        "UPDATE tasks SET status='COMPLETED', completed_at=?, updated_at=? WHERE id=?", (now, now, task_id)
    )


def ignore(conn, task_id: int) -> None:
    conn.execute("UPDATE tasks SET status='IGNORED', updated_at=? WHERE id=?", (_now(), task_id))


def restore(conn, task_id: int) -> None:
    """Undo Complete/Ignore: back to ACTIVE if it has a deadline, else UNSCHEDULED."""
    conn.execute(
        """UPDATE tasks SET completed_at=NULL, updated_at=?,
           status = CASE WHEN deadline_utc IS NULL THEN 'UNSCHEDULED' ELSE 'ACTIVE' END WHERE id=?""",
        (_now(), task_id),
    )


def unschedule(conn, task_id: int) -> None:
    conn.execute(
        "UPDATE tasks SET status='UNSCHEDULED', deadline_utc=NULL, updated_at=? WHERE id=?", (_now(), task_id)
    )
