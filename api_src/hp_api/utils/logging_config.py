from __future__ import annotations

import logging
from logging import Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path

from colorlog import ColoredFormatter


def setup_logging(log_dir: str = "logs", console_level: str = "INFO") -> Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hp_api")
    logger.setLevel(logging.DEBUG)

    if logger.hasHandlers():
        return logger

    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console_formatter = ColoredFormatter(
        fmt="%(log_color)s[%(asctime)s] [%(levelname)s] [%(name)s]%(reset)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
    console_handler.setFormatter(console_formatter)

    file_path = Path(log_dir) / "hp_api.log"
    file_handler = RotatingFileHandler(
        filename=file_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.debug("Logger initialized.")
    return logger
