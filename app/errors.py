from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str, extra: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}


class InvalidRequest(ServiceError):
    def __init__(self, message: str):
        super().__init__(400, "INVALID_REQUEST", message)


class Unauthorized(ServiceError):
    def __init__(self, message: str = "Authorization required"):
        super().__init__(401, "UNAUTHORIZED", message)


class Forbidden(ServiceError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(403, "FORBIDDEN", message)


class NotFound(ServiceError):
    def __init__(self, message: str = "Not found"):
        super().__init__(404, "NOT_FOUND", message)


class ServiceUnavailable(ServiceError):
    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(503, "SERVICE_UNAVAILABLE", message)


def _payload(code: str, message: str, extra: dict | None = None) -> dict:
    return {"code": code, "message": message, **(extra or {})}


async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_payload(exc.code, exc.message, exc.extra),
    )


_CODE_BY_STATUS: dict[int, str] = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "INVALID_REQUEST",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _CODE_BY_STATUS.get(exc.status_code, "ERROR")
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail else "Error"
    return JSONResponse(status_code=exc.status_code, content=_payload(code, detail))


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    message = "Request validation failed"
    errors = exc.errors()
    if errors:
        location = ".".join(str(part) for part in errors[0].get("loc", []) if part != "body")
        if location:
            message = f"{location}: {errors[0].get('msg', message)}"
        else:
            message = str(errors[0].get("msg", message))
    return JSONResponse(status_code=400, content=_payload("INVALID_REQUEST", message))
