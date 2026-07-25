"""Semantic hard-block HTTP wiring — depends on trip routes (later commit)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes.trip import _validate_generation_request
from app.config import get_settings
from app.models.schemas import TripRequest
from app.services.semantic_contract_service import (
    USER_CONTRACT_ACK_MARKER,
    collect_semantic_hard_block_issues,
)


def test_hard_block_requires_ack_http_422() -> None:
    request = TripRequest(
        origin_city="宝鸡扶风",
        city="眉县",
        destination_source="manual",
        start_date="2030-08-01",
        end_date="2030-08-02",
        travel_days=2,
        travelers=1,
        budget=3000,
        transportation="自驾",
        accommodation="经济型酒店",
        preferences=["自然风光"],
        free_text_input="周末跟父母去避暑，不想太累",
    )
    with pytest.raises(HTTPException) as exc:
        _validate_generation_request(request)
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("issues")

    acked = request.model_copy(update={"semantic_risks_acknowledged": True})
    _validate_generation_request(acked)

    marked = request.model_copy(
        update={
            "free_text_input": f"{request.free_text_input} {USER_CONTRACT_ACK_MARKER}"
        }
    )
    assert collect_semantic_hard_block_issues(marked) == []
    _validate_generation_request(marked)


def test_hard_block_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        get_settings(),
        "semantic_contract_hard_block_enabled",
        False,
    )
    request = TripRequest(
        origin_city="宝鸡扶风",
        city="眉县",
        destination_source="manual",
        start_date="2030-08-01",
        end_date="2030-08-02",
        travel_days=2,
        travelers=1,
        budget=3000,
        transportation="自驾",
        accommodation="经济型酒店",
        preferences=["自然风光"],
        free_text_input="周末跟父母去避暑，不想太累",
    )
    _validate_generation_request(request)
