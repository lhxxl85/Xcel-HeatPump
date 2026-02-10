# logging配置文件
# src/hp_sim/utils/logging_config.py
from __future__ import annotations

import logging
from logging import Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path

from colorlog import ColoredFormatter


def setup_logging(log_dir: str = "logs", console_level: str = "INFO") -> Logger:
    """
    初始化项目logger:
    - 终端： 彩色输出， 级别INFO及以上
    - 文件： 普通文本输出， 级别DEBUG及以上， 日志文件大小10MB， 备份5个
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hp_controller")
    logger.setLevel(logging.DEBUG)

    # 如果已经添加了处理器，避免重复添加
    if logger.hasHandlers():
        return logger

    # ----- 控制台(终端)处理器 -----
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

    # ----- 文件处理器 -----
    file_path = Path(log_dir) / "hp_controller.log"
    file_handler = RotatingFileHandler(
        filename=file_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)

    # 将处理器添加到logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    logger.debug("Logger initialized.")
    return logger
