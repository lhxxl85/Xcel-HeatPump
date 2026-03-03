const BIT_REGISTERS = {
  0x0000: {
    section: 'control',
    mode: 'writable',
    nameEn: 'control_flag_1',
    nameZh: '控制位1',
    bits: [
      { bit: 7, en: 'reserved', zh: '预留' },
      { bit: 6, en: 'water_pump_mode', zh: '水泵模式' },
      { bit: 5, en: 'central_control', zh: '集中控制' },
      { bit: 4, en: 'electronic_expansion_valve', zh: '电子膨胀阀' },
      { bit: 3, en: 'water_flow_switch', zh: '水流开关' },
      { bit: 2, en: 'eco_heating', zh: '节能制热' },
      { bit: 1, en: 'circulation_pump', zh: '循环泵' },
      { bit: 0, en: 'power_state', zh: '开机状态' }
    ]
  },
  0x0001: {
    section: 'control',
    mode: 'writable',
    nameEn: 'control_flag_2',
    nameZh: '控制位2',
    bits: [
      { bit: 7, en: 'energy_level_control', zh: '能级控制' },
      { bit: 6, en: 'startup_spray', zh: '开机喷液' },
      { bit: 5, en: 'region', zh: '地区' },
      { bit: 4, en: 'reserved', zh: '预留' },
      { bit: 3, en: 'timer_eco_4', zh: '定时节能4' },
      { bit: 2, en: 'timer_eco_3', zh: '定时节能3' },
      { bit: 1, en: 'timer_eco_2', zh: '定时节能2' },
      { bit: 0, en: 'timer_eco_1', zh: '定时节能1' }
    ]
  },
  0x006D: {
    section: 'output',
    mode: 'status',
    nameEn: 'output_flag_1',
    nameZh: '输出标志1',
    bits: [
      { bit: 7, en: 'enthalpy_valve', zh: '增焓阀' },
      { bit: 6, en: 'four_way_valve', zh: '四通阀' },
      { bit: 5, en: 'fan', zh: '风机' },
      { bit: 4, en: 'water_pump', zh: '水泵' },
      { bit: 3, en: 'compressor_4', zh: '压机4' },
      { bit: 2, en: 'compressor_3', zh: '压机3' },
      { bit: 1, en: 'compressor_2', zh: '压机2' },
      { bit: 0, en: 'compressor_1', zh: '压机1' }
    ]
  },
  0x006E: {
    section: 'output',
    mode: 'status',
    nameEn: 'output_flag_2',
    nameZh: '输出标志2',
    bits: [
      { bit: 7, en: 'chassis_electrical_heating', zh: '底盘电加热' },
      { bit: 6, en: 'water_supply_valve', zh: '供水阀' },
      { bit: 5, en: 'water_return_valve', zh: '回水阀' },
      { bit: 4, en: 'water_makeup_valve', zh: '补水阀' },
      { bit: 3, en: 'fault_output', zh: '故障输出' },
      { bit: 2, en: 'electrical_heating', zh: '电加热' },
      { bit: 1, en: 'three_way_valve', zh: '三通阀' },
      { bit: 0, en: 'spray_valve', zh: '喷液阀' }
    ]
  },
  0x006F: {
    section: 'output',
    mode: 'status',
    nameEn: 'output_flag_3',
    nameZh: '输出标志3',
    bits: [
      { bit: 7, en: 'reserved', zh: '预留' },
      { bit: 6, en: 'reserved', zh: '预留' },
      { bit: 5, en: 'reserved', zh: '预留' },
      { bit: 4, en: 'reserved', zh: '预留' },
      { bit: 3, en: 'reserved', zh: '预留' },
      { bit: 2, en: 'reserved', zh: '预留' },
      { bit: 1, en: 'reserved', zh: '预留' },
      { bit: 0, en: 'solar_pump', zh: '太阳能水泵' }
    ]
  },
  0x0070: {
    section: 'status',
    mode: 'status',
    nameEn: 'running_status',
    nameZh: '运行状态',
    bits: [
      { bit: 7, en: 'reserved', zh: '预留' },
      { bit: 6, en: 'reserved', zh: '预留' },
      { bit: 5, en: 'reserved', zh: '预留' },
      { bit: 4, en: 'reserved', zh: '预留' },
      { bit: 3, en: 'reserved', zh: '预留' },
      { bit: 2, en: 'three_phase_detection', zh: '有三相检测功能' },
      { bit: 1, en: 'reserved', zh: '预留' },
      { bit: 0, en: 'defrost', zh: '除霜' }
    ]
  },
  0x0071: {
    section: 'alarm',
    mode: 'alarm',
    nameEn: 'fault_flag_1',
    nameZh: '故障标志1',
    bits: [
      { bit: 7, en: 'three_phase_missing_phase_fault', zh: '三相缺相故障' },
      { bit: 6, en: 'low_pressure_protection', zh: '低压保护' },
      { bit: 5, en: 'high_pressure_protection', zh: '高压保护' },
      { bit: 4, en: 'outlet_water_temperature_fault', zh: '出水温度故障' },
      { bit: 3, en: 'return_water_temperature_fault', zh: '回水温度故障' },
      { bit: 2, en: 'coil_temperature_fault', zh: '盘管温度故障' },
      { bit: 1, en: 'ambient_temperature_fault', zh: '环境温度故障' },
      { bit: 0, en: 'tank_temperature_fault', zh: '水箱温度故障' }
    ]
  },
  0x0072: {
    section: 'alarm',
    mode: 'alarm',
    nameEn: 'fault_flag_2',
    nameZh: '故障标志2',
    bits: [
      { bit: 7, en: 'low_pressure_2_protection', zh: '低压2保护' },
      { bit: 6, en: 'high_pressure_2_protection', zh: '高压2保护' },
      { bit: 5, en: 'coil_2_temperature_fault', zh: '盘管2温度故障' },
      { bit: 4, en: 'high_low_water_level_fault', zh: '高低水位故障' },
      { bit: 3, en: 'high_temperature_protection', zh: '高温保护' },
      { bit: 2, en: 'electrical_heating_overheat_protection', zh: '电加热过热保护' },
      { bit: 1, en: 'compressor_1_current_fault', zh: '压机1电流故障' },
      { bit: 0, en: 'water_flow_fault', zh: '水流故障' }
    ]
  },
  0x0073: {
    section: 'alarm',
    mode: 'alarm',
    nameEn: 'fault_flag_3',
    nameZh: '故障标志3',
    bits: [
      { bit: 7, en: 'coil_4_temperature_fault', zh: '盘管4温度故障' },
      { bit: 6, en: 'coil_3_temperature_fault', zh: '盘管3温度故障' },
      { bit: 5, en: 'exhaust_4_temperature_fault', zh: '排气4温度故障' },
      { bit: 4, en: 'exhaust_3_temperature_fault', zh: '排气3温度故障' },
      { bit: 3, en: 'exhaust_2_temperature_fault', zh: '排气2温度故障' },
      { bit: 2, en: 'exhaust_1_temperature_fault', zh: '排气1温度故障' },
      { bit: 1, en: 'reserved', zh: '预留' },
      { bit: 0, en: 'compressor_2_current_fault', zh: '压机2电流故障' }
    ]
  },
  0x0074: {
    section: 'alarm',
    mode: 'alarm',
    nameEn: 'fault_flag_4',
    nameZh: '故障标志4',
    bits: [
      { bit: 7, en: 'system_3_exhaust_overheat_protection', zh: '系统3排气温度过高保护' },
      { bit: 6, en: 'system_4_exhaust_overheat_protection', zh: '系统4排气温度过高保护' },
      { bit: 5, en: 'low_pressure_4_protection', zh: '低压4保护' },
      { bit: 4, en: 'high_pressure_4_protection', zh: '高压4保护' },
      { bit: 3, en: 'low_pressure_3_protection', zh: '低压3保护' },
      { bit: 2, en: 'high_pressure_3_protection', zh: '高压3保护' },
      { bit: 1, en: 'compressor_3_current_fault', zh: '压机3电流故障' },
      { bit: 0, en: 'compressor_4_current_fault', zh: '压机4电流故障' }
    ]
  },
  0x0075: {
    section: 'alarm',
    mode: 'alarm',
    nameEn: 'fault_flag_5',
    nameZh: '故障标志5',
    bits: [
      { bit: 7, en: 'mainboard_modbus_communication_fault', zh: '主板MODBUS通信故障' },
      { bit: 6, en: 'wired_controller_modbus_communication_fault', zh: '线控MODBUS通信故障' },
      { bit: 5, en: 'solar_temperature_fault', zh: '太阳能温度故障' },
      { bit: 4, en: 'suction_4_temperature_fault', zh: '回气4温度故障' },
      { bit: 3, en: 'suction_3_temperature_fault', zh: '回气3温度故障' },
      { bit: 2, en: 'suction_2_temperature_fault', zh: '回气2温度故障' },
      { bit: 1, en: 'suction_temperature_fault', zh: '回气温度故障' },
      { bit: 0, en: 'inlet_water_temperature_fault', zh: '进水温度故障' }
    ]
  },
  0x0076: {
    section: 'alarm',
    mode: 'alarm',
    nameEn: 'fault_flag_6',
    nameZh: '故障标志6',
    bits: [
      { bit: 7, en: 'ambient_temperature_too_low_protection', zh: '环境温度过低保护' },
      { bit: 6, en: 'ac_outlet_water_temperature_fault', zh: '空调出水温度故障' },
      { bit: 5, en: 'password_timeout_fault', zh: '密码限时故障' },
      { bit: 4, en: 'ambient_temperature_fault', zh: '环境温度故障' },
      { bit: 3, en: 'reserved', zh: '预留' },
      { bit: 2, en: 'reserved', zh: '预留' },
      { bit: 1, en: 'reserved', zh: '预留' },
      { bit: 0, en: 'reserved', zh: '预留' }
    ]
  },
  0x0077: {
    section: 'alarm',
    mode: 'alarm',
    nameEn: 'fault_flag_7',
    nameZh: '故障标志7',
    bits: [
      { bit: 7, en: 'cooling_subcooling_protection', zh: '制冷过冷保护' },
      { bit: 6, en: 'three_phase_wrong_phase', zh: '三相错相' },
      { bit: 5, en: 'anti_freeze_protection', zh: '防冻保护' },
      { bit: 4, en: 'defrost_subcooling_protection', zh: '除霜过冷保护' },
      { bit: 3, en: 'system_2_exhaust_overheat_protection', zh: '系统2排气温度过高保护' },
      { bit: 2, en: 'system_1_exhaust_overheat_protection', zh: '系统1排气温度过高保护' },
      { bit: 1, en: 'inlet_outlet_temp_delta_too_large_protection', zh: '进出水温差过大保护' },
      { bit: 0, en: 'wind_pressure_switch_fault', zh: '风压开关故障' }
    ]
  }
}

