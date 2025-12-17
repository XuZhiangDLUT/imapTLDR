from __future__ import annotations
import logging
import threading
from datetime import datetime, timedelta
import sys

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler as BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.events import EVENT_JOB_MISSED

from .config import load_config
from .jobs import summarize_job, translate_job, preflight_check_llm


logger = logging.getLogger("mailbot")


def _setup_logging():
    """Apply a clean, consistent log format for all modules.

    Adds lightweight color + bold styling when running in a TTY, while
    falling back to plain text when ANSI colors are not supported.
    Key lifecycle messages (START / DONE / NEXT / WARN / FLAG) get
    icons and highlighted prefixes to make them easier to scan.
    """
    base_fmt = "%(asctime)s | mailbot | %(levelname)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Best-effort color support; gracefully degrades if colorama is missing
    try:
        from colorama import Fore, Style, init as colorama_init  # type: ignore
    except Exception:  # pragma: no cover - ultra-defensive fallback
        class _DummyStyle:
            RESET_ALL = ""
            BRIGHT = ""

        class _DummyFore:
            BLACK = RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = ""

        Fore = _DummyFore()  # type: ignore
        Style = _DummyStyle()  # type: ignore

        def colorama_init():  # type: ignore
            return None

    # Helper for safe ASCII symbols in non-UTF8 terminals
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()

    def _safe(sym: str, fallback: str) -> str:
        try:
            if enc and "utf" in enc:
                sym.encode(enc)
                return sym
        except Exception:
            pass
        return fallback

    globals().update(
        {
            # 中文标签，配合 ColorFormatter 在终端中高亮显示
            "SYM_START": _safe("开始", "开始"),
            "SYM_DONE": _safe("完成", "完成"),
            "SYM_NEXT": _safe("下次", "下次"),
            "SYM_WARN": _safe("警告", "警告"),
            "SYM_FLAG": _safe("标记", "标记"),
        }
    )

    # Decide whether to enable colored output (only for interactive terminals)
    colorama_init()
    use_color = bool(getattr(sys.stdout, "isatty", lambda: False)())

    class ColorFormatter(logging.Formatter):
        """Formatter that highlights level + lifecycle keywords."""

        def __init__(self, fmt: str, datefmt: str):
            super().__init__(fmt=fmt, datefmt=datefmt)

        def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
            # Decorate well-known lifecycle prefixes
            raw_msg = record.getMessage()
            prefix = ""
            rest = raw_msg
            prefix_color = ""

            def _strip_prefix(tag: str, label: str, color: str) -> tuple[str, str, str]:
                """提取生命周期前缀，并用中文标签高亮。"""
                nonlocal rest
                if raw_msg.startswith(tag + " "):
                    rest = raw_msg[len(tag) + 1 :].lstrip()
                    # 只展示中文标签，不再重复英文 tag，避免日志里出现 "START START" 这类噪音
                    return label, rest, color
                return "", raw_msg, ""

            # Ordered by typical frequency
            if raw_msg.startswith("START "):
                prefix, rest, prefix_color = _strip_prefix("START", SYM_START, Fore.CYAN + Style.BRIGHT)
            elif raw_msg.startswith("DONE "):
                prefix, rest, prefix_color = _strip_prefix("DONE", SYM_DONE, Fore.GREEN + Style.BRIGHT)
            elif raw_msg.startswith("NEXT "):
                prefix, rest, prefix_color = _strip_prefix("NEXT", SYM_NEXT, Fore.BLUE + Style.BRIGHT)
            elif raw_msg.startswith("WARN "):
                prefix, rest, prefix_color = _strip_prefix("WARN", SYM_WARN, Fore.YELLOW + Style.BRIGHT)
            elif raw_msg.startswith("FLAG "):
                prefix, rest, prefix_color = _strip_prefix("FLAG", SYM_FLAG, Fore.MAGENTA + Style.BRIGHT)

            if prefix:
                msg_for_display = f"{prefix_color}[{prefix}]{Style.RESET_ALL} {rest}"
            else:
                msg_for_display = raw_msg

            # --- Semantic highlighting for key fields (models, folders, counts, durations) ---

            def _color_kv(message: str, key: str, color: str) -> str:
                """高亮形如 key=value,... 的片段。"""
                idx = message.find(key)
                if idx == -1:
                    return message
                end = message.find(",", idx)
                if end == -1:
                    end = len(message)
                return (
                    message[:idx]
                    + color
                    + message[idx:end]
                    + Style.RESET_ALL
                    + message[end:]
                )

            m = msg_for_display

            # LLM 配置行：高亮模型名 / 提供商 / 思考配置
            if "LLM 配置" in m:
                m = _color_kv(m, "主模型=", Fore.MAGENTA + Style.BRIGHT)
                m = _color_kv(m, "模型=", Fore.MAGENTA + Style.BRIGHT)
                m = _color_kv(m, "兜底模型=", Fore.CYAN)
                m = _color_kv(m, "提供商=", Fore.YELLOW)
                m = _color_kv(m, "思考模式=", Fore.CYAN)
                m = _color_kv(m, "思考 token 上限=", Fore.YELLOW)

            # 初始化 LLM 客户端: base=...
            if "初始化 LLM 客户端" in m:
                m = _color_kv(m, "base=", Fore.CYAN)

            # 扫描文件夹：高亮文件夹名称
            if m.startswith("扫描翻译文件夹: ") or m.startswith("扫描总结文件夹: "):
                marker = ": "
                idx = m.find(marker)
                if idx != -1 and idx + len(marker) < len(m):
                    folder = m[idx + len(marker) :]
                    m = (
                        m[: idx + len(marker)]
                        + Fore.CYAN
                        + folder
                        + Style.RESET_ALL
                    )

            # INBOX 通道相关
            if "扫描 INBOX 关键字通道" in m:
                m = m.replace("INBOX", f"{Fore.CYAN}INBOX{Style.RESET_ALL}")
            if "INBOX 关键字命中" in m:
                m = m.replace("INBOX", f"{Fore.CYAN}INBOX{Style.RESET_ALL}")

            # 总结 payload 保存路径
            if "已保存本次机器总结的请求与结果到文件:" in m:
                marker = "到文件:"
                idx = m.find(marker)
                if idx != -1:
                    path_start = idx + len(marker)
                    # 保留现有空格，再对路径部分着色
                    prefix_txt = m[:path_start]
                    path_txt = m[path_start:].lstrip()
                    if path_txt:
                        m = (
                            prefix_txt
                            + " "
                            + Fore.MAGENTA
                            + Style.BRIGHT
                            + path_txt
                            + Style.RESET_ALL
                        )

            # 未读数量行：高亮数量，>0 用突出颜色，0 使用弱一点的颜色
            if "数量=" in m:
                key = "数量="
                idx = m.find(key)
                if idx != -1:
                    start = idx + len(key)
                    end = start
                    while end < len(m) and m[end].isdigit():
                        end += 1
                    num_str = m[start:end]
                    try:
                        num = int(num_str)
                    except Exception:
                        num = None
                    if num is not None:
                        if num > 0:
                            c = Fore.MAGENTA + Style.BRIGHT
                        else:
                            c = Fore.WHITE
                        m = m[:start] + c + num_str + Style.RESET_ALL + m[end:]

            # 耗时=9s 这类字段
            if "耗时=" in m:
                key = "耗时="
                idx = m.find(key)
                if idx != -1:
                    start = idx + len(key)
                    end = start
                    while end < len(m) and (m[end].isdigit() or m[end].lower() == "s"):
                        end += 1
                    val = m[start:end]
                    if val:
                        m = (
                            m[:start]
                            + Fore.MAGENTA
                            + Style.BRIGHT
                            + val
                            + Style.RESET_ALL
                            + m[end:]
                        )

            msg_for_display = m

            # Colorize log level for quick scanning
            if record.levelno >= logging.CRITICAL:
                level_color = Fore.RED + Style.BRIGHT
            elif record.levelno >= logging.ERROR:
                level_color = Fore.RED
            elif record.levelno >= logging.WARNING:
                level_color = Fore.YELLOW
            elif record.levelno >= logging.INFO:
                level_color = Fore.GREEN
            else:
                level_color = Fore.BLUE

            original_levelname = record.levelname
            original_msg = record.msg
            try:
                record.levelname = f"{level_color}{record.levelname}{Style.RESET_ALL}"
                record.msg = msg_for_display
                record.args = ()
                return super().format(record)
            finally:
                record.levelname = original_levelname
                record.msg = original_msg

    root = logging.getLogger()
    # Force a single uniform StreamHandler on root
    for h in list(root.handlers or []):
        try:
            root.removeHandler(h)
        except Exception:
            pass

    sh = logging.StreamHandler()
    if use_color:
        fmt = ColorFormatter(base_fmt, date_fmt)
    else:
        fmt = logging.Formatter(fmt=base_fmt, datefmt=date_fmt)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    root.setLevel(logging.INFO)

    # Uniformize third-party loggers
    for name in list(logging.root.manager.loggerDict.keys()):
        l = logging.getLogger(str(name))
        l.handlers = []
        l.propagate = True
        if str(name).startswith("apscheduler"):
            l.setLevel(logging.WARNING)  # silence APScheduler info-level noise
        elif str(name).startswith("httpx") or str(name).startswith("httpcore"):
            l.setLevel(logging.WARNING)  # silence HTTP request info lines
        elif str(name).startswith("openai") or str(name).startswith("urllib3") or str(name).startswith("requests"):
            l.setLevel(logging.WARNING)
        else:
            l.setLevel(logging.INFO)

    # Also proactively register common noisy loggers
    for name in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors", "httpx", "httpcore", "openai", "urllib3", "requests"):
        l = logging.getLogger(name)
        l.handlers = []
        l.propagate = True
        if name.startswith("apscheduler"):
            l.setLevel(logging.WARNING)
        else:
            l.setLevel(logging.WARNING)


