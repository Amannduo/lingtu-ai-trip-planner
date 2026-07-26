"""Delivery security: header injection, retries, quota isolation, log hygiene."""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.services.database_service import execute
from app.services.email_quota_service import consume_email_quota
from app.tools import send_email_tool
from app.tools.send_email_tool import send_email


def _enable_fake_smtp(monkeypatch, smtp_cls, *, retries: str = "1") -> None:
    monkeypatch.setattr(get_settings(), "email_quota_enabled", False)
    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-smtp-secret-never-log")
    monkeypatch.setenv("SMTP_FROM", "sender@example.test")
    monkeypatch.setenv("SMTP_SSL", "true")
    monkeypatch.setenv("SMTP_MAX_RETRIES", retries)
    monkeypatch.setenv("SMTP_RETRY_DELAY_SECONDS", "0")
    monkeypatch.setattr(send_email_tool.smtplib, "SMTP_SSL", smtp_cls)


def test_dry_run_when_smtp_disabled(monkeypatch) -> None:
    monkeypatch.setenv("SEND_REAL_EMAILS", "false")
    result = send_email("traveler@example.com", "subject", "body text")
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert result["blocked"] is False
    assert result["to"] == "traveler@example.com"


def test_recipient_crlf_injection_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-smtp-secret-never-log")

    result = send_email(
        "victim@example.com\r\nBcc: attacker@evil.test",
        "hello",
        "body",
    )
    assert result["sent"] is False
    assert result["dry_run"] is False
    assert "非法" in result["message"] or "无效" in result["message"]


def test_subject_crlf_injection_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-smtp-secret-never-log")

    result = send_email(
        "traveler@example.com",
        "hello\r\nBcc: attacker@evil.test",
        "body",
    )
    assert result["sent"] is False
    assert "主题" in result["message"]


def test_from_header_injection_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-smtp-secret-never-log")
    monkeypatch.setenv("SMTP_FROM", "sender@example.test\nBcc: evil@x.test")

    result = send_email("traveler@example.com", "hello", "body")
    assert result["sent"] is False
    assert "发件人" in result["message"]


def test_auth_failure_is_not_retried(monkeypatch) -> None:
    calls = {"login": 0}

    class RejectAuth:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, *_a, **_k):
            calls["login"] += 1
            raise smtplib.SMTPAuthenticationError(535, b"nope")

        def send_message(self, *_a, **_k):
            raise AssertionError("must not send")

    _enable_fake_smtp(monkeypatch, RejectAuth, retries="2")
    result = send_email("traveler@example.com", "t", "b")
    assert result["sent"] is False
    assert "认证失败" in result["message"]
    assert "unit-test-smtp-secret-never-log" not in result["message"]
    assert calls["login"] == 1


def test_transient_disconnect_is_retried_then_succeeds(monkeypatch) -> None:
    state = {"n": 0}

    class FlakyThenOk:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, *_a, **_k):
            return None

        def send_message(self, *_a, **_k):
            state["n"] += 1
            if state["n"] == 1:
                raise smtplib.SMTPServerDisconnected("temporary")
            return None

    _enable_fake_smtp(monkeypatch, FlakyThenOk, retries="1")
    result = send_email("traveler@example.com", "t", "b")
    assert result["sent"] is True
    assert state["n"] == 2


def test_transient_retry_hard_cap(monkeypatch) -> None:
    state = {"n": 0}

    class AlwaysDisconnect:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, *_a, **_k):
            return None

        def send_message(self, *_a, **_k):
            state["n"] += 1
            raise smtplib.SMTPServerDisconnected("temporary")

    # Request more retries than the hard cap allows.
    _enable_fake_smtp(monkeypatch, AlwaysDisconnect, retries="99")
    result = send_email("traveler@example.com", "t", "b")
    assert result["sent"] is False
    # first attempt + max 2 retries = 3
    assert state["n"] == 3


