from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .settings import settings


def configure_logging() -> logging.Logger:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("das_monitor")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler = RotatingFileHandler(
        settings.log_dir / "das_monitor.log", encoding="utf-8", maxBytes=2_000_000, backupCount=5
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


logger = configure_logging()
