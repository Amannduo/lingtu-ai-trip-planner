"""Email sending tool with dry-run fallback."""

from __future__ import annotations

import os
import smtplib
import socket
from email.message import EmailMessage


def send_email(to_email: str, subject: str, body: str) -> dict:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("SMTP_FROM", username or "lingtu@example.com")
    timeout = int(os.getenv("SMTP_TIMEOUT", "20"))

    if not host or not username or not password:
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
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
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
