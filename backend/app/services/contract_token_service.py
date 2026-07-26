"""Signed recommendation-contract token: stateless cross-worker handoff.

A token proves only that *this server* built the enclosed semantic
contract during a recommendation session.  It is NOT a form confirmation
and grants nothing by itself: on redemption the contract re-enters the
normal merge matrix, the TTL check and the 422 hard-block gate.

Security properties:

- signing key = dedicated ``RECOMMENDATION_TOKEN_SIGNING_KEY`` or a key
  derived from ``AUTH_SECRET_KEY`` via HKDF-SHA256 with domain separation
  (the master key is never used raw);
- canonical serialization (sorted keys, no whitespace) before signing;
- constant-time signature comparison;
- payload carries version / issued_at / expires_at / audience / subject;
- Base64 encodes, it does not encrypt: binding ``evidence`` (raw user
  text snippets) is stripped before issuing, and payload size and known
  field counts are capped;
- verification failures degrade silently to the no-token path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

from ..config import get_settings
from ..models.schemas import SemanticTripContract

logger = logging.getLogger(__name__)

TOKEN_VERSION = 1
TOKEN_AUDIENCE = "trip-plan-generation"
TOKEN_TTL_SECONDS = 30 * 60
MAX_TOKEN_CHARS = 16_384
MAX_PAYLOAD_BYTES = 8_192
MAX_KNOWN_CONTRACT_FIELDS = 24

_HKDF_SALT = b"lingtu-contract-token-salt-v1"
_HKDF_INFO = b"lingtu/recommendation-contract/v1"


def _derive_signing_key() -> Optional[bytes]:
    settings = get_settings()
    dedicated = str(
        getattr(settings, "recommendation_token_signing_key", "") or ""
    ).strip()
    if dedicated:
        if len(dedicated) < 32:
            logger.warning(
                "[contract-token] dedicated signing key too short; disabled"
            )
            return None
        return dedicated.encode("utf-8")
    master = str(getattr(settings, "auth_secret_key", "") or "").strip()
    if len(master) < 32:
        return None
    # HKDF-SHA256 (RFC 5869): extract with a fixed salt, expand with a
    # purpose-bound info string — domain-separated from every other use
    # of the master auth key.
    prk = hmac.new(_HKDF_SALT, master.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(prk, _HKDF_INFO + b"\x01", hashlib.sha256).digest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _minimal_contract_payload(contract: SemanticTripContract) -> dict[str, Any]:
    """Contract dump with raw-text evidence removed from every binding."""
    data = contract.model_dump(mode="json")
    for value in data.values():
        if isinstance(value, dict) and "evidence" in value:
            value["evidence"] = ""
    return data


def _known_field_count(contract_payload: dict[str, Any]) -> int:
    return sum(
        1
        for value in contract_payload.values()
        if isinstance(value, dict) and value.get("value") not in (None, "", [])
    )


def issue_contract_token(
    contract: Optional[SemanticTripContract],
    *,
    subject: str,
    now: Optional[float] = None,
) -> Optional[str]:
    """Issue a signed token, or None when disabled/oversized (fail soft)."""
    if contract is None:
        return None
    key = _derive_signing_key()
    if key is None:
        return None
    issued_at = int(now if now is not None else time.time())
    contract_payload = _minimal_contract_payload(contract)
    if _known_field_count(contract_payload) > MAX_KNOWN_CONTRACT_FIELDS:
        logger.warning("[contract-token] too many known fields; not issued")
        return None
    payload = {
        "v": TOKEN_VERSION,
        "iat": issued_at,
        "exp": issued_at + TOKEN_TTL_SECONDS,
        "aud": TOKEN_AUDIENCE,
        "sub": str(subject or "anon"),
        "contract": contract_payload,
    }
    body = _canonical(payload)
    if len(body) > MAX_PAYLOAD_BYTES:
        logger.warning("[contract-token] payload too large; not issued")
        return None
    signature = hmac.new(key, body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(signature)}"


def verify_contract_token(
    token: str,
    *,
    subject: str,
    now: Optional[float] = None,
) -> Optional[SemanticTripContract]:
    """Verify and decode a token; any defect returns None (no-token path).

    ``subject`` must match exactly — an authenticated user can never
    redeem another user's (or an anonymous) token and vice versa.
    """
    key = _derive_signing_key()
    raw = str(token or "").strip()
    if key is None or not raw or len(raw) > MAX_TOKEN_CHARS:
        return None
    parts = raw.split(".")
    if len(parts) != 2:
        return None
    try:
        body = _b64decode(parts[0])
        signature = _b64decode(parts[1])
    except (ValueError, TypeError):
        return None
    expected = hmac.new(key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != TOKEN_VERSION:
        return None
    if payload.get("aud") != TOKEN_AUDIENCE:
        return None
    if str(payload.get("sub") or "") != str(subject or "anon"):
        return None
    moment = now if now is not None else time.time()
    try:
        if float(payload.get("exp", 0)) < moment:
            return None
        if float(payload.get("iat", 0)) > moment + 60:  # clock-skew guard
            return None
    except (TypeError, ValueError):
        return None
    try:
        return SemanticTripContract.model_validate(payload.get("contract"))
    except (TypeError, ValueError):
        return None
