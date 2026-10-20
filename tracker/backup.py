"""Daily database backup using SQLite's online backup API (safe while WAL is active)."""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger("tracker.backup")
KEEP = 14


def backup_daily(db_path: Path, now: datetime, keep: int = KEEP) -> Path | None:
    """Create today's backup if missing and prune to the newest `keep`. Returns the new file, or None."""
    folder = Path(db_path).parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"courses-{now.strftime('%Y-%m-%d')}.db"
    created = None
    if not target.exists():
        tmp = target.with_suffix(".tmp")
        src, dst = sqlite3.connect(db_path), sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        tmp.replace(target)
        created = target
        log.info("backup written: %s", target.name)
    for old in sorted(folder.glob("courses-*.db"))[:-keep]:  # ISO dates sort chronologically
        old.unlink()
    return created
