from __future__ import annotations

from datetime import date, timedelta

import httpx

from app.models.schemas import WeatherInfo
from app.services.amap_service import AmapService


def test_open_meteo_completion_runs_after_amap_weather_timeout() -> None:
    service = AmapService.__new__(AmapService)
    service._weather_cache = {}
    service._cache_get = lambda *_args, **_kwargs: None
    service._cache_set = lambda *_args, **_kwargs: None
    service._get_amap_weather = lambda _city: (_ for _ in ()).throw(
        httpx.TimeoutException("timeout")
    )
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return [
            WeatherInfo(
                date="2030-01-01",
                day_weather="晴",
                night_weather="多云",
                day_temp=28,
                night_temp=18,
                wind_direction="东风",
                wind_power="1-2级",
            )
        ]

    service._complete_weather_with_open_meteo = complete

    result = service.get_weather("北京", "2030-01-01", "2030-01-01")

    assert len(calls) == 1
    assert calls[0]["weather_info"] == []
    assert [item.date for item in result] == ["2030-01-01"]

def test_long_trip_open_meteo_request_is_clamped_to_sixteen_days() -> None:
    captured = {}

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            start = captured["params"]["start_date"]
            return {
                "daily": {
                    "time": [start],
                    "weather_code": [0],
                    "temperature_2m_max": [28],
                    "temperature_2m_min": [18],
                    "wind_speed_10m_max": [8],
                    "wind_direction_10m_dominant": [90],
                }
            }

    class Client:
        @staticmethod
        def get(_url, *, params, timeout):
            captured["params"] = params
            captured["timeout"] = timeout
            return Response()

    service = AmapService.__new__(AmapService)
    service._client = Client()
    service._geocode_location = lambda *_args, **_kwargs: "116.397,39.918"

    start = date.today()
    end = start + timedelta(days=30)
    result = service._complete_weather_with_open_meteo(
        city="北京",
        weather_info=[],
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )

    assert captured["params"]["start_date"] == start.isoformat()
    assert captured["params"]["end_date"] == (start + timedelta(days=15)).isoformat()
    assert [item.date for item in result] == [start.isoformat()]

def test_open_meteo_geocoding_is_independent_and_amap_key_is_not_logged(capsys) -> None:
    requested_date = date.today().isoformat()
    sentinel_key = "sentinel-amap-key-must-not-leak"

    class Response:
        def __init__(self, data):
            self._data = data

        @staticmethod
        def raise_for_status():
            return None

        def json(self):
            return self._data

    class Client:
        @staticmethod
        def get(url, *, params, timeout):
            if "geocoding-api.open-meteo.com" in url:
                return Response(
                    {
                        "results": [
                            {
                                "name": "北京",
                                "country_code": "CN",
                                "longitude": 116.4074,
                                "latitude": 39.9042,
                            }
                        ]
                    }
                )
            return Response(
                {
                    "daily": {
                        "time": [requested_date],
                        "weather_code": [0],
                        "temperature_2m_max": [30],
                        "temperature_2m_min": [20],
                        "wind_speed_10m_max": [8],
                        "wind_direction_10m_dominant": [90],
                    }
                }
            )

    def failing_amap_geocode(*_args, **_kwargs):
        request = httpx.Request(
            "GET",
            f"https://restapi.amap.com/v3/geocode/geo?key={sentinel_key}",
        )
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError(
            "rate limited",
            request=request,
            response=response,
        )

    service = AmapService.__new__(AmapService)
    service._client = Client()
    service._geocode_location = failing_amap_geocode

    result = service._complete_weather_with_open_meteo(
        city="北京",
        weather_info=[],
        start_date=requested_date,
        end_date=requested_date,
    )
    output = capsys.readouterr().out

    assert [item.date for item in result] == [requested_date]
    assert result[0].day_weather == "晴"
    assert sentinel_key not in output
    assert "HTTPStatusError" in output


def test_failed_weather_lookup_uses_short_negative_cache() -> None:
    service = AmapService.__new__(AmapService)
    service._weather_cache = {}
    service._cache_get = lambda *_args, **_kwargs: None
    service._get_amap_weather = lambda _city: []
    service._complete_weather_with_open_meteo = lambda **_kwargs: []
    captured = {}

    def cache_set(_cache, _key, value, ttl_seconds=None):
        captured["value"] = value
        captured["ttl_seconds"] = ttl_seconds

    service._cache_set = cache_set

    result = service.get_weather("北京", "2030-01-01", "2030-01-01")

    assert result == []
    assert captured["value"] == []
    assert captured["ttl_seconds"] == service._NEGATIVE_CACHE_TTL_SECONDS
    assert captured["ttl_seconds"] < service._CACHE_TTL_SECONDS

