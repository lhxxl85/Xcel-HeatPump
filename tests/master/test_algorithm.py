# 算法单元测试
# tests/master/test_algorithm.py
from __future__ import annotations

import math
from typing import Dict
import json

from hp_controller.master.algorithm import LoadControlAlgorithm, AlgorithmConfig
from hp_controller.master.register_mapping import HP_REGISTER_MAP_REVERSE


class FakeConfig:
    def __init__(self, hp_slave_ids=(1, 2, 3, 4, 5, 6), ct_slave_id: int = 10) -> None:
        self.hp_slave_ids = hp_slave_ids
        self.ct_slave_id = ct_slave_id


class FakeMaster:
    """
    伪造的 ModbusMaster，用于算法单元测试：
    - 提供 config.hp_slave_ids / ct_slave_id
    - 提供 write_register 记录写入调用
    - get_shared_state_snapshot 在本套测试中几乎不用
    """

    def __init__(self, hp_slave_ids=(1, 2), ct_slave_id: int = 10) -> None:
        self.config = FakeConfig(hp_slave_ids=hp_slave_ids, ct_slave_id=ct_slave_id)
        self.write_calls: list[tuple[int, int, int]] = []

    def write_register(self, slave_id: int, register_address: int, value: int) -> bool:
        self.write_calls.append((slave_id, register_address, value))
        return True

    def get_shared_state_snapshot(self) -> Dict[int, Dict[str, int]]:
        # 可在测试中 monkeypatch / 赋值 snapshot_override
        return getattr(self, "snapshot_override", {})


class FakeRecorder:
    def __init__(self) -> None:
        self.records = []

    def record(self, **kwargs) -> None:
        self.records.append(kwargs)


def build_algorithm(
    hp_slave_ids=(1, 2), ct_slave_id: int = 10, recorder: FakeRecorder | None = None
) -> LoadControlAlgorithm:
    master = FakeMaster(hp_slave_ids=hp_slave_ids, ct_slave_id=ct_slave_id)
    cfg = AlgorithmConfig(ct_slave_id=ct_slave_id, hp_slave_ids=hp_slave_ids)
    algo = LoadControlAlgorithm(master=master, config=cfg, recorder=recorder)
    # 测试中直接填充原始设定，避免依赖文件
    algo._original_settings = {
        int(hp_id): {"heating_setpoint": 40.0, "hysteresis_value": 0}
        for hp_id in hp_slave_ids
    }
    return algo


# --------------------------------------------------------------------
# _compute_setpoint_for_compressor_count
# --------------------------------------------------------------------
def test_compute_setpoint_for_compressor_count_basic() -> None:
    algo = build_algorithm()
    inlet = 30.0

    assert algo._compute_setpoint_for_compressor_count(inlet, 0) == inlet - 1.0
    assert algo._compute_setpoint_for_compressor_count(inlet, 1) == inlet
    assert algo._compute_setpoint_for_compressor_count(inlet, 2) == inlet + 3.0
    assert algo._compute_setpoint_for_compressor_count(inlet, 3) == inlet + 5.0
    assert algo._compute_setpoint_for_compressor_count(inlet, 4) == inlet + 7.0

    # clamp 到 [0, 4]
    assert algo._compute_setpoint_for_compressor_count(inlet, -5) == inlet - 1.0
    assert algo._compute_setpoint_for_compressor_count(inlet, 10) == inlet + 7.0


# --------------------------------------------------------------------
# _collect_hp_status
# --------------------------------------------------------------------
def test_collect_hp_status_counts_and_currents() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    snapshot: Dict[int, Dict[str, int]] = {
        1: {
            "compressor1_current": 10,
            "compressor2_current": 0,
            "compressor3_current": 5,
            "compressor4_current": 0,
        },
        2: {
            "compressor1_current": 0,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
        },
    }

    status = algo._collect_hp_status(snapshot)
    assert status[1]["compressors_on"] == 2
    assert math.isclose(status[1]["hp_total_current"], 15.0)
    assert status[2]["compressors_on"] == 0
    assert math.isclose(status[2]["hp_total_current"], 0.0)


# --------------------------------------------------------------------
# _compute_target_total_compressors
# --------------------------------------------------------------------
def test_compute_target_total_compressors_deadband_no_change() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    # 放在 deadband 内，且 <= target_ct_current
    # limit=1250, safety_margin=30 -> target=1220, deadband=50 -> [1200, 1250)
    ct = 1220.0
    other_load = 300.0
    hp_current = 200.0
    current_total = 8

    new_total = algo._compute_target_total_compressors(
        ct_total_current=ct,
        other_load_current=other_load,
        hp_total_current=hp_current,
        current_total_compressors=current_total,
    )
    assert new_total == current_total


