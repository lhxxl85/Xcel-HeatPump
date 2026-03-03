from __future__ import annotations

import logging
import signal
import threading

from sync_src.hp_sync.master.client import (
    HpBusConfig,
    ModbusConfig,
    ModbusEndpointConfig,
    ModbusRtuConfig,
    ModbusTcpConfig,
    ModbusMaster,
)
from sync_src.hp_sync.service.redis_sync import RedisWriter, RedisWriterConfig
from sync_src.hp_sync.settings import AppSettings
from sync_src.hp_sync.utils.logging_config import setup_logging
from sync_src.hp_sync.utils.raw_modbus_logger import RawModbusFrameLogger


def _build_ct_endpoint_config(settings: AppSettings) -> ModbusEndpointConfig:
    source = settings.ct
    return ModbusEndpointConfig(
        transport=source.transport,
        tcp=ModbusTcpConfig(
            host=source.tcp.host,
            port=source.tcp.port,
            timeout_sec=source.tcp.timeout_sec,
        ),
        rtu=ModbusRtuConfig(
            port=source.rtu.port,
            baudrate=source.rtu.baudrate,
            parity=source.rtu.parity,
            stopbits=source.rtu.stopbits,
            bytesize=source.rtu.bytesize,
            timeout_sec=source.rtu.timeout_sec,
        ),
    )


def _build_hp_bus_config(settings: AppSettings) -> tuple[HpBusConfig, ...]:
    return (
        HpBusConfig(
            name="AMA5",
            endpoint=ModbusEndpointConfig(
                transport=settings.hp_bus_ama5.transport,
                tcp=ModbusTcpConfig(
                    host=settings.hp_bus_ama5.tcp.host,
                    port=settings.hp_bus_ama5.tcp.port,
                    timeout_sec=settings.hp_bus_ama5.tcp.timeout_sec,
                ),
                rtu=ModbusRtuConfig(
                    port=settings.hp_bus_ama5.rtu.port,
                    baudrate=settings.hp_bus_ama5.rtu.baudrate,
                    parity=settings.hp_bus_ama5.rtu.parity,
                    stopbits=settings.hp_bus_ama5.rtu.stopbits,
                    bytesize=settings.hp_bus_ama5.rtu.bytesize,
                    timeout_sec=settings.hp_bus_ama5.rtu.timeout_sec,
                ),
            ),
            slave_ids=tuple(settings.hp_bus_ama5.slave_ids),
        ),
        HpBusConfig(
            name="AMA3",
            endpoint=ModbusEndpointConfig(
                transport=settings.hp_bus_ama3.transport,
                tcp=ModbusTcpConfig(
                    host=settings.hp_bus_ama3.tcp.host,
                    port=settings.hp_bus_ama3.tcp.port,
                    timeout_sec=settings.hp_bus_ama3.tcp.timeout_sec,
                ),
                rtu=ModbusRtuConfig(
                    port=settings.hp_bus_ama3.rtu.port,
                    baudrate=settings.hp_bus_ama3.rtu.baudrate,
                    parity=settings.hp_bus_ama3.rtu.parity,
                    stopbits=settings.hp_bus_ama3.rtu.stopbits,
                    bytesize=settings.hp_bus_ama3.rtu.bytesize,
                    timeout_sec=settings.hp_bus_ama3.rtu.timeout_sec,
                ),
            ),
            slave_ids=tuple(settings.hp_bus_ama3.slave_ids),
        ),
    )


