"""Authentication persistence, password hashing, and JWT issuance."""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import jwt
from email_validator import EmailNotValidError, validate_email
from pwdlib import PasswordHash
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from .database_service import fetch_one, get_db_connection
from .schema import init_db

ROLE_VALUES = {"user", "manager", "admin"}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{3,32}$")
_password_hash = PasswordHash.recommended()


class AuthError(ValueError):
    """Expected authentication or registration failure."""


class InvalidTokenError(AuthError):
    """JWT is missing, expired, or otherwise invalid."""


def _can_claim_legacy_plans(user_count: int, role: str) -> bool:
    """Only the bootstrap admin may claim pre-auth ``u_current`` plans.

    First registered user who is an admin (via invite) is the sole bootstrap
    principal trusted to absorb legacy anonymous data. Ordinary users and any
    later accounts must never claim unowned history.
    """
    return int(user_count or 0) == 0 and str(role or "").strip().lower() == "admin"


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    username: str
    email: str | None
    role: str
    is_active: bool = True
    token_version: int = 0

    def as_dict(self) -> dict[str, str | bool | None]:
        # token_version is intentionally omitted from public API payloads.
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
        }


def _row_to_user(row: Mapping[str, Any] | None) -> AuthenticatedUser | None:
    if not row:
        return None
    return AuthenticatedUser(
        user_id=str(row["user_id"]),
        username=str(row["username"]),
        email=str(row["email"]) if row.get("email") else None,
        role=str(row["role"]),
        is_active=bool(row["is_active"]),
        token_version=int(row["token_version"] if row.get("token_version") is not None else 0),
    )


def _validate_username(username: str) -> str:
    normalized = username.strip()
    if not _USERNAME_RE.fullmatch(normalized):
        raise AuthError("用户名需为 3-32 位中文、字母、数字、下划线、点或连字符")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise AuthError("密码至少需要 8 个字符")
    if len(password) > 128:
        raise AuthError("密码不能超过 128 个字符")
    if not any(character.isalpha() for character in password):
        raise AuthError("密码至少需要包含一个字母")
    if not any(character.isdigit() for character in password):
        raise AuthError("密码至少需要包含一个数字")


def _validate_email_address(email: str | None) -> str | None:
    value = (email or "").strip()
    if not value:
        return None
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise AuthError("请输入有效的邮箱地址") from exc


def _validate_role_invite(role: str, invite_code: str) -> None:
    if role == "user":
        return

    settings = get_settings()
    configured_code = (
        settings.auth_manager_invite_code
        if role == "manager"
        else settings.auth_admin_invite_code
    )
    if not configured_code:
        raise AuthError(f"当前未开放{role}角色自助注册")
    if not secrets.compare_digest(invite_code.strip(), configured_code):
        raise AuthError("角色授权码无效")


def register_user(
    username: str,
    password: str,
    role: str = "user",
    invite_code: str = "",
    email: str | None = None,
) -> AuthenticatedUser:
    init_db()
    normalized_username = _validate_username(username)
    normalized_email = _validate_email_address(email)
    normalized_role = role.strip().lower()
    if normalized_role not in ROLE_VALUES:
        raise AuthError("不支持的用户角色")
    _validate_password(password)
    _validate_role_invite(normalized_role, invite_code)

    user_id = f"u_{uuid.uuid4().hex[:12]}"
    password_digest = _password_hash.hash(password)
    migrated_legacy_plans = False

    try:
        with get_db_connection() as connection:
            existing = connection.execute(
                text(
                    "SELECT 1 FROM users "
                    "WHERE lower(username) = lower(:username)"
                ),
                {"username": normalized_username},
            ).first()
            if existing:
                raise AuthError("用户名已存在")
            if normalized_email:
                existing_email = connection.execute(
                    text("SELECT 1 FROM users WHERE lower(email) = lower(:email)"),
                    {"email": normalized_email},
                ).first()
                if existing_email:
                    raise AuthError("该邮箱已绑定其他账号")

            user_count = connection.execute(
                text("SELECT COUNT(*) FROM users")
            ).scalar_one()
            connection.execute(
                text(
                    """INSERT INTO users
                       (user_id, username, email, password_hash, role, is_active)
                       VALUES (:user_id, :username, :email, :password_hash, :role, 1)"""
                ),
                {
                    "user_id": user_id,
                    "username": normalized_username,
                    "email": normalized_email,
                    "password_hash": password_digest,
                    "role": normalized_role,
                },
            )

            # Upgrade path for installations that previously stored personal
            # plans under the legacy u_current identity.
            if _can_claim_legacy_plans(user_count, normalized_role):
                legacy_count = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM travel_plans "
                        "WHERE user_id = 'u_current'"
                    )
                ).scalar_one()
                if legacy_count:
                    connection.execute(
                        text(
                            """UPDATE travel_plans
                               SET user_id = :user_id, user_role = :role
                               WHERE user_id = 'u_current'"""
                        ),
                        {"user_id": user_id, "role": normalized_role},
                    )
                    connection.execute(
                        text(
                            "DELETE FROM user_profiles "
                            "WHERE user_id IN ('u_current', :user_id)"
                        ),
                        {"user_id": user_id},
                    )
                    migrated_legacy_plans = True
    except IntegrityError as exc:
        raise AuthError("用户名或邮箱已存在") from exc

    if migrated_legacy_plans:
        from .travel_plan_data_service import get_travel_plan_data_service

        get_travel_plan_data_service()._refresh_profile(user_id)

    return AuthenticatedUser(
        user_id=user_id,
        username=normalized_username,
        email=normalized_email,
        role=normalized_role,
        token_version=0,
    )


