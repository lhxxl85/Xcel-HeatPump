# 聚合与预测模块：基于观测CSV计算运行/浪涌电流与其他负载电流预测
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Any, Optional, Iterable

import pandas as pd


@dataclass
class AggregationConfig:
    csv_path: str = "logs/aggregation_records.csv"
    json_output_path: str = "aggregation_result.json"
    min_samples: int = 100
    hp_ids: Iterable[int] = (1, 2, 3, 4, 5, 6)
    compressor_on_threshold: float = 1.0  # 过滤非运行状态
    surge_percentile: float = 0.95  # 浪涌估计的分位数


class Aggregation:
    def __init__(self, config: Optional[AggregationConfig] = None) -> None:
        self.config = config or AggregationConfig()

    def _load_csv(self) -> Optional[pd.DataFrame]:
        path = Path(self.config.csv_path)
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
            return df
        except Exception:
            return None

    def _calc_hp_stats(self, df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
        """
        计算每台热泵的压缩机运行均值和浪涌电流（过滤非运行状态）
        """
        hp_stats: Dict[str, Dict[str, float]] = {}
        for hp_id in self.config.hp_ids:
            # 收集 per_hp 压缩机电流列，如果存在
            comps: list[pd.Series] = []
            for comp_idx in range(1, 5):
                col = f"heatpump_{hp_id}_compressor_{comp_idx}"
                if col in df.columns:
                    comps.append(df[col])

            # 如果没有展开的压缩机列，则跳过该HP
            if not comps:
                continue

            # 合并所有压缩机电流
            comp_concat = pd.concat(comps, axis=1)
            # 过滤运行状态
            running_vals = comp_concat[
                comp_concat > self.config.compressor_on_threshold
            ].stack()
            if len(running_vals) == 0:
                run_avg = 0.0
            else:
                run_avg = float(running_vals.mean())
            surge = (
                float(running_vals.quantile(self.config.surge_percentile))
                if len(running_vals)
                else 0.0
            )
            hp_stats[str(hp_id)] = {"run": run_avg, "surge": surge}

        return hp_stats

    def _predict_other_current(self, df: pd.DataFrame) -> float:
        """
        简单预测：使用最近窗口的指数滑动平均
        """
        if "other_load_current" not in df.columns:
            return 0.0
        tail = df["other_load_current"].tail(20)
        if len(tail) == 0:
            return 0.0
        # EMA，alpha 可按需配置，默认为 0.3
        ema = tail.ewm(alpha=0.3, adjust=False).mean().iloc[-1]
        return float(ema)

    def run(self) -> Optional[Dict[str, Any]]:
        """
        主入口：加载CSV，验证样本数，计算 run/surge/other_current 并写入JSON
        """
        df = self._load_csv()
        if df is None or len(df) < self.config.min_samples:
            return None

        hp_stats = self._calc_hp_stats(df)
        other_pred = self._predict_other_current(df)

        result = {"hp": hp_stats, "other_current": other_pred}
        try:
            Path(self.config.json_output_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2)
            )
        except Exception:
            pass
        return result


__all__ = ["Aggregation", "AggregationConfig"]
