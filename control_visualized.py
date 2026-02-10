# 控制逻辑记录可视化/整理脚本
# 仅保留核心字段，生成精简版 combined_algorithm_metrics.csv
import ast
from pathlib import Path
from typing import Dict, Iterable, Any

import pandas as pd

LOGS_DIR = Path("logs/algorithm_metrics")
OUTPUT_PATH = Path("logs/combined_algorithm_metrics.csv")
HP_IDS: Iterable[int] = (1, 2, 3, 4, 5, 6)


def _safe_eval(obj: Any) -> Dict:
    """将字符串字典安全转换为 Python 对象，异常时返回空字典。"""
    if isinstance(obj, dict):
        return obj
    if not isinstance(obj, str) or not obj.strip():
        return {}
    try:
        return ast.literal_eval(obj)
    except Exception:
        return {}


def load_raw_metrics() -> pd.DataFrame:
    """读取日志目录内所有 CSV，并附加来源文件名。"""
    frames = []
    for path in sorted(LOGS_DIR.glob("*.csv")):
        df = pd.read_csv(path)
        df["source_file"] = path.name
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def flatten_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    按现有 CSV 结构扁平化：
    - CT 总电流、其他负载、电流配额等公共字段
    - 每台热泵：总电流、可用配额、进水温度、当前设定、压缩机数量
    - 每台热泵的每个压缩机电流（优先 per_hp 实时值，缺失则尝试 compressor_stats 的 avg）
    """
    records = []
    for _, row in df.iterrows():
        hp_totals = _safe_eval(row.get("hp_total_currents_map", {}))

        base = {
            "timestamp": pd.to_datetime(row.get("timestamp"), unit="s", errors="coerce"),
            "source_file": row.get("source_file"),
            "ct_total_current": row.get("ct_total_current"),
            "other_current": row.get("other_load_current"),
            "hp_total_current": row.get("hp_total_current"),
            "current_total_compressors": row.get("current_total_compressors"),
            "raw_target": row.get("raw_target"),
            "new_total": row.get("new_total"),
            "load_available_current": row.get("load_available_current"),
            "heatpump_available_current_avg": row.get("heatpump_available_current_avg"),
        }

        hp_data = dict(base)
        for hp_id in HP_IDS:
            hp_key = str(hp_id)
            # 从字典中获得每个热泵的电流数据
            hp_total_current = hp_totals.get(hp_id)
            # 从新定义一个key，叫做 hp_{hp_id}_total_current
            hp_data[f"hp_{hp_key}_total_current"] = hp_total_current
    

        records.append(hp_data)

    flat_df = pd.DataFrame(records)
    flat_df.sort_values("timestamp", inplace=True)
    return flat_df

def main() -> None:
    df_raw = load_raw_metrics()
    if df_raw.empty:
        print(f"目录 {LOGS_DIR} 下未找到 CSV，未生成输出。")
        return

    df_flat = flatten_metrics(df_raw)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_flat.to_csv(OUTPUT_PATH, index=False)
    print(f"生成 {OUTPUT_PATH}，共 {len(df_flat)} 行，{len(df_flat.columns)} 列。")
    # 把df_flat的timestamp转换成pd.Timestamp类型
    df_flat['timestamp'] = pd.to_datetime(df_flat['timestamp'])
    df_flat.set_index('timestamp', inplace=True)
    df_flat.sort_index(inplace=True)
    # 用matplotlib画出ct_total_current和hp_total_current的折线图
    import matplotlib.pyplot as plt
    # 采用subplots方式，一共画三个图
    fig, axs = plt.subplots(3, 1, figsize=(12, 18), sharex=True)
    # 图1画ct_total_current, other_current, hp_total_current
    axs[0].plot(df_flat.index, df_flat['ct_total_current'], label='CT Total Current', color='blue')
    axs[0].plot(df_flat.index, df_flat['other_current'], label='Other Load Current', color='orange')
    axs[0].plot(df_flat.index, df_flat['hp_total_current'], label='HP Total Current', color='green')
    axs[0].set_title('Current Overview')
    axs[0].set_ylabel('Current (A)')
    axs[0].legend()
    axs[0].grid()
    # 图2画current_total_compressors, raw_target, new_total
    axs[1].plot(df_flat.index, df_flat['current_total_compressors'], label='Current Total Compressors', color='purple')
    axs[1].plot(df_flat.index, df_flat['raw_target'], label='Raw Target', color='red')
    axs[1].plot(df_flat.index, df_flat['new_total'], label='New Total', color='brown')
    axs[1].set_title('Compressor and Target Overview')
    axs[1].set_ylabel('Count / Target')
    axs[1].legend()
    axs[1].grid()
    # 图3化heatpump_available_current_avg和以及hp_{hp_id}_total_current
    axs[2].plot(df_flat.index, df_flat['heatpump_available_current_avg'], label='HP Available Current Avg', color='cyan')
    for hp_id in HP_IDS:
        hp_key = str(hp_id)
        axs[2].plot(df_flat.index, df_flat[f'hp_{hp_key}_total_current'], label=f'HP {hp_id} Total Current')
    axs[2].set_title('Heatpump Current Overview')
    axs[2].set_ylabel('Current (A)')
    axs[2].legend()
    axs[2].grid()
    plt.xlabel('Timestamp')
    plt.tight_layout()
    plt.savefig("logs/algorithm_metrics_overview.png")
    plt.show()

if __name__ == "__main__":
    main()
