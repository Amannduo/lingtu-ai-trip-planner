"""Deterministic destination feasibility rules shared by recommendation and planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


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
        destination = self.normalize_city(destination_city)
        return destination in PRECISE_SHORT_TRIP_CITY_GRAPH[precise]

    def assess(
        self,
        origin_city: Optional[str],
        destination_city: Optional[str],
        travel_days: Optional[int],
        *,
        explicit_destination: bool = False,
    ) -> DestinationFeasibility:
        origin = self.normalize_city(origin_city)
        origin_label = "".join(str(origin_city or "").split()) or origin
        destination = self.normalize_city(destination_city)
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
        if nearby and destination in nearby:
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


_destination_feasibility_service: DestinationFeasibilityService | None = None


def get_destination_feasibility_service() -> DestinationFeasibilityService:
    global _destination_feasibility_service
    if _destination_feasibility_service is None:
        _destination_feasibility_service = DestinationFeasibilityService()
    return _destination_feasibility_service
