"""Map / POI HTTP input bounds, provider error mapping, and log hygiene."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.main import app
from app.models.schemas import Location
from app.services import amap_service as amap_module


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_amap(monkeypatch):
    service = MagicMock()
    service.settings = SimpleNamespace(amap_api_key="test-amap-key-secret-never-leak")
    service.search_poi.return_value = []
    service.get_weather.return_value = []
    service.plan_route.return_value = None
    service.get_poi_detail.return_value = {}
    service.search_poi_around.return_value = []
    monkeypatch.setattr(amap_module, "get_amap_service", lambda: service)
    monkeypatch.setattr("app.api.routes.map.get_amap_service", lambda: service)
    monkeypatch.setattr("app.api.routes.poi.get_amap_service", lambda: service)
    return service


def test_map_poi_search_accepts_valid_input(client, mock_amap) -> None:
    response = client.get("/api/map/poi", params={"keywords": "故宫", "city": "北京"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    mock_amap.search_poi.assert_called_once()
    args = mock_amap.search_poi.call_args[0]
    assert args[0] == "故宫"
    assert args[1] == "北京"


def test_empty_keywords_and_city_rejected(client, mock_amap) -> None:
    empty_kw = client.get("/api/map/poi", params={"keywords": "   ", "city": "北京"})
    assert empty_kw.status_code in {400, 422}
    mock_amap.search_poi.assert_not_called()

    empty_city = client.get("/api/map/poi", params={"keywords": "公园", "city": "  "})
    assert empty_city.status_code in {400, 422}
    mock_amap.search_poi.assert_not_called()


def test_overlong_city_and_keywords_rejected(client, mock_amap) -> None:
    long_city = client.get(
        "/api/map/poi",
        params={"keywords": "公园", "city": "北" * 100},
    )
    assert long_city.status_code in {400, 422}
    long_kw = client.get(
        "/api/map/poi",
        params={"keywords": "景" * 200, "city": "北京"},
    )
    assert long_kw.status_code in {400, 422}
    mock_amap.search_poi.assert_not_called()


def test_control_characters_rejected(client, mock_amap) -> None:
    # Percent-encoded CR/LF so the framework delivers control bytes to the handler.
    response = client.get("/api/map/poi?keywords=%0d%0aInject&city=%E5%8C%97%E4%BA%AC")
    assert response.status_code in {400, 422}
    mock_amap.search_poi.assert_not_called()

    context = client.post(
        "/api/map/context",
        json={
            "city": "北京\x00",
            "locations": [{"longitude": 116.4, "latitude": 39.9}],
        },
    )
    assert context.status_code in {400, 422}
    mock_amap.search_poi_around.assert_not_called()


def test_illegal_route_type_rejected(client, mock_amap) -> None:
    response = client.post(
        "/api/map/route",
        json={
            "origin_address": "天安门",
            "destination_address": "故宫",
            "route_type": "teleport",
        },
    )
    assert response.status_code in {400, 422}
    mock_amap.plan_route.assert_not_called()


def test_location_rejects_nan_and_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Location(longitude=float("nan"), latitude=39.9)
    with pytest.raises(ValidationError):
        Location(longitude=float("inf"), latitude=39.9)
    with pytest.raises(ValidationError):
        Location(longitude=200, latitude=39.9)
    with pytest.raises(ValidationError):
        Location(longitude=116.4, latitude=100)


def test_map_context_rejects_invalid_coordinates(client, mock_amap) -> None:
    response = client.post(
        "/api/map/context",
        json={
            "city": "北京",
            "locations": [{"longitude": 200, "latitude": 39.9}],
            "limit": 16,
        },
    )
    assert response.status_code in {400, 422}
    mock_amap.search_poi_around.assert_not_called()


def test_map_context_rejects_empty_city(client, mock_amap) -> None:
    response = client.post(
        "/api/map/context",
        json={
            "city": "  ",
            "locations": [{"longitude": 116.4, "latitude": 39.9}],
        },
    )
    assert response.status_code in {400, 422}
    mock_amap.search_poi_around.assert_not_called()


def test_provider_exception_is_safe(client, mock_amap, caplog) -> None:
    mock_amap.search_poi.side_effect = RuntimeError(
        "secret-url https://restapi.amap.com/v3?key=test-amap-key-secret-never-leak"
    )
    with caplog.at_level(logging.INFO):
        response = client.get("/api/map/poi", params={"keywords": "公园", "city": "北京"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "test-amap-key-secret-never-leak" not in detail
    assert "restapi.amap.com" not in detail
    assert "RuntimeError" not in detail
    assert "traceback" not in detail.lower()
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "test-amap-key-secret-never-leak" not in joined


def test_route_empty_provider_result_is_not_fabricated_success(client, mock_amap) -> None:
    mock_amap.plan_route.return_value = None
    response = client.post(
        "/api/map/route",
        json={
            "origin_address": "天安门",
            "destination_address": "故宫",
            "route_type": "walking",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None


def test_poi_detail_rejects_bad_id(client, mock_amap) -> None:
    response = client.get("/api/poi/detail/../etc/passwd")
    assert response.status_code in {400, 404, 422}
    mock_amap.get_poi_detail.assert_not_called()


def test_poi_detail_accepts_safe_id(client, mock_amap) -> None:
    mock_amap.get_poi_detail.return_value = {"id": "B000A8UIN8", "name": "故宫"}
    response = client.get("/api/poi/detail/B000A8UIN8")
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_amap.get_poi_detail.assert_called_once_with("B000A8UIN8")


def test_poi_search_empty_keywords(client, mock_amap) -> None:
    response = client.get("/api/poi/search", params={"keywords": "", "city": "北京"})
    assert response.status_code in {400, 422}
    mock_amap.search_poi.assert_not_called()


def test_photo_name_control_chars(client, monkeypatch) -> None:
    mock_unsplash = MagicMock()
    monkeypatch.setattr(
        "app.api.routes.poi.get_unsplash_service",
        lambda: mock_unsplash,
    )
    response = client.get("/api/poi/photo", params={"name": "西湖\x00"})
    assert response.status_code in {400, 422}
    mock_unsplash.get_photo_url.assert_not_called()


def test_health_does_not_leak_key_value(client, mock_amap) -> None:
    response = client.get("/api/map/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["amap_key_configured"] is True
    assert "test-amap-key-secret-never-leak" not in str(body)


def test_weather_strips_and_validates_city(client, mock_amap) -> None:
    response = client.get("/api/map/weather", params={"city": "  西安  "})
    assert response.status_code == 200
    mock_amap.get_weather.assert_called_once_with("西安")


def test_map_and_poi_routes_are_sync_defs() -> None:
    """Sync provider clients must not run inside bare async def handlers."""
    from app.api.routes import map as map_routes
    from app.api.routes import poi as poi_routes
    import inspect

    for fn in (
        map_routes.search_poi,
        map_routes.get_weather,
        map_routes.plan_route,
        map_routes.get_map_context,
        map_routes.health_check,
        poi_routes.get_poi_detail,
        poi_routes.search_poi,
        poi_routes.get_attraction_photo,
    ):
        assert not inspect.iscoroutinefunction(fn)
