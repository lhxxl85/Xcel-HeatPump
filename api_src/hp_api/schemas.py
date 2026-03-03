from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    success: bool
    code: str
    message: str
    data: Any = None


class CmdRequest(BaseModel):
    address: int = Field(..., description="Modbus register address")
    value: int = Field(..., description="Value to write")
