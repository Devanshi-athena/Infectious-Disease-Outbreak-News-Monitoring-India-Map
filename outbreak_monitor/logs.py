"""Logging to the console and to a rotating file in data/logs/."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "data" / "logs" / "collector.log"


def setup_logging(level: int = logging.INFO) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    console = logging.StreamHandler()
    for h in (file_handler, console):
        h.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[file_handler, console], force=True)
    for noisy in ("trafilatura", "htmldate", "courlan", "urllib3", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
