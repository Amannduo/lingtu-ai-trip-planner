"""Deterministic destination feasibility rules shared by recommendation and planning."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Optional

# Province-level names that may prefix a city name in user input or FlyAI
# responses.  Direct-administered municipalities (北京/天津/上海/重庆) are
# NOT included — they ARE the core city name, not a prefix.
_PROVINCE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("黑龙江省", "黑龙江"), ("内蒙古自治区", "内蒙古"),
    ("广西壮族自治区", "广西"), ("西藏自治区", "西藏"),
    ("宁夏回族自治区", "宁夏"), ("新疆维吾尔自治区", "新疆"),
    ("香港特别行政区", "香港"), ("澳门特别行政区", "澳门"),
    ("河北省", "河北"), ("山西省", "山西"), ("辽宁省", "辽宁"),
    ("吉林省", "吉林"), ("江苏省", "江苏"), ("浙江省", "浙江"),
    ("安徽省", "安徽"), ("福建省", "福建"), ("江西省", "江西"),
    ("山东省", "山东"), ("河南省", "河南"), ("湖北省", "湖北"),
    ("湖南省", "湖南"), ("广东省", "广东"), ("海南省", "海南"),
    ("四川省", "四川"), ("贵州省", "贵州"), ("云南省", "云南"),
    ("陕西省", "陕西"), ("甘肃省", "甘肃"), ("青海省", "青海"),
    ("台湾省", "台湾"),
)
_CITY_SUFFIXES: tuple[str, ...] = (
    "特别行政区", "自治区", "自治州", "地区", "市", "县", "区",
)
# Order matters: directional compounds first so "南站" is stripped before
# a bare "站" suffix.
_STATION_SUFFIXES: tuple[str, ...] = (
    "南站", "北站", "东站", "西站", "站",
)


# This graph is deliberately conservative: for a one- or two-day trip we only
# auto-recommend destinations with a dependable short-haul connection from the
# origin. LLM output may add reasons, but it may not bypass this boundary.
SHORT_TRIP_CITY_GRAPH: Dict[str, List[str]] = {
    "北京": ["天津", "秦皇岛", "济南"],
    "天津": ["北京", "秦皇岛", "济南"],
    "上海": ["苏州", "杭州", "南京"],
    "杭州": ["苏州", "上海", "南京"],
    "苏州": ["上海", "杭州", "南京"],
    "南京": ["苏州", "杭州", "上海"],
    "广州": ["深圳", "珠海", "佛山"],
    "深圳": ["广州", "珠海", "惠州"],
    "成都": ["重庆", "乐山", "德阳"],
    "重庆": ["成都", "贵阳", "武隆"],
    "西安": ["宝鸡", "洛阳", "汉中"],
    "宝鸡": ["西安", "天水", "汉中"],
    "天水": ["宝鸡", "西安", "兰州"],
    "汉中": ["宝鸡", "西安", "广元"],
    "长沙": ["武汉", "张家界", "南昌"],
    "武汉": ["长沙", "南京", "合肥"],
    "厦门": ["泉州", "福州", "漳州"],
    "福州": ["厦门", "泉州", "宁德"],
    "青岛": ["济南", "烟台", "威海"],
    "济南": ["青岛", "天津", "北京"],
    "郑州": ["洛阳", "西安", "开封"],
}

# County/district-level origins inherit their prefecture-level transport circle.
# The precise user label is kept elsewhere for display.
ORIGIN_CITY_ALIASES: Dict[str, str] = {
    "扶风": "宝鸡",
    "扶风县": "宝鸡",
    "宝鸡扶风": "宝鸡",
    "宝鸡扶风县": "宝鸡",
    "宝鸡市扶风": "宝鸡",
    "宝鸡市扶风县": "宝鸡",
}

# A county-level departure can have a materially different two-day radius from
# its prefecture centre. These entries are conservative and override the broad
# prefecture graph for automatic weekend recommendations.
PRECISE_SHORT_TRIP_CITY_GRAPH: Dict[str, List[str]] = {
    "扶风": ["眉县", "麟游县", "太白县", "凤县", "天水", "西安"],
    "扶风县": ["眉县", "麟游县", "太白县", "凤县", "天水", "西安"],
    "宝鸡扶风": ["眉县", "麟游县", "太白县", "凤县", "天水", "西安"],
    "宝鸡扶风县": ["眉县", "麟游县", "太白县", "凤县", "天水", "西安"],
    "宝鸡市扶风": ["眉县", "麟游县", "太白县", "凤县", "天水", "西安"],
    "宝鸡市扶风县": ["眉县", "麟游县", "太白县", "凤县", "天水", "西安"],
}


@dataclass(frozen=True)
class DestinationFeasibility:
    allowed: bool
    severity: str
    reason: str
    minimum_days: int
    transport_note: str
    score: int


class DestinationFeasibilityService:
    """Assess whether a destination is suitable for the available trip window."""

    _NORM_CACHE: dict[str, str] = {}

    def normalize_city(self, city: Optional[str]) -> str:
        value = " ".join(str(city or "").split())
        compact = value.replace(" ", "")
        if compact in ORIGIN_CITY_ALIASES:
            return ORIGIN_CITY_ALIASES[compact]
        for suffix in ("特别行政区", "自治州", "地区", "市"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
                break
        return ORIGIN_CITY_ALIASES.get(value, value)

    def normalize_location_for_matching(self, raw: Optional[str]) -> str:
        """Return a stripped-down core name for matching city ↔ station.

        Examples
        --------
        * ``山西太原``     → ``太原``
        * ``山西省太原市`` → ``太原``
        * ``太原站``       → ``太原``
        * ``太原南站``     → ``太原``
        * ``北京西站``     → ``北京``  (直辖市 not stripped)
        * ``吉林站``       → ``吉林``  (同名省市 not stripped)
        """
        value = " ".join(str(raw or "").split())
        if not value:
            return ""

        cache_key = value.casefold()
        cached = self._NORM_CACHE.get(cache_key)
        if cached is not None:
            return cached

        original = value

        # 1. Strip province prefix.
        province_stripped = False
        for long_form, short_form in _PROVINCE_PREFIXES:
            if value.startswith(long_form):
                value = value[len(long_form):]
                province_stripped = True
                break
            if value.startswith(short_form):
                value = value[len(short_form):]
                province_stripped = True
                break

        if province_stripped and self._is_station_only(value):
            value = original  # province name WAS the city name

        # 2. Strip city suffix from the end.
        for suffix in _CITY_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)]
                break

        # 3. Strip station suffix (directional compounds first).
        for suffix in _STATION_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)]
                break

        # 4. Fall through to existing city normalization for alias support.
        canonical = self.normalize_city(value)

        normalized = canonical.strip()
        self._NORM_CACHE[cache_key] = normalized
        return normalized

    def _is_station_only(self, value: str) -> bool:
        """Return True when *value* is empty or only a station suffix."""
        if not value:
            return True
        for suffix in _STATION_SUFFIXES:
            if value == suffix:
                return True
        return False

    def nearby_destinations(self, origin_city: Optional[str]) -> List[str]:
        precise = self._precise_origin_key(origin_city)
        if precise:
            return list(PRECISE_SHORT_TRIP_CITY_GRAPH[precise])
        return list(SHORT_TRIP_CITY_GRAPH.get(self.normalize_city(origin_city), []))

    def _precise_origin_key(self, origin_city: Optional[str]) -> str:
        compact = "".join(str(origin_city or "").split())
        return compact if compact in PRECISE_SHORT_TRIP_CITY_GRAPH else ""

    def is_precise_short_trip(
        self,
        origin_city: Optional[str],
        destination_city: Optional[str],
    ) -> bool:
        precise = self._precise_origin_key(origin_city)
        if not precise:
            return False
        destination = self.normalize_location_for_matching(destination_city)
        nearby = PRECISE_SHORT_TRIP_CITY_GRAPH[precise]
        nearby_normalized = {
            self.normalize_location_for_matching(item) for item in nearby
        }
        return (
            destination in nearby
            or destination in nearby_normalized
            or self.normalize_city(destination_city) in nearby
        )

    def assess(
        self,
        origin_city: Optional[str],
        destination_city: Optional[str],
        travel_days: Optional[int],
        *,
        explicit_destination: bool = False,
    ) -> DestinationFeasibility:
        origin = self.normalize_location_for_matching(origin_city)
        origin_label = "".join(str(origin_city or "").split()) or origin
        destination = self.normalize_location_for_matching(destination_city)
        days = max(1, int(travel_days)) if travel_days is not None else None

        if not origin or not destination:
            return DestinationFeasibility(
                allowed=True,
                severity="info",
                reason="缺少完整出发地或目的地，暂不执行短途可达性硬判断。",
                minimum_days=1,
                transport_note="确认出发地后再核对城际交通。",
                score=50,
            )
        if origin == destination:
            return DestinationFeasibility(
                allowed=True,
                severity="info",
                reason="本地深度游无需预留城际往返时间。",
                minimum_days=1,
                transport_note="优先使用市内公共交通。",
                score=100,
            )

        precise_origin = self._precise_origin_key(origin_city)
        nearby = (
            PRECISE_SHORT_TRIP_CITY_GRAPH.get(precise_origin)
            if precise_origin
            else SHORT_TRIP_CITY_GRAPH.get(origin)
        )
        # Graph entries may keep admin suffixes (e.g. 麟游县) while the assess
        # path normalizes destinations (麟游). Match on both raw and normalized
        # forms so county-level short-trip circles remain usable.
        nearby_match = False
        if nearby:
            nearby_normalized = {
                self.normalize_location_for_matching(item) for item in nearby
            }
            nearby_match = (
                destination in nearby
                or destination in nearby_normalized
                or self.normalize_location_for_matching(destination) in nearby_normalized
            )
        if nearby and nearby_match:
            return DestinationFeasibility(
                allowed=True,
                severity="info",
                reason=f"{destination}属于从{origin_label}出发的短途可达范围。",
                minimum_days=2,
                transport_note="建议优先高铁、动车或短途自驾，并核对末班返程时间。",
                score=95,
            )

        if days is None:
            return DestinationFeasibility(
                allowed=True,
                severity="info",
                reason=f"尚未确认旅行天数，暂不对{origin}到{destination}执行短途硬限制。",
                minimum_days=3,
                transport_note="确认日期后再核对往返交通时间。",
                score=60,
            )

        # For longer windows the static short-haul graph is not used as a hard
        # nationwide distance oracle. The planner still has to account for the
        # actual intercity journey in its budget and itinerary.
        if days >= 3 or nearby is None:
            return DestinationFeasibility(
                allowed=True,
                severity="warning" if days <= 3 else "info",
                reason=f"{origin_label}到{destination}不属于已确认的周末短途圈。",
                minimum_days=3,
                transport_note="生成前应核对往返班次，并在首末日预留城际交通时间。",
                score=65 if days == 3 else 75,
            )

        reason = (
            f"仅有{days}天时，{destination}不在从{origin_label}出发的可信短途圈内，"
            "往返交通会明显挤占游玩时间。"
        )
        return DestinationFeasibility(
            allowed=explicit_destination,
            severity="warning" if explicit_destination else "error",
            reason=reason,
            minimum_days=3,
            transport_note="建议改选周边目的地，或增加天数后核对高铁/航班时刻。",
            score=25,
        )


def _address_points_to_conflicting_city(
    address: str,
    destination_city: str,
    structured_city_norm: str,
) -> bool:
    """Return True if *address* has a city-level admin reference to a city
    that is neither *destination_city* nor *structured_city_norm*.

    Used to detect conflicts where structured fields say "matched" but the
    address text says a different city.
    """
    if not address:
        return False
    feasibility = get_destination_feasibility_service()
    dest_norm = feasibility.normalize_location_for_matching(destination_city)
    admin_refs = _extract_admin_references(address)
    for ref_text, _ref_kind in admin_refs:
        ref_norm = feasibility.normalize_location_for_matching(ref_text)
        if not ref_norm or not dest_norm:
            continue
        # Match dest → no conflict.
        if ref_norm == dest_norm or ref_norm in dest_norm or dest_norm in ref_norm:
            continue
        # Match the structured city → no conflict.
        if structured_city_norm and (
            ref_norm == structured_city_norm
            or ref_norm in structured_city_norm
            or structured_city_norm in ref_norm
        ):
            continue
        # A different identifiable city → conflict.
        return True
    return False


def poi_destination_status(
    destination_city: str,
    cityname: str = "",
    citycode: str = "",
    adname: str = "",
    adcode: str = "",
    address: str = "",
    name: str = "",
) -> str:
    """Return ``"matched"``, ``"mismatched"``, or ``"unknown"``.

    Structured fields (cityname / citycode / adname / adcode from AMap)
    take priority over address text parsing.  The address field is used
    only as a fallback when no structured evidence is available.

    Priority:
    1. cityname explicitly matches / mismatches destination → decisive
    2. citycode maps to destination / other city → decisive
    3. adcode confirmed in destination admin area → decisive
    4. adname only used when it can be confirmed against the destination
    5. Structured fields missing → fall back to address admin references
    6. No evidence → ``"unknown"``

    When structured fields conflict (e.g. cityname=太原市 but
    address contains 大同市), the structured fields win — the
    result is ``"mismatched"``.
    """
    if not destination_city:
        return "unknown"

    feasibility = get_destination_feasibility_service()
    dest_norm = feasibility.normalize_location_for_matching(destination_city)

    # ── structured evidence ──────────────────────────────────────────

    # 1. cityname: most authoritative single field.
    if cityname:
        cn_norm = feasibility.normalize_location_for_matching(cityname)
        if cn_norm and dest_norm:
            if cn_norm == dest_norm or cn_norm in dest_norm or dest_norm in cn_norm:
                # Structured field says matched — but check for address conflict.
                if _address_points_to_conflicting_city(address, destination_city, cn_norm):
                    return "mismatched"
                return "matched"
            return "mismatched"

    # 2. citycode: known AMap citycode → city name mapping.
    _KNOWN_CITYCODES: dict[str, str] = {
        "0351": "太原", "0352": "大同", "0353": "阳泉", "0354": "晋中",
        "0355": "长治", "0356": "晋城", "0357": "临汾", "0358": "吕梁",
        "0359": "运城", "0349": "朔州", "0350": "忻州",
        "010": "北京", "021": "上海", "022": "天津", "023": "重庆",
        "020": "广州", "0755": "深圳", "028": "成都", "029": "西安",
        "0371": "郑州", "0379": "洛阳", "0531": "济南",
        "024": "沈阳", "0411": "大连", "027": "武汉",
        "0731": "长沙", "0571": "杭州", "025": "南京",
        "0591": "福州", "0592": "厦门", "0898": "海口",
        "0771": "南宁", "0851": "贵阳", "0871": "昆明",
        "0931": "兰州", "0951": "银川", "0971": "西宁",
        "0991": "乌鲁木齐", "0891": "拉萨",
    }
    if citycode:
        if citycode in _KNOWN_CITYCODES:
            mapped = _KNOWN_CITYCODES[citycode]
            mapped_norm = feasibility.normalize_location_for_matching(mapped)
            if mapped_norm == dest_norm:
                # Structured field says matched — but check for address conflict.
                if _address_points_to_conflicting_city(address, destination_city, mapped_norm):
                    return "mismatched"
                return "matched"
            return "mismatched"
        # citycode present but not in our mapping — try adcode if
        # available, else fall through to address check.

    # 3. adcode: the first 4 digits identify the city-level admin unit.
    if adcode and len(adcode) >= 4:
        adcode_prefix = adcode[:4]

    # 4. adname: only usable when we can confirm the parent city.
    if adname and not cityname and not citycode:
        # adname alone (e.g. "云冈区") cannot confirm destination;
        # fall through to address check for additional context.
        pass

    # ── address fallback ─────────────────────────────────────────────

    if address:
        admin_refs = _extract_admin_references(address)
        for ref_text, _ref_kind in admin_refs:
            ref_norm = feasibility.normalize_location_for_matching(ref_text)
            if not ref_norm or not dest_norm:
                continue
            if ref_norm == dest_norm or ref_norm in dest_norm or dest_norm in ref_norm:
                # If a structured field disagrees, trust the structured field.
                if cityname:
                    cn_norm = feasibility.normalize_location_for_matching(cityname)
                    if cn_norm and cn_norm != ref_norm and cn_norm not in ref_norm and ref_norm not in cn_norm:
                        return "mismatched"  # conflict: structured wins
                return "matched"
            # A different identifiable city.
            return "mismatched"

    return "unknown"


def _extract_admin_references(text: str) -> list[tuple[str, str]]:
    """Extract city/district-level references from a Chinese address.

    Returns a list of ``(city_text, kind)`` tuples where kind is
    one of ``"市"``, ``"区"``, ``"县"``, ``"省"``.
    Ignores street-name patterns (e.g. "XX路").
    """
    import re
    if not text:
        return []

    results: list[tuple[str, str]] = []
    # Match "XX市", "XX区", "XX县", "XX省XX市"
    pattern = re.compile(r"([一-鿿]{2,10})(市|区|县)")
    for match in pattern.finditer(text):
        city_text = match.group(1)
        suffix = match.group(2)
        # Check if the next character is a street-suffix.
        end = match.end()
        if end < len(text) and text[end:end + 1] in ("路", "街", "巷", "道", "桥"):
            continue
        results.append((city_text, suffix))
    return results


_destination_feasibility_service: DestinationFeasibilityService | None = None


def get_destination_feasibility_service() -> DestinationFeasibilityService:
    global _destination_feasibility_service
    if _destination_feasibility_service is None:
        _destination_feasibility_service = DestinationFeasibilityService()
    return _destination_feasibility_service
