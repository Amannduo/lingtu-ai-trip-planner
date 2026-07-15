"""Server-side role based access control for travel analytics."""

from __future__ import annotations


ROLE_LEVEL = {"guest": 0, "user": 1, "manager": 2, "admin": 3}

SENSITIVE_FIELDS = {
    "contact_name", "contact_phone", "contact_email", "手机号", "电话号码",
    "邮箱", "联系人", "身份证", "密码", "密钥", "token",
}


def normalize_role(role: str | None) -> str:
    value = (role or "guest").strip().lower()
    return value if value in ROLE_LEVEL else "guest"


def role_scope(role: str) -> str:
    normalized = normalize_role(role)
    if normalized in {"guest", "user"}:
        return "personal"
    if normalized == "manager":
        return "global_aggregate"
    return "global"


def check_permission(role: str, intent: str, message: str = "") -> dict:
    normalized = normalize_role(role)
    text = (message or "").lower()
    decision = {"role": normalized, "scope": role_scope(normalized)}

    if intent == "email_report":
        if ROLE_LEVEL[normalized] < ROLE_LEVEL["user"]:
            return {**decision, "allowed": False, "reason": "发送个人分析报告需要先登录。"}
        return {**decision, "allowed": True, "reason": ""}
    if any(field.lower() in text for field in SENSITIVE_FIELDS):
        return {
            **decision,
            "allowed": False,
            "reason": "智能分析不允许查询联系人、手机号、邮箱、认证秘密等敏感字段。",
        }
    if ROLE_LEVEL[normalized] < ROLE_LEVEL["user"]:
        return {**decision, "allowed": False, "reason": "使用旅行数据分析需要先登录。"}
    if intent in {"audit_log", "all_user_detail"} and normalized != "admin":
        return {
            **decision,
            "allowed": False,
            "reason": "该分析涉及全局明细或审计数据，仅 admin 可以执行。",
        }
    if intent == "all_plan_detail" and normalized == "manager":
        return {
            **decision,
            "allowed": False,
            "reason": "manager 只能查看匿名汇总，不能查看逐条旅行计划明细。",
        }
    if intent == "traveler_type_distribution" and normalized == "user":
        return {
            **decision,
            "allowed": False,
            "reason": "普通用户只能分析本人的旅行画像，群体分布需要 manager 或 admin 权限。",
        }
    return {**decision, "allowed": True, "reason": ""}


def scope_user_filter(role: str, user_id: str, column: str = "user_id") -> tuple[str, dict]:
    """Apply personal scope to every user query, independent of wording."""
    normalized = normalize_role(role)
    if normalized in {"guest", "user"}:
        return f" AND {column} = :user_id", {"user_id": user_id}
    return "", {}
