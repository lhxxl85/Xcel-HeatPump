# 热泵项目控制程序
## 项目目录
```bash
heatpump_controller
├── logs
│   ├── algorithm_metrics
├── src
│   ├── hp_controller
│   │   ├── master
│   │   │   ├── __init__.py
│   │   │   ├── algorithm.py  # 热泵控制核心算法逻辑
│   │   │   ├── client.py  # ModbusTCP客户端实现，用于与HP控制器进行通信
│   │   │   └── register_mapping.py  # 定义的热泵和CT的485寄存器地址与含义映射关系
│   │   ├── utils
│   │   │   ├── __init__.py
│   │   │   ├── logging_config.py  # logging配置文件
│   │   │   └── recorder.py  # 热泵算法的PD和CSV记录工具
│   │   ├── __init__.py
│   │   └── main.py  # 主程序入口
│   └── __init__.py
├── tests
│   ├── master
│   │   ├── test_algorithm.py  # 算法单元测试
│   │   └── test_client.py  # 客户端单元测试
│   └── utils_test
│       └── test_logger.py  # logger单元测试模块
├── .coverage
├── .coveragerc
├── .gitignore
├── control_visualized.py  # 可视化输出控制逻辑记录结果
├── drawio.xml
├── Makefile
├── poetry.lock
├── print_tree.py  # 自动代码结构打印脚本，方便查看项目目录结构
├── pyproject.toml
├── pytest.ini
└── README.md
```
## 算法基本策略
==LoadControlAlgorithm==的核心目标是：
> 在不超过配电保护电流上限的前提下，尽可能多地开启热泵压缩机，并在多台热泵之间均衡分配负载。

算法通过 CT 总电流 作为反馈量，实时估算“当前每台压缩机大概消耗多少电流”，再根据当前“非热泵负载”预留的电流空间，计算出 全场期望的压缩机总数量，然后把这个目标均匀分配到每台热泵上。
具体控制动作不是直接“开/关压缩机”，而是通过调整每台机组的 ==heating_setpoint==（供水温度设定点）和 ==hysteresis==，间接驱动现场控制逻辑去开关压缩机。

整体控制流程（单步 ==_step()==）可以概括为：
1. 从 ==ModbusMaster.shared_state== 中读取 CT 总电流 和各热泵的运行数据；

2. 统计当前所有热泵已经开启的压缩机数量，以及热泵总电流；

3. 根据 CT 电流上限、死区、当前平均每台压缩机电流等，计算“目标压缩机总数”；

4. 将目标压缩机数量在多台热泵之间 均衡分配；

5. 根据每台热泵的目标压缩机数，反推应该写入的 ==heating_setpoint==（以及固定的 ==hysteresis==），并通过 Modbus 写入。

### 配置项设计
==AlgorithmConfig==封装了算法的所有关键参数，便于现场调试和配置.
```python
@dataclass
class AlgorithmConfig:
    loop_interval_sec: float = 1.0
    ct_slave_id: int = 10
    hp_slave_ids: Iterable[int] = (1, 2, 3, 4, 5, 6)
    protection_current_limit: float = 1250.0
    deadband: float = 50.0
    safety_margin: float = 30.0
    fallback_per_compressor_current: float = 30.0
    max_compressor_changes_per_cycle: int = 2
    fixed_hysteresis: int = 0
    compressor_on_current_threshold: float = 1.0
```
各字段说明：

* loop_interval_sec
  控制主算法循环 _main_loop 的周期（秒）。每个周期会读取一次现场状态并执行一次 _step()。

* ct_slave_id
  CT 设备在 Modbus 总线上的从站 ID，用于在 snapshot 中找到对应寄存器数据。

* hp_slave_ids
所有热泵从站 ID 列表。算法会遍历这些 ID 去读取每台机的电流、温度等寄存器。

* protection_current_limit
配电的保护电流上限（安培），即 绝对不能长期超过 的总电流。

