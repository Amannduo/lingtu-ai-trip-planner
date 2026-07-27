"""高德地图 Web Service 封装。"""

import json
import re
import threading
from datetime import date as calendar_date, timedelta
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings
from ..models.schemas import Location, POIInfo, WeatherInfo


def _to_text(value: Any) -> str:
    """把高德返回中的可选字段转换成稳定字符串。"""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _parse_location(value: Any) -> Optional[Location]:
    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) >= 2:
            try:
                return Location(longitude=float(parts[0]), latitude=float(parts[1]))
            except ValueError:
                return None

    if isinstance(value, dict):
        lng = value.get("longitude") or value.get("lng") or value.get("lon")
        lat = value.get("latitude") or value.get("lat")
        if lng is not None and lat is not None:
            try:
                return Location(longitude=float(lng), latitude=float(lat))
            except ValueError:
                return None

    return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return 0
        return int(value)
    text = _to_text(value).replace("°C", "").replace("℃", "").replace("°", "").strip()
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else 0


def _bounded_weather_temp(value: Any) -> Optional[int]:
    """Parse a temperature that is safe to store on a trip plan.

    Rejects missing values, NaN/Infinity, and physically implausible readings so
    fallback data cannot invent a believable-looking default temperature.
    """
    if value is None:
        return None
    try:
        if isinstance(value, str):
            text = value.replace("°C", "").replace("℃", "").replace("°", "").strip()
            if not text or text.lower() in {"nan", "inf", "-inf", "none", "null"}:
                return None
            number = float(text)
        else:
            number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    if number < -60 or number > 60:
        return None
    return int(round(number))


