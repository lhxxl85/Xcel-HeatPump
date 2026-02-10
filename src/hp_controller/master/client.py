# ModbusTCP客户端实现，用于与HP控制器进行通信
# src/hp_controller/master/client.py
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, TYPE_CHECKING

from pymodbus.client import ModbusTcpClient, ModbusSerialClient
from pymodbus.exceptions import ModbusException

from .register_mapping import CT_REGISTER_MAP, HP_REGISTER_MAP

if TYPE_CHECKING:
    from .algorithm import LoadControlAlgorithm

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
class ModbusConfig:
    hp: ModbusEndpointConfig = field(default_factory=ModbusEndpointConfig)
    ct: ModbusEndpointConfig = field(default_factory=ModbusEndpointConfig)
    hp_slave_ids: Iterable[int] = (1, 2, 3, 4, 5, 6)
    ct_slave_id: int = 10
    # 是否允许写入寄存器（false 时只读）
    control_mode: bool = False
    # 针对热泵：分成多个连续读取块，减少Modbus调用次数
    hp_register_blocks: tuple[tuple[int, int], ...] = (
        (0x0003, 2),  # 0x0003～0x0004
        (0x0079, 0x0091 - 0x0079 + 1),  # 0x0079～0x0091
    )
    # 针对CT：仍然从7号地址开始读取, 但是就只有一个寄存器
    ct_start_address: int = 7
    ct_register_count: int = 1
    poll_interval_sec: float = 0.5
    reconnect_interval_sec: float = 3.0