* deadband
死区宽度。当 CT 电流在 [limit - deadband, limit) 且未超过目标电流时，算法会 暂时不调整压缩机数量，以减少频繁的启停抖动。

* safety_margin
安全裕度。算法会把“期望 CT 总电流”设为
target_ct_current = protection_current_limit - safety_margin，
也就是 希望总电流永远低于上限一定距离，给现场留出余量。

* fallback_per_compressor_current
当当前没有任何压缩机开启，或者尚无法估计“平均每台压缩机电流”时，使用的兜底值，单位安培。
用于在“所有压缩机都关着”的情况下，也能根据 other_load_current 粗略算出还能开几台压缩机。

* max_compressor_changes_per_cycle
每个控制周期最多允许调整的压缩机数量（加起来的总数）。
例如设为 2，即使算法计算出理想状态需要增加 10 台压缩机，也只会在本周期增加 2 台，以避免动作过猛导致震荡。

* fixed_hysteresis
固定写入到热泵的 hysteresis_value（温度回差）。目前设计建议设为 0，
这样所有开关逻辑主要通过 heating_setpoint 的调整实现，hysteresis 只作为一个固定的现场确认值。

* compressor_on_current_threshold
判断一台压缩机是否“开启”的当前阈值（安培）。
单个压缩机电流大于等于该值，则认为这台压缩机处于 ON 状态。

### 单步控制逻辑
#### 读取 CT 数据
```python
ct_regs = snapshot.get(self.config.ct_slave_id)
ct_total_current = float(ct_regs.get("total_current", 0)) / 10.0
```
* 从 snapshot 中按 ct_slave_id 取出 CT 的寄存器字典；

* 从中读取 "total_current"，并除以 10 得到真实电流（现场按 0.1A 存储）。

如果 CT 数据不存在或解析失败，会直接 return，避免基于错误数据做控制。

#### 收集各热泵状态 _collect_hp_status()
```python
hp_status = {
  hp_id: {
    "compressors_on": int,   # 当前开机的压缩机数量 (0~4)
    "hp_total_current": float,  # 该台热泵总电流（A）
  },
  ...
}
```

内部逻辑：
1. 遍历 hp_slave_ids；

2. 对每台热泵，从 snapshot 中读取 compressor1_current ~ compressor4_current（0.1A 缩放）；

3. 按 compressor_on_current_threshold 统计当前开启压缩机数量 compressors_on；

4. 将 4 个压缩机电流相加，得到该机组总电流 hp_total_current。

如果某个从站数据缺失，会打印 warning 并跳过那台机。

#### 计算当前总压缩机数、热泵总电流及其他负载电流
```python
total_compressors_on = sum(status["compressors_on"] for status in hp_status.values())
hp_total_current = sum(status["hp_total_current"] for status in hp_status.values())
other_load_current = max(0.0, ct_total_current - hp_total_current)
```
* total_compressors_on：全场所有热泵当前已经开启的压缩机总数；

* hp_total_current：所有热泵总电流；

* other_load_current：由 CT 总电流减去热泵电流得到，表示现场 非热泵负载 的电流，最低为 0。

#### 关键算法：目标压缩机总数 _compute_target_total_compressors(...)
1. 估算“每台压缩机平均电流”

```python
max_compressors = len(tuple(self.config.hp_slave_ids)) * 4
if current_total_compressors > 0 and hp_total_current > 0.0:
    avg_per_compressor_current = hp_total_current / current_total_compressors
else:
    avg_per_compressor_current = self.config.fallback_per_compressor_current
```
* max_compressors：理论上全场最大压缩机数量 = 热泵数量 × 4；

* 如果当前有压缩机在运行，用现场实际的 hp_total_current / 当前压缩机数量 作为 “平均每台压缩机电流”；

* 如果目前一台都没开，则用 fallback_per_compressor_current 兜底。

