# 定义的热泵和CT的485寄存器地址与含义映射关系
# src/hp_controller/master/register_mapping.py
from __future__ import annotations

from typing import Dict

# 热泵寄存器地址映射
# 真实热泵485地址
# 0x0079 Inlet Water Temperature
# 0x0003 Heating Setpoint
# 0x0004 Hysteresis Value
# 0x008E Current of Compressor 1
# 0x008F Current of Compressor 2
# 0x0090 Current of Compressor 3
# 0x0091 Current of Compressor 4
HP_REGISTER_MAP: Dict[int, str] = {
    0x0079: "inlet_temperature",
    0x0003: "heating_setpoint",
    0x0004: "hysteresis_value",
    0x008E: "compressor1_current",
    0x008F: "compressor2_current",
    0x0090: "compressor3_current",
    0x0091: "compressor4_current",
}

# CT寄存器地址映射
CT_REGISTER_MAP: Dict[int, str] = {
    7: "total_current",
}

# 反向映射，便于根据名称查找寄存器地址
HP_REGISTER_MAP_REVERSE: Dict[str, int] = {v: k for k, v in HP_REGISTER_MAP.items()}
CT_REGISTER_MAP_REVERSE: Dict[str, int] = {v: k for k, v in CT_REGISTER_MAP.items()}
