# 聚合模块单元测试
from __future__ import annotations

import json
import pandas as pd

from hp_controller.master.aggregation import Aggregation, AggregationConfig


def test_run_returns_none_when_samples_insufficient(tmp_path) -> None:
    csv_path = tmp_path / "agg.csv"
    df = pd.DataFrame([{"other_load_current": 10}])  # 仅1条，不足 min_samples=100 默认
    df.to_csv(csv_path, index=False)

    agg = Aggregation(AggregationConfig(csv_path=str(csv_path), json_output_path=str(tmp_path / "out.json")))
    result = agg.run()
    assert result is None
    assert not (tmp_path / "out.json").exists()


def test_run_computes_stats_and_writes_json(tmp_path) -> None:
    csv_path = tmp_path / "agg.csv"
    # 构造 100 条记录，包含 other_load_current 和两个热泵的压缩机电流
    rows = []
    for _ in range(100):
        rows.append(
            {
                "other_load_current": 200,
                "heatpump_1_compressor_1": 10,
                "heatpump_1_compressor_2": 12,
                "heatpump_1_compressor_3": 0,  # 不运行
                "heatpump_1_compressor_4": 0,
                "heatpump_2_compressor_1": 20,
                "heatpump_2_compressor_2": 22,
                "heatpump_2_compressor_3": 0,
                "heatpump_2_compressor_4": 0,
            }
        )
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    out_path = tmp_path / "out.json"
    agg = Aggregation(
        AggregationConfig(
            csv_path=str(csv_path),
            json_output_path=str(out_path),
            min_samples=50,
            hp_ids=(1, 2),
            compressor_on_threshold=1.0,
            surge_percentile=0.9,
        )
    )
    result = agg.run()
    assert result is not None
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    # other_current 应为 200（EMA 同值）
    assert data["other_current"] == 200
    # HP1 运行均值在阈值之上，应大于 0
    assert data["hp"]["1"]["run"] > 0
    # HP2 浪涌为分位数，应该大于等于运行均值
    assert data["hp"]["2"]["surge"] >= data["hp"]["2"]["run"]


def test_calc_hp_stats_skips_missing_columns(tmp_path) -> None:
    csv_path = tmp_path / "agg.csv"
    pd.DataFrame([{"other_load_current": 10} for _ in range(120)]).to_csv(csv_path, index=False)
    agg = Aggregation(
        AggregationConfig(
            csv_path=str(csv_path),
            json_output_path=str(tmp_path / "out.json"),
            min_samples=1,
            hp_ids=(1,),
        )
    )
    result = agg.run()
    # 没有压缩机列应跳过 hp 计算
    assert result is not None
    assert result["hp"] == {}
