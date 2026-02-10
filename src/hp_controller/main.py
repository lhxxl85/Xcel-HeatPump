# 主程序入口
# src/main.py
from __future__ import annotations

import logging
import signal
import threading
from typing import Optional

from src.hp_controller.master.client import (
    ModbusConfig,
    ModbusEndpointConfig,
    ModbusRtuConfig,
    ModbusTcpConfig,
    ModbusMaster,
)
from src.hp_controller.master.algorithm import AlgorithmConfig, LoadControlAlgorithm
from src.hp_controller.master.aggregation import Aggregation, AggregationConfig
from src.hp_controller.settings import AppSettings
from src.hp_controller.utils.recorder import create_default_recorder
from src.hp_controller.utils.logging_config import setup_logging


def _build_endpoint_config(settings: AppSettings, device: str) -> ModbusEndpointConfig:
    if device == "hp":
        source = settings.hp
    else:
        source = settings.ct

    return ModbusEndpointConfig(
        transport=source.transport,
        tcp=ModbusTcpConfig(
            host=source.tcp.host,
            port=source.tcp.port,
            timeout_sec=source.tcp.timeout_sec,
        ),
        rtu=ModbusRtuConfig(
            port=source.rtu.port,
            baudrate=source.rtu.baudrate,
            parity=source.rtu.parity,
            stopbits=source.rtu.stopbits,
            bytesize=source.rtu.bytesize,
            timeout_sec=source.rtu.timeout_sec,
        ),
    )


def main() -> int:
    settings = AppSettings()
    setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)

    logger = logging.getLogger("hp_controller.main")

    # ------------------------------------------------------------------
    # 构建 ModbusMaster
    # ------------------------------------------------------------------
    master_cfg = ModbusConfig(
        hp=_build_endpoint_config(settings, "hp"),
        ct=_build_endpoint_config(settings, "ct"),
        hp_slave_ids=settings.hp_ids,
        ct_slave_id=settings.ct_id,
        poll_interval_sec=settings.poll_interval_sec,
        control_mode=settings.control_mode,
    )
    master_logger = logging.getLogger("hp_controller.master")
    master = ModbusMaster(config=master_cfg, logger=master_logger)

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

    # 绑定算法模块到ModbusMaster
    master.bind_algorithm(algorithm)

    # ------------------------------------------------------------------
    # 聚合定期任务
    # ------------------------------------------------------------------
    agg_stop_event = threading.Event()
    aggregation_config = AggregationConfig()  # 使用默认路径，可根据需要扩展配置
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

    # ------------------------------------------------------------------
    # 启动 Modbus 轮询和算法线程
    # ------------------------------------------------------------------
    logger.info(
        "Starting Modbus master (hp_ids=%s, ct_id=%d)",
        settings.hp_ids,
        settings.ct_id,
    )
    logger.info("Control mode: %s", "enabled" if settings.control_mode else "disabled")

    # 主动尝试连接一次（失败也没关系，轮询线程会自动重连）
    master.connect()
    master.start_polling()

    logger.info("Starting load control algorithm loop")
    algorithm.start()

    try:
        if settings.print_state:
            # 简单的状态打印循环
            while not stop_event.is_set():
                snapshot = master.get_shared_state_snapshot()
                logger.info("Current shared state: %s", snapshot)
                # 每 5 秒打印一次
                if stop_event.wait(5.0):
                    break
        else:
            # 没有额外动作，只是等待退出信号
            while not stop_event.is_set():
                if stop_event.wait(1.0):
                    break
    finally:
        # ------------------------------------------------------------------
        # 收尾：停止算法和 Modbus
        # ------------------------------------------------------------------
        logger.info("Stopping algorithm...")
        algorithm.stop()

        logger.info("Stopping aggregation loop...")
        agg_stop_event.set()
        if agg_thread:
            agg_thread.join(timeout=5.0)

        logger.info("Stopping Modbus polling and disconnecting...")
        master.stop_polling()
        master.disconnect()

        logger.info("Shutdown complete")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
