# 客户端单元测试
# tests/hp_controller/master/test_client.py
from __future__ import annotations

import time
from typing import Dict, List

import pytest

import hp_controller.master.client as client_mod


# ---------------------------------------------------------------------------
# Dummy / Fixtures
# ---------------------------------------------------------------------------


class DummyResponse:
    """模拟 pymodbus 的正常响应对象"""

    def __init__(self, registers=None, error: bool = False) -> None:
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class DummyModbusException(Exception):
    """用来替代 pymodbus.exceptions.ModbusException 的测试类"""

    def isError(self) -> bool:  # 配合生产代码的 isError() 调用
        return True


class DummyClient:
    """
    替代 ModbusTcpClient，用于单元测试。
    接口兼容当前 pymodbus：使用 device_id 而非 unit。
    """

    def __init__(self) -> None:
        self.connected: bool = False
        self.closed: bool = False
        self.connect_result: bool = True

        self.read_holding_registers_response = None
        self.write_register_response = None

        self.read_calls: List[Dict] = []
        self.write_calls: List[Dict] = []

    # --- 连接相关 ---------------------------------------------------------

    def connect(self) -> bool:
        self.connected = self.connect_result
        return self.connect_result

    def close(self) -> None:
        self.connected = False
        self.closed = True

    # --- Modbus 读写 ------------------------------------------------------

    def read_holding_registers(self, *args, **kwargs):
        """
        兼容生产代码中调用：
        client.read_holding_registers(address=..., count=..., device_id=...)
        """
        self.read_calls.append({"args": args, "kwargs": kwargs})

        if isinstance(self.read_holding_registers_response, Exception):
            raise self.read_holding_registers_response

        return self.read_holding_registers_response

    def write_register(self, *args, **kwargs):
        """
        兼容生产代码中调用：
        client.write_register(address=..., value=..., device_id=...)
        """
        self.write_calls.append({"args": args, "kwargs": kwargs})

        if isinstance(self.write_register_response, Exception):
            raise self.write_register_response

        return self.write_register_response


@pytest.fixture
def dummy_client_factory(monkeypatch):
    """
    为每个测试创建一个新的 DummyClient，并将 ModbusTcpClient 替换成它。
    """

    def _factory() -> DummyClient:
        dummy = DummyClient()

        def client_ctor(*args, **kwargs):
            return dummy

        monkeypatch.setattr(client_mod, "ModbusTcpClient", client_ctor)
        return dummy

    return _factory


# ---------------------------------------------------------------------------
# ModbusShareState tests
# ---------------------------------------------------------------------------


def test_shared_state_update_and_get() -> None:
    shared = client_mod.ModbusShareState()

    shared.update_slave(1, {"a": 10, "b": 20})
    snapshot = shared.get_snapshot()

    assert snapshot[1]["a"] == 10
    assert snapshot[1]["b"] == 20

    # 修改 snapshot 不应该影响内部状态
    snapshot[1]["a"] = 999
    snapshot2 = shared.get_snapshot()
    assert snapshot2[1]["a"] == 10

    regs = shared.get_slave_registers(1)
    assert regs is not None
    assert regs["b"] == 20

    # 未存在的 slave 返回 None
    assert shared.get_slave_registers(99) is None


# ---------------------------------------------------------------------------
# 连接行为 tests
# ---------------------------------------------------------------------------


