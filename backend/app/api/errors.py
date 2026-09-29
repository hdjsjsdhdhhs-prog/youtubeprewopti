"""Map exceptions to a uniform JSON error body: ``{"error": {"code", "message", "details"}}``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _body(code: str, message: str, details: dict | list | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(_body("validation_error", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(_body(f"http_{exc.status_code}", str(exc.detail)), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        # Race past an application-level check (e.g. two concurrent creates with the same name).
        log.warning("integrity_error", path=request.url.path, constraint=_constraint_name(exc))
        return JSONResponse(_body("conflict", "The request conflicts with existing data"), status_code=409)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path, method=request.method)
        return JSONResponse(_body("internal_error", "Internal server error"), status_code=500)
