"""CloudOSError → HTTP mapping and exception handlers.

Every error response body uses the §6 shape:
    {"error": {"code": "<ErrorCode>", "message": str, "details": {}}}
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from cloudos.contracts import CloudOSError, ErrorCode, error_response

log = logging.getLogger("cloudos.api")

# Contract §6/§7 error-code → HTTP status mapping.
STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.PAID_DISABLED: 403,
    ErrorCode.MODEL_NOT_ALLOWED: 403,
    ErrorCode.PRIVACY_BLOCKED: 403,
    ErrorCode.SECRET_DETECTED: 403,
    ErrorCode.QUOTA_EXHAUSTED: 429,
    ErrorCode.LIMIT_REACHED: 429,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}


class AuthFailed(Exception):
    """Raised by the auth dependency; rendered as 401 with the §6 body.

    Note: the shared ErrorCode enum has no UNAUTHORIZED member, so 401 bodies
    carry VALIDATION_ERROR (closest in-enum code) — flagged in the agent report.
    """

    def __init__(self, message: str = "missing or invalid credentials"):
        super().__init__(message)
        self.message = message


class NotFound(Exception):
    """Rendered as 404 with the §6 body (no NOT_FOUND ErrorCode exists — see report)."""

    def __init__(self, message: str = "resource not found"):
        super().__init__(message)
        self.message = message


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CloudOSError)
    async def _cloudos_error(request: Request, exc: CloudOSError) -> JSONResponse:
        status = STATUS_BY_CODE.get(exc.code, 500)
        return JSONResponse(status_code=status, content=exc.to_dict())

    @app.exception_handler(AuthFailed)
    async def _auth_failed(request: Request, exc: AuthFailed) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content=error_response(ErrorCode.VALIDATION_ERROR, exc.message),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=error_response(ErrorCode.VALIDATION_ERROR, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Compact, JSON-safe subset of pydantic's error list (ctx may hold exceptions).
        errors = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content=error_response(ErrorCode.VALIDATION_ERROR, "invalid request", {"errors": errors}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Keep the §6 shape even for framework-raised 404/405 etc.
        detail = exc.detail if isinstance(exc.detail, str) else "http error"
        code = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.VALIDATION_ERROR
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(code, detail),
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals (or secrets) — metadata only.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_response(
                ErrorCode.INTERNAL_ERROR, "internal error", {"type": exc.__class__.__name__}
            ),
        )
