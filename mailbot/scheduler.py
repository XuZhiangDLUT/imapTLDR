from __future__ import annotations
from apscheduler.schedulers.blocking import BlockingScheduler as BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime
import pytz

from .config import load_config
from .jobs import translate_job, summarize_job


def start_scheduler():
    cfg = load_config()
    tzname = cfg.get('timezone', 'Asia/Shanghai')
    tz = pytz.timezone(tzname)

    sch = BackgroundScheduler(timezone=tz)

    # translate every N minutes
    interval_minutes = int(cfg.get('translate', {}).get('interval_minutes', 10))
    sch.add_job(lambda: translate_job(cfg), IntervalTrigger(minutes=interval_minutes), id='translate')

    # summarize crons
    for spec in cfg.get('summarize', {}).get('cron', ['0 7 * * *','0 12 * * *','0 19 * * *']):
        sch.add_job(lambda: summarize_job(cfg), CronTrigger.from_crontab(spec, timezone=tz))

    print('Scheduler started. Press Ctrl+C to exit.')
    sch.start()


if __name__ == '__main__':
    start_scheduler()
