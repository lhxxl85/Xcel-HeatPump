# 主程序入口
# src/main.py
from __future__ import annotations

import logging
import signal
import threading
from typing import Optional

from src.hp_controller.master.redis_master import RedisMaster, RedisMasterConfig
from src.hp_controller.master.algorithm import AlgorithmConfig, LoadControlAlgorithm
from src.hp_controller.master.aggregation import Aggregation, AggregationConfig
from src.hp_controller.settings import AppSettings
from src.hp_controller.utils.recorder import create_default_recorder
from src.hp_controller.utils.logging_config import setup_logging


def main() -> int:
    settings = AppSettings()
    setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)

    logger = logging.getLogger("hp_controller.main")

    # ------------------------------------------------------------------
    # 构建 RedisMaster（读Redis快照、写Redis命令）
    # ------------------------------------------------------------------
    master_cfg = RedisMasterConfig(
        hp_slave_ids=tuple(settings.hp_ids),
        ct_slave_id=settings.ct_id,
        hp_device_name=settings.hp_device_name,
        ct_device_name=settings.ct_device_name,
        host=settings.redis.host,
        port=settings.redis.port,
        db=settings.redis.db,
        username=settings.redis.username,
        password=settings.redis.password,
        socket_timeout_sec=settings.redis.socket_timeout_sec,
        connect_timeout_sec=settings.redis.connect_timeout_sec,
        reconnect_interval_sec=settings.redis.reconnect_interval_sec,
        key_prefix=settings.redis.key_prefix,
        control_mode=settings.control_mode,
    )
    master = RedisMaster(config=master_cfg, logger=logging.getLogger("hp_controller.master"))

    # ------------------------------------------------------------------
    # 构建 Algorithm
    # ------------------------------------------------------------------
    algo_cfg = AlgorithmConfig(
        loop_interval_sec=settings.algo_interval_sec,
        ct_slave_id=settings.ct_id,
        hp_slave_ids=settings.hp_ids,
        protection_current_limit=settings.current_limit,
        deadband=settings.deadband,
        safety_margin=settings.safety_margin,
    )
    algo_logger = logging.getLogger("hp_controller.algorithm")

    recorder = create_default_recorder(
        output_dir="logs/algorithm_metrics",
        flush_interval_sec=60.0,
        enabled=True,
        logger=algo_logger,
    )

    algorithm = LoadControlAlgorithm(
        master=master, config=algo_cfg, logger=algo_logger, recorder=recorder
    )

    # ------------------------------------------------------------------
    # 聚合定期任务
    # ------------------------------------------------------------------
    agg_stop_event = threading.Event()
    aggregation_config = AggregationConfig()
    aggregation = Aggregation(config=aggregation_config)

    def aggregation_loop() -> None:  # pragma: no cover - 后台线程
        interval = settings.aggregation_interval_sec
        if interval <= 0:
            return
        while not agg_stop_event.wait(interval):
            try:
                aggregation.run()
            except Exception as e:  # noqa: BLE001
                logger.error(f"Aggregation job failed: {e}", exc_info=True)

    agg_thread: Optional[threading.Thread] = None
    if settings.aggregation_interval_sec > 0:
        agg_thread = threading.Thread(target=aggregation_loop, daemon=True)
        agg_thread.start()

    # ------------------------------------------------------------------
    # 信号处理 & 退出控制
    # ------------------------------------------------------------------
    stop_event = threading.Event()

    def handle_signal(_signum, _frame) -> None:  # type: ignore[override]
        logger.info("Received termination signal, shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(
        "Starting Redis controller (hp_ids=%s, ct_id=%d)",
        settings.hp_ids,
        settings.ct_id,
    )
    logger.info("Control mode: %s", "enabled" if settings.control_mode else "disabled")

    master.connect()

    logger.info("Starting load control algorithm loop")
    algorithm.start()

    try:
        if settings.print_state:
            while not stop_event.is_set():
                snapshot = master.get_shared_state_snapshot()
                logger.info("Current shared state: %s", snapshot)
                if stop_event.wait(5.0):
                    break
        else:
            while not stop_event.is_set():
                if stop_event.wait(1.0):
                    break
    finally:
        logger.info("Stopping algorithm...")
        algorithm.stop()

        logger.info("Stopping aggregation loop...")
        agg_stop_event.set()
        if agg_thread:
            agg_thread.join(timeout=5.0)

        master.disconnect()
        logger.info("Shutdown complete")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
