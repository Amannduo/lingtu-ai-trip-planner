"""Email sending tool with dry-run fallback and durable abuse limits."""

from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import parseaddr

from ..services.email_quota_service import consume_email_quota

logger = logging.getLogger(__name__)

# Hard caps: never retry more than this many *additional* attempts after the first.
_MAX_TRANSIENT_RETRIES = 2
_MAX_RETRY_DELAY_SECONDS = 2.0
_HEADER_INJECTION_RE = re.compile(r"[\r\n\x00]")
_EMAIL_SHAPE_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Transient classes that may be retried once or twice; permanent errors must not.
_TRANSIENT_SMTP_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPHeloError,
    TimeoutError,
    ConnectionError,
    OSError,
    ssl.SSLError,
)


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _reject_header_injection(value: str, field: str) -> str | None:
    """Return an error message when a header field contains control characters."""
    if _HEADER_INJECTION_RE.search(value):
        return f"邮件{field}包含非法控制字符，已拒绝发送。"
    return None


def _normalise_recipient(raw: str) -> tuple[str | None, str | None]:
    """Validate recipient shape; return (address, error_message)."""
    candidate = (raw or "").strip()
    if not candidate:
        return None, "收件地址无效，无法发送。"
    if _HEADER_INJECTION_RE.search(candidate):
        return None, "邮件收件地址包含非法控制字符，已拒绝发送。"
    # parseaddr tolerates display-name forms; keep only the bare address.
    _name, address = parseaddr(candidate)
    address = (address or candidate).strip()
    if not address or _HEADER_INJECTION_RE.search(address):
        return None, "收件地址无效，无法发送。"
    if len(address) > 254 or not _EMAIL_SHAPE_RE.fullmatch(address):
        return None, "收件地址无效，无法发送。"
    # Domain case-fold only; local-part left intact (a+b vs a stay distinct).
    local, _, domain = address.rpartition("@")
    normalised = f"{local}@{domain.lower()}"
    return normalised, None


def _retry_settings() -> tuple[int, float]:
    try:
        retries = int(_env_first("SMTP_MAX_RETRIES", default="1"))
    except ValueError:
        retries = 1
    retries = max(0, min(retries, _MAX_TRANSIENT_RETRIES))
    try:
        delay = float(_env_first("SMTP_RETRY_DELAY_SECONDS", default="0.5"))
    except ValueError:
        delay = 0.5
    delay = max(0.0, min(delay, _MAX_RETRY_DELAY_SECONDS))
    return retries, delay


def _deliver_via_smtp(
    *,
    host: str,
    port: int,
    timeout: float,
    use_ssl: bool,
    username: str,
    password: str,
    msg: EmailMessage,
) -> None:
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


