"""Role based access control for natural language analysis."""

from __future__ import annotations


ROLE_LEVEL = {
    "guest": 0,
    "user": 1,
    "manager": 2,
    "admin": 3,
}

SENSITIVE_FIELDS = {"contact_name", "contact_phone", "contact_email", "手机号", "邮箱", "联系人"}


def normalize_role(role: str | None) -> str:
    value = (role or "guest").strip().lower()
    return value if value in ROLE_LEVEL else "guest"


def check_permission(role: str, intent: str, message: str = "") -> dict:
    role = normalize_role(role)
    level = ROLE_LEVEL[role]
    text = message or ""

    if any(field in text for field in SENSITIVE_FIELDS) and level < ROLE_LEVEL["admin"]:
        return {
            "allowed": False,
            "role": role,
            "reason": "当前角色无权查询联系人、手机号、邮箱等敏感字段。",
        }

    if intent in {"all_plan_detail", "audit_log", "email_report"} and level < ROLE_LEVEL["admin"]:
        return {
            "allowed": False,
            "role": role,
            "reason": "该问题需要 admin 权限。",
        }

    if intent in {"group_stats", "prediction"} and level < ROLE_LEVEL["manager"]:
        return {
            "allowed": False,
            "role": role,
            "reason": "该问题需要 manager 或 admin 权限。",
        }

    return {"allowed": True, "role": role, "reason": ""}


def scope_user_filter(role: str, user_id: str) -> tuple[str, dict]:
    role = normalize_role(role)
    if role in {"guest", "user"}:
        return " AND user_id = %(user_id)s", {"user_id": user_id}
    return "", {}