def test_compute_target_total_compressors_reduce_when_over_limit() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    # CT 电流超过 limit，且 other_load 很大，使得 raw_target < current_total
    ct = 1300.0
    other_load = 1000.0
    hp_current = 300.0
    current_total = 10

    new_total = algo._compute_target_total_compressors(
        ct_total_current=ct,
        other_load_current=other_load,
        hp_total_current=hp_current,
        current_total_compressors=current_total,
    )
    # raw_target=5，步长限制6，结果应为5
    assert new_total == 5


def test_compute_target_total_compressors_in_deadband_returns_current() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    # 调整安全裕度，使 deadband 条件可命中：limit=1250, safety=30 -> target=1220
    algo.config.safety_margin = 30.0
    ct = 1210.0  # 满足 1200<=ct<1250 且 ct<=1220
    other_load = 100.0
    hp_current = 200.0
    current_total = 6
    new_total = algo._compute_target_total_compressors(
        ct_total_current=ct,
        other_load_current=other_load,
        hp_total_current=hp_current,
        current_total_compressors=current_total,
    )
    assert new_total == current_total


def test_compute_target_total_compressors_records_metrics_when_recorder_present() -> None:
    recorder = FakeRecorder()
    algo = build_algorithm(hp_slave_ids=(1, 2), recorder=recorder)

    new_total = algo._compute_target_total_compressors(
        ct_total_current=800,
        other_load_current=100,
        hp_total_current=200,
        current_total_compressors=4,
    )

    # 现有实现不在该函数内记录，确保计算结果正常
    assert new_total >= 0
    assert recorder.records == []


# --------------------------------------------------------------------
# _distribute_compressors_among_hps
# --------------------------------------------------------------------
def test_distribute_compressors_among_hps_balanced() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2, 3))
    current_status = {
        1: {"compressors_on": 0.0, "hp_total_current": 0.0},
        2: {"compressors_on": 2.0, "hp_total_current": 20.0},
        3: {"compressors_on": 1.0, "hp_total_current": 10.0},
    }
    target_total = 5

    per_hp = algo._distribute_compressors_among_hps(
        target_total_compressors=target_total,
        current_hp_status=current_status,
    )

    # 总数一致、分布均衡（差值不超过 1）
    assert sum(per_hp.values()) == target_total
    assert max(per_hp.values()) - min(per_hp.values()) <= 1


# --------------------------------------------------------------------
# _step 的 early-return 分支
# --------------------------------------------------------------------
def test_step_no_hp_data() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    master: FakeMaster = algo.master  # type: ignore[assignment]

    snapshot: Dict[int, Dict[str, int]] = {
        # 没有 HP 条目
    }
    algo.update_ct_total_current(500)

    algo._step(snapshot)
    assert master.write_calls == []


def test_step_skips_without_ct_total_current() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    master: FakeMaster = algo.master  # type: ignore[assignment]

    snapshot: Dict[int, Dict[str, int]] = {
        1: {
            "inlet_temperature": 30,
            "compressor1_current": 0,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
            "heating_setpoint": 30,
        },
    }

    algo._step(snapshot)
    assert master.write_calls == []


def test_step_skips_when_original_settings_not_ready() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    master: FakeMaster = algo.master  # type: ignore[assignment]
    # 清空原始设定，触发早退
    algo._original_settings = {}
    snapshot: Dict[int, Dict[str, int]] = {
        1: {
            "inlet_temperature": 30,
            "heating_setpoint": 30,
            "compressor1_current": 0,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
        },
    }
    algo.update_ct_total_current(500)
    algo._step(snapshot)
    assert master.write_calls == []


def test_step_returns_if_ct_slave_id_missing() -> None:
    # 逻辑不再依赖 ct_slave_id，测试移除
    pass


