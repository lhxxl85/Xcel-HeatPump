# 热泵控制核心算法逻辑
# src/hp_controller/master/algorithm.py
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
import math
import pandas as pd

from .redis_master import RedisMaster
from ..utils.recorder import AlgorithmMetricsRecorder


@dataclass
class AlgorithmConfig:
    """算法配置参数"""

    # 周期性检查间隔（秒）
    loop_interval_sec: float = 1.0

    # CT从站ID(默认直接从ModbusMaster.config.ct_slave_id获取)
    ct_slave_id: int = 10

    # 热泵从站ID(默认直接从ModbusMaster.config.hp_slave_id获取)
    hp_slave_ids: Iterable[int] = (1, 2, 3, 4, 5, 6)

    # 电流保护阈值（安培）
    protection_current_limit: float = 1200.0

    # 死区 (在limit - deadband ~ limit 之间不触发保护)
    deadband: float = 50.0

    # 目标电流的安全裕度（希望运行在limit - safety_margin以下）
    safety_margin: float = 200.0

    # 当前没有任何压缩机开机的“每台压缩机电流”兜底估计（安培）
    fallback_per_compressor_current: float = 30.0

    # 每个周期最多调整的压缩机数量（全场总数）
    max_compressor_changes_per_cycle: int = 6

    # 固定的hysteresis值（建议为0, 这样只需要调节heating_setpoint即可）
    fixed_hysteresis: int = 0

    # 判断压缩机是否“开启”的最小电流阈值（安培）
    compressor_on_current_threshold: float = 1.0

    # 每个周期允许的设定温度最大变化幅度（摄氏度），防止设定震荡过大
    max_setpoint_step: float = 5.0

    # 低频更新的CT电流安全裕度（安培）
    safe_margin_current: float = 100.0

    # 单台热泵电流与配额的上/下偏差阈值（安培），超过则调节
    hp_current_upper_tol: float = 20.0
    hp_current_lower_tol: float = 20.0

    # 压缩机启动瞬时允许超出的电流阈值（浪涌容限，安培）
    surge_allowance_current: float = 5.0

    # 下调功率的容忍值（安培），仅当 hp_cur > 配额 + 容忍值 时才下调
    powerdown_tolerant_current: float = 10.0
    
    # 上调功率的耐心值（安培），仅当 hp_cur + 浪涌 < 配额 + 容忍值 时才上调
    powerup_patient_current: float = 5.0

    # 聚合结果文件（数学模块输出）与兜底值
    agg_result_path: str = "aggregation_result.json"
    fallback_run_current: float = 30.0
    fallback_surge_current: float = 40.0
    fallback_other_current: float = 0.0

    # 观测记录CSV路径与条数上限
    stats_csv_path: str = "logs/aggregation_records.csv"
    stats_csv_limit: int = 100
    heating_setpoint_register_address: int = 0x0004
    hysteresis_register_address: int = 0x0003


