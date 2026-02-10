# 配置加载
# src/hp_controller/settings.py
from __future__ import annotations

from typing import Iterable, Literal, Tuple

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModbusTcpSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5020
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


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    hp: DeviceModbusSettings = Field(default_factory=DeviceModbusSettings)
    ct: DeviceModbusSettings = Field(default_factory=DeviceModbusSettings)

    hp_ids: Tuple[int, ...] = (1, 2, 3, 4, 5, 6)
    ct_id: int = 10
    poll_interval_sec: float = 0.5
    algo_interval_sec: float = 1.0
    current_limit: float = 1200.0
    deadband: float = 50.0
    safety_margin: float = 30.0
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    print_state: bool = False
    aggregation_interval_sec: float = 60.0
    log_dir: str = "logs"
    control_mode: bool = False

    @field_validator("hp_ids", mode="before")
    @classmethod
    def _parse_hp_ids(cls, value: Iterable[int] | str) -> Tuple[int, ...]:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return tuple(int(part) for part in parts)
        return tuple(int(x) for x in value)
