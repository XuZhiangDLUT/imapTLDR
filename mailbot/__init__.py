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
try:
    import cssutils
    cssutils.log.setLevel(logging.CRITICAL)  # suppress warnings/errors that are non-fatal in our workflow
except Exception:
    pass
