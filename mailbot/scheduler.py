from __future__ import annotations
import logging
import threading
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler as BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.events import EVENT_JOB_MISSED

from .config import load_config
from .jobs import summarize_job, translate_job


logger = logging.getLogger("mailbot")


def _setup_logging():
    """Apply a clean, consistent log format for all modules."""
    fmt = logging.Formatter(
        fmt="%(asctime)s | mailbot | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    # Force a single uniform StreamHandler on root
    for h in list(root.handlers or []):
        try:
            root.removeHandler(h)
        except Exception:
            pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    root.setLevel(logging.INFO)

    # Ensure apscheduler loggers propagate to root and don't override format
    for name in list(logging.root.manager.loggerDict.keys()):
        if str(name).startswith("apscheduler"):
            l = logging.getLogger(name)
            l.handlers = []
            l.setLevel(logging.INFO)
            l.propagate = True


def start_scheduler():
    _setup_logging()
    cfg = load_config()
    tzname = cfg.get("timezone", "Asia/Shanghai")
    tz = pytz.timezone(tzname)

    # Translate interval is measured from finish time (fixed-delay)
    interval_minutes = int(cfg.get("translate", {}).get("interval_minutes", 10))
    translate_delay = timedelta(minutes=interval_minutes)

    # Single-thread critical section to avoid race; summarize has higher priority by policy
    RUN_LOCK = threading.RLock()
    summarize_pending = {"flag": False}

    sch = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1})

    def _run_summarize():
        with RUN_LOCK:
            t0 = datetime.now(tz)
            logger.info("▶ summarize start")
            try:
                summarize_job(cfg)
            except Exception as e:
                logger.exception(f"summarize error: {e}")
            finally:
                dt = int((datetime.now(tz) - t0).total_seconds())
                logger.info(f"✓ summarize end | duration={dt}s")

    def _schedule_translate_next(delay: timedelta):
        run_at = datetime.now(tz) + delay
        sch.add_job(_run_translate, DateTrigger(run_date=run_at), id="translate", replace_existing=True)
        logger.info(f"⏭ next translate at {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    def _run_translate():
        with RUN_LOCK:
            t0 = datetime.now(tz)
            logger.info("▶ translate start")
            try:
                translate_job(cfg)
            except Exception as e:
                logger.exception(f"translate error: {e}")
            finally:
                dt = int((datetime.now(tz) - t0).total_seconds())
                logger.info(f"✓ translate end | duration={dt}s")

        # schedule next translate from finish time
        _schedule_translate_next(translate_delay)

        # if summarize was delayed while translating, run catch-up immediately
        if summarize_pending.get("flag"):
            summarize_pending["flag"] = False
            sch.add_job(_run_summarize, DateTrigger(run_date=datetime.now(tz) + timedelta(seconds=1)), id="summarize-catchup", replace_existing=True)
            logger.info("⚑ summarize pending → scheduled immediate catch-up")

    # Summarize jobs (strict on-the-hour cron). If missed, run ASAP afterwards
    summarize_specs = cfg.get("summarize", {}).get("cron", ["0 7 * * *", "0 12 * * *", "0 19 * * *"])
    for spec in summarize_specs:
        jid = f"summarize:{spec}"
        sch.add_job(_run_summarize, CronTrigger.from_crontab(spec, timezone=tz), id=jid, misfire_grace_time=3600)

    # Translate is scheduled as a one-shot; after each finish it re-schedules itself
    _schedule_translate_next(timedelta(seconds=1))

    # Listen for missed summarize runs (e.g., blocked by translate)
    def _listener(event):
        try:
            if event.code == EVENT_JOB_MISSED and isinstance(getattr(event, "job_id", ""), str):
                if str(event.job_id).startswith("summarize"):
                    summarize_pending["flag"] = True
                    logger.info("⚠ summarize misfired → will run right after translate finishes")
        except Exception:
            pass

    sch.add_listener(_listener, EVENT_JOB_MISSED)

    # Startup banner + next runs
    logger.info("⏳ scheduler starting...")

    def _safe_next_time(job):
        try:
            nrt = getattr(job, 'next_run_time', None)
            if nrt is None:
                trig = getattr(job, 'trigger', None)
                if trig is not None:
                    try:
                        now = datetime.now(tz)
                        nrt = trig.get_next_fire_time(None, now)
                    except Exception:
                        nrt = None
            if nrt and hasattr(nrt, 'astimezone'):
                try:
                    return nrt.astimezone(tz)
                except Exception:
                    return nrt
            return nrt
        except Exception:
            return None

    for j in sch.get_jobs():
        when = _safe_next_time(j)
        when_s = when.strftime("%Y-%m-%d %H:%M:%S %Z") if when else "N/A"
        logger.info(f"⏰ next at {when_s} → {j.id}")

    sch.start()


if __name__ == '__main__':
    start_scheduler()

