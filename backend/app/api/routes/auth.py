"""Auth endpoints: login / logout / me. Session in an HttpOnly cookie, CSRF via double-submit."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import AppSettings, CurrentAuth, DbSession, client_ip
from app.core.config import Settings
from app.core.errors import AuthenticationError, RateLimitedError
from app.domains.identity import service
from app.domains.identity.schemas import LoginRequest, MeResponse, UserOut, WorkspaceOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _me(ctx: service.AuthContext) -> MeResponse:
    return MeResponse(
        user=UserOut(id=ctx.user.id, email=ctx.user.email, display_name=ctx.user.display_name),
        workspace=WorkspaceOut(id=ctx.workspace.id, name=ctx.workspace.name, slug=ctx.workspace.slug),
        role=ctx.role,
    )


def _set_cookies(response: Response, settings: Settings, issued: service.IssuedSession) -> None:
    max_age = settings.session_absolute_hours * 3600
    cookies = (
        (settings.session_cookie_name, issued.token, True),
        # Readable by the frontend JS so it can echo it in the CSRF header.
        (settings.csrf_cookie_name, issued.csrf_token, False),
    )
    for name, value, httponly in cookies:
        response.set_cookie(
            name, value, max_age=max_age, path="/", secure=settings.cookie_secure,
            httponly=httponly, samesite="lax",
        )


def _clear_cookies(response: Response, settings: Settings) -> None:
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/", secure=settings.cookie_secure, samesite="lax")


@router.post("/login", response_model=MeResponse)
async def login(body: LoginRequest, request: Request, response: Response, db: DbSession,
                settings: AppSettings) -> MeResponse:
    try:
        issued = await service.login(
            db, settings, email=body.email, password=body.password,
            ip=client_ip(request), user_agent=request.headers.get("user-agent"),
        )
    except (AuthenticationError, RateLimitedError):
        await db.commit()  # persist the failed-attempt audit record used for rate limiting
        raise
    _set_cookies(response, settings, issued)
    ctx = await service.resolve_session(db, settings, issued.token)
    return _me(ctx)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(ctx: CurrentAuth, request: Request, response: Response, db: DbSession,
                 settings: AppSettings) -> Response:
    await service.logout(db, ctx, ip=client_ip(request))
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_cookies(response, settings)
    return response


@router.get("/me", response_model=MeResponse)
async def me(ctx: CurrentAuth) -> MeResponse:
    return _me(ctx)