def start_scheduler():
    _setup_logging()
    cfg = load_config()

    # 启动前预检：检查所有 LLM 任务的 API 是否可用
    logger.info("START 启动前预检...")
    try:
        preflight_check_llm(cfg)
    except Exception as e:
        logger.error(f"预检失败，调度器无法启动: {e}")
        raise SystemExit(1)

    tzname = cfg.get("timezone", "Asia/Shanghai")
    tz = pytz.timezone(tzname)

    # Translate interval is measured from finish time (fixed-delay)
    interval_minutes = int(cfg.get("translate", {}).get("interval_minutes", 10))
    translate_delay = timedelta(minutes=interval_minutes)
    summarize_cfg = cfg.get("summarize", {})
    follow_translate_interval = bool(
        summarize_cfg.get("follow_translate_interval", False)
    )

    # Single-thread critical section to avoid race; summarize has higher priority by policy
    RUN_LOCK = threading.RLock()
    summarize_pending = {"flag": False}

    sch = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1})

    def _run_summarize():
        with RUN_LOCK:
            t0 = datetime.now(tz)
            logger.info("START 开始执行机器总结")
            try:
                summarize_job(cfg)
            except Exception as e:
                logger.exception(f"机器总结出错: {e}")
            finally:
                dt = int((datetime.now(tz) - t0).total_seconds())
                logger.info(f"DONE 机器总结完成 | 耗时={dt}s")

    def _schedule_translate_next(delay: timedelta):
        run_at = datetime.now(tz) + delay
        sch.add_job(_run_translate, DateTrigger(run_date=run_at), id="translate", replace_existing=True)
        logger.info(f"NEXT 下次机器翻译时间: {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    def _run_translate():
        with RUN_LOCK:
            t0 = datetime.now(tz)
            logger.info("START 开始执行机器翻译")
            try:
                translate_job(cfg)
            except Exception as e:
                logger.exception(f"机器翻译出错: {e}")
            finally:
                dt = int((datetime.now(tz) - t0).total_seconds())
                logger.info(f"DONE 机器翻译完成 | 耗时={dt}s")

        # schedule next translate from finish time
        _schedule_translate_next(translate_delay)

        if follow_translate_interval:
            logger.info(
                "NEXT 当前配置为'总结跟随翻译间隔'，本次翻译结束后立即执行总结"
            )
            _run_summarize()

        # if summarize was delayed while translating, run catch-up immediately
        if summarize_pending.get("flag"):
            summarize_pending["flag"] = False
            sch.add_job(_run_summarize, DateTrigger(run_date=datetime.now(tz) + timedelta(seconds=1)), id="summarize-catchup", replace_existing=True)
            logger.info("FLAG 检测到错过的总结任务，本次翻译结束后将立即补跑一次总结")

    # Summarize jobs (strict on-the-hour cron). If missed, run ASAP afterwards
    summarize_specs = summarize_cfg.get("cron", ["0 7 * * *", "0 12 * * *", "0 19 * * *"])
    if follow_translate_interval:
        logger.info("当前配置：总结任务跟随翻译间隔运行，定时规则已禁用")
    else:
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
                    logger.info("WARN 检测到定时总结错过执行，将在当前翻译任务结束后立即补跑")
        except Exception:
            pass

    sch.add_listener(_listener, EVENT_JOB_MISSED)

    # Startup banner + next runs
    logger.info("START 启动调度器...")

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
        logger.info(f"NEXT 下次运行时间 {when_s} -> {j.id}")

    try:
        # BlockingScheduler will swallow KeyboardInterrupt and perform graceful shutdown
        # (wait=True). To allow immediate exit on Ctrl+C, catch here and stop without waiting.
        sch.start()
    except KeyboardInterrupt:
        logger.info("WARN 收到 Ctrl+C，立即停止调度器（不等待当前任务完成）")
        try:
            sch.shutdown(wait=False)
        except Exception:
            pass


if __name__ == '__main__':
    start_scheduler()
