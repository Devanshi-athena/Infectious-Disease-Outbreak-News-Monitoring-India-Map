"""Run collection immediately and then every `refresh_interval_minutes` (config/diseases.yaml).

Run:  python -m outbreak_monitor.scheduler      (Ctrl+C to stop)

Keyword and source changes apply from the next run; a new interval needs a restart.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from .collector import run_and_report
from .config import load_disease_config
from .logs import setup_logging

log = logging.getLogger(__name__)


def _job():
    try:
        run_and_report()
    except Exception:  # keep the scheduler alive whatever happens in one run
        log.exception("Scheduled collection failed")


def main():
    setup_logging()
    minutes = load_disease_config().settings.refresh_interval_minutes
    scheduler = BlockingScheduler()
    # max_instances=1: never start a run while the previous one is still going;
    # coalesce: if runs were missed (e.g. laptop asleep), do just one catch-up run.
    scheduler.add_job(_job, "interval", minutes=minutes, next_run_time=datetime.now(),
                      max_instances=1, coalesce=True)
    log.info("Scheduler started: collecting every %d minutes", minutes)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