2. 计算期望给热泵的电流 & 理论目标压缩机数
```python
limit = self.config.protection_current_limit
deadband = self.config.deadband
safety_margin = self.config.safety_margin

target_ct_current = limit - safety_margin
desired_hp_current = max(0.0, target_ct_current - other_load_current)
raw_target = int(desired_hp_current / max(avg_per_compressor_current, 0.1))
raw_target = max(0, min(max_compressors, raw_target))
```
* target_ct_current：我们希望 CT 总电流长期保持的目标值，略低于保护上限；

* desired_hp_current：扣除当前其他负载之后，还可以给热泵使用的电流空间；

* raw_target：用这个空间除以单台压缩机平均电流，得到“理论上可以开的压缩机数量”，再约束到 [0, max_compressors]。

3. 死区逻辑：减少无谓调整
```python
if (
    limit - deadband <= ct_total_current < limit
    and ct_total_current <= target_ct_current
):
    # 在死区内且未超过目标，保持不变
    return current_total_compressors
```
当 CT 总电流在 [limit - deadband, limit) 且不高于 target_ct_current 时，
即快到上限但仍在目标值附近，算法不会立即减小压缩机数量，以避免 1~2A 的微小波动导致频繁启停。

4. 超限保护 + 单周期步长限制
```python
if ct_total_current >= limit:
    self.logger.warning("... exceeds limit ..., reducing compressors")

delta = raw_target - current_total_compressors
max_step = self.config.max_compressor_changes_per_cycle
if delta > max_step:
    delta = max_step
elif delta < -max_step:
    delta = -max_step

new_total = current_total_compressors + delta
new_total = max(0, min(max_compressors, new_total))
```
* 当 CT 总电流超过 limit 时，会打印 warning，并优先向下调整；

* 不管理论目标是多少，每个周期最大只调整 max_compressor_changes_per_cycle 台；

* new_total 是本周期最终将要追求的 目标压缩机总数。

5. 多机均衡：目标压缩机在各热泵间的分配
```python
def _distribute_compressors_among_hps(...):
    hp_ids = list(self.config.hp_slave_ids)
    h_np = len(hp_ids)
    base = target_total_compressors // h_np
    remainder = target_total_compressors % h_np

    sorted_hp = sorted(
        hp_ids,
        key=lambda hid: current_hp_status.get(hid, {}).get("compressors_on", 0),
    )

    for idx, hp_id in enumerate(sorted_hp):
        target = base + (1 if idx < remainder else 0)
        per_hp_targets[hp_id] = max(0, min(4, int(target)))
```
* 先算出每台机的“基础目标数” base，以及余数 remainder；

* 按当前 已开启的压缩机数量升序排序——也就是当前开得少的机组优先拿到“+1”的机会；

* 前 remainder 台机器分配 base + 1 台，后面的分配 base；

* 每台机目标数限制在 0~4 范围内。


6. 最终控制动作：写入 heating_setpoint / hysteresis

```python
def _apply_hp_targets(self, per_hp_target, snapshot):
    for hp_id, target_compressors in per_hp_target.items():
        regs = snapshot.get(hp_id)
        t_in = float(regs.get("inlet_temperature", 0)) / 10.0

        new_setpoint = self._compute_setpoint_for_compressor_count(
            inlet_temp=t_in,
            target_compressors=target_compressors,
            hysteresis=self.config.fixed_hysteresis,
        )

        setpoint_address = HP_REGISTER_MAP_REVERSE["heating_setpoint"]

        ok_s = self.master.write_register(
            slave_id=hp_id,
            register_address=setpoint_address,
            value=int(new_setpoint * 10),
        )

        ok_h = self.master.write_register(
            slave_id=hp_id,
            register_address=HP_REGISTER_MAP_REVERSE["hysteresis_value"],
            value=int(self.config.fixed_hysteresis),
        )
```
* 读取当前 inlet_temperature（板换/进水温度）；

* 调用 _compute_setpoint_for_compressor_count()，根据 当前进水温度 + 目标压缩机数 反推应该写入的 heating_setpoint；

* 将计算结果以 0.1°C 精度写入热泵（乘以 10 存储）；

* 同时写入固定的 hysteresis_value（通常为 0）。