def _bounded_wind_speed_kmh(value: Any) -> Optional[float]:
    """Open-Meteo wind_speed_10m_max is km/h; reject impossible values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    if number < 0 or number > 250:
        return None
    return number


class AmapService:
    """高德 Web Service 封装，带进程内缓存和简单限速。"""

    _MIN_INTERVAL_SECONDS = 0.45
    _CACHE_TTL_SECONDS = 600
    _NEGATIVE_CACHE_TTL_SECONDS = 30
    _CACHE_MAX_ENTRIES = 512

    def __init__(self):
        self.settings = get_settings()
        if not self.settings.amap_api_key:
            raise ValueError("AMAP_API_KEY 未配置，请在 .env 文件中设置 AMAP_API_KEY")
        self._rate_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._last_request_at = 0.0
        self._client = httpx.Client()
        self._poi_cache: Dict[Any, Any] = {}
        self._weather_cache: Dict[Any, Any] = {}
        self._geo_cache: Dict[Any, Any] = {}
        self._detail_cache: Dict[Any, Any] = {}

    def _cache_get(self, cache: Dict[Any, Any], key: Any) -> Any:
        with self._cache_lock:
            item = cache.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < time.monotonic():
                cache.pop(key, None)
                return None
            return value

    def _cache_set(
        self,
        cache: Dict[Any, Any],
        key: Any,
        value: Any,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        with self._cache_lock:
            now = time.monotonic()
            expired = [item_key for item_key, item in cache.items() if item[0] < now]
            for item_key in expired:
                cache.pop(item_key, None)
            while len(cache) >= self._CACHE_MAX_ENTRIES:
                oldest_key = min(cache, key=lambda item_key: cache[item_key][0])
                cache.pop(oldest_key, None)
            ttl = self._CACHE_TTL_SECONDS if ttl_seconds is None else max(0, ttl_seconds)
            cache[key] = (now + ttl, value)

    def close(self) -> None:
        self._client.close()

    def _get_json(
        self,
        url: str,
        params: Dict[str, Any],
        timeout: Optional[float] = None,
        retries: int = 1,
    ) -> Dict[str, Any]:
        request_params = dict(params)
        request_params["key"] = self.settings.amap_api_key
        request_params["output"] = "JSON"

        for attempt in range(retries + 1):
            # Only serialize request starts. Holding this lock during network
            # I/O allowed one slow provider call to block every user.
            with self._rate_lock:
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < self._MIN_INTERVAL_SECONDS:
                    time.sleep(self._MIN_INTERVAL_SECONDS - elapsed)
                self._last_request_at = time.monotonic()

            response = self._client.get(
                url,
                params=request_params,
                timeout=timeout or get_settings().amap_route_timeout,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("infocode") == "10021" and attempt < retries:
                print("AMap QPS limit hit; retrying after backoff")
                time.sleep(1.0)
                continue
            return data
        return {}

    def _parse_poi_item(self, item: Dict[str, Any], query: str, city: str) -> Optional[POIInfo]:
        location = _parse_location(item.get("location") or item.get("point"))
        if location is None:
            return None
        name = _to_text(item.get("name"))
        if not name:
            return None
        biz_ext = item.get("biz_ext") if isinstance(item.get("biz_ext"), dict) else {}
        photos = [
            _to_text(photo.get("url"))
            for photo in (item.get("photos") or [])
            if isinstance(photo, dict) and photo.get("url")
        ]
        return POIInfo(
            id=_to_text(item.get("id") or item.get("poiid") or name),
            name=name,
            type=_to_text(item.get("type") or item.get("typecode") or query),
            address=_to_text(item.get("address") or item.get("pname") or city),
            location=location,
            tel=_to_text(item.get("tel")) or None,
            rating=_safe_float(biz_ext.get("rating")),
            photos=photos[:3],
            district=_to_text(item.get("adname")),
        )

    def search_poi(self, keywords: str, city: str, citylimit: bool = True) -> List[POIInfo]:
        """搜索 POI。"""
        cache_key = ("poi", (keywords or "").strip(), (city or "").strip(), bool(citylimit))
        cached = self._cache_get(self._poi_cache, cache_key)
        if cached is not None:
            return cached

        try:
            normalized = (keywords or "").strip()
            fallback = "酒店" if any(word in normalized for word in ("酒店", "宾馆", "住宿")) else "景点"
            queries = []
            for item in (normalized, fallback):
                if item and item not in queries:
                    queries.append(item)

            for query in queries:
                data = self._get_json(
                    "https://restapi.amap.com/v3/place/text",
                    {
                        "keywords": query,
                        "city": city,
                        "citylimit": str(citylimit).lower(),
                        "offset": 20,
                        "page": 1,
                        "extensions": "all",
                    },
                )
                print(
                    "AMap POI response: "
                    f"status={data.get('status')}, count={len(data.get('pois') or [])}"
                )

                if data.get("status") != "1":
                    print(f"AMap POI failed: infocode={data.get('infocode')}")
                    continue

                pois: List[POIInfo] = []
                for item in data.get("pois") or []:
                    if not isinstance(item, dict):
                        continue
                    poi = self._parse_poi_item(item, query, city)
                    if poi is not None:
                        pois.append(poi)

                if pois:
                    self._cache_set(self._poi_cache, cache_key, pois)
                    return pois

            self._cache_set(self._poi_cache, cache_key, [])
            return []

        except httpx.TimeoutException:
            print("AMap POI timeout")
            return []
        except Exception as e:
            print(f"AMap POI failed: {type(e).__name__}")
            return []

    def search_poi_around(
        self,
        keywords: str,
        center: Location,
        *,
        radius: int = 10000,
        city: str = "",
    ) -> List[POIInfo]:
        """Search POIs around a verified coordinate, used for location-aware hotels."""
        location_text = f"{center.longitude:.6f},{center.latitude:.6f}"
        cache_key = ("around", keywords.strip(), location_text, int(radius))
        cached = self._cache_get(self._poi_cache, cache_key)
        if cached is not None:
            return cached
        try:
            data = self._get_json(
                "https://restapi.amap.com/v3/place/around",
                {
                    "keywords": keywords,
                    "location": location_text,
                    "radius": max(1000, min(int(radius), 50000)),
                    "sortrule": "distance",
                    "offset": 20,
                    "page": 1,
                    "extensions": "all",
                },
            )
            if data.get("status") != "1":
                return []
            pois = [
                poi
                for item in (data.get("pois") or [])
                if isinstance(item, dict)
                for poi in [self._parse_poi_item(item, keywords, city)]
                if poi is not None
            ]
            self._cache_set(self._poi_cache, cache_key, pois)
            return pois
        except Exception as exc:
            print(f"AMap around POI failed: {type(exc).__name__}")
            return []

    def get_weather(
        self,
        city: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[WeatherInfo]:
        """Query weather with AMap primary and Open-Meteo date completion.

        Failures never raise to the planner: empty results become advisory
        weather gaps, not structural plan failures.
        """
        city_key = (city or "").strip()
        cache_key = ("weather", city_key, start_date or "", end_date or "")
        cached = self._cache_get(self._weather_cache, cache_key)
        if cached is not None:
            return list(cached)

        weather_info: List[WeatherInfo] = []
        try:
            weather_info = self._get_amap_weather(city_key)
        except httpx.TimeoutException:
            print("AMap weather timeout")
        except Exception as exc:
            # Never log full exception text: request URLs may embed the AMap key.
            print(f"AMap weather failed: {type(exc).__name__}")

        # Open-Meteo only completes missing in-range dates. It never overwrites
        # AMap-confirmed days and never invents out-of-horizon forecasts.
        if start_date and end_date and city_key:
            try:
                weather_info = self._complete_weather_with_open_meteo(
                    city=city_key,
                    weather_info=weather_info,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as exc:
                print(f"Open-Meteo weather completion failed: {type(exc).__name__}")
        weather_info = self._filter_requested_weather(
            weather_info, start_date, end_date
        )
        self._cache_set(
            self._weather_cache,
            cache_key,
            weather_info,
            ttl_seconds=(
                self._CACHE_TTL_SECONDS
                if weather_info
                else self._NEGATIVE_CACHE_TTL_SECONDS
            ),
        )
        return list(weather_info)

    def _get_amap_weather(self, city: str) -> List[WeatherInfo]:
        data = self._get_json(
            "https://restapi.amap.com/v3/weather/weatherInfo",
            {"city": city, "extensions": "all"},
        )
        print(f"AMap weather response: status={data.get('status')}")

        if not isinstance(data, dict) or data.get("status") != "1":
            print(
                "AMap weather failed: "
                f"infocode={data.get('infocode') if isinstance(data, dict) else 'invalid'}"
            )
            return []

        weather_info: List[WeatherInfo] = []
        for forecast in data.get("forecasts") or []:
            if not isinstance(forecast, dict):
                continue
            for cast in forecast.get("casts") or []:
                if not isinstance(cast, dict):
                    continue
                item = self._weather_item_from_fields(
                    date=_to_text(cast.get("date")),
                    day_weather=_to_text(
                        cast.get("dayweather") or cast.get("day_weather")
                    ),
                    night_weather=_to_text(
                        cast.get("nightweather") or cast.get("night_weather")
                    ),
                    day_temp=cast.get("daytemp") or cast.get("day_temp"),
                    night_temp=cast.get("nighttemp") or cast.get("night_temp"),
                    wind_direction=_to_text(
                        cast.get("daywind") or cast.get("wind_direction")
                    ),
                    wind_power=_to_text(
                        cast.get("daypower") or cast.get("wind_power")
                    ),
                    provider="amap",
                )
                if item is not None:
                    weather_info.append(item)

        for live in data.get("lives") or []:
            if not isinstance(live, dict):
                continue
            report_time = _to_text(live.get("reporttime"))
            weather = _to_text(live.get("weather"))
            item = self._weather_item_from_fields(
                date=report_time.split(" ")[0] if report_time else "",
                day_weather=weather,
                night_weather=weather,
                day_temp=live.get("temperature"),
                night_temp=live.get("temperature"),
                wind_direction=_to_text(live.get("winddirection")),
                wind_power=_to_text(live.get("windpower")),
                provider="amap",
            )
            if item is not None:
                weather_info.append(item)

        return weather_info

    def _complete_weather_with_open_meteo(
        self,
        city: str,
        weather_info: List[WeatherInfo],
        start_date: str,
        end_date: str,
    ) -> List[WeatherInfo]:
        requested_dates = self._date_range(start_date, end_date)
        if not requested_dates:
            return weather_info

        today = calendar_date.today()
        # Open-Meteo free forecast horizon is about 16 days inclusive of today.
        forecast_horizon = today + timedelta(days=15)
        supported_dates = [
            value
            for value in requested_dates
            if today <= calendar_date.fromisoformat(value) <= forecast_horizon
        ]

        weather_by_date: Dict[str, WeatherInfo] = {}
        for item in weather_info:
            key = (item.date or "")[:10]
            if len(key) == 10 and key not in weather_by_date:
                weather_by_date[key] = item

        missing_dates = [
            value for value in supported_dates if value not in weather_by_date
        ]
        if not missing_dates:
            return [
                weather_by_date[value]
                for value in requested_dates
                if value in weather_by_date
            ]

        parsed_location = self._resolve_weather_location(city)
        if parsed_location is None:
            return [
                weather_by_date[date]
                for date in requested_dates
                if date in weather_by_date
            ]

        timeout = max(1.0, float(get_settings().amap_route_timeout or 12))
        try:
            response = self._client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": parsed_location.latitude,
                    "longitude": parsed_location.longitude,
                    "daily": (
                        "weather_code,temperature_2m_max,temperature_2m_min,"
                        "wind_speed_10m_max,wind_direction_10m_dominant"
                    ),
                    # Open-Meteo defaults to Celsius and km/h; keep them explicit.
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                    "timezone": "Asia/Shanghai",
                    "start_date": supported_dates[0],
                    "end_date": supported_dates[-1],
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            print("Open-Meteo weather response received")
            daily = data.get("daily") if isinstance(data, dict) else None
            if not isinstance(daily, dict):
                return [
                    weather_by_date[date]
                    for date in requested_dates
                    if date in weather_by_date
                ]

            times = daily.get("time") or []
            if not isinstance(times, list):
                return [
                    weather_by_date[date]
                    for date in requested_dates
                    if date in weather_by_date
                ]
            codes = daily.get("weather_code") or []
            max_temps = daily.get("temperature_2m_max") or []
            min_temps = daily.get("temperature_2m_min") or []
            wind_speeds = daily.get("wind_speed_10m_max") or []
            wind_dirs = daily.get("wind_direction_10m_dominant") or []

            for index, date in enumerate(times):
                if not isinstance(date, str) or date not in supported_dates:
                    continue
                if date in weather_by_date:
                    # Never overwrite AMap (or earlier) confirmed days.
                    continue
                day_temp = _bounded_weather_temp(self._list_value(max_temps, index))
                night_temp = _bounded_weather_temp(self._list_value(min_temps, index))
                if day_temp is None or night_temp is None:
                    continue
                wind_speed = _bounded_wind_speed_kmh(self._list_value(wind_speeds, index))
                code_text = self._weather_code_text(self._list_value(codes, index))
                if code_text == "未知" and day_temp is None:
                    continue
                item = self._weather_item_from_fields(
                    date=date,
                    day_weather=code_text,
                    night_weather=code_text,
                    day_temp=day_temp,
                    night_temp=night_temp,
                    wind_direction=self._wind_direction_text(
                        self._list_value(wind_dirs, index)
                    ),
                    wind_power=(
                        self._wind_power_text(wind_speed)
                        if wind_speed is not None
                        else ""
                    ),
                    provider="open_meteo",
                )
                if item is not None:
                    weather_by_date[date] = item
        except Exception as exc:
            print(f"Open-Meteo weather fallback failed: {type(exc).__name__}")

        return [
            weather_by_date[date]
            for date in requested_dates
            if date in weather_by_date
        ]

    def _weather_item_from_fields(
        self,
        *,
        date: str,
        day_weather: str,
        night_weather: str,
        day_temp: Any,
        night_temp: Any,
        wind_direction: str,
        wind_power: str,
        provider: str,
    ) -> Optional[WeatherInfo]:
        normalized_date = (date or "")[:10]
        try:
            calendar_date.fromisoformat(normalized_date)
        except ValueError:
            return None
        parsed_day = _bounded_weather_temp(day_temp)
        parsed_night = _bounded_weather_temp(night_temp)
        if parsed_day is None or parsed_night is None:
            return None
        day_text = (day_weather or "").strip()
        night_text = (night_weather or "").strip()
        invalid = {"", "未知", "暂无", "无", "--", "null", "none"}
        if day_text.casefold() in invalid and night_text.casefold() in invalid:
            return None
        # Keep provider only in description tags is not available on schema;
        # callers distinguish fallback via completion path and quality gaps.
        _ = provider
        return WeatherInfo(
            date=normalized_date,
            day_weather=day_text or night_text,
            night_weather=night_text or day_text,
            day_temp=parsed_day,
            night_temp=parsed_night,
            wind_direction=wind_direction or "",
            wind_power=wind_power or "",
        )

    def _filter_requested_weather(
        self,
        weather_info: List[WeatherInfo],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> List[WeatherInfo]:
        if not start_date or not end_date:
            return weather_info
        requested = set(self._date_range(start_date, end_date))
        if not requested:
            return weather_info
        filtered: List[WeatherInfo] = []
        seen: set[str] = set()
        for item in weather_info:
            key = (item.date or "")[:10]
            if key in requested and key not in seen:
                filtered.append(item)
                seen.add(key)
        return filtered

    def _resolve_weather_location(self, city: str) -> Optional[Location]:
        """Resolve a forecast coordinate without making Open-Meteo depend on AMap."""
        try:
            amap_location = _parse_location(self._geocode_location(city, city))
            if amap_location is not None:
                return amap_location
        except Exception as exc:
            # httpx exceptions may include a request URL containing the AMap
            # key, so only the exception class is safe to log.
            print(f"AMap weather geocode failed: {type(exc).__name__}")

        try:
            response = self._client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": city,
                    "count": 5,
                    "language": "zh",
                    "format": "json",
                    "countryCode": "CN",
                },
                timeout=get_settings().amap_route_timeout,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                return None
            for item in results:
                if not isinstance(item, dict):
                    continue
                try:
                    longitude = float(item.get("longitude"))
                    latitude = float(item.get("latitude"))
                except (TypeError, ValueError):
                    continue
                if 73.0 <= longitude <= 136.0 and 3.0 <= latitude <= 54.0:
                    return Location(longitude=longitude, latitude=latitude)
        except Exception as exc:
            print(f"Open-Meteo geocode failed: {type(exc).__name__}")
        return None

    def _date_range(self, start_date: str, end_date: str) -> List[str]:
        from datetime import datetime, timedelta

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return []
        if end < start:
            return []
        return [
            (start + timedelta(days=index)).strftime("%Y-%m-%d")
            for index in range((end - start).days + 1)
        ]

    def _list_value(self, values: List[Any], index: int) -> Any:
        return values[index] if index < len(values) else None

    def _weather_code_text(self, code: Any) -> str:
        try:
            value = int(code)
        except (TypeError, ValueError):
            return "未知"
        if value == 0:
            return "晴"
        if value in {1, 2, 3}:
            return "多云"
        if value in {45, 48}:
            return "雾"
        if value in {51, 53, 55, 56, 57}:
            return "毛毛雨"
        if value in {61, 63, 65, 66, 67, 80, 81, 82}:
            return "雨"
        if value in {71, 73, 75, 77, 85, 86}:
            return "雪"
        if value in {95, 96, 99}:
            return "雷阵雨"
        return "多云"

    def _wind_direction_text(self, degrees: Any) -> str:
        try:
            value = float(degrees) % 360
        except (TypeError, ValueError):
            return ""
        directions = ["北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风"]
        index = int((value + 22.5) // 45) % 8
        return directions[index]

    def _wind_power_text(self, speed: Any) -> str:
        try:
            value = float(speed)
        except (TypeError, ValueError):
            return ""
        if value < 12:
            return "1-2级"
        if value < 20:
            return "3级"
        if value < 29:
            return "4级"
        if value < 39:
            return "5级"
        return "6级以上"

    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking",
        timeout: Optional[float] = None,
        origin_location: Optional[Location] = None,
        destination_location: Optional[Location] = None,
    ) -> Dict[str, Any]:
        """规划路线。"""
        timeout = timeout or get_settings().amap_route_timeout
        try:
            origin = (
                f"{origin_location.longitude:.6f},{origin_location.latitude:.6f}"
                if origin_location is not None
                else self._geocode_location(origin_address, origin_city, timeout)
            )
            destination = (
                f"{destination_location.longitude:.6f},{destination_location.latitude:.6f}"
                if destination_location is not None
                else self._geocode_location(destination_address, destination_city, timeout)
            )
            if not origin or not destination:
                print("AMap route failed: geocode unavailable")
                return {}

            data = self._request_direction(
                origin=origin,
                destination=destination,
                route_type=route_type,
                origin_city=origin_city,
                destination_city=destination_city,
                timeout=timeout
            )
            print(f"AMap route response: status={data.get('status')}")
            return data

        except httpx.TimeoutException:
            print("AMap route timeout")
            return {}
        except Exception as exc:
            print(f"AMap route failed: {type(exc).__name__}")
            return {}

    def _geocode_location(
        self,
        address: str,
        city: Optional[str] = None,
        timeout: Optional[float] = None
    ) -> Optional[str]:
        """Resolve an address to an AMap coordinate string."""
        cache_key = ("geo", (address or "").strip(), (city or "").strip())
        cached = self._cache_get(self._geo_cache, cache_key)
        if cached is not None:
            return cached

        params = {"address": address}
        if city:
            params["city"] = city

        data = self._get_json(
            "https://restapi.amap.com/v3/geocode/geo",
            params,
            timeout=timeout,
        )
        if data.get("status") != "1":
            print(f"AMap geocode failed: infocode={data.get('infocode')}")
            return None

        geocodes = data.get("geocodes") or []
        if not geocodes:
            return None
        location = geocodes[0].get("location")
        result = location if isinstance(location, str) and "," in location else None
        self._cache_set(self._geo_cache, cache_key, result)
        return result

    def _request_direction(
        self,
        origin: str,
        destination: str,
        route_type: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        endpoint_map = {
            "walking": "https://restapi.amap.com/v3/direction/walking",
            "driving": "https://restapi.amap.com/v3/direction/driving",
            "transit": "https://restapi.amap.com/v3/direction/transit/integrated"
        }
        normalized_type = route_type if route_type in endpoint_map else "walking"
        params = {"origin": origin, "destination": destination}

        if normalized_type == "transit":
            params["city"] = origin_city or destination_city or ""
            if destination_city:
                params["cityd"] = destination_city

        data = self._get_json(endpoint_map[normalized_type], params, timeout=timeout)
        if data.get("status") != "1":
            print(f"AMap route API failed: infocode={data.get('infocode')}")
            return {}
        return data

    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """地理编码。"""
        try:
            location = self._geocode_location(address, city)
            return _parse_location(location)
        except Exception as exc:
            print(f"AMap geocode failed: {type(exc).__name__}")
            return None

    def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        """获取 POI 详情。"""
        cache_key = ("detail", (poi_id or "").strip())
        cached = self._cache_get(self._detail_cache, cache_key)
        if cached is not None:
            return cached

        try:
            data = self._get_json(
                "https://restapi.amap.com/v3/place/detail",
                {"id": poi_id, "extensions": "all"},
            )
            print(f"AMap POI detail response: status={data.get('status')}")

            if data.get("status") != "1":
                print(f"AMap POI detail failed: infocode={data.get('infocode')}")
                return {}

            pois = data.get("pois") or []
            result = pois[0] if pois and isinstance(pois[0], dict) else data
            self._cache_set(self._detail_cache, cache_key, result)
            return result if isinstance(result, dict) else {"raw": result}

        except httpx.TimeoutException:
            print("AMap POI detail timeout")
            return {}
        except Exception as exc:
            print(f"AMap POI detail failed: {type(exc).__name__}")
            return {}


_amap_service: AmapService | None = None
_amap_service_lock = threading.Lock()


def get_amap_service() -> AmapService:
    """获取线程安全初始化的高德地图服务实例。"""
    global _amap_service
    if _amap_service is None:
        with _amap_service_lock:
            if _amap_service is None:
                _amap_service = AmapService()
    return _amap_service


def shutdown_amap_service() -> None:
    """Release pooled HTTP connections during application shutdown."""
    global _amap_service
    with _amap_service_lock:
        service = _amap_service
        _amap_service = None
    if service is not None:
        service.close()