def send_email(
    to_email: str,
    subject: str,
    body: str,
    *,
    user_id: str | None = None,
    client_ip: str | None = None,
    email_type: str | None = None,
) -> dict:
    """Send one email with quota pre-consume and best-effort SMTP delivery.

    Quota is consumed *before* the SMTP call. Failed attempts still occupy the
    attempt budget so a broken SMTP path cannot be used for unbounded retries.
    Permanent SMTP errors are not retried; transient network/SMTP disconnects
    may be retried a small, hard-capped number of times.
    """
    real_emails = _env_first("SEND_REAL_EMAILS", default="false")
    host = _env_first("SMTP_HOST", "EMAIL_HOST")
    username = _env_first("SMTP_USERNAME", "EMAIL_USER")
    password = _env_first("SMTP_PASSWORD", "EMAIL_PASSWORD")
    from_email = _env_first(
        "SMTP_FROM",
        "EMAIL_FROM",
        default=username or "lingtu@example.com",
    )

    recipient, recipient_error = _normalise_recipient(to_email)
    if recipient_error or not recipient:
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": recipient_error or "收件地址无效，无法发送。",
            "to": (to_email or "").strip()[:254],
        }

    subject_text = str(subject or "")
    for field, value in (
        ("主题", subject_text),
        ("发件人", from_email),
    ):
        injection = _reject_header_injection(value, field)
        if injection:
            return {
                "sent": False,
                "dry_run": False,
                "blocked": False,
                "message": injection,
                "to": recipient,
            }

    if not _truthy(real_emails) or not host or not username or not password:
        # Dry-run still returns content for local preview; never contacts SMTP.
        return {
            "sent": False,
            "dry_run": True,
            "blocked": False,
            "message": "SMTP 未配置，已生成邮件内容但未真实发送。",
            "to": recipient,
            "subject": subject_text,
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
            "message": "SMTP 配置无效，请检查端口和超时设置。",
            "to": recipient,
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
        msg["To"] = recipient
        msg["Subject"] = subject_text
        msg.set_content(str(body or ""))
    except (TypeError, ValueError):
        return {
            "sent": False,
            "dry_run": False,
            "blocked": False,
            "message": "邮件地址或内容无效，无法发送。",
            "to": recipient,
        }

    # email_type is accepted for call-site clarity / future isolation but the
    # durable abuse budget remains per authenticated user (daily) and peer IP
    # (hourly). Shared budgets prevent any single channel from escaping limits.
    _ = (email_type or "").strip()[:64]

    try:
        quota = consume_email_quota(user_id, client_ip)
    except Exception as exc:
        # Do not log exception text (may include connection strings / secrets).
        logger.warning("Email quota evaluation failed: %s", type(exc).__name__)
        quota = {
            "allowed": False,
            "message": "邮件限额服务暂时不可用，已阻止真实投递。",
            "scope": "unavailable",
            "retry_after_seconds": 0,
        }
    if not quota.get("allowed"):
        return {
            "sent": False,
            "dry_run": False,
            "blocked": True,
            "message": str(quota.get("message") or "邮件发送已被限额阻止。"),
            "to": recipient,
            "quota_scope": quota.get("scope"),
            "retry_after_seconds": int(
                quota.get("retry_after_seconds") or 0
            ),
        }

    max_retries, retry_delay = _retry_settings()
    attempts = max_retries + 1
    last_error_name = "SMTPException"

    for attempt in range(attempts):
        try:
            _deliver_via_smtp(
                host=host,
                port=port,
                timeout=timeout,
                use_ssl=use_ssl,
                username=username,
                password=password,
                msg=msg,
            )
            return {
                "sent": True,
                "dry_run": False,
                "blocked": False,
                "message": "邮件发送成功。",
                "to": recipient,
            }
        except smtplib.SMTPAuthenticationError:
            # Permanent: do not retry and never echo credentials.
            return {
                "sent": False,
                "dry_run": False,
                "blocked": False,
                "message": "SMTP 认证失败，请检查用户名和密码。",
                "to": recipient,
            }
        except smtplib.SMTPRecipientsRefused:
            return {
                "sent": False,
                "dry_run": False,
                "blocked": False,
                "message": "收件地址被邮件服务器拒绝。",
                "to": recipient,
            }
        except smtplib.SMTPException as exc:
            last_error_name = type(exc).__name__
            # Only a narrow set of SMTP failures are treated as transient.
            if not isinstance(exc, _TRANSIENT_SMTP_ERRORS):
                logger.warning("SMTP delivery failed: %s", last_error_name)
                return {
                    "sent": False,
                    "dry_run": False,
                    "blocked": False,
                    "message": f"邮件发送异常（{last_error_name}）。",
                    "to": recipient,
                }
            if attempt >= max_retries:
                logger.warning(
                    "SMTP delivery failed after retries: %s",
                    last_error_name,
                )
                return {
                    "sent": False,
                    "dry_run": False,
                    "blocked": False,
                    "message": f"邮件发送异常（{last_error_name}）。",
                    "to": recipient,
                }
            if retry_delay > 0:
                time.sleep(retry_delay)
        except _TRANSIENT_SMTP_ERRORS as exc:
            last_error_name = type(exc).__name__
            if attempt >= max_retries:
                logger.warning(
                    "SMTP connection failed after retries: %s",
                    last_error_name,
                )
                return {
                    "sent": False,
                    "dry_run": False,
                    "blocked": False,
                    "message": f"邮件服务器连接失败（{last_error_name}）。",
                    "to": recipient,
                }
            if retry_delay > 0:
                time.sleep(retry_delay)
        except Exception as exc:
            logger.warning(
                "Unexpected email delivery failure: %s",
                type(exc).__name__,
            )
            return {
                "sent": False,
                "dry_run": False,
                "blocked": False,
                "message": f"邮件发送失败（{type(exc).__name__}）。",
                "to": recipient,
            }

    return {
        "sent": False,
        "dry_run": False,
        "blocked": False,
        "message": f"邮件发送异常（{last_error_name}）。",
        "to": recipient,
    }
