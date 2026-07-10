"""高德地图 Web Service 封装。"""

import json
import re
import threading
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


def _preview(value: Any, length: int = 200) -> str:
    if isinstance(value, str):
        return value[:length]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:length]
    except Exception:
        return str(value)[:length]


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


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    text = _to_text(value).replace("°C", "").replace("℃", "").replace("°", "").strip()
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else 0


class AmapService:
    """高德 Web Service 封装，带进程内缓存和简单限速。"""

    _MIN_INTERVAL_SECONDS = 0.45
    _CACHE_TTL_SECONDS = 600

    def __init__(self):
        self.settings = get_settings()
        if not self.settings.amap_api_key:
            raise ValueError("AMAP_API_KEY 未配置，请在 .env 文件中设置 AMAP_API_KEY")
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._poi_cache: Dict[Any, Any] = {}
        self._weather_cache: Dict[Any, Any] = {}
        self._geo_cache: Dict[Any, Any] = {}
        self._detail_cache: Dict[Any, Any] = {}

    def _cache_get(self, cache: Dict[Any, Any], key: Any) -> Any:
        item = cache.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            cache.pop(key, None)
            return None
        return value

    def _cache_set(self, cache: Dict[Any, Any], key: Any, value: Any) -> None:
        cache[key] = (time.monotonic() + self._CACHE_TTL_SECONDS, value)

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
            with self._request_lock:
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < self._MIN_INTERVAL_SECONDS:
                    time.sleep(self._MIN_INTERVAL_SECONDS - elapsed)
                response = httpx.get(
                    url,
                    params=request_params,
                    timeout=timeout or get_settings().amap_route_timeout,
                )
                self._last_request_at = time.monotonic()

            response.raise_for_status()
            data = response.json()
            if data.get("infocode") == "10021" and attempt < retries:
                print("AMap QPS limit hit; retrying after backoff")
                time.sleep(1.0)
                continue
            return data
        return {}

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
                        "extensions": "base",
                    },
                )
                print(f"AMap POI result: {_preview(data)}...")

                if data.get("status") != "1":
                    print(f"AMap POI failed: info={data.get('info')}, infocode={data.get('infocode')}")
                    continue

                pois: List[POIInfo] = []
                for item in data.get("pois") or []:
                    if not isinstance(item, dict):
                        continue
                    location = _parse_location(item.get("location") or item.get("point"))
                    if location is None:
                        continue
                    name = _to_text(item.get("name"))
                    if not name:
                        continue
                    pois.append(POIInfo(
                        id=_to_text(item.get("id") or item.get("poiid") or name),
                        name=name,
                        type=_to_text(item.get("type") or item.get("typecode") or query),
                        address=_to_text(item.get("address") or item.get("pname") or city),
                        location=location,
                        tel=_to_text(item.get("tel")) or None,
                    ))

                if pois:
                    self._cache_set(self._poi_cache, cache_key, pois)
                    return pois

            self._cache_set(self._poi_cache, cache_key, [])
            return []

        except httpx.TimeoutException:
            print(f"AMap POI timeout: keywords={keywords}, city={city}")
            return []
        except Exception as e:
            print(f"AMap POI failed: {str(e)}")
            return []

    def get_weather(
        self,
        city: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[WeatherInfo]:
        """查询天气。"""
        cache_key = ("weather", (city or "").strip(), start_date or "", end_date or "")
        cached = self._cache_get(self._weather_cache, cache_key)
        if cached is not None:
            return cached

        try:
            weather_info = self._get_amap_weather(city)
            if start_date and end_date:
                weather_info = self._complete_weather_with_open_meteo(
                    city=city,
                    weather_info=weather_info,
                    start_date=start_date,
                    end_date=end_date,
                )
            self._cache_set(self._weather_cache, cache_key, weather_info)
            return weather_info

        except httpx.TimeoutException:
            print(f"AMap weather timeout: city={city}")
            return []
        except Exception as e:
            print(f"AMap weather failed: {str(e)}")
            return []

    def _get_amap_weather(self, city: str) -> List[WeatherInfo]:
        data = self._get_json(
            "https://restapi.amap.com/v3/weather/weatherInfo",
            {"city": city, "extensions": "all"},
        )
        print(f"AMap weather result: {_preview(data)}...")

        if data.get("status") != "1":
            print(f"AMap weather failed: info={data.get('info')}, infocode={data.get('infocode')}")
            return []

        weather_info: List[WeatherInfo] = []
        for forecast in data.get("forecasts") or []:
            if not isinstance(forecast, dict):
                continue
            for cast in forecast.get("casts") or []:
                if not isinstance(cast, dict):
                    continue
                weather_info.append(WeatherInfo(
                    date=_to_text(cast.get("date")),
                    day_weather=_to_text(cast.get("dayweather") or cast.get("day_weather")),
                    night_weather=_to_text(cast.get("nightweather") or cast.get("night_weather")),
                    day_temp=_safe_int(cast.get("daytemp") or cast.get("day_temp")),
                    night_temp=_safe_int(cast.get("nighttemp") or cast.get("night_temp")),
                    wind_direction=_to_text(cast.get("daywind") or cast.get("wind_direction")),
                    wind_power=_to_text(cast.get("daypower") or cast.get("wind_power")),
                ))

        for live in data.get("lives") or []:
            if not isinstance(live, dict):
                continue
            report_time = _to_text(live.get("reporttime"))
            weather = _to_text(live.get("weather"))
            weather_info.append(WeatherInfo(
                date=report_time.split(" ")[0] if report_time else "",
                day_weather=weather,
                night_weather=weather,
                day_temp=_safe_int(live.get("temperature")),
                night_temp=_safe_int(live.get("temperature")),
                wind_direction=_to_text(live.get("winddirection")),
                wind_power=_to_text(live.get("windpower")),
            ))

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

        weather_by_date = {
            item.date[:10]: item
            for item in weather_info
            if item.date and len(item.date) >= 10
        }
        missing_dates = [date for date in requested_dates if date not in weather_by_date]
        if not missing_dates:
            return [weather_by_date[date] for date in requested_dates]

        location = self._geocode_location(city, city)
        if not location:
            return [weather_by_date[date] for date in requested_dates if date in weather_by_date]

        parsed_location = _parse_location(location)
        if parsed_location is None:
            return [weather_by_date[date] for date in requested_dates if date in weather_by_date]

        try:
            response = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": parsed_location.latitude,
                    "longitude": parsed_location.longitude,
                    "daily": (
                        "weather_code,temperature_2m_max,temperature_2m_min,"
                        "wind_speed_10m_max,wind_direction_10m_dominant"
                    ),
                    "timezone": "Asia/Shanghai",
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout=get_settings().amap_route_timeout,
            )
            response.raise_for_status()
            data = response.json()
            print(f"Open-Meteo weather result: {_preview(data)}...")
            daily = data.get("daily") if isinstance(data, dict) else None
            if not isinstance(daily, dict):
                return [weather_by_date[date] for date in requested_dates if date in weather_by_date]

            times = daily.get("time") or []
            codes = daily.get("weather_code") or []
            max_temps = daily.get("temperature_2m_max") or []
            min_temps = daily.get("temperature_2m_min") or []
            wind_speeds = daily.get("wind_speed_10m_max") or []
            wind_dirs = daily.get("wind_direction_10m_dominant") or []

            for index, date in enumerate(times):
                if date in weather_by_date:
                    continue
                weather_by_date[date] = WeatherInfo(
                    date=date,
                    day_weather=self._weather_code_text(self._list_value(codes, index)),
                    night_weather=self._weather_code_text(self._list_value(codes, index)),
                    day_temp=_safe_int(self._list_value(max_temps, index)),
                    night_temp=_safe_int(self._list_value(min_temps, index)),
                    wind_direction=self._wind_direction_text(self._list_value(wind_dirs, index)),
                    wind_power=self._wind_power_text(self._list_value(wind_speeds, index)),
                )
        except Exception as e:
            print(f"Open-Meteo weather fallback failed: {str(e)}")

        return [weather_by_date[date] for date in requested_dates if date in weather_by_date]

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
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """规划路线。"""
        timeout = timeout or get_settings().amap_route_timeout
        try:
            origin = self._geocode_location(origin_address, origin_city, timeout)
            destination = self._geocode_location(destination_address, destination_city, timeout)
            if not origin or not destination:
                print(f"AMap route failed: geocode failed {origin_address} -> {destination_address}")
                return {}

            data = self._request_direction(
                origin=origin,
                destination=destination,
                route_type=route_type,
                origin_city=origin_city,
                destination_city=destination_city,
                timeout=timeout
            )
            print(f"AMap route result: {_preview(data)}...")
            return data

        except httpx.TimeoutException:
            print(f"AMap route timeout: {origin_address} -> {destination_address} ({timeout}s)")
            return {}
        except Exception as e:
            print(f"AMap route failed: {str(e)}")
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
            print(f"AMap geocode failed: {address}, info={data.get('info')}")
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
            print(f"AMap route API failed: info={data.get('info')}, infocode={data.get('infocode')}")
            return {}
        return data

    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """地理编码。"""
        try:
            location = self._geocode_location(address, city)
            return _parse_location(location)
        except Exception as e:
            print(f"AMap geocode failed: {str(e)}")
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
            print(f"AMap POI detail result: {_preview(data)}...")

            if data.get("status") != "1":
                print(f"AMap POI detail failed: info={data.get('info')}, infocode={data.get('infocode')}")
                return {}

            pois = data.get("pois") or []
            result = pois[0] if pois and isinstance(pois[0], dict) else data
            self._cache_set(self._detail_cache, cache_key, result)
            return result if isinstance(result, dict) else {"raw": result}

        except httpx.TimeoutException:
            print(f"AMap POI detail timeout: {poi_id}")
            return {}
        except Exception as e:
            print(f"AMap POI detail failed: {str(e)}")
            return {}


_amap_service = None


def get_amap_service() -> AmapService:
    """获取高德地图服务实例。"""
    global _amap_service

    if _amap_service is None:
        _amap_service = AmapService()

    return _amap_service
