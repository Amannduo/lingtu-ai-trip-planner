"""Email sending tool with dry-run fallback and durable abuse limits."""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from ..services.email_quota_service import consume_email_quota

logger = logging.getLogger(__name__)


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def send_email(
    to_email: str,
    subject: str,
    body: str,
    *,
    user_id: str | None = None,
    client_ip: str | None = None,
) -> dict:
    real_emails = _env_first("SEND_REAL_EMAILS", default="false")
    host = _env_first("SMTP_HOST", "EMAIL_HOST")
    username = _env_first("SMTP_USERNAME", "EMAIL_USER")
    password = _env_first("SMTP_PASSWORD", "EMAIL_PASSWORD")
    from_email = _env_first(
        "SMTP_FROM",
        "EMAIL_FROM",
        default=username or "lingtu@example.com",
    )

    if not _truthy(real_emails) or not host or not username or not password:
        return {
            "sent": False,
            "dry_run": True,
            "blocked": False,
            "message": "\u0053\u004d\u0054\u0050 \u672a\u914d\u7f6e\uff0c\u5df2\u751f\u6210\u90ae\u4ef6\u5185\u5bb9\u4f46\u672a\u771f\u5b9e\u53d1\u9001\u3002",
            "to": to_email,
            "subject": subject,
            "body": body,
        }

    try:
        port = int(_env_first("SMTP_PORT", "EMAIL_PORT", default="587"))
        timeout = float(
            _env_first("SMTP_TIMEOUT", "EMAIL_TIMEOUT", default="20")
        )
        if not 1 <= port <= 65535 or not 0 < timeout <= 120:
            raise ValueError
    except (TypeError, ValueError):
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": "\u0053\u004d\u0054\u0050 \u914d\u7f6e\u65e0\u6548\uff0c\u8bf7\u68c0\u67e5\u7aef\u53e3\u548c\u8d85\u65f6\u8bbe\u7f6e\u3002",
            "to": to_email,
        }

    use_ssl = _truthy(
        _env_first(
            "SMTP_SSL",
            "EMAIL_SSL",
            default="true" if port == 465 else "false",
        )
    )

    try:
        msg = EmailMessage()
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
    except (TypeError, ValueError):
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": "\u90ae\u4ef6\u5730\u5740\u6216\u5185\u5bb9\u65e0\u6548\uff0c\u65e0\u6cd5\u53d1\u9001\u3002",
            "to": to_email,
        }

    try:
        quota = consume_email_quota(user_id, client_ip)
    except Exception:
        logger.exception("Email quota evaluation failed")
        quota = {
            "allowed": False,
            "message": "\u90ae\u4ef6\u9650\u989d\u670d\u52a1\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u5df2\u963b\u6b62\u771f\u5b9e\u6295\u9012\u3002",
            "scope": "unavailable",
            "retry_after_seconds": 0,
        }
    if not quota.get("allowed"):
        return {
            "sent": False,
            "dry_run": False,
            "blocked": True,
            "message": str(quota.get("message") or "\u90ae\u4ef6\u53d1\u9001\u5df2\u88ab\u9650\u989d\u963b\u6b62\u3002"),
            "to": to_email,
            "quota_scope": quota.get("scope"),
            "retry_after_seconds": int(
                quota.get("retry_after_seconds") or 0
            ),
        }

    try:
        tls_context = ssl.create_default_context()
        if use_ssl:
            smtp_client = smtplib.SMTP_SSL(
                host,
                port,
                timeout=timeout,
                context=tls_context,
            )
        else:
            smtp_client = smtplib.SMTP(host, port, timeout=timeout)

        with smtp_client as smtp:
            if not use_ssl:
                smtp.starttls(context=tls_context)
            smtp.login(username, password)
            smtp.send_message(msg)
        return {
            "sent": True,
            "dry_run": False,
            "blocked": False,
            "message": "\u90ae\u4ef6\u53d1\u9001\u6210\u529f\u3002",
            "to": to_email,
        }
    except smtplib.SMTPAuthenticationError:
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": "\u0053\u004d\u0054\u0050 \u8ba4\u8bc1\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7528\u6237\u540d\u548c\u5bc6\u7801\u3002",
            "to": to_email,
        }
    except smtplib.SMTPException as exc:
        logger.warning("SMTP delivery failed: %s", type(exc).__name__)
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": f"\u90ae\u4ef6\u53d1\u9001\u5f02\u5e38\uff08{type(exc).__name__}\uff09\u3002",
            "to": to_email,
        }
    except (OSError, ssl.SSLError) as exc:
        logger.warning("SMTP connection failed: %s", type(exc).__name__)
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": f"\u90ae\u4ef6\u670d\u52a1\u5668\u8fde\u63a5\u5931\u8d25\uff08{type(exc).__name__}\uff09\u3002",
            "to": to_email,
        }
    except Exception as exc:
        logger.exception("Unexpected email delivery failure")
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": f"\u90ae\u4ef6\u53d1\u9001\u5931\u8d25\uff08{type(exc).__name__}\uff09\u3002",
            "to": to_email,
        }
