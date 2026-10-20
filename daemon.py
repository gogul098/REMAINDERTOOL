"""Periodic job (Task Scheduler runs this hourly via pythonw.exe)."""
import logging

from tracker.config import ConfigError, load_config
from tracker.logsetup import setup_logging
from tracker.runner import run_once

if __name__ == "__main__":
    setup_logging()
    log = logging.getLogger("tracker.daemon")
    try:
        log.info("daemon pass start")
        run_once(load_config())
        log.info("daemon pass done")
    except ConfigError as e:
        log.error("config error: %s", e)
    except Exception:  # noqa: BLE001
        log.exception("daemon crashed")
