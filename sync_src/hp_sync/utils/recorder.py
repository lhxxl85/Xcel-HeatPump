# 热泵算法的PD和CSV记录工具
# src/hp_controller/utils/recorder.py
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd


@dataclass
class AlgorithmLogConfig:
    """
    算法日志配置
    """

    # 是否开启记录功能
    enabled: bool = True

    # CSV文件输出目录
    output_dir: str = "logs/algorithm"

    # 刷新到磁盘的时间间隔（秒）
    flush_interval_sec: float = 60.0  # 默认每60秒刷新一次

    # CSV 文件名前缀
    file_prefix: str = "algorithm"


class AlgorithmMetricsRecorder:
    """
    算法运行记录指标
    - 在内存中维护一个 Pandas DataFrame, 缓存算法每一步的关键数据
    - 周期性（按 flush_interval_sec 配置）将数据刷新到 CSV 文件
    - 文件名格式: {file_prefix}_YYYYMMDD_HHMMSS.csv

    记录字段（来自算法传入）：
    - timestamp: 时间戳 (float, time.time())
    - ct_total_current: CT 总电流 (float)
    - other_load_current: 其他负载电流 (float)
    - hp_total_current: 热泵总电流 (float)
    - current_total_compressors
    - raw_target
    - new_total
    """

    def __init__(
        self,
        config: Optional[AlgorithmLogConfig] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or AlgorithmLogConfig()
        base_logger = logger or logging.getLogger("hp_controller.utils.recorder")
        self.logger = base_logger.getChild("AlgorithmMetricsRecorder")

        self._lock = threading.Lock()
        self._buffer: pd.DataFrame = pd.DataFrame()
        self._last_flush_ts: float = time.time()

        # 确保输出目录存在
        self._output_dir = Path(self.config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------------
    # 对外接口: 记录一条指标数据
    # ------------------------------------------------------------------------
    def record(
        self,
        *,
        ct_total_current: float,
        other_load_current: float,
        hp_total_current: float,
        current_total_compressors: int,
        raw_target: int,
        new_total: int,
        **extra: Any,
    ) -> None:
        """
        记录一条指标数据到内存缓存
        extra 可以包含额外的字段
        """
        if not self.config.enabled:
            return

        timestamp = time.time()
        data = {
            "timestamp": timestamp,
            "ct_total_current": ct_total_current,
            "other_load_current": other_load_current,
            "hp_total_current": hp_total_current,
            "current_total_compressors": current_total_compressors,
            "raw_target": raw_target,
            "new_total": new_total,
        }
        data.update(extra)
        self.logger.debug("Recording metrics: %s", data)

        with self._lock:
            self._buffer = pd.concat(
                [self._buffer, pd.DataFrame([data])], ignore_index=True
            )
            now = time.time()
            if now - self._last_flush_ts >= self.config.flush_interval_sec:
                self._flush_to_disk()
                self._last_flush_ts = now

    # ------------------------------------------------------------------------
    # 对外接口： 主动刷新数据到磁盘
    # ------------------------------------------------------------------------
    def flush(self) -> None:
        """
        主动将内存中的数据刷新到磁盘
        """
        if not self.config.enabled:
            return

        with self._lock:
            self._flush_to_disk()
            self._last_flush_ts = time.time()

    # ------------------------------------------------------------------------
    # 内部方法： 将buffer写入到csv（需要锁内部调用）
    # ------------------------------------------------------------------------
    def _flush_to_disk(self) -> None:
        if self._buffer.empty:
            return

        try:
            # 生成文件名: {file_prefix}_YYYYMMDD_HHMMSS.csv
            ts_struct = time.localtime()
            filename = (
                f"{self.config.file_prefix}_"
                f"{ts_struct.tm_year:04d}{ts_struct.tm_mon:02d}{ts_struct.tm_mday:02d}_"
                f"{ts_struct.tm_hour:02d}{ts_struct.tm_min:02d}{ts_struct.tm_sec:02d}.csv"
            )
            filepath = self._output_dir / filename

            # 判断是否已经存在，存在则追加，否则新建
            file_exists = filepath.exists()

            if file_exists:
                self._buffer.to_csv(
                    filepath,
                    mode="a",
                    header=False,
                    index=False,
                    encoding="utf-8",
                )
            else:
                self._buffer.to_csv(
                    filepath,
                    mode="w",
                    header=True,
                    index=False,
                    encoding="utf-8",
                )

            self.logger.info("Flushed %d records to %s", len(self._buffer), filepath)

            # 清空buffer
            self._buffer = pd.DataFrame()
        except Exception as e:
            self.logger.error("Failed to flush metrics to disk: %s", e)
            # 保留buffer以便下次尝试


# ------------------------------------------------------------------------
# 工厂函数： 方便在main / algorithm中创建记录器
# ------------------------------------------------------------------------
def create_default_recorder(
    output_dir: str = "logs/algorithm",
    flush_interval_sec: float = 60.0,
    enabled: bool = True,
    logger: Optional[logging.Logger] = None,
) -> AlgorithmMetricsRecorder:
    config = AlgorithmLogConfig(
        enabled=enabled,
        output_dir=output_dir,
        flush_interval_sec=flush_interval_sec,
        file_prefix="algorithm",
    )
    recorder = AlgorithmMetricsRecorder(config=config, logger=logger)
    return recorder
