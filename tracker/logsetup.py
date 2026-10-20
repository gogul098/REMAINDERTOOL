import logging
from logging.handlers import RotatingFileHandler

from .config import ROOT


def setup_logging(name: str = "tracker") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    (ROOT / "logs").mkdir(exist_ok=True)
    h = RotatingFileHandler(ROOT / "logs" / "tracker.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(h)
    return log