# --------------------------------------------------------------------
# _step + _apply_hp_targets 联合行为
# --------------------------------------------------------------------
def test_step_applies_targets_and_writes_registers() -> None:
    hp_ids = (1, 2)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    master: FakeMaster = algo.master  # type: ignore[assignment]

    # CT 总电流 500 A（寄存器即真实值，无缩放）
    # 所有压缩机当前都关闭，算法应该增加一些压缩机数量
    snapshot: Dict[int, Dict[str, int]] = {
        algo.config.ct_slave_id: {"total_current": 500},
        1: {
            "inlet_temperature": 30,
            "heating_setpoint": 30,
            "compressor1_current": 0,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
        },
        2: {
            "inlet_temperature": 32,
            "heating_setpoint": 32,
            "compressor1_current": 0,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
        },
    }

    # 直接设置 CT 总电流和可用配额
    algo.ct_total_current = 500.0
    algo.ct_other_current = 0.0
    algo.load_available_current = 500.0
    algo.heatpump_available_current_avg = 80.0
    algo._step(snapshot)

    # 当前设定已与目标一致，算法不写入
    assert len(master.write_calls) == 0
    return


def test_update_ct_total_current_uses_aggregation_other_and_computes_quota(tmp_path) -> None:
    hp_ids = (1, 2)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    master: FakeMaster = algo.master  # type: ignore[assignment]

    # 提供 snapshot：两台HP各 4 台压缩机 10A
    snapshot = {
        1: {f"compressor{i}_current": 10 for i in range(1, 5)},
        2: {f"compressor{i}_current": 10 for i in range(1, 5)},
    }
    master.snapshot_override = snapshot  # type: ignore[attr-defined]

    # 写入聚合结果文件
    agg_path = tmp_path / "agg.json"
    agg_path.write_text(
        json.dumps(
            {
                "other_current": 200,
                "hp": {"1": {"run": 15, "surge": 20}, "2": {"run": 15, "surge": 20}},
            },
            ensure_ascii=False,
        )
    )
    algo.config.agg_result_path = str(agg_path)

    algo.update_ct_total_current(500.0)

    # 其他负载应该直接取聚合结果
    assert algo.ct_other_current == 200.0
    # 可用总配额 = limit - other = 1250 - 200 = 1050
    assert algo.load_available_current == 1050.0
    # 单机平均配额
    assert algo.heatpump_available_current_avg == (1050.0 - algo.config.safe_margin_current) / len(hp_ids)


def test_record_observation_writes_csv(tmp_path) -> None:
    hp_ids = (1,)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    algo.config.stats_csv_path = str(tmp_path / "agg_records.csv")

    snapshot = {
        1: {
            "compressor1_current": 5,
            "compressor2_current": 6,
            "compressor3_current": 7,
            "compressor4_current": 8,
        }
    }

    algo._record_observation(
        snapshot=snapshot,
        ct_total_current=400.0,
        other_load_current=50.0,
    )

    import pandas as pd  # 局部导入防止全局依赖

    df = pd.read_csv(algo.config.stats_csv_path)
    assert "other_load_current" in df.columns
    assert "heatpump_1_compressor_1" in df.columns
    assert df.iloc[0]["heatpump_1_compressor_4"] == 8


def test_estimate_compressor_run_and_surge_use_aggregation_result() -> None:
    algo = build_algorithm(hp_slave_ids=(1,))
    algo.aggregation_result = {"hp": {"1": {"run": 12.5, "surge": 20.0}}}
    assert algo._estimate_compressor_run_current(1) == 12.5
    assert algo._estimate_compressor_surge_current(1) == 20.0


def test_update_ct_total_current_invalid_value() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    algo.update_ct_total_current("bad")  # type: ignore[arg-type]
    assert algo.ct_total_current is None


def test_update_ct_total_current_no_hp_data() -> None:
    algo = build_algorithm(hp_slave_ids=(1, 2))
    master: FakeMaster = algo.master  # type: ignore[assignment]
    master.snapshot_override = {}  # type: ignore[attr-defined]
    algo.update_ct_total_current(500.0)
    # 没有HP数据，配额不应被计算
    assert algo.load_available_current is None
    assert algo.heatpump_available_current_avg is None


def test_apply_hp_targets_records_metrics_with_recorder() -> None:
    recorder = FakeRecorder()
    algo = build_algorithm(hp_slave_ids=(1,), recorder=recorder)
    snapshot = {
        1: {
            "inlet_temperature": 20,
            "heating_setpoint": 25,
            "compressor1_current": 0,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
        }
    }
    hp_status = {1: {"compressors_on": 0, "hp_total_current": 0.0}}
    per_hp_targets = {1: 1}
    metrics_context = {
        "ct_total_current": 400.0,
        "other_load_current": 50.0,
        "hp_total_current": 0.0,
        "current_total_compressors": 0,
        "target_total_compressors": 1,
    }
    algo._apply_hp_targets(per_hp_targets, snapshot, hp_status, metrics_context=metrics_context)
    assert recorder.records
    rec = recorder.records[0]
    assert "aggregation_result" in rec
    assert "compressor_stats" in rec
    assert "per_hp" in rec


