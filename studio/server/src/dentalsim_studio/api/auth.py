"""Sign-in routes (FR-001, contracts/studio-api.md "Auth and system").

- `POST /api/auth/login` is the only route reachable without a session: it is on `public_router`.
- `POST /api/auth/logout` and `GET /api/auth/session` are on `router`, which the app includes as protected.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dentalsim_studio.api.deps import ReposDep, SessionDep, get_sessions
from dentalsim_studio.api.errors import ApiError, error_responses
from dentalsim_studio.auth.password import SET_PASSWORD_COMMAND, password_is_set, verify_password
from dentalsim_studio.auth.sessions import SESSION_COOKIE

public_router = APIRouter(prefix="/api/auth", tags=["auth"])
router = APIRouter(prefix="/api/auth", tags=["auth"])

WRONG_PASSWORD = "Wrong password."
NO_PASSWORD = f"No owner password is set. On the main PC, run: {SET_PASSWORD_COMMAND}"
TOO_MANY_ATTEMPTS = "Too many sign-in attempts. Wait a minute, then try again."


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1024)


class LoginResult(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    signed_in: Literal[True] = True


class SessionInfo(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    signed_in: Literal[True] = True
    expires_at: datetime = Field(description="When the session ends unless another request comes first (UTC)")


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(SESSION_COOKIE, session_id, path="/", secure=True, httponly=True, samesite="strict")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


@public_router.post("/login", response_model=LoginResult, responses=error_responses(401, 422, 429))
def login(body: LoginRequest, request: Request, response: Response, repos: ReposDep) -> LoginResult:
    """Start a session. At most 5 attempts a minute; the 6th is refused with 429 even with the right password."""
    sessions = get_sessions(request)
    wait_s = sessions.register_attempt(request.client.host if request.client else "unknown")
    if wait_s is not None:
        raise ApiError(429, "not_signed_in", TOO_MANY_ATTEMPTS, headers={"Retry-After": str(max(1, math.ceil(wait_s)))})
    if not password_is_set(repos):
        raise ApiError(401, "not_signed_in", NO_PASSWORD)
    if not verify_password(repos, body.password):
        raise ApiError(401, "not_signed_in", WRONG_PASSWORD)
    sessions.end(request.cookies.get(SESSION_COOKIE))
    set_session_cookie(response, sessions.create().id)
    return LoginResult()


@router.post("/logout", status_code=204, response_class=Response)
def logout(request: Request, session: SessionDep) -> Response:
    get_sessions(request).end(session.id)
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/session", response_model=SessionInfo)
def session_info(session: SessionDep) -> SessionInfo:
    return SessionInfo(expires_at=session.expires_at)
