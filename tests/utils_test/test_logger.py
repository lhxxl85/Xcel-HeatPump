# logger单元测试模块
# tests/uilts_test/test_logger.py
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil

from hp_controller.utils.logging_config import setup_logging


def test_setup_logging(tmp_path):
    # 使用 pytest 的 tmp_path 作为日志目录，避免污染工程目录
    log_dir = tmp_path / "test_logs"

    # ---- 备份当前 logging 状态（root 和 hp_controller logger），以便测试结束后恢复 ----
    root_logger = logging.getLogger()
    original_root_handlers = root_logger.handlers.copy()

    hp_logger = logging.getLogger("hp_controller")
    original_hp_handlers = hp_logger.handlers.copy()
    original_hp_level = hp_logger.level
    original_hp_propagate = hp_logger.propagate

    try:
        # 清空 root logger 的 handler，避免 logger.hasHandlers() 直接返回 True
        root_logger.handlers.clear()

        # 清空 hp_controller 自身 handlers，并关闭向上冒泡
        hp_logger.handlers.clear()
        hp_logger.propagate = False

        # ---- 第一次调用：应当创建 console + file 两个 handler ----
        logger = setup_logging(str(log_dir))

        # 返回的其实就是同一个 hp_controller logger
        assert logger is hp_logger
        assert logger.name == "hp_controller"
        assert logger.level == logging.DEBUG

        # 确认只添加了两个 handler
        assert len(logger.handlers) == 2

        # 按类型区分控制台和文件 handler
        file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        console_handlers = [h for h in logger.handlers if not isinstance(h, RotatingFileHandler)]

        assert len(file_handlers) == 1
        assert len(console_handlers) == 1

        file_handler = file_handlers[0]
        console_handler = console_handlers[0]

        # 等级检查：控制台 INFO，文件 DEBUG
        assert console_handler.level == logging.INFO
        assert file_handler.level == logging.DEBUG

        # 文件路径检查：应该在 log_dir 中，文件名为 hp_controller.log
        log_path = Path(file_handler.baseFilename)
        assert log_path.parent == log_dir
        assert log_path.name == "hp_controller.log"

        # 试着写一条日志，确保不会抛异常且文件会被创建
        logger.info("test message")
        for h in logger.handlers:
            h.flush()

        assert log_dir.exists()
        assert log_path.exists()

        # ---- 第二次调用：应该复用现有 logger，不再重复添加 handler ----
        logger2 = setup_logging(str(log_dir))
        assert logger2 is logger
        assert len(logger2.handlers) == 2  # 仍然是两个，不应增长

    finally:
        # ---- 恢复 logging 状态，避免影响其他测试 ----
        root_logger.handlers = original_root_handlers
        hp_logger.handlers = original_hp_handlers
        hp_logger.setLevel(original_hp_level)
        hp_logger.propagate = original_hp_propagate

        # 清理测试日志目录
        shutil.rmtree(log_dir, ignore_errors=True)
