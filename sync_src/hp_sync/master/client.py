# ModbusTCP客户端实现，用于与HP控制器进行通信
# src/hp_controller/master/client.py
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
import struct
import time
from typing import Dict, Iterable, Mapping, Optional, Any

from pymodbus.client import ModbusTcpClient, ModbusSerialClient
from pymodbus.exceptions import ModbusException

from .register_mapping import CT_REGISTER_MAP, HP_REGISTER_MAP
from sync_src.hp_sync.utils.raw_modbus_logger import RawModbusFrameLogger

# ---------------------------------------------------------------------------
# Modbus 连接配置
# ---------------------------------------------------------------------------


@dataclass
class ModbusTcpConfig:
    host: str = "127.0.0.1"
    port: int = 5020
    timeout_sec: float = 3.0


@dataclass
class ModbusRtuConfig:
    port: str = "/dev/ttyUSB0"
    baudrate: int = 9600
    parity: str = "N"  # N, E, O
    stopbits: int = 1
    bytesize: int = 8
    timeout_sec: float = 3.0


@dataclass
class ModbusEndpointConfig:
    transport: str = "tcp"  # "tcp" | "rtu"
    tcp: ModbusTcpConfig = field(default_factory=ModbusTcpConfig)
    rtu: ModbusRtuConfig = field(default_factory=ModbusRtuConfig)

    def normalized_transport(self) -> str:
        return self.transport.strip().lower()


@dataclass
class HpBusConfig:
    name: str
    endpoint: ModbusEndpointConfig
    slave_ids: tuple[int, ...]


@dataclass
class ModbusConfig:
    hp_buses: tuple[HpBusConfig, ...] = field(
        default_factory=lambda: (
            HpBusConfig(
                name="AMA5",
                endpoint=ModbusEndpointConfig(
                    transport="rtu",
                    rtu=ModbusRtuConfig(port="/dev/ttyAMA5"),
                ),
                slave_ids=(1, 3, 4),
            ),
            HpBusConfig(
                name="AMA3",
                endpoint=ModbusEndpointConfig(
                    transport="rtu",
                    rtu=ModbusRtuConfig(port="/dev/ttyAMA3"),
                ),
                slave_ids=(5, 6, 7),
            ),
        )
    )
    ct: ModbusEndpointConfig = field(default_factory=ModbusEndpointConfig)
    ct_slave_id: int = 10
    # 是否允许写入寄存器（false 时只读）
    control_mode: bool = False
    # 针对热泵：分成多个连续读取块，减少Modbus调用次数
    hp_register_blocks: tuple[tuple[int, int], ...] = (
        (0x0000, 46),  # 0x0000～0x002D
        (0x006D, 0x0091 - 0x006D + 1),  # 0x0079～0x0091
    )
    # 针对CT：仍然从7号地址开始读取, 但是就只有一个寄存器
    ct_start_address: int = 13
    ct_register_count: int = 6
    poll_interval_sec: float = 0.5
    reconnect_interval_sec: float = 3.0
    request_gap_sec: float = 0.2

    @property
    def hp_slave_ids(self) -> tuple[int, ...]:
        slave_ids: list[int] = []
        for bus in self.hp_buses:
            slave_ids.extend(bus.slave_ids)
        return tuple(slave_ids)