class LoadControlAlgorithm:
    """
    负载控制主算法：
    - 周期性检查ModbusMaster.shared_state中CT和HP的状态
    - 在线估计“每个压缩机”对CT电流的贡献（0～40A之间）
    - 根据保护阈值/死区计算目标压缩机总数
    - 在6太热泵之间均衡的分配压缩机数量
    - 通过调节热泵的heating_setpoint来控制压缩机开关
    """

    def __init__(
        self,
        master: RedisMaster,
        config: Optional[AlgorithmConfig] = None,
        logger: Optional[logging.Logger] = None,
        recorder: Optional[AlgorithmMetricsRecorder] = None,
    ) -> None:
        self.recorder = recorder
        self.master = master
        self.config = config or AlgorithmConfig(
            ct_slave_id=master.config.ct_slave_id,
            hp_slave_ids=tuple(master.config.hp_slave_ids),
        )

        base_logger = logger or logging.getLogger("hp_controller.master.algorithm")
        self.logger = base_logger.getChild("LoadControlAlgorithm")

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._original_settings_path = Path.cwd() / "original_setting.json"
        self._original_settings: Dict[int, Dict[str, float]] = {}
        # CT总电流（来自云端API），以及派生变量
        self.ct_total_current: Optional[float] = None
        self.ct_other_current: Optional[float] = None
        self.hp_total_currents: Dict[int, float] = {}
        self.load_available_current: Optional[float] = None
        self.heatpump_available_current_avg: Optional[float] = None
        # 记录各热泵压缩机运行时的平均/最大电流，用于估算启动/运行消耗
        # {hp_id: {comp_index: {"avg": x, "max": y}}}
        self._compressor_stats: Dict[int, Dict[int, Dict[str, float]]] = {}
        # 聚合结果缓存
        self.aggregation_result: Dict[str, Any] = {}
        # 观测记录缓存
        self._stats_df: Optional[pd.DataFrame] = None
        self._prev_comm_status: dict[str, dict[int, int]] = {"hp": {}, "ct": {}}

    # -------------------------------------------------------
    # 线程控制
    # -------------------------------------------------------
    def start(self) -> None:  # pragma: no cover
        """
        启动后台算法线程
        """
        if self._thread and self._thread.is_alive():
            self.logger.warning("Algorithm thread is already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        self.logger.info("Algorithm thread started")

    def stop(self) -> None:  # pragma: no cover
        """
        停止后台算法线程
        """
        if not self._thread:
            self.logger.warning("Algorithm thread is not running")
            return

        self._stop_event.set()
        self._thread.join(timeout=5.0)
        self._thread = None
        self.logger.info("Algorithm thread stopped")

    # -------------------------------------------------------
    # 主循环
    # -------------------------------------------------------
    def _main_loop(self) -> None:
        """
        主循环函数
        """
        while not self._stop_event.is_set():  # pragma: no cover
            try:
                comm_status = self.master.get_comm_status_snapshot()
                if not self._is_comm_status_healthy(comm_status):
                    if self._stop_event.wait(self.config.loop_interval_sec):
                        break
                    continue
                snapshot = self.master.get_shared_state_snapshot()
                self._ensure_original_settings(snapshot)
                self._step(snapshot)
            except Exception as e:  # noqa: BLE001
                self.logger.error(
                    f"Unexpected error in algorithm loop: {e}", exc_info=True
                )

            if self._stop_event.wait(self.config.loop_interval_sec):
                break

    def _is_comm_status_healthy(self, current: Mapping[str, Mapping[int, int]]) -> bool:
        hp_current = current.get("hp", {})
        ct_current = current.get("ct", {})
        hp_prev = self._prev_comm_status.get("hp", {})
        ct_prev = self._prev_comm_status.get("ct", {})

        for hp_id in self.config.hp_slave_ids:
            cur = hp_current.get(int(hp_id), -1)
            if cur < 0:
                self.logger.warning("HP %s comm_status=%s, pause control", hp_id, cur)
                return False
            prev = hp_prev.get(int(hp_id))
            if prev is not None and prev == cur:
                self.logger.warning(
                    "HP %s comm_status not advanced (%s), pause control",
                    hp_id,
                    cur,
                )
                return False

        ct_id = int(self.config.ct_slave_id)
        ct_cur = ct_current.get(ct_id, -1)
        if ct_cur < 0:
            self.logger.warning("CT %s comm_status=%s, pause control", ct_id, ct_cur)
            return False
        ct_prev_value = ct_prev.get(ct_id)
        if ct_prev_value is not None and ct_prev_value == ct_cur:
            self.logger.warning(
                "CT %s comm_status not advanced (%s), pause control",
                ct_id,
                ct_cur,
            )
            return False

        self._prev_comm_status = {
            "hp": {int(k): int(v) for k, v in hp_current.items()},
            "ct": {int(k): int(v) for k, v in ct_current.items()},
        }
        return True

    def _refresh_current_budget_from_snapshot(
        self,
        snapshot: Mapping[int, Mapping[str, int | float]],
        hp_status: Mapping[int, Mapping[str, float]],
    ) -> bool:
        ct_regs = snapshot.get(self.config.ct_slave_id)
        if not ct_regs:
            self.logger.warning("CT slave %s data not available", self.config.ct_slave_id)
            return False

        raw_total_current = ct_regs.get("total_current")
        if raw_total_current is None:
            raw_total_current = (
                float(ct_regs.get("current_l1", 0.0))
                + float(ct_regs.get("current_l2", 0.0))
                + float(ct_regs.get("current_l3", 0.0))
            )

        try:
            self.ct_total_current = float(raw_total_current)
        except Exception:  # noqa: BLE001
            self.logger.warning("Invalid CT total_current=%s", raw_total_current)
            return False

        hp_total_current_sum = sum(
            status["hp_total_current"] for status in hp_status.values()
        )
        self.hp_total_currents = {
            int(hp_id): status["hp_total_current"]
            for hp_id, status in hp_status.items()
        }

        other_load_current = max(0.0, self.ct_total_current - hp_total_current_sum)
        self.ct_other_current = other_load_current
        self.load_available_current = max(
            0.0, self.config.protection_current_limit - other_load_current
        )
        self.heatpump_available_current_avg = max(
            0.0,
            (self.load_available_current - self.config.safe_margin_current)
            / max(1, len(tuple(self.config.hp_slave_ids))),
        )
        return True

    # -------------------------------------------------------
    # 云端CT电流更新入口
    # -------------------------------------------------------
    def update_ct_total_current(self, ct_total_current: float) -> None:
        """
        由外部（云端API）调用，用于更新当前CT总电流。
        不做复杂校验，只存储并在下一周期使用。
        """
        try:
            self.ct_total_current = float(ct_total_current)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Invalid ct_total_current={ct_total_current}: {e}")  # pragma: no cover - 防御性
            return

        # 读取聚合结果（run/surge/other_current）
        self._load_aggregation_result()

        # 立即使用当前快照计算配额
        snapshot = self.master.get_shared_state_snapshot()
        hp_status = self._collect_hp_status(snapshot)
        if not hp_status:
            self.logger.warning("No heat pump data available when updating CT current")  # pragma: no cover
            return

        hp_total_current_sum = sum(
            status["hp_total_current"] for status in hp_status.values()
        )
        self.hp_total_currents = {
            int(hp_id): status["hp_total_current"]
            for hp_id, status in hp_status.items()
        }

        # other_current 优先用聚合结果的估计
        # agg_other = self.aggregation_result.get("other_current")
        # if agg_other is None:
        #     other_load_current = max(0.0, self.ct_total_current - hp_total_current_sum)
        # else:
        #     other_load_current = float(agg_other)
        # 这里逻辑重写，不用聚合结果，直接用实时差值
        other_load_current = max(0.0, self.ct_total_current - hp_total_current_sum)
        self.ct_other_current = other_load_current

        load_available_current = max(
            0.0, self.config.protection_current_limit - other_load_current
        )
        self.load_available_current = load_available_current
        self.heatpump_available_current_avg = max(
            0.0,
            (load_available_current - self.config.safe_margin_current)
            / max(1, len(tuple(self.config.hp_slave_ids))),
        )

    # -------------------------------------------------------
    # 单步控制逻辑
    # -------------------------------------------------------
    def _step(self, snapshot: Mapping[int, Mapping[str, int | float]]) -> None:
        """
        读取CT和HP状态快照，执行单步控制逻辑
        """
        if not self._original_settings:
            self.logger.warning(
                "Original settings not ready, skip control step for now"
            )
            return

        # 统计当前所有热泵压缩机开机数量
        hp_status = self._collect_hp_status(snapshot)
        if not hp_status:
            # 没有热泵数据
            self.logger.warning("No heat pump data available")  # pragma: no cover
            return

        if not self._refresh_current_budget_from_snapshot(snapshot, hp_status):
            self.logger.warning("CT current data not ready, skip control step")
            return

        total_compressors_on = sum(
            status["compressors_on"] for status in hp_status.values()
        )
        hp_total_current_sum = sum(
            status["hp_total_current"] for status in hp_status.values()
        )

        # 配额在 update_ct_total_current 中计算，这里只用当前电流与既定配额计算目标
        heatpump_available_current_avg = self.heatpump_available_current_avg or 0.0
        per_hp_targets: Dict[int, int] = {}
        for hp_id in self.config.hp_slave_ids:
            status = hp_status.get(hp_id)
            if not status:
                continue
            current_compressors = int(status["compressors_on"])
            hp_cur = status["hp_total_current"]
            run_current_est = self._estimate_compressor_run_current(hp_id)
            surge_current_est = self._estimate_compressor_surge_current(hp_id)

            target = current_compressors
            # 增加压缩机：仅当运行+浪涌均不超过配额+容限，且只加1
            if (
                hp_cur + run_current_est <= heatpump_available_current_avg - self.config.powerup_patient_current
                and hp_cur + surge_current_est
                <= heatpump_available_current_avg + self.config.surge_allowance_current
            ):
                target = min(4, current_compressors + 1)
            # 减少压缩机：若超过配额，按估算运行电流计算需要减几台
            elif hp_cur > heatpump_available_current_avg + self.config.powerdown_tolerant_current:
                over = hp_cur - heatpump_available_current_avg - self.config.powerdown_tolerant_current
                need_drop = int(math.ceil(over / max(run_current_est, 1e-3)))
                target = max(0, current_compressors - need_drop)

            per_hp_targets[int(hp_id)] = target

        # 记录当前派生的 other_load_current（用于记录，可反映最新 CT/HP 状态）
        derived_other_current = max(0.0, self.ct_total_current - hp_total_current_sum)
        self.ct_other_current = derived_other_current

        metrics_context: Dict[str, Any] = {
            "ct_total_current": self.ct_total_current,
            "other_load_current": derived_other_current,
            "hp_total_current": hp_total_current_sum,
            "current_total_compressors": float(total_compressors_on),
            "target_total_compressors": float(sum(per_hp_targets.values())),
            "load_available_current": self.load_available_current,
            "heatpump_available_current_avg": self.heatpump_available_current_avg,
            "hp_total_currents_map": self.hp_total_currents,
        }

        # 根据目标压缩机数量，通过setpoint/hysteresis控制热泵，并记录决策
        self._apply_hp_targets(
            per_hp_targets,
            snapshot,
            hp_status,
            metrics_context=metrics_context,
        )

        # 记录观测数据到 CSV（仅用于离线聚合/诊断）
        try:
            self._record_observation(
                snapshot=snapshot,
                ct_total_current=self.ct_total_current,
                other_load_current=metrics_context["other_load_current"],
            )
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Failed to record observation: {e}", exc_info=True)  # pragma: no cover

    # -------------------------------------------------------
    # 估计压缩机电流（运行/浪涌）
    # -------------------------------------------------------
    def _estimate_compressor_run_current(self, hp_id: int) -> float:
        hp_stats = self.aggregation_result.get("hp", {}).get(str(hp_id))
        if hp_stats and "run" in hp_stats:
            return float(hp_stats["run"])
        return float(self.config.fallback_run_current)

    def _estimate_compressor_surge_current(self, hp_id: int) -> float:
        hp_stats = self.aggregation_result.get("hp", {}).get(str(hp_id))
        if hp_stats and "surge" in hp_stats:
            return float(hp_stats["surge"])
        return float(self.config.fallback_surge_current)

    def _update_compressor_stats(
        self, hp_id: int, currents: Iterable[float], threshold: float
    ) -> None:
        """
        简单的指数平均和最大值记录，用于估算运行/浪涌电流
        """
        hp_stats = self._compressor_stats.setdefault(hp_id, {})
        alpha = 0.2
        for idx, cur in enumerate(currents):
            comp_stats = hp_stats.setdefault(idx, {"avg": 0.0, "max": 0.0})
            if cur >= threshold:
                prev_avg = comp_stats.get("avg", 0.0)
                comp_stats["avg"] = prev_avg * (1 - alpha) + cur * alpha
                comp_stats["max"] = max(comp_stats.get("max", 0.0), cur)
            else:  # pragma: no cover - 仅用于统计，低于阈值不更新
                continue

    # -------------------------------------------------------
    # 辅助函数: 收集HP状态
    # -------------------------------------------------------
    def _collect_hp_status(
        self,
        snapshot: Mapping[int, Mapping[str, int | float]],
    ) -> Dict[int, Dict[str, float]]:
        """
        收集所有热泵的状态信息
        返回字典，键为从站ID，值为状态字典
        状态字典包含：
        - compressors_on: 当前开机的压缩机数量
        - hp_total_current: 热泵总电流（安培）
        """
        hp_status: Dict[int, Dict[str, float]] = {}
        th = self.config.compressor_on_current_threshold  # 压缩机开机电流阈值

        for hp_id in self.config.hp_slave_ids:
            regs = snapshot.get(hp_id)
            if not regs:
                self.logger.warning(f"Heat pump slave {hp_id} data not available")
                continue

            # 寄存器读取
            # 现场提供压缩机电流为整数安培值，无缩放
            currents = [
                float(regs.get(f"compressor{i + 1}_current", 0)) for i in range(4)
            ]
            # 更新压缩机电流统计（运行时的平均与最大）
            self._update_compressor_stats(int(hp_id), currents, th)

            on_count = sum(1 for cur in currents if cur >= th)
            hp_total_current = sum(currents)

            hp_status[int(hp_id)] = {
                "compressors_on": on_count,
                "hp_total_current": hp_total_current,
            }

        return hp_status

    # -------------------------------------------------------
    # 辅助函数: 计算目标压缩机数量 
    # DEPRECATED: 由 _step 中的逻辑替代
    # -------------------------------------------------------
    def _compute_target_total_compressors(
        self,
        ct_total_current: float,
        other_load_current: float,
        hp_total_current: float,
        current_total_compressors: int,
    ) -> int:
        """
        根据当前CT电流和保护阈值，计算目标总压缩机数量
        """
        max_compressors = len(tuple(self.config.hp_slave_ids)) * 4  # 每台热泵4台压缩机

        # 当前有压缩机开的话，用当前平均电流，否则用fallback估计
        if current_total_compressors > 0 and hp_total_current > 0.0:
            avg_per_compressor_current = hp_total_current / current_total_compressors
        else:
            avg_per_compressor_current = self.config.fallback_per_compressor_current

        limit = self.config.protection_current_limit
        deadband = self.config.deadband
        safety_margin = self.config.safety_margin

        target_ct_current = limit - safety_margin

        # 期望给热泵的电流
        desired_hp_current = max(0.0, target_ct_current - other_load_current)
        raw_target = int(desired_hp_current / max(avg_per_compressor_current, 0.1))

        # 上下界
        raw_target = max(0, min(max_compressors, raw_target))

        # 根据当前CT电流和死区做保护
        if (
            limit - deadband <= ct_total_current < limit
            and ct_total_current <= target_ct_current
        ):
            # 在死区内且未超过目标，保持不变
            self.logger.info(
                f"CT current {ct_total_current:.1f}A within deadband, maintaining current compressors {current_total_compressors}"
            )
            return current_total_compressors

        # 超过limit时，优先下调
        if ct_total_current >= limit:
            self.logger.warning(
                f"CT current {ct_total_current:.1f}A exceeds limit {limit}A, reducing compressors"
            )

        # 单周期步长限制
        delta = raw_target - current_total_compressors
        max_step = self.config.max_compressor_changes_per_cycle
        if delta > max_step:
            delta = max_step
        elif delta < -max_step:  # pragma: no cover
            delta = -max_step

        new_total = current_total_compressors + delta
        new_total = max(0, min(max_compressors, new_total))

        self.logger.info(
            "CT Total Load I={:.1f}A, other_load={:.1f}A, hp_current={:.1f}A, "
            "current={}, raw_target={}, new_total={}, avg_per_compressor={:.1f}A".format(
                ct_total_current,
                other_load_current,
                hp_total_current,
                current_total_compressors,
                raw_target,
                new_total,
                avg_per_compressor_current,
            )
        )

        # if self.recorder is not None:
        #     self.recorder.record(
        #         ct_total_current=ct_total_current,
        #         other_load_current=other_load_current,
        #         hp_total_current=hp_total_current,
        #         current_total_compressors=current_total_compressors,
        #         raw_target=raw_target,
        #         new_total=new_total,
        #     )

        self.logger.debug(
            f"Adjusting total compressors from {current_total_compressors} to {new_total}"
        )
        return new_total

    # -------------------------------------------------------
    # 辅助函数: 分配压缩机目标
    # -------------------------------------------------------
    def _distribute_compressors_among_hps(
        self,
        target_total_compressors: int,
        current_hp_status: Mapping[int, Mapping[str, float]],
    ) -> Dict[int, int]:
        """
        返回per_hp_targets[hp_id] = target_compressors_on
        保证sum(targets) == target_total
        并且尽量均衡分配
        """
        hp_ids = list(self.config.hp_slave_ids)
        h_np = len(hp_ids)  # 热泵数量
        if h_np == 0:  # pragma: no cover
            return {}

        base = target_total_compressors // h_np  # 每台热泵的基础目标压缩机数量
        remainder = target_total_compressors % h_np  # 余数

        # 按当前on数量排序，少的优先拿到+1的名额，保证均衡
        sorted_hp = sorted(
            hp_ids,
            key=lambda hid: current_hp_status.get(hid, {}).get("compressors_on", 0),
        )

        per_hp_targets: Dict[int, int] = {}
        for idx, hp_id in enumerate(sorted_hp):
            target = base + (1 if idx < remainder else 0)
            # 限制在0~4之间
            per_hp_targets[int(hp_id)] = max(0, min(4, int(target)))

        return per_hp_targets

    # -------------------------------------------------------
    # 辅助函数: 应用热泵目标 -> 写入heating_setpoint/hysteresis
    # -------------------------------------------------------
    def _apply_hp_targets(
        self,
        per_hp_target: Mapping[int, int],
        snapshot: Mapping[int, Mapping[str, int | float]],
        current_hp_status: Mapping[int, Mapping[str, float]],
        *,
        metrics_context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        根据目标压缩机数量，计算并写入heating_setpoint和hysteresis
        """
        per_hp_records: Dict[int, Dict[str, float]] = {}

        for hp_id, target_compressors in per_hp_target.items():
            regs = snapshot.get(hp_id)
            if not regs:  # pragma: no cover
                self.logger.warning(
                    f"Heat pump slave {hp_id} data not available for applying targets"
                )
                continue

            try:
                # 进水温度为现场整数值，无缩放
                t_in = float(regs.get("inlet_temperature", 0))
            except Exception as e:  # pragma: no cover
                self.logger.error(
                    f"Error reading inlet_temperature for HP {hp_id}: {e}",
                    exc_info=True,
                )
                continue

            # 计算新的heating_setpoint
            desired_setpoint = self._compute_setpoint_for_compressor_count(
                inlet_temp=t_in,
                target_compressors=target_compressors,
                hysteresis=self.config.fixed_hysteresis,
            )

            current_compressors = int(
                current_hp_status.get(hp_id, {}).get("compressors_on", 0)
            )

            # 按原始设定做约束
            orig = self._original_settings.get(hp_id)
            current_setpoint = float(regs.get("heating_setpoint", 0))

            if orig:
                orig_setpoint = float(orig.get("heating_setpoint", current_setpoint))
            else:
                orig_setpoint = current_setpoint
            # 算法介入时强制将回差锁定为配置值（现场要求固定为0，降低震荡）
            orig_hysteresis = self.config.fixed_hysteresis

            # 每周期限幅，减少设定震荡
            max_step = self.config.max_setpoint_step
            upper_limit = current_setpoint + max_step
            lower_limit = current_setpoint - max_step

            new_setpoint = desired_setpoint

            if target_compressors > current_compressors:
                # 需要增加功率：只允许往原始设定靠拢，不超过原始
                if current_setpoint >= orig_setpoint:
                    self.logger.debug(
                        f"HP {hp_id} already at original setpoint {orig_setpoint:.1f}C, skip increase"
                    )
                    continue
                new_setpoint = min(desired_setpoint, orig_setpoint, upper_limit)
            elif target_compressors < current_compressors:
                # 需要减小功率：只允许设定下降或保持，不得向上回调
                down_target = min(desired_setpoint, current_setpoint)
                new_setpoint = max(down_target, lower_limit)
            else:
                # 目标与当前一致，仅在偏离期望设定较多时微调 toward desired_setpoint
                if abs(current_setpoint - desired_setpoint) <= 1:
                    continue
                if desired_setpoint > current_setpoint:
                    new_setpoint = min(desired_setpoint, upper_limit)
                else:
                    new_setpoint = max(desired_setpoint, lower_limit)

            # 如变化极小，则无需写入
            if (
                abs(new_setpoint - current_setpoint) < 0.1
                and orig_hysteresis == self.config.fixed_hysteresis
            ):
                continue

            per_hp_records[int(hp_id)] = {
                "inlet_temperature": t_in,
                "current_setpoint": current_setpoint,
                "orig_setpoint": orig_setpoint,
                "desired_setpoint": desired_setpoint,
                "new_setpoint": new_setpoint,
                "current_compressors": float(current_compressors),
                "target_compressors": float(target_compressors),
                "hp_total_current": current_hp_status.get(hp_id, {}).get(
                    "hp_total_current", 0.0
                ),
                "compressor1_current": float(regs.get("compressor1_current", 0)),
                "compressor2_current": float(regs.get("compressor2_current", 0)),
                "compressor3_current": float(regs.get("compressor3_current", 0)),
                "compressor4_current": float(regs.get("compressor4_current", 0)),
                "hysteresis_written": float(orig_hysteresis),
            }

            setpoint_address = self.config.heating_setpoint_register_address

            # 再设置heating_set
            ok_s = self.master.write_register(
                slave_id=hp_id,
                register_address=setpoint_address,
                value=int(round(new_setpoint)),
            )

            # hysteresis 固定不变，但是为了保证现场这个点确实是0, 也写一次
            ok_h = self.master.write_register(
                slave_id=hp_id,
                register_address=self.config.hysteresis_register_address,
                value=int(orig_hysteresis),
            )

            if not ok_s or not ok_h:  # pragma: no cover
                self.logger.error(
                    f"Failed to write heating_setpoint or hysteresis for HP {hp_id}"
                )
                continue
            else:
                self.logger.info(
                    f"Set HP {hp_id} heating_setpoint to {new_setpoint:.1f}C for target compressors {target_compressors} when inlet_temp={t_in:.1f}C"
                )

        if self.recorder is not None and metrics_context is not None:
            # 组装必需字段（record的固定参数）和额外信息
            extras: Dict[str, Any] = dict(metrics_context)
            # 避免与固定参数重复
            for k in (
                "ct_total_current",
                "other_load_current",
                "hp_total_current",
                "current_total_compressors",
                "raw_target",
                "new_total",
                "target_total_compressors",
            ):
                extras.pop(k, None)
            extras["per_hp"] = per_hp_records
            # 同时记录聚合统计与观测统计
            extras.setdefault("load_available_current", self.load_available_current)
            extras.setdefault(
                "heatpump_available_current_avg", self.heatpump_available_current_avg
            )
            extras.setdefault("hp_total_currents_map", self.hp_total_currents)
            # 记录压缩机统计（平均/浪涌）
            extras.setdefault("compressor_stats", self._compressor_stats)
            extras.setdefault("aggregation_result", self.aggregation_result)
            self.recorder.record(
                ct_total_current=float(metrics_context.get("ct_total_current", 0.0)),
                other_load_current=float(
                    metrics_context.get("other_load_current", 0.0)
                ),
                hp_total_current=float(metrics_context.get("hp_total_current", 0.0)),
                current_total_compressors=int(
                    metrics_context.get("current_total_compressors", 0)
                ),
                raw_target=int(metrics_context.get("target_total_compressors", 0)),
                new_total=int(metrics_context.get("target_total_compressors", 0)),
                **extras,
            )

    @staticmethod
    def _compute_setpoint_for_compressor_count(
        inlet_temp: float,
        target_compressors: int,
        hysteresis: int = 0,
    ) -> float:
        """
        根据inlet_temperature/目标压缩机数量， 反推heating_setpoint

        在H固定时（hysteresis）我们假设为0, 现场逻辑等价为:
            - 0 台: T_set = T_in - 1
            - 1 台: T_set = T_in
            - 2 台: T_set = T_in + 3
            - 3 台: T_set = T_in + 5
            - 4 台: T_set = T_in + 7
        """
        _ = hysteresis  # 目前未使用，预留接口

        c = max(0, min(4, target_compressors))

        if c == 0:
            return inlet_temp - 1.0
        elif c == 1:
            return inlet_temp + 1.0
        elif c == 2:
            return inlet_temp + 3.0
        elif c == 3:
            return inlet_temp + 5.0
        else:  # c == 4
            return inlet_temp + 7.0

    # -------------------------------------------------------
    # 聚合结果加载与观测记录
    # -------------------------------------------------------
    def _load_aggregation_result(self) -> None:
        """
        读取 aggregation_result.json，用兜底值填充缺失字段
        """
        path = Path(self.config.agg_result_path)
        if not path.exists():
            self.aggregation_result = {}
            return
        try:
            content = json.loads(path.read_text())
            if not isinstance(content, dict):
                raise ValueError("aggregation_result is not a dict")
            self.aggregation_result = content
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Failed to load aggregation_result: {e}")  # pragma: no cover - 防御性日志
            self.aggregation_result = {}

    def _load_stats_csv(self) -> None:
        path = Path(self.config.stats_csv_path)
        if not path.exists():
            self._stats_df = pd.DataFrame()
            return
        try:
            self._stats_df = pd.read_csv(path)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Failed to load stats csv: {e}")  # pragma: no cover
            self._stats_df = pd.DataFrame()

    def _record_observation(
        self,
        snapshot: Mapping[int, Mapping[str, int | float]],
        ct_total_current: float,
        other_load_current: float,
    ) -> None:
        """
        将一次观测写入CSV，保留最近 stats_csv_limit 条
        """
        if self._stats_df is None:
            self._load_stats_csv()
        row: Dict[str, Any] = {
            "timestamp": pd.Timestamp.utcnow().isoformat(),
        }
        # 记录其他负载
        row["other_load_current"] = other_load_current
        # 展开每台热泵的每个压缩机电流
        for hp_id in self.config.hp_slave_ids:
            regs = snapshot.get(hp_id, {})
            for comp_idx in range(1, 5):
                col = f"heatpump_{hp_id}_compressor_{comp_idx}"
                row[col] = float(regs.get(f"compressor{comp_idx}_current", 0))
        new_df = pd.concat([self._stats_df, pd.DataFrame([row])], ignore_index=True)
        if len(new_df) > self.config.stats_csv_limit:
            new_df = new_df.iloc[-self.config.stats_csv_limit :]
        self._stats_df = new_df
        try:
            Path(self.config.stats_csv_path).parent.mkdir(parents=True, exist_ok=True)
            new_df.to_csv(self.config.stats_csv_path, index=False)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Failed to write stats csv: {e}")  # pragma: no cover

    # -------------------------------------------------------
    # 原始设定文件的处理
    # -------------------------------------------------------
    def _ensure_original_settings(
        self, snapshot: Mapping[int, Mapping[str, int | float]]
    ) -> bool:
        """
        确保 self._original_settings 已经加载：
        - 如果文件存在，读取
        - 如果不存在且有现场数据，则创建
        """
        if self._original_settings:
            return True

        if self._original_settings_path.exists():
            try:
                content = json.loads(self._original_settings_path.read_text())
                # 文件中使用字符串key，转换为int
                parsed: Dict[int, Dict[str, float]] = {}
                for k, v in content.items():
                    try:
                        hp_id = int(k)
                    except ValueError:
                        continue  # pragma: no cover
                    if isinstance(v, dict):
                        parsed[hp_id] = {
                            "heating_setpoint": float(v.get("heating_setpoint", 0.0)),
                            "hysteresis_value": float(v.get("hysteresis_value", 0.0)),
                        }
                if parsed:
                    self._original_settings = parsed
                    self.logger.info(
                        f"Loaded original settings from {self._original_settings_path}"
                    )
                    return True
            except Exception as e:  # noqa: BLE001
                self.logger.error(
                    f"Failed to load original settings file: {e}", exc_info=True
                )  # pragma: no cover

        # 文件不存在或读取失败，尝试用当前快照创建
        hp_settings: Dict[int, Dict[str, float]] = {}
        for hp_id in self.config.hp_slave_ids:
            regs = snapshot.get(hp_id)
            if not regs:
                continue  # pragma: no cover
            sp_raw = regs.get("heating_setpoint")
            hyst_raw = regs.get("hysteresis_value")
            if sp_raw is None or hyst_raw is None:
                continue  # pragma: no cover
            hp_settings[int(hp_id)] = {
                "heating_setpoint": float(sp_raw),
                "hysteresis_value": float(hyst_raw),
            }

        if len(hp_settings) == len(tuple(self.config.hp_slave_ids)):
            try:
                self._original_settings = hp_settings
                self._original_settings_path.write_text(
                    json.dumps(
                        {str(k): v for k, v in hp_settings.items()},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                self.logger.info(
                    f"Created original settings file at {self._original_settings_path}"
                )
                return True
            except Exception as e:  # noqa: BLE001
                self.logger.error(
                    f"Failed to write original settings file: {e}", exc_info=True
                )  # pragma: no cover

        return False