def test_domain_case_is_normalised_local_part_is_not(monkeypatch) -> None:
    captured: list[str] = []

    class CaptureSMTP:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, *_a, **_k):
            return None

        def send_message(self, message):
            captured.append(str(message["To"]))

    _enable_fake_smtp(monkeypatch, CaptureSMTP)
    result = send_email("User+Tag@Example.COM", "t", "b")
    assert result["sent"] is True
    assert captured == ["User+Tag@example.com"]


def test_quota_unavailable_blocks_without_exception_text(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-smtp-secret-never-log")
    monkeypatch.setenv("SMTP_FROM", "sender@example.test")

    def boom(*_a, **_k):
        raise RuntimeError("secret-db-url://password@host/db")

    monkeypatch.setattr(send_email_tool, "consume_email_quota", boom)
    with caplog.at_level(logging.WARNING):
        result = send_email(
            "traveler@example.com",
            "t",
            "b",
            user_id="u_test",
            client_ip="127.0.0.1",
        )
    assert result["sent"] is False
    assert result["blocked"] is True
    assert "secret-db-url" not in result["message"]
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "secret-db-url" not in joined
    assert "RuntimeError" in joined


def test_quota_window_boundary_allows_after_period(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_quota_enabled", True)
    monkeypatch.setattr(settings, "email_user_daily_limit", 1)
    monkeypatch.setattr(settings, "email_ip_hourly_limit", 100)
    monkeypatch.setattr(
        settings,
        "auth_secret_key",
        "test-email-quota-secret-key-with-at-least-32-characters",
    )
    execute("DELETE FROM email_send_quotas")
    try:
        now = datetime(2026, 7, 25, 23, 59, 0, tzinfo=timezone.utc)
        first = consume_email_quota("u_boundary", "203.0.113.10", now=now)
        assert first["allowed"] is True
        blocked = consume_email_quota("u_boundary", "203.0.113.10", now=now)
        assert blocked["allowed"] is False
        assert blocked["scope"] == "user"
        assert blocked["retry_after_seconds"] >= 1

        next_day = now.replace(hour=0, minute=0, second=0) + timedelta(days=1)
        allowed = consume_email_quota(
            "u_boundary",
            "203.0.113.10",
            now=next_day,
        )
        assert allowed["allowed"] is True
    finally:
        execute("DELETE FROM email_send_quotas")


def test_failed_smtp_still_consumes_quota(monkeypatch) -> None:
    """Attempt budget is pre-consumed so failures cannot spam SMTP forever."""
    settings = get_settings()
    monkeypatch.setattr(settings, "email_quota_enabled", True)
    monkeypatch.setattr(settings, "email_user_daily_limit", 1)
    monkeypatch.setattr(settings, "email_ip_hourly_limit", 100)
    monkeypatch.setattr(
        settings,
        "auth_secret_key",
        "test-email-quota-secret-key-with-at-least-32-characters",
    )

    class RejectAuth:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, *_a, **_k):
            raise smtplib.SMTPAuthenticationError(535, b"nope")

        def send_message(self, *_a, **_k):
            raise AssertionError("unreachable")

    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-smtp-secret-never-log")
    monkeypatch.setenv("SMTP_FROM", "sender@example.test")
    monkeypatch.setenv("SMTP_SSL", "true")
    monkeypatch.setenv("SMTP_MAX_RETRIES", "0")
    monkeypatch.setattr(send_email_tool.smtplib, "SMTP_SSL", RejectAuth)

    execute("DELETE FROM email_send_quotas")
    try:
        first = send_email(
            "a@example.com",
            "t",
            "b",
            user_id="u_fail_quota",
            client_ip="198.51.100.9",
        )
        assert first["sent"] is False
        second = send_email(
            "b@example.com",
            "t",
            "b",
            user_id="u_fail_quota",
            client_ip="198.51.100.9",
        )
        assert second["sent"] is False
        assert second["blocked"] is True
    finally:
        execute("DELETE FROM email_send_quotas")


def test_client_cannot_control_smtp_host_via_arguments(monkeypatch) -> None:
    """send_email has no host parameter; host always comes from server env."""
    import inspect

    sig = inspect.signature(send_email)
    assert "host" not in sig.parameters
    assert "password" not in sig.parameters
    assert "smtp" not in sig.parameters
