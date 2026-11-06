"""The one error shape of the studio API and the handlers that produce it (contracts/studio-api.md "Error shape").

Every failed request answers `{"code", "message", "problems", "missing"}`:

- `ApiError`, raised by the routes, carries its own status and code.
- `dentalsim.errors.Refused` keeps its code; the status comes from `STATUS_BY_CODE`, and an unknown refusal code
  answers 409.
- `NotFound` answers 404 `not_found` and `Conflict` 409 `conflict`; another engine error answers 500
  `engine_error` with its plain-language message.
- Request validation answers 422 `validation_failed`, with one problem per invalid field (the values are never
  echoed, so a password never appears in a reply).
- Routing errors (no such path, wrong method) answer their status with a matching code.
- Anything else answers 500 `internal_error` with a generic message, and the traceback goes to the server log.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dentalsim.errors import Conflict, DentalsimError, NotFound, Refused

logger = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, int] = {
    "not_signed_in": 401,
    "validation_failed": 422,
    "run_refused": 409,
    "estimate_too_large": 409,
    "publish_refused": 409,
    "not_found": 404,
    "conflict": 409,
    "out_of_date": 409,
}
REFUSED_DEFAULT_STATUS = 409

_CODE_BY_STATUS = {
    401: "not_signed_in",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_failed",
}
_LOCATIONS = ("body", "query", "path", "header", "cookie")

INTERNAL_ERROR_MESSAGE = "Something went wrong in the studio server. The details are in the server log on the main PC."


class ErrorItem(BaseModel):
    """One problem or missing item. `message` is always there; the other keys depend on the item."""

    model_config = ConfigDict(extra="allow")

    message: str
    code: str | None = None
    item: str | None = None


class ErrorBody(BaseModel):
    """The body of every failed request. All four keys are always present."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    code: str = Field(
        description=(
            "not_signed_in, validation_failed, run_refused, estimate_too_large, publish_refused, not_found, "
            "conflict, out_of_date, or another refusal code; internal_error and engine_error for server faults"
        )
    )
    message: str = Field(description="Plain-language explanation for the owner")
    problems: list[ErrorItem] = []
    missing: list[ErrorItem] = []


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` documenting the error shape for `statuses`."""
    return {status: {"model": ErrorBody, "description": "Error"} for status in statuses}


class ApiError(Exception):
    """An error a route answers with: any status and code, in the error shape."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        problems: Sequence[Mapping[str, Any]] = (),
        missing: Sequence[Mapping[str, Any]] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.problems = [dict(p) for p in problems]
        self.missing = [dict(m) for m in missing]
        self.headers = dict(headers or {})


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    problems: Sequence[Mapping[str, Any]] = (),
    missing: Sequence[Mapping[str, Any]] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {"code": code, "message": message, "problems": list(problems), "missing": list(missing)}
    return JSONResponse(body, status_code=status_code, headers=dict(headers) if headers else None)


def status_for_refusal(code: str) -> int:
    return STATUS_BY_CODE.get(code, REFUSED_DEFAULT_STATUS)


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(
        exc.status_code, exc.code, exc.message, problems=exc.problems, missing=exc.missing, headers=exc.headers
    )


async def _refused(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, Refused)
    return error_response(
        status_for_refusal(exc.code), exc.code, exc.message, problems=exc.problems, missing=exc.missing
    )


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    return error_response(404, "not_found", str(exc) or "Not found.")


async def _conflict(request: Request, exc: Exception) -> JSONResponse:
    return error_response(409, "conflict", str(exc) or "The record changed while it was being edited.")


async def _engine_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("engine error in %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "engine_error", str(exc) or INTERNAL_ERROR_MESSAGE)


def validation_problems(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """One problem per invalid field, in plain words, without the submitted values."""
    problems = []
    for error in errors:
        loc = [str(part) for part in error.get("loc", ())]
        if len(loc) > 1 and loc[0] in _LOCATIONS:
            loc = loc[1:]
        field = ".".join(loc) or "request"
        problems.append({"code": "invalid_value", "field": field, "message": f"{field}: {error.get('msg', 'invalid')}"})
    return problems


async def _validation_failed(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    problems = validation_problems(exc.errors())
    if len(problems) == 1:
        message = f"The request is not valid: {problems[0]['message']}."
    else:
        message = f"The request has {len(problems)} values that are not valid."
    return error_response(422, "validation_failed", message, problems=problems)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _CODE_BY_STATUS.get(exc.status_code, "error")
    if exc.status_code == 404:
        message = f"There is nothing at {request.url.path}."
    elif exc.status_code == 405:
        message = f"{request.url.path} does not accept {request.method} requests."
    else:
        message = exc.detail if isinstance(exc.detail, str) else "The request failed."
    return error_response(exc.status_code, code, message, headers=exc.headers)


class UnexpectedErrorMiddleware:
    """Turns an unexpected exception into a 500 in the error shape, and logs its traceback once.

    It sits inside Starlette's outermost error middleware, which would otherwise answer in plain text and re-raise.
    When the response has already started (an event stream, say), the exception is re-raised instead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_and_track(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_and_track)
        except Exception:
            logger.exception("unexpected error in %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            await error_response(500, "internal_error", INTERNAL_ERROR_MESSAGE)(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(Refused, _refused)
    app.add_exception_handler(NotFound, _not_found)
    app.add_exception_handler(Conflict, _conflict)
    app.add_exception_handler(DentalsimError, _engine_error)
    app.add_exception_handler(RequestValidationError, _validation_failed)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_middleware(UnexpectedErrorMiddleware)
