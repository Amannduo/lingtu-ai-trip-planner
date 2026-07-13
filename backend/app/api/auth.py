"""FastAPI authentication dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import get_settings
from ..services.auth_service import (
    AuthenticatedUser,
    InvalidTokenError,
    user_from_access_token,
)

_bearer = HTTPBearer(auto_error=False)


def get_optional_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> AuthenticatedUser | None:
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)
    if not token and credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if not token:
        return None

    try:
        return user_from_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user(
    user: AuthenticatedUser | None = Depends(get_optional_current_user),
) -> AuthenticatedUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user