def authenticate_user(username: str, password: str) -> AuthenticatedUser:
    init_db()
    identifier = username.strip()
    with get_db_connection() as connection:
        row = connection.execute(
            text(
                """SELECT user_id, username, email, password_hash, role, is_active,
                          token_version
                   FROM users
                   WHERE lower(username) = lower(:identifier)
                      OR lower(email) = lower(:identifier)"""
            ),
            {"identifier": identifier},
        ).mappings().first()
        if not row or not bool(row["is_active"]):
            # Uniform message: do not enumerate username/email existence.
            raise AuthError("用户名或密码不正确")

        try:
            verified = _password_hash.verify(password, str(row["password_hash"]))
        except Exception:
            verified = False
        if not verified:
            raise AuthError("用户名或密码不正确")

        connection.execute(
            text(
                "UPDATE users SET last_login_at = CURRENT_TIMESTAMP "
                "WHERE user_id = :user_id"
            ),
            {"user_id": row["user_id"]},
        )
        return _row_to_user(row)  # type: ignore[return-value]


def get_user_by_id(user_id: str) -> AuthenticatedUser | None:
    init_db()
    row = fetch_one(
        """SELECT user_id, username, email, role, is_active, token_version
           FROM users WHERE user_id = :user_id""",
        {"user_id": user_id},
    )
    user = _row_to_user(row)
    return user if user and user.is_active else None


def update_user_email(user_id: str, email: str | None) -> AuthenticatedUser:
    init_db()
    normalized_email = _validate_email_address(email)
    try:
        with get_db_connection() as connection:
            if normalized_email:
                existing = connection.execute(
                    text(
                        "SELECT 1 FROM users "
                        "WHERE lower(email) = lower(:email) AND user_id <> :user_id"
                    ),
                    {"email": normalized_email, "user_id": user_id},
                ).first()
                if existing:
                    raise AuthError("该邮箱已绑定其他账号")
            result = connection.execute(
                text(
                    "UPDATE users SET email = :email, updated_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = :user_id"
                ),
                {"email": normalized_email, "user_id": user_id},
            )
            if result.rowcount != 1:
                raise AuthError("用户不存在或已停用")
    except IntegrityError as exc:
        raise AuthError("该邮箱已绑定其他账号") from exc

    user = get_user_by_id(user_id)
    if not user:
        raise AuthError("用户不存在或已停用")
    return user


def create_access_token(user: AuthenticatedUser) -> str:
    settings = get_settings()
    if not settings.auth_secret_key:
        raise AuthError("服务端未配置 AUTH_SECRET_KEY")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.auth_access_token_minutes)
    payload = {
        "sub": user.user_id,
        "type": "access",
        "iat": now,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
        "ver": int(user.token_version),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm="HS256")


def user_from_access_token(token: str) -> AuthenticatedUser:
    settings = get_settings()
    if not settings.auth_secret_key:
        raise InvalidTokenError("认证服务尚未配置")

    try:
        payload = jwt.decode(
            token,
            settings.auth_secret_key,
            algorithms=["HS256"],
            options={"require": ["sub", "type", "iat", "exp", "jti", "ver"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("登录状态已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("登录状态无效，请重新登录") from exc

    if payload.get("type") != "access":
        raise InvalidTokenError("登录状态无效，请重新登录")
    user_id = str(payload.get("sub") or "")
    user = get_user_by_id(user_id)
    if not user:
        raise InvalidTokenError("登录状态无效，请重新登录")
    try:
        token_version = int(payload.get("ver"))
    except (TypeError, ValueError) as exc:
        raise InvalidTokenError("登录状态无效，请重新登录") from exc
    if token_version != user.token_version:
        # Revoked or superseded session (logout / password change / etc.).
        raise InvalidTokenError("登录状态无效，请重新登录")
    return user


def revoke_user_tokens(user_id: str) -> None:
    """Invalidate every access token issued with the current token version.

    This is a user-level revocation: all devices sharing the same account
    must re-authenticate after logout or other security events.
    """
    init_db()
    with get_db_connection() as connection:
        result = connection.execute(
            text(
                "UPDATE users SET token_version = token_version + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        if result.rowcount != 1:
            raise AuthError("用户不存在或已停用")
