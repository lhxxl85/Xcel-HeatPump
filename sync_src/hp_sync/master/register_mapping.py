from __future__ import annotations

# 直接复用 src 原始映射，保证寄存器定义单一来源。
from src.hp_controller.master.register_mapping import (  # noqa: F401
    BIT_Translate,
    CT_REGISTER_MAP,
    CT_REGISTER_MAP_REVERSE,
    HP_REGISTER_MAP,
    HP_REGISTER_MAP_REVERSE,
    bit_tranlte,
)

__all__ = [
    "HP_REGISTER_MAP",
    "CT_REGISTER_MAP",
    "HP_REGISTER_MAP_REVERSE",
    "CT_REGISTER_MAP_REVERSE",
    "BIT_Translate",
    "bit_tranlte",
]
