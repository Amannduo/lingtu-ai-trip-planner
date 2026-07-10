"""Sensitive word and risky request filtering."""

from __future__ import annotations

import re


SENSITIVE_KEYWORDS = [
    "身份证",
    "手机号",
    "电话号码",
    "邮箱",
    "联系人",
    "contact_phone",
    "contact_email",
    "contact_name",
    "password",
    "密钥",
    "token",
]

SQL_DANGER_PATTERN = re.compile(
    r"\b(drop|delete|update|insert|alter|truncate|create|grant|revoke|copy|execute)\b",
    re.IGNORECASE,
)


def check_sensitive_text(text: str) -> dict:
    normalized = text or ""
    hits = [keyword for keyword in SENSITIVE_KEYWORDS if keyword.lower() in normalized.lower()]
    has_dangerous_sql = bool(SQL_DANGER_PATTERN.search(normalized))
    return {
        "passed": not hits and not has_dangerous_sql,
        "hits": hits,
        "has_dangerous_sql": has_dangerous_sql,
        "message": "请求包含敏感字段或危险 SQL 操作" if hits or has_dangerous_sql else "",
    }


def mask_sensitive_row(row: dict) -> dict:
    masked = dict(row)
    if masked.get("contact_phone"):
        value = str(masked["contact_phone"])
        masked["contact_phone"] = value[:3] + "****" + value[-4:]
    if masked.get("contact_email"):
        value = str(masked["contact_email"])
        name, _, domain = value.partition("@")
        masked["contact_email"] = (name[:2] + "***@" + domain) if domain else "***"
    if masked.get("contact_name"):
        value = str(masked["contact_name"])
        masked["contact_name"] = value[:1] + "**"
    return masked
