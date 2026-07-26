"""Backend-enforced registration and login routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from ...config import get_settings
from ...services.auth_service import (
    AuthError,
    AuthenticatedUser,
    authenticate_user,
    create_access_token,
    register_user,
    revoke_user_tokens,
    update_user_email,
)
from ..auth import get_current_user

router = APIRouter(prefix="/auth", tags=["用户认证"])


class AuthUserResponse(BaseModel):
    user_id: str
    username: str
    email: EmailStr | None = None
    role: str
    is_active: bool = True


class AuthResponse(BaseModel):
    success: bool = True
    user: AuthUserResponse


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: EmailStr | None = None
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["user", "manager", "admin"] = "user"
    invite_code: str = Field(default="", max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class UpdateEmailRequest(BaseModel):
    email: EmailStr | None = None


def _public_user(user: AuthenticatedUser) -> AuthUserResponse:
    return AuthUserResponse(**user.as_dict())


def _set_auth_cookie(response: Response, token: str, http_request: Request) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_access_token_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure or http_request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, response: Response, http_request: Request):
    try:
        user = register_user(
            username=request.username,
            password=request.password,
            role=request.role,
            invite_code=request.invite_code,
            email=str(request.email) if request.email else None,
        )
        _set_auth_cookie(response, create_access_token(user), http_request)
        return AuthResponse(user=_public_user(user))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, response: Response, http_request: Request):
    try:
        user = authenticate_user(request.username, request.password)
        _set_auth_cookie(response, create_access_token(user), http_request)
        return AuthResponse(user=_public_user(user))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.get("/me", response_model=AuthResponse)
def me(user: AuthenticatedUser = Depends(get_current_user)):
    return AuthResponse(user=_public_user(user))


@router.patch("/me", response_model=AuthResponse)
def update_me(
    request: UpdateEmailRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        updated = update_user_email(
            user.user_id,
            str(request.email) if request.email else None,
        )
        return AuthResponse(user=_public_user(updated))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/logout")
def logout(
    response: Response,
    http_request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    settings = get_settings()
    revoke_user_tokens(user.user_id)
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure or http_request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"success": True}