# mailbot package initializer

# Setup unified logger with timestamps for all console logs
import sys
import logging

LOGGER_NAME = "mailbot"
logger = logging.getLogger(LOGGER_NAME)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# Silence cssutils (used by premailer) noisy logs in email HTML processing
# 这些 CSS 属性警告（如 -webkit-text-size-adjust, mso-table-lspace 等）
# 是邮件 HTML 中常见的浏览器前缀属性，cssutils 不认识它们，但不影响功能
try:
    import cssutils
    # 完全静默 cssutils 日志，包括 WARNING 和 ERROR
    cssutils.log.setLevel(logging.CRITICAL + 10)
    # 同时设置其内部 logger
    cssutils_logger = logging.getLogger('cssutils')
    cssutils_logger.setLevel(logging.CRITICAL + 10)
    cssutils_logger.propagate = False
    cssutils_logger.handlers = []
except Exception:
    pass