@dataclass
class ModbusShareState:
    """
    公共共享区域：
    data[slave_id][register_name] = value
    """

    hp_data: Dict[int, Dict[str, int]] = field(default_factory=dict)
    hp_updated_at: Dict[int, float] = field(default_factory=dict)
    ct_data: Dict[int, Dict[str, int]] = field(default_factory=dict)
    ct_updated_at: Dict[int, float] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update_hp_slave(self, slave_id: int, registers: Mapping[str, int]) -> None:
        if len(registers) == 0:
            return
        with self.lock:
            merged = dict(self.hp_data.get(slave_id, {}))
            merged.update(registers)
            self.hp_data[slave_id] = merged
            self.hp_updated_at[slave_id] = time.monotonic()

    def update_ct_slave(self, slave_id: int, registers: Mapping[str, int]) -> None:
        if len(registers) == 0:
            return
        with self.lock:
            merged = dict(self.ct_data.get(slave_id, {}))
            merged.update(registers)
            self.ct_data[slave_id] = merged
            self.ct_updated_at[slave_id] = time.monotonic()

    def get_snapshot(self) -> Dict[int, Dict[str, int]]:
        with self.lock:
            data: Dict[int, Dict[str, int]] = {
                sid: regs.copy() for sid, regs in self.hp_data.items()
            }
            for sid, regs in self.ct_data.items():
                data[sid] = regs.copy()
            return data

    def get_fresh_snapshot(self, max_age_sec: float) -> Dict[int, Dict[str, int]]:
        now = time.monotonic()
        with self.lock:
            data: Dict[int, Dict[str, int]] = {
                sid: regs.copy()
                for sid, regs in self.hp_data.items()
                if (now - self.hp_updated_at.get(sid, 0.0)) <= max_age_sec
            }
            for sid, regs in self.ct_data.items():
                if (now - self.ct_updated_at.get(sid, 0.0)) <= max_age_sec:
                    data[sid] = regs.copy()
            return data

    def get_fresh_partitioned_snapshot(
        self, max_age_sec: float
    ) -> tuple[Dict[int, Dict[str, int]], Dict[int, Dict[str, int]]]:
        now = time.monotonic()
        with self.lock:
            hp = {
                sid: regs.copy()
                for sid, regs in self.hp_data.items()
                if (now - self.hp_updated_at.get(sid, 0.0)) <= max_age_sec
            }
            ct = {
                sid: regs.copy()
                for sid, regs in self.ct_data.items()
                if (now - self.ct_updated_at.get(sid, 0.0)) <= max_age_sec
            }
            return hp, ct

    def get_slave_registers(self, slave_id: int) -> Optional[Dict[str, int]]:
        with self.lock:
            regs = self.hp_data.get(slave_id)
            if regs is None:
                regs = self.ct_data.get(slave_id)
            if regs is not None:
                return regs.copy()
            return None


