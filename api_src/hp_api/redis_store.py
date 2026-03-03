from __future__ import annotations

import json
import logging
import time
from typing import Any

import redis

from register_i18n import CN_TO_EN_REGISTER_NAME, to_english_register_name
from src.hp_controller.master.register_mapping import CT_REGISTER_MAP, HP_REGISTER_MAP

from .settings import RedisSettings


HP_NAME_TO_ADDRESS: dict[str, int] = {
    to_english_register_name(cn_name): int(address)
    for address, cn_name in HP_REGISTER_MAP.items()
}
CT_NAME_TO_ADDRESS: dict[str, int] = {
    str(name): int(address)
    for address, name in CT_REGISTER_MAP.items()
}
EN_TO_CN_REGISTER_NAME: dict[str, str] = {
    en_name: cn_name for cn_name, en_name in CN_TO_EN_REGISTER_NAME.items()
}


class RedisStore:
    def __init__(
        self,
        config: RedisSettings,
        logger: logging.Logger | None = None,
    ) -> None:
        base_logger = logger or logging.getLogger("hp_api.redis")
        self.logger = base_logger.getChild("RedisStore")
        self.config = config
        self._client: redis.Redis | None = None
        self._last_connect_attempt_ts: float = 0.0

    def connect(self) -> bool:
        now = time.monotonic()
        if now - self._last_connect_attempt_ts < 1.0:
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
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Redis connect failed: %s", exc)
            self._client = None
            return False

    def _ensure(self) -> bool:
        if self._client is None:
            return self.connect()
        try:
            self._client.ping()
            return True
        except Exception:  # noqa: BLE001
            self._client = None
            return self.connect()

    @staticmethod
    def _parse_value(value: str | None) -> Any:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return ""

        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            pass

        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text

    def _prefixed(self, key: str, key_prefix: str) -> str:
        if not key_prefix:
            return key
        return f"{key_prefix}{key}"

    def get_device_raw_status(
        self,
        device_name: str,
        device_id: int,
        key_prefix: str = "",
    ) -> dict[str, Any]:
        if not self._ensure() or self._client is None:
            return {}

        pattern = self._prefixed(f"{device_name}:{device_id}:*", key_prefix)
        keys = sorted(self._client.keys(pattern))
        if not keys:
            return {}

        pipe = self._client.pipeline(transaction=False)
        for key in keys:
            pipe.get(key)
        values = pipe.execute()

        payload: dict[str, Any] = {}
        for key, raw in zip(keys, values):
            suffix = key.split(":")[-1]
            payload[suffix] = self._parse_value(raw)

        return payload

    def get_device_status_items(
        self,
        *,
        device_name: str,
        device_id: int,
        is_heatpump: bool,
        lang: str,
        key_prefix: str = "",
    ) -> dict[str, Any]:
        raw_payload = self.get_device_raw_status(device_name, device_id, key_prefix)
        if not raw_payload:
            return {"comm_status": -1, "items": []}

        address_map = HP_NAME_TO_ADDRESS if is_heatpump else CT_NAME_TO_ADDRESS
        comm_status = raw_payload.get("comm_status", -1)
        try:
            comm_status_value = int(comm_status)
        except Exception:  # noqa: BLE001
            comm_status_value = -1
        raw_payload = {k: v for k, v in raw_payload.items() if k != "comm_status"}

        known_items: list[dict[str, Any]] = []
        unknown_items: list[dict[str, Any]] = []

        for name, value in raw_payload.items():
            display_name = EN_TO_CN_REGISTER_NAME.get(name, name) if lang == "zh" else name
            address = address_map.get(name)
            if address is None:
                unknown_items.append(
                    {
                        "address": -1,
                        "name": display_name,
                        "value": value,
                        "read-only": True,
                    }
                )
                continue

            known_items.append(
                {
                    "address": int(address),
                    "name": display_name,
                    "value": value,
                    "read-only": not (0 <= int(address) < 0x006D),
                }
            )

        known_items.sort(key=lambda item: int(item["address"]))
        unknown_items.sort(key=lambda item: str(item["name"]))

        ordered = known_items + unknown_items
        for idx, item in enumerate(ordered, start=1):
            item["display-order"] = idx

        return {"comm_status": comm_status_value, "items": ordered}

    def heatpump_exists(self, device_name: str, device_id: int, key_prefix: str = "") -> bool:
        if not self._ensure() or self._client is None:
            return False
        pattern = self._prefixed(f"{device_name}:{device_id}:*", key_prefix)
        keys = self._client.keys(pattern)
        return len(keys) > 0

    def ct_exists(self, device_name: str, device_id: int, key_prefix: str = "") -> bool:
        if not self._ensure() or self._client is None:
            return False
        pattern = self._prefixed(f"{device_name}:{device_id}:*", key_prefix)
        keys = self._client.keys(pattern)
        return len(keys) > 0

    def set_heatpump_cmd(
        self,
        *,
        device_name: str,
        device_id: int,
        address: int,
        value: int,
        key_prefix: str = "",
    ) -> bool:
        if not self._ensure() or self._client is None:
            return False

        key = self._prefixed(f"{device_name}:{device_id}:cmd", key_prefix)
        payload = json.dumps({"address": int(address), "value": int(value)}, ensure_ascii=False)
        try:
            self._client.set(name=key, value=payload)
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Failed to set cmd key %s: %s", key, exc)
            self._client = None
            return False