def test_connect_success(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    assert master.connect() is True
    assert dummy.connected is True


def test_connect_failure(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    dummy.connect_result = False
    master = client_mod.ModbusMaster()

    assert master.connect() is False
    assert dummy.connected is False


def test_ensure_connection_already_connected(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True
    assert master._ensure_connection() is True  # type: ignore[attr-defined]


def test_ensure_connection_reconnect_success(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = False
    dummy.connect_result = True

    assert master._ensure_connection() is True  # type: ignore[attr-defined]
    assert dummy.connected is True


def test_ensure_connection_reconnect_failure(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = False
    dummy.connect_result = False

    assert master._ensure_connection() is False  # type: ignore[attr-defined]


def test_disconnect_stops_polling_and_closes(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True

    master.start_polling()
    time.sleep(0.02)
    master.disconnect()

    assert dummy.closed is True
    assert master._poll_thread is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# read_all_slaves_once tests（用 device_id）
# ---------------------------------------------------------------------------
def test_read_all_slaves_once_success_hp_and_ct(
    dummy_client_factory, monkeypatch
) -> None:
    dummy = dummy_client_factory()

    # 替换 ModbusException，便于 isinstance 检查
    monkeypatch.setattr(client_mod, "ModbusException", DummyModbusException)

    # 构造 HP & CT 的寄存器值
    hp_block1 = [111, 5]  # 0x0003, 0x0004
    hp_block2 = [0] * (0x0091 - 0x0079 + 1)
    hp_block2[0] = 321  # 0x0079
    hp_block2[0x008E - 0x0079] = 101
    hp_block2[0x008F - 0x0079] = 102
    hp_block2[0x0090 - 0x0079] = 103
    hp_block2[0x0091 - 0x0079] = 104
    ct_registers = [80]  # 只读取总电流（地址7）

    def read_mock(*_args, **kwargs):
        """
        兼容调用签名：address=..., count=..., device_id=...
        """
        device_id = kwargs.get("device_id")
        if device_id in (1, 2, 3, 4, 5, 6):
            address = kwargs.get("address")
            count = kwargs.get("count")
            if address == 0x0003 and count == 2:
                return DummyResponse(hp_block1)
            if address == 0x0079 and count == len(hp_block2):
                return DummyResponse(hp_block2)
            raise AssertionError(f"Unexpected HP read: address={address}, count={count}")
        if device_id == 10:
            assert kwargs.get("address") == 7
            assert kwargs.get("count") == len(ct_registers)
            return DummyResponse(ct_registers)
        raise AssertionError(f"Unexpected device_id: {device_id}")

    dummy.read_holding_registers = read_mock  # type: ignore[assignment]

    master = client_mod.ModbusMaster()
    dummy.connected = True

    master.read_all_slaves_once()
    snapshot = master.get_shared_state_snapshot()

    # 验证 HP1
    hp_regs = snapshot[1]
    assert hp_regs["heating_setpoint"] == 111
    assert hp_regs["hysteresis_value"] == 5
    assert hp_regs["inlet_temperature"] == 321
    assert hp_regs["compressor1_current"] == 101
    assert hp_regs["compressor2_current"] == 102
    assert hp_regs["compressor3_current"] == 103
    assert hp_regs["compressor4_current"] == 104

    # 验证 CT
    ct_regs = snapshot[10]
    assert ct_regs[client_mod.CT_REGISTER_MAP[7]] == 80


def test_read_all_slaves_once_connection_failure(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    dummy.connect_result = False

    master = client_mod.ModbusMaster()
    master.read_all_slaves_once()

    # 连接失败时不应有数据
    assert master.get_shared_state_snapshot() == {}


def test_read_all_slaves_once_modbus_error_response(
    dummy_client_factory,
) -> None:
    dummy = dummy_client_factory()
    dummy.connected = True

    # 返回 error=True 的 DummyResponse，让 isError() 为 True
    dummy.read_holding_registers_response = DummyResponse(
        registers=[0] * 8, error=True
    )

    master = client_mod.ModbusMaster()
    master.read_all_slaves_once()

    # 错误响应不应写入共享状态
    assert master.get_shared_state_snapshot() == {}


def test_read_all_slaves_once_raises_exception(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    dummy.connected = True

    dummy.read_holding_registers_response = RuntimeError("read error")

    def read_with_exception(*_args, **_kwargs):
        raise dummy.read_holding_registers_response  # type: ignore[misc]

    dummy.read_holding_registers = read_with_exception  # type: ignore[assignment]

    master = client_mod.ModbusMaster()
    master.read_all_slaves_once()

    # 出现异常时，client 应该被关闭
    assert dummy.connected is False
    assert dummy.closed is True


# ---------------------------------------------------------------------------
# polling 线程 tests
# ---------------------------------------------------------------------------


def test_start_and_stop_polling_calls_read(dummy_client_factory) -> None:
    dummy_client_factory()  # 只做替换

    # 调整 poll_interval 为很小，加快测试
    master = client_mod.ModbusMaster(
        config=client_mod.ModbusConfig(poll_interval_sec=0.01)
    )

    call_counter = {"n": 0}

    def fake_read() -> None:
        call_counter["n"] += 1

    master.read_all_slaves_once = fake_read  # type: ignore[assignment]

    master.start_polling()
    time.sleep(0.05)
    master.stop_polling()

    assert call_counter["n"] >= 1


# ---------------------------------------------------------------------------
# 写寄存器 tests（使用 device_id）
# ---------------------------------------------------------------------------
def test_write_register_invalid_slave_id(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True

    ok = master.write_register(slave_id=99, register_address=0, value=123)
    assert ok is False
    assert dummy.write_calls == []


def test_write_register_connection_failure(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = False
    dummy.connect_result = False

    ok = master.write_register(slave_id=1, register_address=0, value=123)
    assert ok is False
    assert dummy.write_calls == []


def test_write_register_success(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True
    dummy.write_register_response = DummyResponse(error=False)

    ok = master.write_register(slave_id=1, register_address=2, value=456)
    assert ok is True
    assert len(dummy.write_calls) == 1

    call_kwargs = dummy.write_calls[0]["kwargs"]
    assert call_kwargs["address"] == 2
    assert call_kwargs["value"] == 456
    # 关键：生产代码应该用 device_id，而不是 unit
    assert call_kwargs["device_id"] == 1


def test_write_register_modbus_error(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True
    dummy.write_register_response = DummyResponse(error=True)

    ok = master.write_register(slave_id=1, register_address=0, value=123)
    assert ok is False


def test_write_register_raise_exception(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True
    dummy.write_register_response = RuntimeError("write error")

    def write_with_exception(*_args, **_kwargs):
        raise dummy.write_register_response  # type: ignore[misc]

    dummy.write_register = write_with_exception  # type: ignore[assignment]

    ok = master.write_register(slave_id=1, register_address=0, value=123)
    assert ok is False
    assert dummy.connected is False
    assert dummy.closed is True


def test_write_registers_aggregate_result(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True

    # 第一次写成功，第二次失败
    responses = [
        DummyResponse(error=False),
        DummyResponse(error=True),
    ]

    def write_register_side_effect(*_args, **_kwargs):
        return responses.pop(0)

    dummy.write_register = write_register_side_effect  # type: ignore[assignment]

    ok = master.write_registers(slave_id=1, register_values={0: 100, 1: 200})
    # 至少一个失败，因此整体应为 False
    assert ok is False


# ---------------------------------------------------------------------------
# 状态包装方法 tests
# ---------------------------------------------------------------------------


def test_get_shared_state_and_slave_registers_wrapper(dummy_client_factory) -> None:
    dummy_client_factory()
    master = client_mod.ModbusMaster()

    master.shared_state.update_slave(1, {"x": 1, "y": 2})

    snapshot = master.get_shared_state_snapshot()
    assert snapshot[1]["x"] == 1

    regs = master.get_slave_registers(1)
    assert regs is not None
    assert regs["y"] == 2
    
    
def test_shared_state_update_slave_with_empty_mapping_does_nothing() -> None:
    shared = client_mod.ModbusShareState()
    # 先放一条正常数据
    shared.update_slave(1, {"a": 1})
    snap_before = shared.get_snapshot()

    # 传入空 dict，应当直接 return，不改变已有数据
    shared.update_slave(1, {})
    snap_after = shared.get_snapshot()

    assert snap_after == snap_before
    
def test_connect_when_already_connected_returns_true(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True  # 模拟已经连上
    assert master.connect() is True
    # 不应该再调用 dummy.connect() 改变状态
    assert dummy.connected is True

def test_disconnect_when_not_connected_does_not_close(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = False  # 一开始就没连接
    master.disconnect()

    # 不应该调用 close，closed 仍应为 False
    assert dummy.closed is False

def test_start_polling_twice_logs_warning_and_not_restart(dummy_client_factory, caplog) -> None:
    dummy_client_factory()
    master = client_mod.ModbusMaster(
        config=client_mod.ModbusConfig(poll_interval_sec=0.01)
    )

    master.start_polling()
    first_thread = master._poll_thread

    with caplog.at_level("WARNING"):
        master.start_polling()
        # 不应创建新的线程对象
        assert master._poll_thread is first_thread
        # 有 warning 日志
        assert any("Polling thread already running" in r.message for r in caplog.records)

    master.stop_polling()

def test_polling_loop_catches_exception_from_read(dummy_client_factory, caplog) -> None:
    dummy_client_factory()
    master = client_mod.ModbusMaster(
        config=client_mod.ModbusConfig(poll_interval_sec=0.01)
    )

    def bad_read() -> None:
        raise RuntimeError("boom in read")

    master.read_all_slaves_once = bad_read  # type: ignore[assignment]

    with caplog.at_level("ERROR"):
        master.start_polling()
        # 给线程一点时间跑
        import time as _time
        _time.sleep(0.03)
        master.stop_polling()

    # 确认异常被捕获并记录日志，而不是直接炸掉线程
    assert any("Unexpected exception in polling loop" in r.message for r in caplog.records)

class _TestModbusException(client_mod.ModbusException):  # type: ignore[misc]
    """专门用于测试 isinstance(response, ModbusException) 为 True 的场景。"""
    def isError(self) -> bool:  # 与真实响应接口保持一致
        return True


def test_write_register_modbus_exception_instance(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True
    # 返回一个真正意义上的 "ModbusException" 实例
    dummy.write_register_response = _TestModbusException("write error")

    ok = master.write_register(slave_id=1, register_address=0, value=123)
    # 应该走到 isinstance(response, ModbusException) 分支，返回 False
    assert ok is False

def test_write_register_exception_and_close_raises(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True

    def write_raise(*_args, **_kwargs):
        raise RuntimeError("write failed")

    def close_raise():
        # close 自己也炸
        raise RuntimeError("close failed")

    dummy.write_register = write_raise  # type: ignore[assignment]
    dummy.close = close_raise  # type: ignore[assignment]

    ok = master.write_register(slave_id=1, register_address=0, value=123)

    # write_register 的外层 except 应该吞掉 close 的异常，整体返回 False
    assert ok is False

def test_read_all_slaves_once_exception_and_close_raises(dummy_client_factory) -> None:
    dummy = dummy_client_factory()
    master = client_mod.ModbusMaster()

    dummy.connected = True

    def read_raise(*_args, **_kwargs):
        raise RuntimeError("read failed")

    def close_raise():
        raise RuntimeError("close failed")

    dummy.read_holding_registers = read_raise  # type: ignore[assignment]
    dummy.close = close_raise  # type: ignore[assignment]

    # 不应抛出异常
    master.read_all_slaves_once()
    # 即使 close 抛异常，也只是被吞掉，不会让测试炸掉

def test_master_get_slave_registers_returns_none_for_unknown_slave(dummy_client_factory) -> None:
    dummy_client_factory()
    master = client_mod.ModbusMaster()

    # shared_state 里没有 999 这个 slave
    regs = master.get_slave_registers(999)
    assert regs is None