def main() -> int:
    settings = AppSettings()
    setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)

    logger = logging.getLogger("hp_sync.main")

    modbus_config = ModbusConfig(
        hp_buses=_build_hp_bus_config(settings),
        ct=_build_ct_endpoint_config(settings),
        ct_slave_id=settings.ct_id,
        poll_interval_sec=settings.poll_interval_sec,
        reconnect_interval_sec=settings.reconnect_interval_sec,
        control_mode=True,
    )

    raw_frame_logger = RawModbusFrameLogger(
        log_dir=settings.log_dir,
        filename_prefix="raw_modbus",
        retention_days=7,
    )

    master = ModbusMaster(
        config=modbus_config,
        logger=logging.getLogger("hp_sync.modbus"),
        raw_frame_logger=raw_frame_logger,
    )

    redis_writer = RedisWriter(
        config=RedisWriterConfig(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.db,
            username=settings.redis.username,
            password=settings.redis.password,
            socket_timeout_sec=settings.redis.socket_timeout_sec,
            connect_timeout_sec=settings.redis.connect_timeout_sec,
            key_prefix=settings.redis.key_prefix,
            key_ttl_sec=settings.redis.key_ttl_sec,
            reconnect_interval_sec=settings.redis.reconnect_interval_sec,
        ),
        logger=logging.getLogger("hp_sync.redis"),
    )

    stop_event = threading.Event()

    def handle_signal(_signum, _frame) -> None:  # type: ignore[override]
        logger.info("Received termination signal, shutting down sync service")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("Starting sync service with HP IDs=%s CT ID=%s", settings.hp_ids, settings.ct_id)
    if settings.ct_id in settings.hp_ids:
        logger.warning(
            "CT ID (%s) overlaps HP IDs (%s). Redis key namespace will be disambiguated by device name only.",
            settings.ct_id,
            settings.hp_ids,
        )
    master.connect()
    master.start_polling()

    try:
        while not stop_event.is_set():
            hp_snapshot, ct_snapshot = master.shared_state.get_fresh_partitioned_snapshot(
                max_age_sec=settings.data_stale_after_sec
            )
            ok = redis_writer.write_partitioned_snapshot(
                hp_snapshot,
                ct_snapshot,
                hp_device_name=settings.hp_device_name,
                ct_device_name=settings.ct_device_name,
            )
            if not ok:
                logger.warning("Redis sync not successful in this cycle")

            comm_status = master.get_comm_status_snapshot()
            hp_comm_status = {
                hp_id: comm_status.get("hp", {}).get(hp_id, -1)
                for hp_id in settings.hp_ids
            }
            ct_comm_status = {
                settings.ct_id: comm_status.get("ct", {}).get(settings.ct_id, -1)
            }
            comm_ok = redis_writer.write_comm_status(
                hp_status=hp_comm_status,
                ct_status=ct_comm_status,
                hp_device_name=settings.hp_device_name,
                ct_device_name=settings.ct_device_name,
            )
            if not comm_ok:
                logger.warning("Redis comm_status sync not successful in this cycle")

            commands = redis_writer.fetch_write_commands(
                hp_slave_ids=settings.hp_ids,
                hp_device_name=settings.hp_device_name,
            )
            for device_id, cmd in commands.items():
                address = cmd["address"]
                value = cmd["value"]

                if address > 0x006D:
                    redis_writer.clear_write_command(
                        hp_device_name=settings.hp_device_name,
                        device_id=device_id,
                    )
                    logger.warning(
                        "Ignored write command for heatpump:%s (address=0x%04X > 0x006D), command cleared",
                        device_id,
                        address,
                    )
                    continue

                write_ok = master.write_register(
                    slave_id=device_id,
                    register_address=address,
                    value=value,
                )
                if write_ok:
                    redis_writer.clear_write_command(
                        hp_device_name=settings.hp_device_name,
                        device_id=device_id,
                    )
                    logger.info(
                        "Write command applied and cleared for heatpump:%s (address=0x%04X value=%s)",
                        device_id,
                        address,
                        value,
                    )
                else:
                    logger.warning(
                        "Write command failed for heatpump:%s (address=0x%04X value=%s), command retained",
                        device_id,
                        address,
                        value,
                    )

            if stop_event.wait(settings.redis_sync_interval_sec):
                break
    finally:
        master.stop_polling()
        master.disconnect()
        raw_frame_logger.close()
        redis_writer.close()
        logger.info("Sync service stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
