from __future__ import annotations

import logging
from typing import Literal
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .redis_store import RedisStore
from .schemas import ApiResponse, CmdRequest
from .settings import ApiSettings
from .utils.logging_config import setup_logging


def success_response(message: str, data: Any = None) -> JSONResponse:
    body = ApiResponse(success=True, code="OK", message=message, data=data)
    return JSONResponse(status_code=200, content=body.model_dump())


def error_response(status_code: int, code: str, message: str, data: Any = None) -> JSONResponse:
    body = ApiResponse(success=False, code=code, message=message, data=data)
    return JSONResponse(status_code=status_code, content=body.model_dump())


settings = ApiSettings()
setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)
logger = logging.getLogger("hp_api.main")
store = RedisStore(config=settings.redis, logger=logging.getLogger("hp_api.redis"))

app = FastAPI(title="HeatPump API", version="1.0.0")
_allow_origins = [x.strip() for x in settings.api_cors_allow_origins.split(",") if x.strip()]
if not _allow_origins:
    _allow_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    logger.info("%s %s", request.method, request.url.path)
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException):
    code = "HTTP_ERROR"
    if exc.status_code == 404:
        code = "RESOURCE_NOT_FOUND"
    elif exc.status_code == 405:
        code = "METHOD_NOT_ALLOWED"
    return error_response(exc.status_code, code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return error_response(422, "VALIDATION_ERROR", "Request validation failed", exc.errors())


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    logger.error("Unhandled error: %s", exc, exc_info=True)
    return error_response(500, "INTERNAL_ERROR", "Internal server error")


@app.get("/api/v1/heatpump/{slave_id}/status")
def get_heatpump_status(slave_id: int, lang: Literal["en", "zh"] = "en"):
    if not store.heatpump_exists(settings.hp_device_name, slave_id, settings.redis.key_prefix):
        return error_response(404, "RESOURCE_NOT_FOUND", f"heatpump slave_id {slave_id} not found")

    payload = store.get_device_status_items(
        device_name=settings.hp_device_name,
        device_id=slave_id,
        is_heatpump=True,
        lang=lang,
        key_prefix=settings.redis.key_prefix,
    )
    return success_response("heatpump status fetched", payload)


@app.get("/api/v1/ct/{slave_id}/status")
def get_ct_status(slave_id: int, lang: Literal["en", "zh"] = "en"):
    if not store.ct_exists(settings.ct_device_name, slave_id, settings.redis.key_prefix):
        return error_response(404, "RESOURCE_NOT_FOUND", f"ct slave_id {slave_id} not found")

    payload = store.get_device_status_items(
        device_name=settings.ct_device_name,
        device_id=slave_id,
        is_heatpump=False,
        lang=lang,
        key_prefix=settings.redis.key_prefix,
    )
    return success_response("ct status fetched", payload)


@app.post("/api/v1/heatpump/{slave_id}/cmd")
def post_heatpump_cmd(slave_id: int, body: CmdRequest):
    if not store.heatpump_exists(settings.hp_device_name, slave_id, settings.redis.key_prefix):
        return error_response(404, "RESOURCE_NOT_FOUND", f"heatpump slave_id {slave_id} not found")

    # 0x006D 之前才允许（即 address < 0x006D）
    if body.address >= 0x006D:
        return error_response(
            422,
            "RESOURCE_UNAVAILABLE",
            f"address {body.address} is out of writable range (< 0x006D required)",
        )

    ok = store.set_heatpump_cmd(
        device_name=settings.hp_device_name,
        device_id=slave_id,
        address=body.address,
        value=body.value,
        key_prefix=settings.redis.key_prefix,
    )
    if not ok:
        return error_response(503, "REDIS_UNAVAILABLE", "failed to write command to redis")

    return success_response("command accepted", {"slave_id": slave_id, "address": body.address, "value": body.value})


if __name__ == "__main__":
    uvicorn.run(
        "api_src.hp_api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )
