"""Email sending tool with dry-run fallback."""

from __future__ import annotations

import os
import smtplib
import socket
from email.message import EmailMessage


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def send_email(to_email: str, subject: str, body: str) -> dict:
    real_emails = _env_first("SEND_REAL_EMAILS", default="true")
    host = _env_first("SMTP_HOST", "EMAIL_HOST")
    port = int(_env_first("SMTP_PORT", "EMAIL_PORT", default="587"))
    username = _env_first("SMTP_USERNAME", "EMAIL_USER")
    password = _env_first("SMTP_PASSWORD", "EMAIL_PASSWORD")
    from_email = _env_first("SMTP_FROM", "EMAIL_FROM", default=username or "lingtu@example.com")
    timeout = int(_env_first("SMTP_TIMEOUT", "EMAIL_TIMEOUT", default="20"))
    use_ssl = _truthy(_env_first("SMTP_SSL", "EMAIL_SSL", default="true" if port == 465 else "false"))

    if not _truthy(real_emails) or not host or not username or not password:
        return {
            "sent": False,
            "dry_run": True,
            "message": "SMTP 未配置，已生成邮件内容但未真实发送。",
            "to": to_email,
            "subject": subject,
            "body": body,
        }

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(host, port, timeout=timeout) as smtp:
            if not use_ssl:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
        return {"sent": True, "dry_run": False, "message": "邮件发送成功。", "to": to_email}
    except (socket.gaierror, ConnectionRefusedError, socket.timeout) as exc:
        return {
            "sent": False,
            "dry_run": False,
            "message": f"邮件服务器连接失败: {exc}",
            "to": to_email,
        }
    except smtplib.SMTPAuthenticationError:
        return {
            "sent": False,
            "dry_run": False,
            "message": "SMTP 认证失败，请检查用户名和密码。",
            "to": to_email,
        }
    except smtplib.SMTPException as exc:
        return {
            "sent": False,
            "dry_run": False,
            "message": f"邮件发送异常: {exc}",
            "to": to_email,
        }
