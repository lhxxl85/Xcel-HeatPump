from __future__ import annotations

import argparse
import logging
from typing import Dict

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

from src.hp_controller.master.register_mapping import HP_REGISTER_MAP


def read_hp_once(
    port: str,
    baudrate: int,
    parity: str,
    stopbits: int,
    bytesize: int,
    timeout_sec: float,
    slave_id: int,
) -> None:
    logger = logging.getLogger("modbusrtu_test")
    client = ModbusSerialClient(
        port=port,
        baudrate=baudrate,
        parity=parity,
        stopbits=stopbits,
        bytesize=bytesize,
        timeout=timeout_sec,
    )

    if not client.connect():
        logger.error("无法连接到串口: %s", port)
        return

    try:
        # 与当前主程序一致的读取块
        register_blocks = (
            (0x0003, 2),
            (0x0079, 0x0091 - 0x0079 + 1),
        )
        mapped: Dict[str, int] = {}

        for start_address, count in register_blocks:
            logger.info(
                "读取 slave=%s addr=0x%04X count=%s",
                slave_id,
                start_address,
                count,
            )
            response = client.read_holding_registers(
                address=start_address,
                count=count,
                device_id=slave_id,
            )

            if response is None:
                logger.error(
                    "读取返回 None (addr=0x%04X, count=%s)", start_address, count
                )
                continue
            if isinstance(response, ModbusException) or response.isError():
                logger.error("读取错误: %s", response)
                continue

            logger.info(
                "原始寄存器 (addr=0x%04X, count=%s): %s",
                start_address,
                count,
                getattr(response, "registers", None),
            )

            values = response.registers or []
            for offset, value in enumerate(values):
                address = start_address + offset
                name = HP_REGISTER_MAP.get(address)
                if name:
                    mapped[name] = value

        logger.info("映射结果: %s", mapped)

    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Modbus RTU 读寄存器测试")
    parser.add_argument("--port", default="/dev/ttyAMA3")
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--parity", default="N")
    parser.add_argument("--stopbits", type=int, default=1)
    parser.add_argument("--bytesize", type=int, default=8)
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    parser.add_argument("--slave-id", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    read_hp_once(
        port=args.port,
        baudrate=args.baudrate,
        parity=args.parity,
        stopbits=args.stopbits,
        bytesize=args.bytesize,
        timeout_sec=args.timeout_sec,
        slave_id=args.slave_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