const PRIORITY_ORDER = [0x0000, 0x0001]

export function isBitRegisterAddress(address) {
  return Object.prototype.hasOwnProperty.call(BIT_REGISTERS, Number(address))
}

export function bitMask(bit) {
  return 1 << Number(bit)
}

export function isBitOn(rawValue, bit) {
  const val = Number(rawValue)
  if (!Number.isFinite(val)) {
    return false
  }
  return (val & bitMask(bit)) !== 0
}

export function setBitValue(rawValue, bit, enabled) {
  let next = Number(rawValue)
  if (!Number.isFinite(next)) {
    next = 0
  }
  if (enabled) {
    next |= bitMask(bit)
  } else {
    next &= ~bitMask(bit)
  }
  return next & 0xffff
}

function formatRegisterName(definition, lang) {
  return lang === 'zh' ? definition.nameZh : definition.nameEn
}

function defaultBits() {
  return Array.from({ length: 8 }, (_, idx) => {
    const bit = 7 - idx
    return { bit, en: `bit_${bit}`, zh: `位${bit}` }
  })
}

export function buildBitRegisterPanels(items, lang) {
  const bitItems = (items || []).filter((x) => isBitRegisterAddress(x.address))

  const sorted = [...bitItems].sort((a, b) => {
    const ai = PRIORITY_ORDER.indexOf(Number(a.address))
    const bi = PRIORITY_ORDER.indexOf(Number(b.address))
    if (ai !== -1 || bi !== -1) {
      if (ai === -1) return 1
      if (bi === -1) return -1
      return ai - bi
    }
    return Number(a.address) - Number(b.address)
  })

  return sorted.map((item) => {
    const address = Number(item.address)
    const definition = BIT_REGISTERS[address]
    const bits = (definition.bits || defaultBits()).map((b) => ({
      bit: b.bit,
      label: lang === 'zh' ? b.zh : b.en,
      on: isBitOn(item.value, b.bit)
    }))

    return {
      address,
      section: definition.section || 'status',
      mode: definition.mode,
      readOnly: item['read-only'] !== false,
      name: formatRegisterName(definition, lang),
      rawValue: Number(item.value) || 0,
      bits
    }
  })
}