class ModbusMaster:
    """
    Modbus上位机
    - 支持 TCP 和 RTU 串口两种模式（由 MODBUS_TRANSPORT_MODE 控制）
    - 定期轮循：
      多总线热泵 slave: 由 hp_buses 配置决定
      slave 10: 电表
    - 读取到的数据存入ModbusShareState
    - 提供写接口：对1～6号从站的寄存器写入数据
    - 具有容错和灾难恢复
    """

    def __init__(
        self,
        config: Optional[ModbusConfig] = None,
        logger: Optional[logging.Logger] = None,
        raw_frame_logger: Optional[RawModbusFrameLogger] = None,
    ) -> None:
        base_logger = logger or logging.getLogger("hp_controller.master.client")
        self.logger = base_logger.getChild("ModbusMaster")
        self.raw_frame_logger = raw_frame_logger
        self.config = config or ModbusConfig()
        self.hp_bus_clients: dict[
            str,
            tuple[
                ModbusEndpointConfig,
                ModbusTcpClient | ModbusSerialClient,
                tuple[int, ...],
            ],
        ] = {}
        self.hp_client_by_slave: dict[int, ModbusTcpClient | ModbusSerialClient] = {}
        self.hp_endpoint_by_slave: dict[int, ModbusEndpointConfig] = {}
        self.hp_bus_name_by_slave: dict[int, str] = {}
        self._init_hp_bus_clients()
        self.ct_client = self._create_client(self.config.ct, "CT")
        self.shared_state = ModbusShareState()
        self._stop_event = threading.Event()
        self._hp_poll_thread: Optional[threading.Thread] = None
        self._ct_poll_thread: Optional[threading.Thread] = None
        self._last_reconnect_attempt_ts: dict[int, float] = {}
        self._last_response_ts_by_client: dict[int, float] = {}
        self._hp_comm_status: dict[int, int] = {}
        self._ct_comm_status: dict[int, int] = {}
        self._comm_status_lock = threading.Lock()
        self._hp_io_lock = threading.RLock()
        self._hp_poll_pause_event = threading.Event()

        # 构建一个从站ID列表
        self.slave_ids = list(self.config.hp_slave_ids) + [self.config.ct_slave_id]

        self.algorithm: Optional[Any] = (
            None  # 关联的算法模块 用于去更新算法模块内部状态
        )

    # --------------------------------------------------------------------------
    # 绑定算法模块
    # --------------------------------------------------------------------------
    def bind_algorithm(self, algorithm: Any) -> None:
        """
        绑定一个LoadControlAlgorithm实例，用于更新算法模块的内部状态
        """
        self.algorithm = algorithm

    def _init_hp_bus_clients(self) -> None:
        for bus in self.config.hp_buses:
            label = f"HP[{bus.name}]"
            client = self._create_client(bus.endpoint, label)
            self.hp_bus_clients[bus.name] = (bus.endpoint, client, bus.slave_ids)
            for slave_id in bus.slave_ids:
                if slave_id in self.hp_client_by_slave:
                    prev_bus = self.hp_bus_name_by_slave[slave_id]
                    self.logger.warning(
                        "Duplicate HP slave_id=%s found in bus '%s' and '%s', overriding with '%s'",
                        slave_id,
                        prev_bus,
                        bus.name,
                        bus.name,
                    )
                self.hp_client_by_slave[slave_id] = client
                self.hp_endpoint_by_slave[slave_id] = bus.endpoint
                self.hp_bus_name_by_slave[slave_id] = bus.name

    # ---------------------------------------------------------------------------
    # 根据配置创建Modbus客户端
    # ---------------------------------------------------------------------------
    def _create_client(
        self, endpoint: ModbusEndpointConfig, label: str
    ) -> ModbusTcpClient | ModbusSerialClient:
        mode = endpoint.normalized_transport()
        if mode == "tcp":
            tcp = endpoint.tcp
            self.logger.info(
                f"Using Modbus TCP Client for {label} on {tcp.host}:{tcp.port}"
            )
            return ModbusTcpClient(
                host=tcp.host,
                port=tcp.port,
                timeout=tcp.timeout_sec,
            )
        elif mode == "rtu":
            rtu = endpoint.rtu
            self.logger.info(
                f"Using Modbus RTU Serial Client for {label} on port {rtu.port}, baudrate {rtu.baudrate}, parity {rtu.parity}, stopbits {rtu.stopbits}, bytesize {rtu.bytesize}"
            )
            return ModbusSerialClient(
                port=rtu.port,
                baudrate=rtu.baudrate,
                parity=rtu.parity,
                stopbits=rtu.stopbits,
                bytesize=rtu.bytesize,
                timeout=rtu.timeout_sec,
                trace_packet=self._trace_packet,
            )
        else:  # pragma: no cover
            self.logger.error(
                f"Invalid Modbus transport '{endpoint.transport}', defaulting to TCP"
            )
            tcp = endpoint.tcp
            return ModbusTcpClient(
                host=tcp.host,
                port=tcp.port,
                timeout=tcp.timeout_sec,
            )

    def _trace_packet(self, sending: bool, data: bytes) -> bytes:
        """Trace raw RTU bytes (including CRC) for TX/RX logging."""
        if self.raw_frame_logger is not None:
            self.raw_frame_logger.log_frame(sending=sending, frame=data)
        return data

    def _endpoint_label(self, endpoint: ModbusEndpointConfig) -> str:
        mode = endpoint.normalized_transport()
        if mode == "rtu":
            rtu = endpoint.rtu
            return f"RTU:{rtu.port}"
        tcp = endpoint.tcp
        return f"TCP:{tcp.host}:{tcp.port}"

    # ---------------------------------------------------------------------------
    # 连接/断开
    # ---------------------------------------------------------------------------
    def connect(self) -> bool:
        """
        主动连接到Modbus服务器, 返回是否连接成功
        """
        hp_ok = True
        for bus_name, (endpoint, client, _slave_ids) in self.hp_bus_clients.items():
            bus_ok = self._connect_client(client, endpoint, f"HP[{bus_name}]")
            hp_ok = hp_ok and bus_ok
        ct_ok = self._connect_client(self.ct_client, self.config.ct, "CT")
        return hp_ok and ct_ok

    def disconnect(self) -> None:
        """
        断开与Modbus服务器的连接
        """
        self.stop_polling()
        for bus_name, (_endpoint, client, _slave_ids) in self.hp_bus_clients.items():
            self._disconnect_client(client, f"HP[{bus_name}]")
        self._disconnect_client(self.ct_client, "CT")
        self.logger.info("Disconnected from Modbus server(s)")

    def _connect_client(
        self,
        client: ModbusTcpClient | ModbusSerialClient,
        endpoint: ModbusEndpointConfig,
        label: str,
    ) -> bool:
        if client.connected:
            return True
        connected = client.connect()
        endpoint_label = self._endpoint_label(endpoint)
        if connected:
            self.logger.info(f"Connected {label} Modbus client at {endpoint_label}")
        else:
            self.logger.error(f"Failed to connect {label} Modbus client at {endpoint_label}")
        return connected

    def _disconnect_client(
        self, client: ModbusTcpClient | ModbusSerialClient, label: str
    ) -> None:
        if client.connected:
            client.close()  # pragma: no cover
        self.logger.info(f"Disconnected {label} Modbus client")

    def _ensure_connection(
        self,
        client: ModbusTcpClient | ModbusSerialClient,
        endpoint: ModbusEndpointConfig,
        label: str,
    ) -> bool:
        """
        确保与Modbus服务器的连接，如果未连接则尝试连接
        """
        if client.connected:
            return True

        now = time.monotonic()
        key = id(client)
        last_attempt = self._last_reconnect_attempt_ts.get(key, 0.0)
        if now - last_attempt < self.config.reconnect_interval_sec:
            return False

        self._last_reconnect_attempt_ts[key] = now
        self.logger.info(f"{label} Modbus client not connected, attempting to connect...")
        connected = client.connect()
        if connected:
            self.logger.info(f"Successfully connected {label} Modbus client")
        else:
            self.logger.error(f"Failed to connect {label} Modbus client")
        return connected

    def _extract_registers_from_response(
        self,
        start_address: int,
        values: Iterable[int],
        register_map: Mapping[int, str],
    ) -> Dict[str, int]:
        """
        将连续读取的寄存器列表按真实地址映射到字段名
        """
        registers: Dict[str, int] = {}
        for offset, value in enumerate(values or []):
            address = start_address + offset
            name = register_map.get(address)
            if name:
                registers[name] = value
        return registers

    def _mark_hp_comm_success(self, slave_id: int) -> None:
        with self._comm_status_lock:
            prev = self._hp_comm_status.get(slave_id, -1)
            next_value = 0 if prev < 0 or prev >= 254 else prev + 1
            self._hp_comm_status[slave_id] = next_value

    def _mark_hp_comm_failure(self, slave_id: int) -> None:
        with self._comm_status_lock:
            self._hp_comm_status[slave_id] = -1

    def _mark_ct_comm_success(self, slave_id: int) -> None:
        with self._comm_status_lock:
            prev = self._ct_comm_status.get(slave_id, -1)
            next_value = 0 if prev < 0 or prev >= 254 else prev + 1
            self._ct_comm_status[slave_id] = next_value

    def _mark_ct_comm_failure(self, slave_id: int) -> None:
        with self._comm_status_lock:
            self._ct_comm_status[slave_id] = -1

    def get_comm_status_snapshot(self) -> Dict[str, Dict[int, int]]:
        with self._comm_status_lock:
            return {
                "hp": dict(self._hp_comm_status),
                "ct": dict(self._ct_comm_status),
            }

    def _decode_float_registers(
        self,
        start_address: int,
        values: Iterable[int],
        register_map: Mapping[int, str],
    ) -> Dict[str, float]:
        """
        将连续寄存器列表按起始地址映射为 float（每个字段占2个寄存器）
        """
        registers: Dict[str, float] = {}
        values_list = list(values or [])
        for address, name in register_map.items():
            offset = address - start_address
            if offset < 0 or offset + 1 >= len(values_list):
                continue
            high = values_list[offset]
            low = values_list[offset + 1]
            try:
                raw = struct.pack(">HH", high, low)
                registers[name] = struct.unpack(">f", raw)[0]
            except Exception as e:  # noqa: BLE001
                self.logger.error(
                    f"Failed to decode float for CT register {address} ({name}): {e}"
                )
        return registers

    def _wait_request_gap(self, client: ModbusTcpClient | ModbusSerialClient) -> None:
        gap = max(0.0, float(self.config.request_gap_sec))
        if gap <= 0.0:
            return
        key = id(client)
        now = time.monotonic()
        last_response_ts = self._last_response_ts_by_client.get(key)
        if last_response_ts is None:
            return
        remain = gap - (now - last_response_ts)
        if remain > 0:
            time.sleep(remain)

    def _mark_response_received(
        self, client: ModbusTcpClient | ModbusSerialClient
    ) -> None:
        self._last_response_ts_by_client[id(client)] = time.monotonic()

    def pause_hp_polling(self) -> None:
        self._hp_poll_pause_event.set()

    def resume_hp_polling(self) -> None:
        self._hp_poll_pause_event.clear()

    # ---------------------------------------------------------------------------
    # 轮循读取
    # ---------------------------------------------------------------------------
    def read_ct_once(self) -> None:
        """
        读取CT寄存器数据一次，更新共享状态
        """
        try:
            if not self._ensure_connection(self.ct_client, self.config.ct, "CT"):
                self._mark_ct_comm_failure(self.config.ct_slave_id)
                return
            response = self.ct_client.read_holding_registers(
                address=self.config.ct_start_address,
                count=self.config.ct_register_count,
                device_id=self.config.ct_slave_id,
            )

            if isinstance(response, ModbusException) or response.isError():
                self.logger.error(
                    f"Modbus read error from CT slave {self.config.ct_slave_id}: {response}"
                )
                self._mark_ct_comm_failure(self.config.ct_slave_id)
                return

            ct_registers = self._decode_float_registers(
                self.config.ct_start_address, response.registers, CT_REGISTER_MAP
            )
            total_current = (
                float(ct_registers.get("current_l1", 0.0))
                + float(ct_registers.get("current_l2", 0.0))
                + float(ct_registers.get("current_l3", 0.0))
            )
            ct_registers["total_current"] = total_current
            self.logger.info(
                "Read from CT slave %s: L1=%.3f, L2=%.3f, L3=%.3f, total=%.3f",
                self.config.ct_slave_id,
                ct_registers.get("current_l1", 0.0),
                ct_registers.get("current_l2", 0.0),
                ct_registers.get("current_l3", 0.0),
                total_current,
            )
            if self.algorithm:
                self.algorithm.update_ct_total_current(total_current)

            self.shared_state.update_ct_slave(self.config.ct_slave_id, ct_registers)
            self._mark_ct_comm_success(self.config.ct_slave_id)

        except Exception as e:
            self.logger.error(
                f"Exception while reading from CT slave {self.config.ct_slave_id}: {e}"
            )
            self._mark_ct_comm_failure(self.config.ct_slave_id)
            try:
                self.ct_client.close()
            except Exception:
                pass

    def read_hp_once(self) -> None:
        """
        读取所有热泵寄存器数据一次，更新共享状态
        """
        self.logger.info("Starting HP polling cycle")
        for bus_name, (endpoint, client, slave_ids) in self.hp_bus_clients.items():
            hp_connected = self._ensure_connection(client, endpoint, f"HP[{bus_name}]")
            if not hp_connected:
                self.logger.warning(
                    "HP bus %s not connected, skip this bus in current cycle",
                    bus_name,
                )
                for slave_id in slave_ids:
                    self._mark_hp_comm_failure(slave_id)
                continue

            for slave_id in slave_ids:
                if self._hp_poll_pause_event.is_set():
                    self.logger.debug("HP polling paused, skip remaining HP reads in current cycle")
                    return

                try:
                    hp_registers: Dict[str, int] = {}
                    slave_ok = True
                    for start_address, count in self.config.hp_register_blocks:
                        if self._hp_poll_pause_event.is_set():
                            return

                        self.logger.info(
                            "Reading HP slave %s on bus %s (addr=0x%04X, count=%s)",
                            slave_id,
                            bus_name,
                            start_address,
                            count,
                        )
                        with self._hp_io_lock:
                            self._wait_request_gap(client)
                            response = client.read_holding_registers(
                                address=start_address,
                                count=count,
                                device_id=slave_id,
                            )
                            self._mark_response_received(client)

                        if response is None:
                            self.logger.error(
                                "Modbus read returned None from HP slave %s on bus %s (addr=0x%04X, count=%s)",
                                slave_id,
                                bus_name,
                                start_address,
                                count,
                            )
                            slave_ok = False
                            break
                        if isinstance(response, ModbusException) or response.isError():
                            self.logger.error(
                                "Modbus read error from HP slave %s on bus %s (addr=0x%04X, count=%s): %s",
                                slave_id,
                                bus_name,
                                start_address,
                                count,
                                response,
                            )
                            slave_ok = False
                            break
                        self.logger.info(
                            "Raw registers from HP slave %s on bus %s (addr=0x%04X, count=%s): %s",
                            slave_id,
                            bus_name,
                            start_address,
                            count,
                            getattr(response, "registers", None),
                        )

                        hp_registers.update(
                            self._extract_registers_from_response(
                                start_address, response.registers, HP_REGISTER_MAP
                            )
                        )

                    if slave_ok and hp_registers:
                        self.shared_state.update_hp_slave(slave_id, hp_registers)
                        self._mark_hp_comm_success(slave_id)
                        self.logger.debug(f"Read from HP slave {slave_id}: {hp_registers}")
                    else:
                        self._mark_hp_comm_failure(slave_id)

                except Exception as e:
                    self.logger.error(
                        "Exception while reading from HP slave %s on bus %s: %s",
                        slave_id,
                        bus_name,
                        e,
                    )
                    self._mark_hp_comm_failure(slave_id)
                    try:
                        client.close()
                    except Exception:
                        pass

    def read_all_slaves_once(self) -> None:
        """
        读取所有从站的寄存器数据一次，更新共享状态
        """
        self.read_ct_once()
        self.read_hp_once()

    def start_polling(self) -> None:
        """
        启动后台线程，定期轮循读取所有从站数据
        """
        if (
            (self._hp_poll_thread and self._hp_poll_thread.is_alive())
            or (self._ct_poll_thread and self._ct_poll_thread.is_alive())
        ):
            self.logger.warning("Polling thread already running")
            return

        self._stop_event.clear()
        self._ct_poll_thread = threading.Thread(
            target=self._polling_loop_ct, daemon=True
        )
        self._hp_poll_thread = threading.Thread(
            target=self._polling_loop_hp, daemon=True
        )
        self._ct_poll_thread.start()
        self._hp_poll_thread.start()
        self.logger.info("Started polling threads")

    def stop_polling(self) -> None:
        """
        停止后台轮循线程
        """
        self._stop_event.set()
        if self._ct_poll_thread is not None:
            self._ct_poll_thread.join(timeout=5.0)
            self._ct_poll_thread = None
        if self._hp_poll_thread is not None:
            self._hp_poll_thread.join(timeout=5.0)
            self._hp_poll_thread = None
        self.logger.info("Stopped polling thread")

    def _polling_loop_ct(self) -> None:
        """后台轮循线程的主循环，定期读取CT数据"""
        while not self._stop_event.is_set():  # pragma: no cover
            try:
                self.read_ct_once()
            except Exception as e:  # noqa: BLE001
                self.logger.error(f"Unexpected exception in CT polling loop: {e}")

            if self._stop_event.wait(self.config.poll_interval_sec):
                break

    def _polling_loop_hp(self) -> None:
        """后台轮循线程的主循环，定期读取HP数据"""
        while not self._stop_event.is_set():  # pragma: no cover
            if self._hp_poll_pause_event.is_set():
                if self._stop_event.wait(0.02):
                    break
                continue
            try:
                self.read_hp_once()
            except Exception as e:  # noqa: BLE001
                self.logger.error(f"Unexpected exception in HP polling loop: {e}")

            # 等待下一个轮循周期，或者提前退出
            if self._stop_event.wait(self.config.poll_interval_sec):
                break

    # ---------------------------------------------------------------------------
    # 写寄存器, 给算法模块调用
    # ---------------------------------------------------------------------------
    def write_register(self, slave_id: int, register_address: int, value: int) -> bool:
        """
        向指定从站的寄存器写入一个值
        返回是否写入成功
        """
        if not self.config.control_mode:
            self.logger.info(
                "Control mode disabled, skip writing to slave %s register %s value %s",
                slave_id,
                register_address,
                value,
            )
            return True
        if slave_id not in self.config.hp_slave_ids:
            self.logger.error(f"Invalid slave_id {slave_id} for writing")
            return False

        hp_client = self.hp_client_by_slave.get(slave_id)
        hp_endpoint = self.hp_endpoint_by_slave.get(slave_id)
        hp_bus_name = self.hp_bus_name_by_slave.get(slave_id, "unknown")
        if hp_client is None or hp_endpoint is None:
            self.logger.error("No HP bus found for slave_id %s", slave_id)
            return False

        if not self._ensure_connection(
            hp_client,
            hp_endpoint,
            f"HP[{hp_bus_name}]",
        ):
            self.logger.error("Cannot write register, Modbus client not connected")
            return False

        try:
            with self._hp_io_lock:
                self._wait_request_gap(hp_client)
                response = hp_client.write_register(
                    address=register_address,
                    value=value,
                    device_id=slave_id,
                )
                self._mark_response_received(hp_client)
            if isinstance(response, ModbusException) or response.isError():
                self.logger.error(
                    f"Modbus write error to slave {slave_id}, register {register_address}: {response}"
                )
                return False

            register_name = HP_REGISTER_MAP.get(register_address)
            if register_name:
                self.shared_state.update_hp_slave(slave_id, {register_name: value})
            self.logger.debug(
                f"Wrote to slave {slave_id}, register {register_address}: {value}"
            )
            return True

        except Exception as e:  # noqa: BLE001
            self.logger.error(
                f"Exception while writing to slave {slave_id}, register {register_address}: {e}"
            )
            # 出现异常时，断开连接，等待下次重连
            try:
                hp_client.close()
            except Exception:
                pass
            return False

    def write_registers(
        self, slave_id: int, register_values: Mapping[int, int]
    ) -> bool:
        """
        向指定从站的多个寄存器写入值
        register_values: {register_address: value, ...}
        返回是否全部写入成功
        """
        all_success = True
        for address, value in register_values.items():
            success = self.write_register(slave_id, address, value)
            if not success:
                all_success = False
        return all_success

    # ---------------------------------------------------------------------------
    # 状态查询接口
    # ---------------------------------------------------------------------------
    def get_shared_state_snapshot(self) -> Dict[int, Dict[str, int]]:
        """
        获取当前共享状态的快照
        """
        return self.shared_state.get_snapshot()

    def get_slave_registers(self, slave_id: int) -> Optional[Dict[str, int]]:
        """
        获取指定从站的寄存器数据快照
        """
        return self.shared_state.get_slave_registers(slave_id)
