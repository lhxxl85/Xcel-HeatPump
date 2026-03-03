from __future__ import annotations

from typing import Iterable, Literal, Tuple

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModbusTcpSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 502
    timeout_sec: float = 3.0


class ModbusRtuSettings(BaseModel):
    port: str = "/dev/ttyUSB0"
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    timeout_sec: float = 3.0


class DeviceModbusSettings(BaseModel):
    transport: Literal["tcp", "rtu"] = "tcp"
    tcp: ModbusTcpSettings = Field(default_factory=ModbusTcpSettings)
    rtu: ModbusRtuSettings = Field(default_factory=ModbusRtuSettings)

    @field_validator("transport", mode="before")
    @classmethod
    def _normalize_transport(cls, value: str) -> str:
        return str(value).strip().lower()


class HpBusSettings(BaseModel):
    transport: Literal["tcp", "rtu"] = "rtu"
    tcp: ModbusTcpSettings = Field(default_factory=ModbusTcpSettings)
    rtu: ModbusRtuSettings = Field(default_factory=ModbusRtuSettings)
    slave_ids: Tuple[int, ...] = ()

    @field_validator("transport", mode="before")
    @classmethod
    def _normalize_transport(cls, value: str) -> str:
        return str(value).strip().lower()

    @field_validator("slave_ids", mode="before")
    @classmethod
    def _parse_slave_ids(cls, value: Iterable[int] | str) -> Tuple[int, ...]:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return tuple(int(part) for part in parts)
        return tuple(int(x) for x in value)


class RedisSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    username: str | None = None
    password: str | None = None
    socket_timeout_sec: float = 2.0
    connect_timeout_sec: float = 2.0
    key_prefix: str = ""
    key_ttl_sec: int = 0
    reconnect_interval_sec: float = 3.0


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    hp_bus_ama5: HpBusSettings = Field(
        default_factory=lambda: HpBusSettings(
            transport="rtu",
            rtu=ModbusRtuSettings(port="/dev/ttyAMA5"),
            slave_ids=(1, 3, 4),
        )
    )
    hp_bus_ama3: HpBusSettings = Field(
        default_factory=lambda: HpBusSettings(
            transport="rtu",
            rtu=ModbusRtuSettings(port="/dev/ttyAMA3"),
            slave_ids=(5, 6, 7),
        )
    )
    ct: DeviceModbusSettings = Field(default_factory=DeviceModbusSettings)

    ct_id: int = 10
    poll_interval_sec: float = 0.5
    reconnect_interval_sec: float = 3.0

    redis: RedisSettings = Field(default_factory=RedisSettings)
    redis_sync_interval_sec: float = 0.5
    command_poll_interval_sec: float = 0.1
    data_stale_after_sec: float = 3.0
    hp_device_name: str = "heatpump"
    ct_device_name: str = "ct"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: str = "logs"

    @property
    def hp_ids(self) -> Tuple[int, ...]:
        return tuple(self.hp_bus_ama5.slave_ids) + tuple(self.hp_bus_ama3.slave_ids)
