from __future__ import annotations

import re

# 根目录翻译文件：中文寄存器名 -> 英文寄存器名
# Redis key 会通过该映射统一输出英文寄存器名称。
CN_TO_EN_REGISTER_NAME: dict[str, str] = {
    "控制位1": "control_flag_1",
    "控制位2": "control_flag_2",
    "模式": "mode",
    "空调回差": "ac_hysteresis",
    "空调制热设置温度": "ac_heating_set_temperature",
    "空调制冷设置温度": "ac_cooling_set_temperature",
    "空调自动设置温度": "ac_auto_set_temperature",
    "热水回差": "hot_water_hysteresis",
    "水箱水温设定值": "tank_set_temperature",
    "曲线平移": "curve_offset",
    "曲线斜率": "curve_slope",
    "第一时段定时时间": "period1_timer",
    "第二时段定时时间": "period2_timer",
    "第三时段定时时间": "period3_timer",
    "第四时段定时时间": "period4_timer",
    "第1时段的制热设定温度": "period1_heating_set_temperature",
    "第2时段的制热设定温度": "period2_heating_set_temperature",
    "第3时段的制热设定温度": "period3_heating_set_temperature",
    "第4时段的制热设定温": "period4_heating_set_temperature",
    "允许电热开启环境温度": "electrical_heat_enable_ambient_temperature",
    "太阳能水泵启动回差设定": "solar_pump_start_hysteresis",
    "回水设定温度": "return_water_set_temperature",
    "允许补水温度": "make_up_water_allowed_temperature",
    "除霜进入盘管温度设定": "defrost_enter_coil_temperature",
    "除霜退出温度设定": "defrost_exit_temperature",
    "进入除霜环境与盘管温度差": "defrost_enter_ambient_coil_delta",
    "环温过低保护设定温": "low_ambient_protection_temperature",
    "电子膨胀阀排气控制回差": "eev_discharge_control_hysteresis",
    "制热目标过热度设定": "heating_target_superheat",
    "当环境温度≥17℃，电子膨胀阀的最小开度设定": "eev_min_opening_ambient_ge_17c",
    "当环境温度＜-9℃，电子膨胀阀的最小开度": "eev_min_opening_ambient_lt_minus9c",
    "水箱模式、制热模式设定温上限": "tank_and_heating_set_temperature_upper_limit",
    "水箱和进水温度与显示温度偏差设定": "tank_inlet_display_temp_offset",
    "喷液电磁阀回差设定": "spray_solenoid_hysteresis",
    "增焓电磁阀启动环温设定": "enthalpy_solenoid_start_ambient_temperature",
    "电加热用途": "electrical_heating_usage",
    "电加热延时启动时间": "electrical_heating_start_delay",
    "延长除霜周期的环境温度点设置": "extend_defrost_cycle_ambient_temperature_point",
    "压缩机电流设定": "compressor_current_setting",
    "除霜周期设定": "defrost_cycle_setting",
    "最长除霜时间设定": "max_defrost_time_setting",
    "模块调节周期": "module_adjustment_cycle",
    "启动水泵环境温度设定": "water_pump_start_ambient_temperature",
    "测试控制": "test_control",
    "机型": "unit_type",
    "输出标志1": "output_flag_1",
    "输出标志2": "output_flag_2",
    "输出标志3": "output_flag_3",
    "运行状态": "running_status",
    "故障标志1": "fault_flag_1",
    "故障标志2": "fault_flag_2",
    "故障标志3": "fault_flag_3",
    "故障标志4": "fault_flag_4",
    "故障标志5": "fault_flag_5",
    "故障标志6": "fault_flag_6",
    "故障标志7": "fault_flag_7",
    "故障代号": "fault_code",
    "进水温度": "inlet_water_temperature",
    "水箱温度": "tank_temperature",
    "外环境温度": "ambient_temperature",
    "回水温度": "return_water_temperature",
    "出水温度": "outlet_water_temperature",
    "压机1电流": "compressor_1_current",
    "压机2电流": "compressor_2_current",
    "压机3电流": "compressor_3_current",
    "压机4电流": "compressor_4_current",
    "current_l1": "current_l1",
    "current_l2": "current_l2",
    "current_l3": "current_l3",
    "total_current": "total_current",
}


EN_SAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_]+")


def to_english_register_name(register_name: str) -> str:
    """
    将寄存器名称转换为英文 key。
    - 已配置映射: 返回标准英文名。
    - 未配置映射: 尽量生成 ASCII 安全 key，避免 Redis key 出现中文。
    """
    mapped = CN_TO_EN_REGISTER_NAME.get(register_name)
    if mapped:
        return mapped

    fallback = EN_SAFE_PATTERN.sub("_", register_name.strip())
    fallback = fallback.strip("_").lower()
    if fallback:
        return fallback
    return "unknown_register"
