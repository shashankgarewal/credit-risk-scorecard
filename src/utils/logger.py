import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv
from src.utils.config import LOG_DIR

load_dotenv()
is_dev_env = (os.getenv("DEV_ENV") or "").lower() in ("true", "1")

logger = logging.getLogger(__name__)

if is_dev_env:
    logger.setLevel(logging.DEBUG)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # rotate logs every day and keep 14 days of logs
    file_handler = TimedRotatingFileHandler(
        LOG_DIR / "app.log", when="midnight", backupCount=14
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(filename)s | %(funcName)s:%(lineno)d | %(message)s,",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(file_handler)

    # also stdout to console in dev
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    logger.addHandler(console_handler)

else:
    logger.setLevel(logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    ))
    logger.addHandler(console_handler)
