from __future__ import annotations
from apscheduler.schedulers.blocking import BlockingScheduler as BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import logging

from .config import load_config
from .jobs import summarize_job

logger = logging.getLogger("mailbot")


def start_scheduler():
    cfg = load_config()
    tzname = cfg.get('timezone', 'Asia/Shanghai')
    tz = pytz.timezone(tzname)

    sch = BackgroundScheduler(timezone=tz)

    # summarize crons
    def _run_summarize():
        logger.info("Scheduler tick → summarize job triggered")
        summarize_job(cfg)

    for spec in cfg.get('summarize', {}).get('cron', ['0 7 * * *','0 12 * * *','0 19 * * *']):
        sch.add_job(_run_summarize, CronTrigger.from_crontab(spec, timezone=tz))

    # run once immediately before scheduling loop
    logger.info('Scheduler warm-up: run summarize once now')
    try:
        _run_summarize()
    except Exception as e:
        logger.info(f"Warm-up summarize failed: {e}")

    logger.info('Scheduler started. Press Ctrl+C to exit.')
    sch.start()


if __name__ == '__main__':
    start_scheduler()
