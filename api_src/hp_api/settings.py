from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    username: str | None = None
    password: str | None = None
    socket_timeout_sec: float = 2.0
    connect_timeout_sec: float = 2.0
    key_prefix: str = ""


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8010
    api_cors_allow_origins: str = "*"
    log_dir: str = "logs"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    redis: RedisSettings = Field(default_factory=RedisSettings)
    hp_device_name: str = "heatpump"
    ct_device_name: str = "ct"
