from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Dict, Mapping, Optional

import redis


@dataclass
class RedisMasterConfig:
    hp_slave_ids: tuple[int, ...] = (1, 3, 4, 5, 6, 7)
    ct_slave_id: int = 10
    hp_device_name: str = "heatpump"
    ct_device_name: str = "ct"
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    username: str | None = None
    password: str | None = None
    socket_timeout_sec: float = 2.0
    connect_timeout_sec: float = 2.0
    reconnect_interval_sec: float = 3.0
    key_prefix: str = ""
    control_mode: bool = False


class RedisMaster:
    """
    兼容旧算法调用风格的数据网关：
    - 读取 Redis 中由 sync service 同步的 HP/CT 数据
    - 写入 Redis 命令键 heatpump:{id}:cmd
    """

    HP_SOURCE_TO_CANONICAL = {
        "ac_heating_set_temperature": "heating_setpoint",
        "ac_hysteresis": "hysteresis_value",
        "inlet_water_temperature": "inlet_temperature",
        "compressor_1_current": "compressor1_current",
        "compressor_2_current": "compressor2_current",
        "compressor_3_current": "compressor3_current",
        "compressor_4_current": "compressor4_current",
    }
    CT_FIELDS = (
        "current_l1",
        "current_l2",
        "current_l3",
        "total_current",
    )

    def __init__(
        self,
        config: Optional[RedisMasterConfig] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        base_logger = logger or logging.getLogger("hp_controller.master.redis")
        self.logger = base_logger.getChild("RedisMaster")
        self.config = config or RedisMasterConfig()
        self._client: redis.Redis | None = None
        self._last_connect_attempt_ts: float = 0.0

    def _data_key(self, device_name: str, device_id: int, register_name: str) -> str:
        key = f"{device_name}:{device_id}:{register_name}"
        if self.config.key_prefix:
            key = f"{self.config.key_prefix}{key}"
        return key

    def _cmd_key(self, device_id: int) -> str:
        key = f"{self.config.hp_device_name}:{device_id}:cmd"
        if self.config.key_prefix:
            key = f"{self.config.key_prefix}{key}"
        return key

    @staticmethod
    def _to_float(value: str | None) -> float | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

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

    def _ensure_connection(self) -> bool:
        if self._client is None:
            return self.connect()
        try:
            self._client.ping()
            return True
        except Exception:  # noqa: BLE001
            self._client = None
            return self.connect()

    def disconnect(self) -> None:
        self._client = None

    def get_shared_state_snapshot(self) -> Dict[int, Dict[str, float]]:
        if not self._ensure_connection() or self._client is None:
            return {}

        snapshot: Dict[int, Dict[str, float]] = {}
        try:
            pipe = self._client.pipeline(transaction=False)
            hp_key_index: list[tuple[int, str, str]] = []
            ct_key_index: list[tuple[int, str]] = []

            for hp_id in self.config.hp_slave_ids:
                for source_name, canonical_name in self.HP_SOURCE_TO_CANONICAL.items():
                    pipe.get(self._data_key(self.config.hp_device_name, hp_id, source_name))
                    hp_key_index.append((hp_id, source_name, canonical_name))

            for field in self.CT_FIELDS:
                pipe.get(self._data_key(self.config.ct_device_name, self.config.ct_slave_id, field))
                ct_key_index.append((self.config.ct_slave_id, field))

            values = pipe.execute()
            pos = 0
            for hp_id, _source, canonical in hp_key_index:
                num = self._to_float(values[pos])
                pos += 1
                if num is None:
                    continue
                regs = snapshot.setdefault(hp_id, {})
                regs[canonical] = num

            for ct_id, field in ct_key_index:
                num = self._to_float(values[pos])
                pos += 1
                if num is None:
                    continue
                regs = snapshot.setdefault(ct_id, {})
                regs[field] = num

            return snapshot
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis snapshot read failed: %s", exc)
            self._client = None
            return {}

    def get_comm_status_snapshot(self) -> dict[str, dict[int, int]]:
        if not self._ensure_connection() or self._client is None:
            return {"hp": {}, "ct": {}}

        hp_status: dict[int, int] = {}
        ct_status: dict[int, int] = {}
        try:
            pipe = self._client.pipeline(transaction=False)
            hp_ids = tuple(self.config.hp_slave_ids)
            for hp_id in hp_ids:
                pipe.get(f"{self._data_key(self.config.hp_device_name, hp_id, 'comm_status')}")
            pipe.get(
                f"{self._data_key(self.config.ct_device_name, self.config.ct_slave_id, 'comm_status')}"
            )
            values = pipe.execute()

            pos = 0
            for hp_id in hp_ids:
                raw = values[pos]
                pos += 1
                try:
                    hp_status[hp_id] = int(str(raw).strip()) if raw is not None else -1
                except ValueError:
                    hp_status[hp_id] = -1

            raw_ct = values[pos] if pos < len(values) else None
            try:
                ct_status[self.config.ct_slave_id] = (
                    int(str(raw_ct).strip()) if raw_ct is not None else -1
                )
            except ValueError:
                ct_status[self.config.ct_slave_id] = -1

            return {"hp": hp_status, "ct": ct_status}
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis comm_status read failed: %s", exc)
            self._client = None
            return {"hp": {}, "ct": {}}

    def write_register(self, slave_id: int, register_address: int, value: int) -> bool:
        if not self.config.control_mode:
            self.logger.info(
                "Control mode disabled, skip writing command to heatpump:%s (address=%s value=%s)",
                slave_id,
                register_address,
                value,
            )
            return True
        if slave_id not in self.config.hp_slave_ids:
            self.logger.error("Invalid hp slave_id=%s for redis command write", slave_id)
            return False

        if not self._ensure_connection() or self._client is None:
            return False

        payload = {"address": int(register_address), "value": int(value)}
        key = self._cmd_key(slave_id)
        try:
            self._client.set(name=key, value=json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis command write failed for %s: %s", key, exc)
            self._client = None
            return False