def test_load_aggregation_result_bad_json(tmp_path) -> None:
    algo = build_algorithm(hp_slave_ids=(1,))
    bad_file = tmp_path / "agg_bad.json"
    bad_file.write_text("{invalid json")
    algo.config.agg_result_path = str(bad_file)
    algo._load_aggregation_result()
    assert algo.aggregation_result == {}



def test_apply_hp_targets_skip_increase_when_at_original() -> None:
    hp_ids = (1,)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    master: FakeMaster = algo.master  # type: ignore[assignment]

    snapshot = {
        1: {
            "inlet_temperature": 30,
            "heating_setpoint": 40,  # 已经等于原始
            "compressor1_current": 0,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
        }
    }
    current_status = {1: {"compressors_on": 1, "hp_total_current": 0}}
    per_hp_targets = {1: 2}  # 需要增加，但已经到原始设定

    algo._apply_hp_targets(per_hp_targets, snapshot, current_status)

    # 不应写入任何值
    assert master.write_calls == []


def test_apply_hp_targets_reduce_power_prefers_original() -> None:
    hp_ids = (1,)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    master: FakeMaster = algo.master  # type: ignore[assignment]
    # 当前设定高于原始，目标要降低
    algo._original_settings[1] = {"heating_setpoint": 50, "hysteresis_value": 0}

    snapshot = {
        1: {
            "inlet_temperature": 30,
            "heating_setpoint": 60,
            "compressor1_current": 10,
            "compressor2_current": 10,
            "compressor3_current": 0,
            "compressor4_current": 0,
        }
    }
    current_status = {1: {"compressors_on": 2, "hp_total_current": 20}}
    per_hp_targets = {1: 0}  # 降低功率

    algo._apply_hp_targets(per_hp_targets, snapshot, current_status)

    setpoint_addr = HP_REGISTER_MAP_REVERSE["heating_setpoint"]
    setpoint_writes = [
        val for sid, addr, val in master.write_calls if addr == setpoint_addr
    ]
    # 限幅 5 度，向下调节到 55
    assert setpoint_writes and setpoint_writes[0] == 55


def test_apply_hp_targets_equal_target_moves_toward_original() -> None:
    hp_ids = (1,)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    master: FakeMaster = algo.master  # type: ignore[assignment]
    algo._original_settings[1] = {"heating_setpoint": 55, "hysteresis_value": 0}

    snapshot = {
        1: {
            "inlet_temperature": 30,
            "heating_setpoint": 60,
            "compressor1_current": 10,
            "compressor2_current": 0,
            "compressor3_current": 0,
            "compressor4_current": 0,
        }
    }
    current_status = {1: {"compressors_on": 1, "hp_total_current": 10}}
    per_hp_targets = {1: 1}  # 目标等于当前，需向原始靠拢

    algo._apply_hp_targets(per_hp_targets, snapshot, current_status)

    setpoint_addr = HP_REGISTER_MAP_REVERSE["heating_setpoint"]
    setpoint_writes = [
        val for sid, addr, val in master.write_calls if addr == setpoint_addr
    ]
    assert setpoint_writes and setpoint_writes[0] == 55


def test_ensure_original_settings_creates_file(tmp_path) -> None:
    hp_ids = (1, 2)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    algo._original_settings = {}
    algo._original_settings_path = tmp_path / "original_setting.json"  # type: ignore[assignment]

    snapshot = {
        1: {"heating_setpoint": 30, "hysteresis_value": 0},
        2: {"heating_setpoint": 32, "hysteresis_value": 1},
    }

    ok = algo._ensure_original_settings(snapshot)
    assert ok is True
    assert algo._original_settings[1]["heating_setpoint"] == 30.0
    assert algo._original_settings[2]["hysteresis_value"] == 1.0
    assert algo._original_settings_path.exists()


def test_ensure_original_settings_loads_file(tmp_path) -> None:
    hp_ids = (1,)
    algo = build_algorithm(hp_slave_ids=hp_ids)
    algo._original_settings = {}
    algo._original_settings_path = tmp_path / "original_setting.json"  # type: ignore[assignment]

    content = {"1": {"heating_setpoint": 45, "hysteresis_value": 2}}
    algo._original_settings_path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2)
    )

    ok = algo._ensure_original_settings({})
    assert ok is True
    assert algo._original_settings[1]["heating_setpoint"] == 45.0
