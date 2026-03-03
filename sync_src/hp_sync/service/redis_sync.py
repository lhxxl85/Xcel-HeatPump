from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

import redis

from register_i18n import to_english_register_name
from sync_src.hp_sync.master.register_mapping import HP_REGISTER_MAP


@dataclass
class RedisWriterConfig:
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


class RedisWriter:
    def __init__(
        self,
        config: RedisWriterConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        base_logger = logger or logging.getLogger("hp_sync.redis")
        self.logger = base_logger.getChild("RedisWriter")
        self.config = config
        self._client: redis.Redis | None = None
        self._last_connect_attempt_ts: float = 0.0

    def connect(self) -> bool:
        now = time.monotonic()
        if now - self._last_connect_attempt_ts < self.config.reconnect_interval_sec:
            return False

        self._last_connect_attempt_ts = now
        try:
            self._client = redis.Redis(
                host=self.config.host,
                port=self.config.port,
                db=self.config.db,
                username=self.config.username,
                password=self.config.password,
                socket_timeout=self.config.socket_timeout_sec,
                socket_connect_timeout=self.config.connect_timeout_sec,
                decode_responses=True,
            )
            self._client.ping()
            self.logger.info(
                "Connected Redis at %s:%s/%s",
                self.config.host,
                self.config.port,
                self.config.db,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis connect failed: %s", exc)
            self._client = None
            return False

    def close(self) -> None:
        self._client = None

    def _ensure_connection(self) -> bool:
        if self._client is None:
            return self.connect()
        try:
            self._client.ping()
            return True
        except Exception:  # noqa: BLE001
            self._client = None
            return self.connect()

    def write_partitioned_snapshot(
        self,
        hp_snapshot: Mapping[int, Mapping[str, int | float]],
        ct_snapshot: Mapping[int, Mapping[str, int | float]],
        *,
        hp_device_name: str,
        ct_device_name: str,
    ) -> bool:
        if not hp_snapshot and not ct_snapshot:
            return True
        if not self._ensure_connection():
            return False
        if self._client is None:
            return False

        try:
            pipe = self._client.pipeline(transaction=False)
            for device_id, registers in hp_snapshot.items():
                for register_name, value in registers.items():
                    english_name = to_english_register_name(register_name)
                    key = f"{hp_device_name}:{device_id}:{english_name}"
                    if self.config.key_prefix:
                        key = f"{self.config.key_prefix}{key}"
                    if self.config.key_ttl_sec > 0:
                        pipe.set(name=key, value=value, ex=self.config.key_ttl_sec)
                    else:
                        pipe.set(name=key, value=value)

            for device_id, registers in ct_snapshot.items():
                for register_name, value in registers.items():
                    english_name = to_english_register_name(register_name)
                    key = f"{ct_device_name}:{device_id}:{english_name}"
                    if self.config.key_prefix:
                        key = f"{self.config.key_prefix}{key}"
                    if self.config.key_ttl_sec > 0:
                        pipe.set(name=key, value=value, ex=self.config.key_ttl_sec)
                    else:
                        pipe.set(name=key, value=value)

            pipe.execute()
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis write failed: %s", exc)
            self._client = None
            return False

    def _command_key(self, hp_device_name: str, device_id: int) -> str:
        key = f"{hp_device_name}:{device_id}:cmd"
        if self.config.key_prefix:
            key = f"{self.config.key_prefix}{key}"
        return key

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if not text:
                return None
            try:
                return int(text, 16) if text.startswith("0x") else int(text)
            except ValueError:
                return None
        return None

    def fetch_write_commands(
        self,
        *,
        hp_slave_ids: tuple[int, ...],
        hp_device_name: str,
    ) -> dict[int, dict[str, int]]:
        """
        读取 heatpump:{id}:cmd，格式:
        {
          "address": xxx,
          "value": xxx
        }
        """
        if not self._ensure_connection() or self._client is None:
            return {}

        commands: dict[int, dict[str, int]] = {}
        for device_id in hp_slave_ids:
            key = self._command_key(hp_device_name, device_id)
            try:
                raw = self._client.get(key)
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Redis read command failed for %s: %s", key, exc)
                self._client = None
                return commands

            if raw is None or str(raw).strip() == "":
                continue

            try:
                payload = json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Invalid command JSON on %s: %s (%s)", key, raw, exc)
                continue

            if not isinstance(payload, dict):
                self.logger.warning("Invalid command type on %s: %s", key, type(payload))
                continue

            address = self._to_int(payload.get("address"))
            value = self._to_int(payload.get("value"))
            if address is None or value is None:
                self.logger.warning(
                    "Invalid command fields on %s: address=%s value=%s",
                    key,
                    payload.get("address"),
                    payload.get("value"),
                )
                continue

            commands[device_id] = {"address": address, "value": value}

        return commands

    def clear_write_command(self, *, hp_device_name: str, device_id: int) -> bool:
        if not self._ensure_connection() or self._client is None:
            return False
        key = self._command_key(hp_device_name, device_id)
        try:
            self._client.set(name=key, value="")
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis clear command failed for %s: %s", key, exc)
            self._client = None
            return False

    def update_written_register_value(
        self,
        *,
        hp_device_name: str,
        device_id: int,
        address: int,
        value: int,
    ) -> bool:
        if not self._ensure_connection() or self._client is None:
            return False
        register_name = HP_REGISTER_MAP.get(address)
        if register_name is None:
            self.logger.warning(
                "Cannot update redis register cache for heatpump:%s, unknown register address=0x%04X",
                device_id,
                address,
            )
            return False

        english_name = to_english_register_name(register_name)
        key = f"{hp_device_name}:{device_id}:{english_name}"
        if self.config.key_prefix:
            key = f"{self.config.key_prefix}{key}"
        try:
            if self.config.key_ttl_sec > 0:
                self._client.set(name=key, value=value, ex=self.config.key_ttl_sec)
            else:
                self._client.set(name=key, value=value)
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis update written register failed for %s: %s", key, exc)
            self._client = None
            return False

    def write_comm_status(
        self,
        *,
        hp_status: Mapping[int, int],
        ct_status: Mapping[int, int],
        hp_device_name: str,
        ct_device_name: str,
    ) -> bool:
        if not self._ensure_connection() or self._client is None:
            return False
        try:
            pipe = self._client.pipeline(transaction=False)
            for device_id, status in hp_status.items():
                key = f"{hp_device_name}:{device_id}:comm_status"
                if self.config.key_prefix:
                    key = f"{self.config.key_prefix}{key}"
                pipe.set(name=key, value=int(status))
            for device_id, status in ct_status.items():
                key = f"{ct_device_name}:{device_id}:comm_status"
                if self.config.key_prefix:
                    key = f"{self.config.key_prefix}{key}"
                pipe.set(name=key, value=int(status))
            pipe.execute()
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis comm_status write failed: %s", exc)
            self._client = None
            return False