@dataclass
class ModbusShareState:
    """
    公共共享区域：
    data[slave_id][register_name] = value
    """

    data: Dict[int, Dict[str, int]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update_slave(self, slave_id: int, registers: Mapping[str, int]) -> None:
        if len(registers) == 0:
            return
        with self.lock:
            self.data[slave_id] = dict(registers)

    def get_snapshot(self) -> Dict[int, Dict[str, int]]:
        with self.lock:
            return {sid: regs.copy() for sid, regs in self.data.items()}

    def get_slave_registers(self, slave_id: int) -> Optional[Dict[str, int]]:
        with self.lock:
            regs = self.data.get(slave_id)
            if regs is not None:
                return regs.copy()
            return None


class ModbusMaster:
    """
    Modbus上位机
    - 支持 TCP 和 RTU 串口两种模式（由 MODBUS_TRANSPORT_MODE 控制）
    - 定期轮循：
      slave 1~6: 热泵
      slave 10: 电表
    - 读取到的数据存入ModbusShareState
    - 提供写接口：对1～6号从站的寄存器写入数据
    - 具有容错和灾难恢复
    """

    def __init__(
        self,
        config: Optional[ModbusConfig] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        base_logger = logger or logging.getLogger("hp_controller.master.client")
        self.logger = base_logger.getChild("ModbusMaster")
        self.config = config or ModbusConfig()
        self.hp_client = self._create_client(self.config.hp, "HP")
        self.ct_client = self._create_client(self.config.ct, "CT")
        self.shared_state = ModbusShareState()
        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None

        # 构建一个从站ID列表
        self.slave_ids = list(self.config.hp_slave_ids) + [self.config.ct_slave_id]

        # 模拟CT慢更新控制计数器
        self._ct_slow_update_counter = 5  # 确保第一次就更新
        # 多少次轮循后，才更新CT数据
        self._ct_slow_update_interval = 5  # 每30次轮循更新一次CT数据

        self.algorithm: Optional["LoadControlAlgorithm"] = (
            None  # 关联的算法模块 用于去更新算法模块内部状态
        )

    # --------------------------------------------------------------------------
    # 绑定算法模块
    # --------------------------------------------------------------------------
    def bind_algorithm(self, algorithm: "LoadControlAlgorithm") -> None:
        """
        绑定一个LoadControlAlgorithm实例，用于更新算法模块的内部状态
        """
        self.algorithm = algorithm

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
        hp_ok = self._connect_client(self.hp_client, self.config.hp, "HP")
        ct_ok = self._connect_client(self.ct_client, self.config.ct, "CT")
        return hp_ok and ct_ok

    def disconnect(self) -> None:
        """
        断开与Modbus服务器的连接
        """
        self.stop_polling()
        self._disconnect_client(self.hp_client, "HP")
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

    # ---------------------------------------------------------------------------
    # 轮循读取
    # ---------------------------------------------------------------------------
    def read_all_slaves_once(self) -> None:
        """
        读取所有从站的寄存器数据一次，更新共享状态
        """
        hp_connected = self._ensure_connection(self.hp_client, self.config.hp, "HP")
        if hp_connected:
            # 先读取所有热泵
            for slave_id in self.config.hp_slave_ids:
                try:
                    hp_registers: Dict[str, int] = {}
                    for start_address, count in self.config.hp_register_blocks:
                        response = self.hp_client.read_holding_registers(
                            address=start_address,
                            count=count,
                            device_id=slave_id,
                        )

                        if isinstance(response, ModbusException) or response.isError():
                            self.logger.error(
                                f"Modbus read error from HP slave {slave_id} (addr=0x{start_address:04X}, count={count}): {response}"
                            )
                            continue

                        hp_registers.update(
                            self._extract_registers_from_response(
                                start_address, response.registers, HP_REGISTER_MAP
                            )
                        )

                    self.shared_state.update_slave(slave_id, hp_registers)
                    self.logger.debug(f"Read from HP slave {slave_id}: {hp_registers}")

                except Exception as e:
                    self.logger.error(
                        f"Exception while reading from HP slave {slave_id}: {e}"
                    )
                    # 出现异常时，断开连接，等待下次重连
                    try:
                        self.hp_client.close()
                    except Exception:
                        pass

        # 再读取 CT
        try:
            # 模拟CT慢更新：不是每次轮循都读取CT数据
            self._ct_slow_update_counter += 1
            if self._ct_slow_update_counter < self._ct_slow_update_interval:
                return
            self._ct_slow_update_counter = 0
            if not self._ensure_connection(self.ct_client, self.config.ct, "CT"):
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
                return

            ct_registers = self._extract_registers_from_response(
                self.config.ct_start_address, response.registers, CT_REGISTER_MAP
            )
            self.logger.info(
                f"Read from CT slave {self.config.ct_slave_id}: {ct_registers}"
            )
            # 更新共享状态
            if self.algorithm:
                self.algorithm.update_ct_total_current(
                    float(ct_registers["total_current"])
                )  # 假设CT的电流值在寄存器0x0007

            self.shared_state.update_slave(self.config.ct_slave_id, ct_registers)

        except Exception as e:
            self.logger.error(
                f"Exception while reading from CT slave {self.config.ct_slave_id}: {e}"
            )
            try:
                self.ct_client.close()
            except Exception:
                pass

    def start_polling(self) -> None:
        """
        启动后台线程，定期轮循读取所有从站数据
        """
        if self._poll_thread and self._poll_thread.is_alive():
            self.logger.warning("Polling thread already running")
            return

        self._stop_event.clear()
        self._poll_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._poll_thread.start()
        self.logger.info("Started polling thread")

    def stop_polling(self) -> None:
        """
        停止后台轮循线程
        """
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
            self._poll_thread = None
        self.logger.info("Stopped polling thread")

    def _polling_loop(self) -> None:
        """后台轮循线程的主循环，定期读取所有从站数据"""
        while not self._stop_event.is_set():  # pragma: no cover
            try:
                self.read_all_slaves_once()
            except Exception as e:  # noqa: BLE001
                # reall_all_slaves_once 内部已经捕获异常，这里作为最后的保险
                self.logger.error(f"Unexpected exception in polling loop: {e}")

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

        if not self._ensure_connection(self.hp_client, self.config.hp, "HP"):
            self.logger.error("Cannot write register, Modbus client not connected")
            return False

        try:
            response = self.hp_client.write_register(
                address=register_address,
                value=value,
                device_id=slave_id,
            )
            if isinstance(response, ModbusException) or response.isError():
                self.logger.error(
                    f"Modbus write error to slave {slave_id}, register {register_address}: {response}"
                )
                return False

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
                self.hp_client.close()
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
