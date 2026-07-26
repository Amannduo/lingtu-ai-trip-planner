"""Testable semantic trip contract: field provenance, merge, conflict detection.

Sources:
- user_explicit: user stated a concrete value in free text
- rule_inferred: deterministic rule derived a value (may need confirmation)
- form_confirmed: already present on the client form / prior hop
- system_default: internal filler; never treated as user intent
- unknown: still open
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from ..models.schemas import (
    FieldBinding,
    FieldConfidence,
    FieldSource,
    RecommendationContext,
    SemanticTripContract,
    TripRequest,
)
from .business_calendar import resolve_business_date
from .city_mention_service import (
    NEGATION_FILLER,
    NEGATOR_ALTERNATION,
    extract_mentioned_destination,
    is_negated_at,
    known_destination_cities,
)


NUMBER_WORDS = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

DIGIT_WORDS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

PROTECTED_SOURCES = frozenset({"form_confirmed", "user_explicit"})
APPLY_SOURCES = frozenset({"form_confirmed", "user_explicit", "rule_inferred"})

EARLY_ARRIVAL_HINT_DEFAULT = (
    "建议周五下午或傍晚出发，提前抵达后休息或在酒店周边简单活动，周六再开始完整游玩。"
)
WEEKEND_MARKERS = ("下周末", "这个周末", "这周末", "本周末", "周末")

# Labels of the machine-authored block the recommender writes into
# ``free_text_input``. That block is system output, not a user utterance —
# re-parsing it made the advisory line "建议周五下午出发" look like an explicit
# Friday departure and silently expanded a 2-day weekend into 3 days.
MACHINE_FREE_TEXT_LABELS = (
    "目的地",
    "约束",
    "时段",
    "抵达建议",
    "同行",
    "理由",
    "出发",
    "城际",
    "优先",
    "范围",
    "排除",
)
_MACHINE_LABEL_ALTERNATION = "|".join(MACHINE_FREE_TEXT_LABELS)
_MACHINE_LABEL_RE = re.compile(r"【(" + _MACHINE_LABEL_ALTERNATION + r")】")
# A machine segment runs to the next 【 or end of line, so an inline block is
# stripped as reliably as a line-per-label one.
_MACHINE_SEGMENT_RE = re.compile(
    r"【(?:" + _MACHINE_LABEL_ALTERNATION + r")】[^【\n]*"
)
_USER_QUOTE_RE = re.compile(r"【原文】[ \t]*([^【\n]*)")
# The recommender always writes every label plus 【原文】. Below that, the text
# is user-authored and must be left alone.
_MACHINE_BLOCK_MIN_LABELS = 3

# Longest marker first, in ONE alternation: otherwise the bare 周末 inside an
# already-negated 下周末 is re-tested with the negator outside its window.
_WEEKEND_MARKER_RE = re.compile(
    "|".join(sorted(WEEKEND_MARKERS, key=len, reverse=True))
)

_SEPARABLE_VERB_RE = re.compile(r"(避|散|透|逛|歇|玩|睡|吃)个(?=[暑步气街脚够觉饭])")

# A date only outranks a weekend window when it reads as the travel date;
# "这个周末出去玩，8月1号之前得定好" is a deadline, not a departure.
_DATE_CORE = r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]"
_ANY_DATE_RE = re.compile(_DATE_CORE)
_DATE_CHANGE_PREFIX = r"(?:改成|改到|改为|推到|挪到|定在|定为)"
# Weak verbs (去/到/开始/走) only count as departure when the sentence is
# explicitly rescheduling: "我8月10号去上海出差，这个周末想在附近转转" is not a
# departure date, and must not cancel the requested weekend.
_DEPARTURE_DATE_RES = (
    # Rescheduling: any following verb (or none) still means the travel date.
    re.compile(
        _DATE_CHANGE_PREFIX + r"\s*" + _DATE_CORE
        + r"\s*(?:那天|这天)?\s*(?:出发|走|启程|动身|出行|去|到|开始)?"
    ),
    # Otherwise only an unambiguous departure verb counts.
    re.compile(_DATE_CORE + r"\s*(?:那天|这天)?\s*(?:出发|启程|动身|出行)"),
)


def _find_departure_date(text: str) -> Optional[re.Match[str]]:
    """First date that reads as the travel date, with groups (year, month, day)."""
    for pattern in _DEPARTURE_DATE_RES:
        match = pattern.search(text)
        if match:
            return match
    return None


def extract_user_utterance(text: str | None) -> str:
    """Return only the user-authored part of a free-text field.

    A recognizable machine block (【原文】 present, or at least
    ``_MACHINE_BLOCK_MIN_LABELS`` distinct machine labels) has its segments
    removed and its 【原文】 content restored. Anything else — including a user
    who typed one bracketed line themselves — is returned untouched.
    """
    raw = str(text or "")
    if "【" not in raw:
        return raw.strip()
    labels = set(_MACHINE_LABEL_RE.findall(raw))
    quotes = [match.group(1).strip() for match in _USER_QUOTE_RE.finditer(raw)]
    if not quotes and len(labels) < _MACHINE_BLOCK_MIN_LABELS:
        return raw.strip()

    remainder = _MACHINE_SEGMENT_RE.sub(" ", _USER_QUOTE_RE.sub(" ", raw))
    parts = [*(quote for quote in quotes if quote)]
    parts.extend(line.strip() for line in remainder.splitlines() if line.strip())
    return "\n".join(parts).strip()


_MACHINE_ENTRY_RE = re.compile(
    r"【(" + _MACHINE_LABEL_ALTERNATION + r")】([^【\n]*)"
)
# Labels that record a decided constraint (safe for downstream rules) versus
# labels that are model prose or an unconfirmed suggestion (never a constraint).
DECIDED_MACHINE_LABELS = frozenset({"时段", "约束", "同行", "范围", "排除", "目的地"})


def parse_machine_block(text: str | None) -> Dict[str, str]:
    """Return the recommender's 【label】→content map from a free-text field."""
    return {
        match.group(1): match.group(2).strip()
        for match in _MACHINE_ENTRY_RE.finditer(str(text or ""))
    }


def decided_constraint_text(text: str | None) -> str:
    """User words plus system-decided constraints, without advisory prose.

    Downstream rules must not read 【理由】 or 【抵达建议】: those are generated
    copy, and treating them as user intent is how "建议周五下午出发" once turned
    a two-day weekend into three days.
    """
    block = parse_machine_block(text)
    parts = [user_intent_text(text)]
    parts.extend(
        content
        for label, content in block.items()
        if label in DECIDED_MACHINE_LABELS and content
    )
    return " ".join(part for part in parts if part).strip()


EXCLUDABLE_THEMES = (
    "海边", "海岛", "爬山", "登山", "沙漠", "草原", "古镇", "博物馆",
    "购物", "夜市", "游乐园", "温泉", "自驾", "长途大巴",
)
# 不去北京西站接人 mentions a station, not a rejected destination.
_STATION_AFTER_RE = re.compile(r"^[东南西北]?站|^机场")
_EXCLUSION_ENUMERATION = r"(?:\s*[和与、,，]\s*(?:%s))*"
_excluded_city_pattern_cache: Optional[re.Pattern[str]] = None
_excluded_theme_pattern_cache: Optional[re.Pattern[str]] = None


def _build_exclusion_pattern(terms: Iterable[str]) -> re.Pattern[str]:
    """One compiled alternation for all terms, with 和/与/、 enumeration."""
    alternation = "|".join(
        re.escape(term) for term in sorted(set(terms), key=len, reverse=True)
    )
    return re.compile(
        r"(?:" + NEGATOR_ALTERNATION + r")" + NEGATION_FILLER + r"[去到看玩选往]{0,2}\s*"
        r"((?:" + alternation + r")" + (_EXCLUSION_ENUMERATION % alternation) + r")"
    )


def _excluded_city_pattern() -> re.Pattern[str]:
    global _excluded_city_pattern_cache
    if _excluded_city_pattern_cache is None:
        _excluded_city_pattern_cache = _build_exclusion_pattern(
            known_destination_cities()
        )
    return _excluded_city_pattern_cache


def _excluded_theme_pattern() -> re.Pattern[str]:
    global _excluded_theme_pattern_cache
    if _excluded_theme_pattern_cache is None:
        _excluded_theme_pattern_cache = _build_exclusion_pattern(EXCLUDABLE_THEMES)
    return _excluded_theme_pattern_cache


def _find_excluded(text: str, pattern: re.Pattern[str]) -> List[str]:
    found: List[str] = []
    for match in pattern.finditer(text):
        offset = match.start(1)
        # Keep the separators so each term's own end offset stays correct: the
        # station guard is per term ("不想去天津和北京站" still rejects 天津).
        for index, term in enumerate(re.split(r"([和与、,，])", match.group(1))):
            end = offset + len(term)
            offset = end
            if index % 2:  # separator
                continue
            item = term.strip()
            if not item or item in found:
                continue
            if _STATION_AFTER_RE.match(text[end:end + 3]):
                continue
            found.append(item)
    return found


def has_affirmative_weekend(text: str) -> bool:
    """Whether the text asks for a weekend trip (negated mentions don't count)."""
    return any(
        not is_negated_at(text, match.start())
        for match in _WEEKEND_MARKER_RE.finditer(text)
    )


def parse_chinese_number(text: str) -> Optional[int]:
    """Parse common Chinese integers used in trip constraints.

    Supports: 三 / 十二 / 二十 / 三十五 / 一百 / 三千 / 三千五 / 一万 /
    一万二（=12000） / Arabic with 千/万.
    """
    raw = "".join(str(text or "").split())
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)

    arabic = re.fullmatch(r"(\d+(?:\.\d+)?)([千万])?", raw)
    if arabic:
        amount = float(arabic.group(1))
        unit = arabic.group(2)
        if unit == "万":
            return int(amount * 10000)
        if unit == "千":
            return int(amount * 1000)
        return int(amount)

    total = 0
    section = 0
    number = 0
    last_multiplier = 0
    for char in raw:
        if char in DIGIT_WORDS:
            number = DIGIT_WORDS[char]
            continue
        if char == "十":
            section += (number or 1) * 10
            number = 0
            last_multiplier = 10
            continue
        if char == "百":
            section += (number or 1) * 100
            number = 0
            last_multiplier = 100
            continue
        if char == "千":
            section += (number or 1) * 1000
            number = 0
            last_multiplier = 1000
            continue
        if char == "万":
            section += number
            total += (section or 1) * 10000
            section = 0
            number = 0
            last_multiplier = 10000
            continue
        return None

    if number:
        # Shorthand: 一万二 -> 12000, 三千五 -> 3500
        if last_multiplier == 10000:
            total += section + number * 1000
        elif last_multiplier == 1000:
            total += section + number * 100
        elif last_multiplier == 100:
            total += section + number * 10
        else:
            total += section + number
    else:
        total += section
    return total if total > 0 else None


def bind(
    value: Any,
    source: FieldSource = "unknown",
    confidence: FieldConfidence = "low",
    *,
    pending: bool = False,
    evidence: str = "",
    conflicts: Optional[List[str]] = None,
) -> FieldBinding:
    if value is None and source == "unknown":
        return FieldBinding(
            value=None,
            source="unknown",
            confidence="low",
            pending_confirmation=pending,
            evidence=evidence,
            conflicts=list(conflicts or []),
        )
    return FieldBinding(
        value=value,
        source=source,
        confidence=confidence,
        pending_confirmation=pending,
        evidence=(evidence or "")[:500],
        conflicts=list(conflicts or []),
    )


def unknown_binding() -> FieldBinding:
    return bind(None, "unknown", "low")


class SemanticContractService:
    """Build, merge and project SemanticTripContract across agent hops."""

    def extract_from_text(
        self,
        text: str,
        *,
        reference_date: Optional[date] = None,
        now: Optional[datetime] = None,
        user_timezone: Optional[str] = None,
        business_timezone: Optional[str] = None,
    ) -> SemanticTripContract:
        """Extract a message-scoped contract. Ambiguous values stay pending.

        Relative weekend dates use ``resolve_business_date`` — callers should
        pass ``reference_date`` or ``now`` in tests; production uses the
        configured business timezone (default Asia/Shanghai).
        """
        contract = SemanticTripContract(raw_text=text or "")
        normalized = (text or "").replace("，", ",").replace("。", " ").strip()
        # Separable verb-object forms: 避个暑 / 散个步 / 透个气 read the same as
        # 避暑 / 散步 / 透气, and dropping the infix keeps one keyword table.
        normalized = _SEPARABLE_VERB_RE.sub(r"\1", normalized)
        if not normalized:
            contract.refresh_pending_fields()
            return contract

        today = resolve_business_date(
            reference_date=reference_date,
            now=now,
            user_timezone=user_timezone,
            business_timezone=business_timezone,
        )

        origin_match = re.search(
            r"从([\u4e00-\u9fff]{2,12}?)(?:出发|出游|出去|走|去)",
            normalized,
        )
        if origin_match:
            label = self._clean_location_label(origin_match.group(1))
            # Avoid treating 从周末... as origin; require plausible place label.
            if label and not any(tok in label for tok in ("周末", "周五", "明天", "今天")):
                contract.origin_city = bind(
                    label,
                    "user_explicit",
                    "high",
                    evidence=origin_match.group(0),
                )

        traveler_pattern = re.compile(
            r"([1-9]\d?|一|两|二|三|四|五|六|七|八|九|十)"
            r"(?:个|位|名)?"
            r"(年轻人|年青人|青年人?|成年人?|成人|老年人?|老人|学生|男生|女生|人)"
        )
        traveler_matches = [
            match
            for match in traveler_pattern.finditer(normalized)
            if not self._is_lodging_capacity(normalized, match)
        ]
        traveler_match = traveler_matches[0] if traveler_matches else None
        vague_count = bool(
            re.search(r"(大概|大约|左右|差不多|估计).{0,6}人", normalized)
        ) or any(
            re.match(r"\s*(?:左右|上下|大约|大概|差不多)", normalized[match.end():])
            for match in traveler_matches
        )
        if traveler_match:
            parsed_counts = [
                int(match.group(1))
                if match.group(1).isdigit()
                else NUMBER_WORDS.get(match.group(1))
                for match in traveler_matches
            ]
            connectors = [
                normalized[left.end():right.start()]
                for left, right in zip(traveler_matches, traveler_matches[1:])
            ]
            additive_groups = len(traveler_matches) > 1 and all(
                re.fullmatch(r"\s*(?:和|与|及|以及|加上?|另有|、|，|,)\s*", connector)
                for connector in connectors
            )
            ambiguous_groups = len(traveler_matches) > 1 and not additive_groups
            valid_counts = [count for count in parsed_counts if count is not None]
            count = sum(valid_counts) if additive_groups else valid_counts[0]
            evidence = (
                normalized[traveler_matches[0].start():traveler_matches[-1].end()]
                if additive_groups
                else " / ".join(match.group(0) for match in traveler_matches)
            )
            out_of_range = not 1 <= count <= 20
            pending_count = vague_count or ambiguous_groups or out_of_range
            if not out_of_range:
                contract.travelers = bind(
                    count,
                    "user_explicit",
                    "medium" if pending_count else "high",
                    pending=pending_count,
                    evidence=evidence,
                )
            else:
                # RecommendationContext accepts 1..20 only. Keep the evidence for
                # clarification, but never project an out-of-domain value and 500.
                note = f"travelers: {evidence} 合计{count}人，超出系统支持的1至20人"
                contract.travelers = bind(
                    None,
                    "unknown",
                    "low",
                    pending=True,
                    evidence=f"{evidence}（系统支持1至20人）",
                    conflicts=[note],
                )
                contract.conflicts.append(note)
            if any(match.group(2) != "人" for match in traveler_matches):
                # Preserve explicit party descriptions instead of reducing them
                # to only a scalar traveler count.
                contract.travel_party = bind(
                    evidence,
                    "user_explicit",
                    "medium" if pending_count else "high",
                    pending=pending_count,
                    evidence=evidence,
                )
        else:
            travelers, travel_party, party_evidence = self._infer_travel_party(normalized)
            if travelers is not None:
                contract.travelers = bind(
                    travelers,
                    "rule_inferred",
                    "high",
                    evidence=party_evidence,
                )
            if travel_party:
                party_pending = travelers is None
                contract.travel_party = bind(
                    travel_party,
                    "rule_inferred",
                    "medium" if party_pending else "high",
                    pending=party_pending,
                    evidence=party_evidence,
                )

        # Explicit count + kinship → keep party as soft signal / conflict, don't drop silently
        if traveler_match and re.search(r"父母|爸妈|爸爸妈妈|爹妈", normalized):
            if not contract.travel_party.is_known():
                contract.travel_party = bind(
                    "含父母同行（人数以用户明示为准）",
                    "rule_inferred",
                    "medium",
                    pending=True,
                    evidence="父母",
                )
            if contract.travelers.is_known() and contract.travelers.value != 3:
                note = (
                    f"用户明示{contract.travelers.value}人，同时提到父母；"
                    "人数以明示为准，同行关系待确认"
                )
                contract.travelers.conflicts.append(note)
                contract.travel_party.conflicts.append(note)
                contract.conflicts.append(note)

        days_match = re.search(
            r"([1-9]\d?|一|两|二|三|四|五|六|七|八|九|十)天",
            normalized,
        )
        if days_match:
            raw = days_match.group(1)
            days = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw)
            if days is not None:
                contract.travel_days = bind(
                    days,
                    "user_explicit",
                    "high",
                    evidence=days_match.group(0),
                )
        elif "周末" in normalized:
            contract.travel_days = bind(
                2,
                "rule_inferred",
                "high",
                evidence="周末",
            )

        # Budget must be tied to 预算 / 费用 words, or a number with 元/块.
        # Never treat "最多3人" / "大约2天" as a budget amount.
        budget_connector = r"(?:大约|大概|约|控制在|不超过|最多|为|是|改成|改为|调整为|到|:|：)?"
        budget_match = re.search(
            rf"(?:预算|总预算|花费|费用|开销)\s*{budget_connector}"
            r"\s*(\d+(?:\.\d+)?)\s*(k|K|千|万)?",
            normalized,
        )
        if not budget_match:
            budget_match = re.search(
                r"(?:控制在|不超过|大约|大概|最多|约)\s*"
                r"(\d+(?:\.\d+)?)\s*(k|K|千|万)?\s*(?:元|块钱?|人民币)",
                normalized,
            )
        # Chinese amounts: 预算三千 / 预算三万 / 预算一万二
        # Do not use open-ended \D between 预算 and numeral — it would swallow 三 from 三千.
        cn_budget = re.search(
            rf"(?:预算|总预算|花费|费用|开销)\s*{budget_connector}"
            r"\s*([一二两三四五六七八九十百千万零〇]+)\s*(?:元|块钱?)?",
            normalized,
        )
        # Do NOT match bare「50元/门票80元」— that is not a trip budget.
        if budget_match:
            amount = float(budget_match.group(1))
            unit = budget_match.group(2)
            multiplier = (
                10000 if unit == "万" else 1000 if unit in {"k", "K", "千"} else 1
            )
            contract.budget = bind(
                int(amount * multiplier),
                "user_explicit",
                "high",
                evidence=budget_match.group(0),
            )
        elif cn_budget:
            parsed = parse_chinese_number(cn_budget.group(1))
            # Avoid treating bare single digits that are really day/people nearby
            if parsed is not None and parsed >= 100:
                contract.budget = bind(
                    parsed,
                    "user_explicit",
                    "high",
                    evidence=cn_budget.group(0),
                )
            elif parsed is not None and "元" in cn_budget.group(0):
                contract.budget = bind(
                    parsed,
                    "user_explicit",
                    "medium",
                    pending=True,
                    evidence=cn_budget.group(0),
                )
        elif any(k in normalized for k in ("预算不太多", "预算有限", "省钱", "预算别太高")):
            # Amount unknown; accommodation preference may still be inferred.
            contract.budget = bind(
                None,
                "unknown",
                "low",
                pending=True,
                evidence="预算含糊，金额未知",
            )

        preferences: List[str] = []
        preference_keywords = {
            "历史文化": ("历史", "文化", "博物馆", "古城", "古镇"),
            "自然风光": ("自然", "山水", "海边", "风景", "户外", "避暑"),
            "美食": ("美食", "吃", "小吃", "餐厅"),
            "艺术": ("艺术", "展览", "美术馆", "设计"),
            "购物": ("购物", "买东西", "逛街"),
            "休闲": ("放松", "散心", "透气", "不想太累", "慢一点", "避开人群", "避暑"),
        }
        for preference, keywords in preference_keywords.items():
            # "不要海边" must not add 自然风光; only affirmative mentions count.
            if any(
                self._mentioned_affirmatively(normalized, keyword)
                for keyword in keywords
            ):
                preferences.append(preference)
        if preferences:
            contract.preferences = bind(
                preferences,
                "user_explicit",
                "high",
                evidence="、".join(preferences),
            )

        if any(
            keyword in normalized
            for keyword in (
                "不想太累",
                "轻松",
                "慢一点",
                "带父母",
                "跟父母",
                "和父母",
                "陪父母",
                "爸妈",
                "带老人",
                "避暑",
            )
        ):
            contract.pace = bind("轻松", "user_explicit", "high", evidence="轻松/父母/避暑")
        elif any(keyword in normalized for keyword in ("特种兵", "多玩几个", "行程丰富")):
            contract.pace = bind("紧凑", "user_explicit", "high", evidence="紧凑行程")

        if any(keyword in normalized for keyword in ("住得舒服", "酒店舒服", "品质酒店")):
            contract.accommodation = bind(
                "舒适型酒店", "user_explicit", "high", evidence="住得舒服"
            )
        elif "民宿" in normalized:
            contract.accommodation = bind("民宿", "user_explicit", "high", evidence="民宿")
        elif any(keyword in normalized for keyword in ("省钱", "预算有限", "预算别太高", "预算不太多")):
            contract.accommodation = bind(
                "经济型酒店", "rule_inferred", "medium", pending=True, evidence="省钱倾向"
            )

        if "自驾" in normalized or "开车" in normalized:
            contract.transportation = bind("自驾", "user_explicit", "high", evidence="自驾")
        elif "步行" in normalized or "徒步" in normalized:
            contract.transportation = bind("步行", "user_explicit", "high", evidence="步行")
        elif "高铁" in normalized or "公共交通" in normalized:
            contract.transportation = bind(
                "公共交通", "user_explicit", "high", evidence="公共交通/高铁"
            )

        self._apply_destination_city(normalized, contract)
        self._apply_scope_and_exclusions(normalized, contract)
        self._apply_relative_dates(normalized, contract, today=today)
        self._apply_weekend_and_friday_semantics(normalized, contract, today=today)
        contract.refresh_pending_fields()
        return contract

    @staticmethod
    def _mentioned_affirmatively(text: str, keyword: str) -> bool:
        """Whether *keyword* appears at least once without a direct negation."""
        for match in re.finditer(re.escape(keyword), text):
            if not is_negated_at(text, match.start()):
                return True
        return False

    def _apply_scope_and_exclusions(
        self, text: str, contract: SemanticTripContract
    ) -> None:
        """Capture range requirements and explicit exclusions.

        Without these the recommender silently loses "附近的城市"、"不想去昆明"
        and "不要海边" — the user then sees options they already ruled out.
        """
        nearby_markers = ("附近", "周边", "周围", "短途", "近一点", "近点", "不远")
        far_markers = ("远一点", "远点", "远途", "长途", "跨省玩", "去远的")
        nearby_hit = next(
            (m for m in nearby_markers if self._mentioned_affirmatively(text, m)),
            None,
        )
        far_hit = next(
            (m for m in far_markers if self._mentioned_affirmatively(text, m)),
            None,
        )
        if nearby_hit and far_hit:
            note = f"destination_scope: 同时出现「{nearby_hit}」与「{far_hit}」，范围待确认"
            contract.destination_scope = bind(
                "nearby",
                "rule_inferred",
                "low",
                pending=True,
                evidence=f"{nearby_hit}/{far_hit}",
                conflicts=[note],
            )
            contract.conflicts.append(note)
        elif nearby_hit:
            contract.destination_scope = bind(
                "nearby", "user_explicit", "high", evidence=nearby_hit
            )
        elif far_hit:
            contract.destination_scope = bind(
                "far", "user_explicit", "high", evidence=far_hit
            )

        chosen = (
            str(contract.destination_city.value)
            if contract.destination_city.is_known()
            else ""
        )
        excluded_cities: List[str] = []
        for city in _find_excluded(text, _excluded_city_pattern()):
            if chosen and city == chosen:
                # "不想去大同，还是去大同吧": the affirmative choice wins, but the
                # reversal is recorded rather than silently forgotten.
                note = f"excluded_destinations: 先排除后又选择了「{city}」，以最新选择为准"
                if note not in contract.conflicts:
                    contract.conflicts.append(note)
                continue
            excluded_cities.append(city)
        if excluded_cities:
            contract.excluded_destinations = bind(
                excluded_cities,
                "user_explicit",
                "high",
                evidence="、".join(excluded_cities),
            )

        excluded_themes = _find_excluded(text, _excluded_theme_pattern())
        if excluded_themes:
            contract.excluded_themes = bind(
                excluded_themes,
                "user_explicit",
                "high",
                evidence="、".join(excluded_themes),
            )

    def _apply_destination_city(self, text: str, contract: SemanticTripContract) -> None:
        """Write explicit destination mentions into the contract (not recommender-only)."""
        if contract.destination_city.is_known():
            return
        origin = (
            str(contract.origin_city.value)
            if contract.origin_city.is_known()
            else None
        )
        destination = extract_mentioned_destination(text, origin)
        if not destination:
            return
        contract.destination_city = bind(
            destination,
            "user_explicit",
            "high",
            evidence=destination,
        )

    def contract_from_form(self, context: RecommendationContext) -> SemanticTripContract:
        contract = SemanticTripContract(raw_text="")
        mapping = {
            "origin_city": context.origin_city,
            "budget": context.budget,
            "travel_days": context.travel_days,
            "travelers": context.travelers,
            "start_date": context.start_date,
            "end_date": context.end_date,
            "transportation": context.transportation,
            "accommodation": context.accommodation,
        }
        for name, value in mapping.items():
            if value is None or value == "":
                continue
            setattr(
                contract,
                name,
                bind(value, "form_confirmed", "high", evidence="form"),
            )
        if context.preferences:
            contract.preferences = bind(
                list(context.preferences),
                "form_confirmed",
                "high",
                evidence="form",
            )
        contract.refresh_pending_fields()
        return contract

    def merge(
        self,
        base: SemanticTripContract,
        incoming: SemanticTripContract,
    ) -> SemanticTripContract:
        """Merge message extraction into form-backed contract without silent overwrite."""
        merged = base.model_copy(deep=True)
        if incoming.raw_text:
            merged.raw_text = incoming.raw_text

        field_names = [
            "origin_city",
            "destination_city",
            "start_date",
            "end_date",
            "travel_days",
            "travelers",
            "travel_party",
            "budget",
            "pace",
            "preferences",
            "transportation",
            "accommodation",
            "date_pattern",
            "weekend_style",
            "early_arrival_hint",
            "departure_mode",
            "destination_scope",
        ]
        for name in field_names:
            current: FieldBinding = getattr(merged, name)
            new: FieldBinding = getattr(incoming, name)
            setattr(merged, name, self._merge_field(name, current, new, merged))

        # Exclusions accumulate across turns: "不要海边" said once stays true
        # until the user takes it back, so a later message must not drop it.
        for name in ("excluded_destinations", "excluded_themes"):
            setattr(
                merged,
                name,
                self._merge_exclusions(getattr(merged, name), getattr(incoming, name)),
            )

        # Date window consistency: non-pending dates define travel_days.
        # Protected mismatched day counts are recorded, then aligned to the window
        # only when the window itself is form_confirmed or user_explicit.
        if (
            merged.start_date.is_known()
            and merged.end_date.is_known()
            and not merged.start_date.pending_confirmation
            and not merged.end_date.pending_confirmation
        ):
            try:
                start = date.fromisoformat(str(merged.start_date.value))
                end = date.fromisoformat(str(merged.end_date.value))
                if end >= start:
                    days = (end - start).days + 1
                    window_source = merged.start_date.source
                    if not merged.travel_days.is_known():
                        merged.travel_days = bind(
                            days,
                            window_source
                            if window_source in PROTECTED_SOURCES
                            else "rule_inferred",
                            "high",
                            evidence="date_window",
                        )
                    elif merged.travel_days.value != days:
                        note = (
                            f"travel_days={merged.travel_days.value} 与日期窗口"
                            f"{merged.start_date.value}~{merged.end_date.value}"
                            f"（{days}天）不一致"
                        )
                        merged.conflicts.append(note)
                        if window_source in PROTECTED_SOURCES:
                            # Confirmed calendar window is source of truth for day count.
                            merged.travel_days = bind(
                                days,
                                window_source,
                                "high",
                                evidence="date_window",
                                conflicts=[note],
                            )
                        else:
                            merged.travel_days = merged.travel_days.model_copy(
                                deep=True,
                                update={
                                    "pending_confirmation": True,
                                    "conflicts": list(merged.travel_days.conflicts)
                                    + [note],
                                },
                            )
            except ValueError:
                pass

        # Conflicts detected while reading the message (out-of-range party size,
        # contradictory range, an exclusion the user then reversed) are part of
        # the merged record — dropping them made them invisible to every caller.
        merged.conflicts.extend(incoming.conflicts)

        # Dedupe conflicts
        seen = set()
        unique_conflicts = []
        for item in merged.conflicts:
            if item not in seen:
                seen.add(item)
                unique_conflicts.append(item)
        merged.conflicts = unique_conflicts
        merged.refresh_pending_fields()
        return merged

    def to_recommendation_context(
        self,
        contract: SemanticTripContract,
        *,
        base: Optional[RecommendationContext] = None,
        include_pending: bool = True,
    ) -> RecommendationContext:
        """Project contract into RecommendationContext for planning.

        include_pending=True keeps high-signal pending values for ranking only;
        form_patch / frontend apply paths should use apply_values() instead.
        """
        data = (base or RecommendationContext()).model_dump()
        for name in (
            "origin_city",
            "budget",
            "travel_days",
            "travelers",
            "start_date",
            "end_date",
            "transportation",
            "accommodation",
        ):
            binding: FieldBinding = getattr(contract, name)
            if not binding.is_known():
                continue
            if binding.pending_confirmation and not include_pending:
                continue
            if binding.source == "system_default":
                continue
            data[name] = binding.value

        pref_binding = contract.preferences
        if pref_binding.is_known() and isinstance(pref_binding.value, list):
            if include_pending or not pref_binding.pending_confirmation:
                existing = list(data.get("preferences") or [])
                for item in pref_binding.value:
                    if item not in existing:
                        existing.append(item)
                data["preferences"] = existing

        return RecommendationContext(**data)

    def apply_values(self, contract: SemanticTripContract) -> Dict[str, Any]:
        """Flat dict safe for frontend form auto-fill."""
        payload: Dict[str, Any] = {}
        for name in (
            "origin_city",
            "destination_city",
            "start_date",
            "end_date",
            "travel_days",
            "travelers",
            "travel_party",
            "budget",
            "pace",
            "transportation",
            "accommodation",
            "date_pattern",
            "weekend_style",
            "early_arrival_hint",
            "departure_mode",
        ):
            binding: FieldBinding = getattr(contract, name)
            # early_arrival_hint is advisory: expose when known even if not form-confirmed
            if name == "early_arrival_hint" and binding.is_known():
                payload[name] = binding.value
                continue
            if binding.is_apply_safe():
                payload[name] = binding.value
        if contract.preferences.is_apply_safe() and isinstance(
            contract.preferences.value, list
        ):
            payload["preferences"] = list(contract.preferences.value)
        return payload

    def flat_values(
        self,
        contract: SemanticTripContract,
        *,
        include_pending: bool = True,
    ) -> Dict[str, Any]:
        """Compatibility flat dict for tests and internal callers."""
        payload: Dict[str, Any] = {"raw_text": contract.raw_text}
        for name in (
            "origin_city",
            "destination_city",
            "start_date",
            "end_date",
            "travel_days",
            "travelers",
            "travel_party",
            "budget",
            "pace",
            "transportation",
            "accommodation",
            "date_pattern",
            "weekend_style",
            "early_arrival_hint",
            "departure_mode",
        ):
            binding: FieldBinding = getattr(contract, name)
            if not binding.is_known():
                continue
            if binding.pending_confirmation and not include_pending:
                continue
            payload[name] = binding.value
        if contract.preferences.is_known() and isinstance(contract.preferences.value, list):
            if include_pending or not contract.preferences.pending_confirmation:
                payload["preferences"] = list(contract.preferences.value)
        return payload

    def interpreted_payload(self, contract: SemanticTripContract) -> Dict[str, Any]:
        """API payload: apply-safe flats + full contract for audit/UI."""
        payload = self.apply_values(contract)
        payload["raw_text"] = contract.raw_text
        payload["semantic_contract"] = contract.model_dump(mode="json")
        payload["conflicts"] = list(contract.conflicts)
        # The subset the user actually has to resolve. Shipping it means the UI
        # does not maintain a second, drifting copy of the same rule.
        payload["blocking_conflicts"] = blocking_conflicts(contract)
        payload["pending_fields"] = list(contract.pending_fields)
        return payload

    def needs_more_info(
        self,
        contract: SemanticTripContract,
        context: RecommendationContext,
        latest_message: str,
    ) -> tuple[bool, str]:
        """Return whether to pause for confirmation, plus one focused question."""
        has_origin = contract.origin_city.is_known() or bool(context.origin_city)
        # Pace alone is not a destination direction.
        has_theme = (
            contract.preferences.is_known()
            or contract.destination_city.is_known()
            or bool(context.preferences)
        )
        has_window = (
            contract.travel_days.is_known()
            or (contract.start_date.is_known() and contract.end_date.is_known())
            or bool(context.travel_days)
            or bool(context.start_date and context.end_date)
        )
        has_budget = contract.budget.is_known() or context.budget is not None
        has_travelers = contract.travelers.is_known() or context.travelers is not None
        has_pace = contract.pace.is_known()

        place_or_theme = has_origin or has_theme
        soft_signals = sum([has_window, has_budget, has_travelers, has_pace])

        text = (latest_message or "").strip()
        if len(text) < 4 and not place_or_theme and soft_signals == 0:
            return True, "你更想要自然放松、城市美食，还是历史文化？选一个方向就够了。"

        if not place_or_theme and soft_signals == 0:
            return True, "你更想要自然放松、城市美食，还是历史文化？选一个方向就够了。"

        # Pace / budget / travelers / weekend alone are not enough to recommend.
        if not place_or_theme:
            return True, "先告诉我出发地，或你更想自然放松、城市美食还是历史文化？"

        if has_origin and not has_theme and not has_window and not has_budget:
            return True, "从出发地看，你更想自然放松、城市美食，还是历史文化？"

        # Identity conflicts on critical fields while still under-specified
        critical_conflicts = [
            c
            for c in contract.conflicts
            if any(
                key in c
                for key in (
                    "origin_city",
                    "出发",
                    "日期",
                    "travel_days",
                    "budget",
                    "travelers",
                    "人数",
                )
            )
        ]
        if critical_conflicts and not has_theme and not has_window:
            return True, "我核对到需求有冲突，请确认出发地、日期或人数后再继续。"

        return False, ""

    def set_destination(
        self,
        contract: SemanticTripContract,
        city: str,
        *,
        explicit: bool,
        evidence: str = "",
    ) -> SemanticTripContract:
        updated = contract.model_copy(deep=True)
        updated.destination_city = bind(
            city,
            "user_explicit" if explicit else "rule_inferred",
            "high" if explicit else "medium",
            evidence=evidence or city,
        )
        updated.refresh_pending_fields()
        return updated

    def _merge_field(
        self,
        name: str,
        current: FieldBinding,
        new: FieldBinding,
        merged: SemanticTripContract,
    ) -> FieldBinding:
        if not new.is_known() and new.source == "unknown" and not new.pending_confirmation:
            return current

        if not current.is_known():
            # Prefer incoming known / pending signal
            if new.is_known() or new.pending_confirmation:
                return new.model_copy(deep=True)
            return current

        if not new.is_known() and new.pending_confirmation:
            # A pending signal with a concrete conflict (for example an explicit
            # out-of-range traveler total) must not disappear behind a form value.
            if new.conflicts:
                notes = list(new.conflicts)
                merged.conflicts.extend(notes)
                return current.model_copy(
                    deep=True,
                    update={
                        "pending_confirmation": True,
                        "conflicts": list(current.conflicts) + notes,
                    },
                )
            # A generic unknown without conflicting evidence keeps the form value.
            return current

        if self._values_equal(name, current.value, new.value):
            # Same value: upgrade evidence/source priority toward stronger source
            if new.source == "user_explicit" and current.source != "user_explicit":
                return new.model_copy(deep=True)
            if current.source == "form_confirmed":
                return current
            if new.source in PROTECTED_SOURCES and current.source not in PROTECTED_SOURCES:
                return new.model_copy(deep=True)
            return current

        # Latest high-confidence user_explicit always wins over form / older values.
        # Form defaults (e.g. 公共交通) must not trap a clear new utterance (自驾).
        if (
            new.source == "user_explicit"
            and new.confidence == "high"
            and not new.pending_confirmation
        ):
            note = (
                f"{name}: 最新用户明示 {new.value!r} 覆盖旧值 {current.value!r}"
                f"（{current.source}）"
            )
            merged.conflicts.append(note)
            result = new.model_copy(deep=True)
            result.conflicts = list(result.conflicts) + [note]
            return result

        # Conflict: remaining protected current (form_confirmed) wins over rule_inferred
        current_protected = current.source in PROTECTED_SOURCES
        new_protected = new.source in PROTECTED_SOURCES

        if current_protected:
            note = (
                f"{name}: 已确认/明示值为 {current.value!r}，"
                f"新输入建议 {new.value!r}（{new.source}），保留前者并待确认"
            )
            result = current.model_copy(deep=True)
            result.pending_confirmation = True
            result.conflicts = list(result.conflicts) + [note]
            merged.conflicts.append(note)
            return result

        if new_protected and not current_protected:
            note = (
                f"{name}: 新输入明示 {new.value!r} 覆盖规则/默认值 {current.value!r}"
            )
            merged.conflicts.append(note)
            result = new.model_copy(deep=True)
            result.conflicts = list(result.conflicts) + [note]
            return result

        # Both inferred: keep current, flag conflict
        note = (
            f"{name}: 规则值冲突 {current.value!r} vs {new.value!r}，保留前者并待确认"
        )
        result = current.model_copy(deep=True)
        result.pending_confirmation = True
        result.conflicts = list(result.conflicts) + [note]
        merged.conflicts.append(note)
        return result

    def _merge_exclusions(
        self, current: FieldBinding, new: FieldBinding
    ) -> FieldBinding:
        merged_values: List[str] = []
        for binding in (current, new):
            if not binding.is_known() or not isinstance(binding.value, list):
                continue
            for item in binding.value:
                text = str(item).strip()
                if text and text not in merged_values:
                    merged_values.append(text)
        if not merged_values:
            return current
        evidence = new.evidence or current.evidence or "、".join(merged_values)
        return bind(merged_values, "user_explicit", "high", evidence=evidence)

    def _values_equal(self, name: str, left: Any, right: Any) -> bool:
        if isinstance(left, list) and isinstance(right, list):
            return list(left) == list(right)
        if name in {"origin_city", "destination_city"}:
            # "山西太原" and "太原" are the same place, not a conflict to report.
            return _normalize_compare(name, left) == _normalize_compare(name, right)
        return left == right

    def _clean_location_label(self, value: str) -> str:
        return "".join(str(value or "").split()).strip("，,。 ")

    def _is_lodging_capacity(self, text: str, match: re.Match) -> bool:
        """Return whether a person-count phrase describes a room/table/ticket."""
        before = text[max(0, match.start() - 8):match.start()]
        after = text[match.end():match.end() + 8]
        direct_capacity = re.match(
            r"(?:间|房|房型|桌|票)|"
            r"(?:(?:豪华|标准|家庭|商务|大床|双床|主题|套|海景){1,2})"
            r"(?:间|房|房型)",
            after,
        )
        contextual_capacity = (
            re.search(r"(?:住|入住|订|预订|预定|开).{0,4}$", before)
            and re.search(r".{0,6}(?:间|房|房型)", after)
        )
        return bool(direct_capacity or contextual_capacity)

    def _infer_travel_party(
        self, text: str
    ) -> tuple[Optional[int], Optional[str], str]:
        family_size = re.search(r"(?:一家|全家)([一二两三四五六1-6])口", text)
        if family_size:
            raw = family_size.group(1)
            size = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
            return size, f"一家{size}口", family_size.group(0)

        with_parents = re.search(
            r"(?:我|自己)?(?:和|跟|带上?|陪着?)(?:我的?)?"
            r"(?:父母|爸妈|爸爸妈妈|爹妈)",
            text,
        )
        if with_parents:
            return 3, "你和父母", with_parents.group(0)

        if any(keyword in text for keyword in ("夫妻俩", "两口子", "夫妻二人")):
            return 2, "夫妻二人", "夫妻"

        with_one_companion = re.search(
            r"(?:我|自己)?(?:和|跟|带上?|陪着?)(?:我的?)?"
            r"(?:爸爸|妈妈|父亲|母亲|爷爷|奶奶|外公|外婆|爱人|对象)",
            text,
        )
        if with_one_companion:
            return 2, "你和同行家人", with_one_companion.group(0)

        # Vague companion without count → party pending, no invented travelers
        if re.search(r"(和|跟|带|陪).{0,4}(朋友|同事|同学)", text):
            return None, "与朋友同行（人数待确认）", "朋友"

        return None, None, ""

    def _apply_relative_dates(
        self,
        text: str,
        contract: SemanticTripContract,
        *,
        today: date,
    ) -> None:
        # A stated *departure* date outranks weekend inference — otherwise
        # "不要周末了，改成9月15号出发" keeps the old weekend window. A bare date
        # mention ("8月1号之前得定好") must not hijack the window.
        departure = _find_departure_date(text)
        explicit = departure or _ANY_DATE_RE.search(text)

        # Weekend calendar dates: rule_inferred + pending (do not auto-confirm).
        if not departure and has_affirmative_weekend(text):
            days_until_saturday = (5 - today.weekday()) % 7
            start = today + timedelta(days=days_until_saturday)
            next_weekend = self._mentioned_affirmatively(text, "下周末")
            if next_weekend:
                start += timedelta(days=7)
            evidence = "下周末" if next_weekend else "周末"
            contract.start_date = bind(
                start.isoformat(),
                "rule_inferred",
                "medium",
                pending=True,
                evidence=evidence,
            )
            contract.end_date = bind(
                (start + timedelta(days=1)).isoformat(),
                "rule_inferred",
                "medium",
                pending=True,
                evidence=evidence,
            )
            if not contract.travel_days.is_known():
                contract.travel_days = bind(2, "rule_inferred", "high", evidence=evidence)
            return

        if not explicit:
            return
        year = int(explicit.group(1) or today.year)
        month = int(explicit.group(2))
        day = int(explicit.group(3))
        try:
            start = date(year, month, day)
            if not explicit.group(1) and start < today:
                start = date(year + 1, month, day)
        except ValueError:
            return
        days = int(contract.travel_days.value or 1) if contract.travel_days.is_known() else 1
        contract.start_date = bind(
            start.isoformat(),
            "user_explicit",
            "high",
            evidence=explicit.group(0),
        )
        contract.end_date = bind(
            (start + timedelta(days=days - 1)).isoformat(),
            "user_explicit" if contract.travel_days.source == "user_explicit" else "rule_inferred",
            "high" if contract.travel_days.is_known() else "medium",
            pending=not contract.travel_days.is_known(),
            evidence=explicit.group(0),
        )
        contract.date_pattern = bind(
            "explicit", "user_explicit", "high", evidence=explicit.group(0)
        )

    def _apply_weekend_and_friday_semantics(
        self,
        text: str,
        contract: SemanticTripContract,
        *,
        today: date,
    ) -> None:
        """Attach weekend / Friday early-arrival semantics without silent 3-day expand."""
        # A stated departure date defines the window; a lingering 周末 mention
        # must not then label a Tuesday trip as "周六—周日" and offer a Friday card.
        stated_dates = (
            contract.start_date.is_known()
            and contract.start_date.source == "user_explicit"
            and not contract.start_date.pending_confirmation
        )
        has_weekend = has_affirmative_weekend(text) and not stated_dates
        friday_match = re.search(
            r"(?:周五|星期五)(?:下午|晚上|晚|傍晚|出发|走|去)?",
            text,
        )
        explicit_friday_depart = bool(
            friday_match
            and re.search(
                r"(?:周五|星期五).{0,6}(?:下午|晚上|晚|傍晚|出发|走)|"
                r"(?:下午|晚上|晚|傍晚).{0,4}(?:周五|星期五)",
                text,
            )
        ) or bool(
            friday_match and re.search(r"(?:周五|星期五).{0,8}(?:从|出发)", text)
        )

        if has_weekend and not explicit_friday_depart:
            contract.date_pattern = bind(
                "weekend", "rule_inferred", "high", evidence="周末"
            )
            contract.weekend_style = bind(
                "sat_sun", "rule_inferred", "high", evidence="周末默认六日"
            )
            if not contract.travel_days.is_known():
                contract.travel_days = bind(2, "rule_inferred", "high", evidence="周末")
            elif int(contract.travel_days.value or 0) != 2 and contract.travel_days.source == "rule_inferred":
                # Keep explicit user day count if stated (e.g. 玩三天 + 周末).
                pass
            # Advisory only — never upgrades travel_days to 3.
            contract.early_arrival_hint = bind(
                EARLY_ARRIVAL_HINT_DEFAULT,
                "rule_inferred",
                "high",
                evidence="周末短途建议",
            )
            # departure_mode stays unknown unless user selects Friday option.
            return

        if explicit_friday_depart:
            # Departure *slot* is user-explicit; calendar day may still be inferred.
            contract.date_pattern = bind(
                "explicit" if re.search(r"\d{1,2}月\d{1,2}", text) else "weekend",
                "user_explicit" if re.search(r"\d{1,2}月\d{1,2}", text) else "rule_inferred",
                "high",
                evidence=friday_match.group(0) if friday_match else "周五",
            )
            contract.weekend_style = bind(
                "fri_sun_optional",
                "user_explicit",
                "high",
                evidence="周五出发",
            )
            contract.departure_mode = bind(
                "evening_before",
                "user_explicit",
                "high",
                evidence="周五下午/晚上出发",
            )
            contract.early_arrival_hint = bind(
                EARLY_ARRIVAL_HINT_DEFAULT,
                "user_explicit",
                "high",
                evidence="周五出发",
            )
            # 3-day span is implied by Fri+weekend; concrete Y-M-D is rule_inferred+pending
            # unless the user also gave a calendar date.
            if has_weekend or not contract.travel_days.is_known():
                contract.travel_days = bind(
                    3, "user_explicit", "high", evidence="周五—周日三天"
                )
                has_calendar = bool(re.search(r"(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]", text))
                if not has_calendar:
                    days_until_friday = (4 - today.weekday()) % 7
                    friday = today + timedelta(days=days_until_friday)
                    if "下周末" in text or "下周五" in text:
                        friday = friday + timedelta(days=7)
                    sunday = friday + timedelta(days=2)
                    contract.start_date = bind(
                        friday.isoformat(),
                        "rule_inferred",
                        "medium",
                        pending=True,
                        evidence="推断最近周五（时段已明示，具体日期待确认）",
                    )
                    contract.end_date = bind(
                        sunday.isoformat(),
                        "rule_inferred",
                        "medium",
                        pending=True,
                        evidence="推断周五—周日",
                    )
            return

        if not contract.date_pattern.is_known():
            if contract.start_date.is_known() and contract.start_date.source == "user_explicit":
                contract.date_pattern = bind(
                    "explicit", "user_explicit", "high", evidence="明确日期"
                )
            else:
                contract.date_pattern = bind("unknown", "unknown", "low")


_semantic_contract_service: Optional[SemanticContractService] = None


def get_semantic_contract_service() -> SemanticContractService:
    global _semantic_contract_service
    if _semantic_contract_service is None:
        _semantic_contract_service = SemanticContractService()
    return _semantic_contract_service


USER_CONTRACT_ACK_MARKER = "[用户已确认待核对约束]"

CRITICAL_HARD_BLOCK_FIELDS = frozenset(
    {
        "origin_city",
        "destination_city",
        "start_date",
        "end_date",
        "travel_days",
        "travelers",
        "travel_party",
        "budget",
        "pace",
    }
)


def strip_contract_ack_marker(text: str | None) -> str:
    """Remove UI acknowledgment stamps before intent extraction."""
    raw = str(text or "")
    return raw.replace(USER_CONTRACT_ACK_MARKER, " ").strip()


def user_intent_text(text: str | None) -> str:
    """Free text reduced to user-authored intent, ready for extraction.

    Strips the acknowledgment stamp and the recommender's machine block so the
    system never re-reads its own advisory copy as a new user constraint.
    """
    return extract_user_utterance(strip_contract_ack_marker(text))


def attach_contract_to_trip_request(request: TripRequest) -> TripRequest:
    """Rebuild a server-owned contract from form fields + free text.

    Client-supplied contracts are ignored for authority: form values become
    form_confirmed, free-text extraction is merged with provenance rules.

    Generation still uses TripRequest field values as the execution source of
    truth; this contract is for validation, quality, and audit.
    """
    service = get_semantic_contract_service()
    form = RecommendationContext(
        origin_city=request.origin_city,
        budget=request.budget,
        travel_days=request.travel_days,
        travelers=request.travelers,
        start_date=request.start_date,
        end_date=request.end_date,
        preferences=list(request.preferences or []),
        transportation=request.transportation,
        accommodation=request.accommodation,
    )
    form_contract = service.contract_from_form(form)
    form_contract.destination_city = bind(
        request.city,
        "form_confirmed",
        "high",
        evidence="trip_request.city",
    )
    message_contract = service.extract_from_text(
        user_intent_text(request.free_text_input)
    )
    merged = service.merge(form_contract, message_contract)
    updates: Dict[str, Any] = {"semantic_contract": merged}
    # Surface weekend semantics onto TripRequest for planner consumption.
    if request.date_pattern is None and merged.date_pattern.is_known():
        updates["date_pattern"] = merged.date_pattern.value
    if request.weekend_style is None and merged.weekend_style.is_known():
        updates["weekend_style"] = merged.weekend_style.value
    if request.early_arrival_hint is None and merged.early_arrival_hint.is_known():
        updates["early_arrival_hint"] = str(merged.early_arrival_hint.value)
    if request.departure_mode is None and merged.departure_mode.is_known():
        # Only promote departure_mode when user-explicit / form-confirmed on request path
        if merged.departure_mode.source in PROTECTED_SOURCES or (
            request.travel_days == 3
            and str(merged.departure_mode.value) == "evening_before"
        ):
            updates["departure_mode"] = merged.departure_mode.value
    # If form already carries departure_mode / friday window, keep request values.
    if request.departure_mode is None and request.travel_days == 3:
        # Detect Fri-Sun window from dates.
        try:
            start = date.fromisoformat(request.start_date)
            end = date.fromisoformat(request.end_date)
            if (end - start).days + 1 == 3 and start.weekday() == 4:
                updates["departure_mode"] = "evening_before"
                updates.setdefault("weekend_style", "fri_sun_optional")
        except ValueError:
            pass
    return request.model_copy(update=updates)


def user_acknowledged_contract_risks(request: TripRequest) -> bool:
    if bool(getattr(request, "semantic_risks_acknowledged", False)):
        return True
    return USER_CONTRACT_ACK_MARKER in (request.free_text_input or "")


def _is_audit_only_conflict(note: str) -> bool:
    text = str(note or "")
    # Successful free-text overwrite notes are not blockers by themselves;
    # request/form divergence is checked separately against user_explicit values.
    if "最新用户明示" in text and "覆盖旧值" in text:
        return True
    if "覆盖规则/默认值" in text:
        return True
    # Form calendar already won over weekend/date inference — not a ship-stopper.
    if re.match(r"^(start_date|end_date|travel_days)\s*:", text) and "保留前者" in text:
        return True
    if "与日期窗口" in text:
        return True
    # The user reversed their own exclusion — already resolved, just recorded.
    if "以最新选择为准" in text:
        return True
    # Notes about non-critical fields never block generation, mirroring
    # ``_pending_is_hard_block``.
    field = re.match(r"^([a-z_]+)\s*:", text)
    if field and field.group(1) not in CRITICAL_HARD_BLOCK_FIELDS:
        return True
    return False


FIELD_LABELS: Dict[str, str] = {
    "origin_city": "出发地",
    "destination_city": "目的地",
    "start_date": "开始日期",
    "end_date": "结束日期",
    "travel_days": "天数",
    "travelers": "人数",
    "travel_party": "同行关系",
    "budget": "预算",
    "pace": "节奏",
    "preferences": "偏好",
    "transportation": "交通方式",
    "accommodation": "住宿",
    "date_pattern": "日期模式",
    "weekend_style": "周末形态",
    "departure_mode": "出发时段",
    "early_arrival_hint": "提前抵达建议",
    "destination_scope": "目的地范围",
    "excluded_destinations": "排除的目的地",
    "excluded_themes": "排除的类型",
}


def field_label(name: str) -> str:
    """Chinese label for a contract field; never leak the raw key to users."""
    return FIELD_LABELS.get(name, name)


def humanize_conflict(note: str) -> str:
    """Replace the machine field prefix in a conflict note with its label.

    The stored notes keep the raw ``field:`` prefix because the audit-only
    rules match on it; only what reaches a person is relabelled.
    """
    text = str(note or "")
    match = re.match(r"^([a-z_]+)\s*:\s*", text)
    if not match:
        return text
    return f"{field_label(match.group(1))}：{text[match.end():]}"


def blocking_conflicts(contract: SemanticTripContract | None) -> list[str]:
    """Conflicts the user still has to resolve, in user-facing wording.

    Single definition shared by the hard-block gate, the recommender reply and
    the frontend banner, so one contract cannot read as "conflicting" in one
    place and "clean" in another.
    """
    if contract is None:
        return []
    return [
        humanize_conflict(note)
        for note in contract.conflicts
        if note and not _is_audit_only_conflict(note)
    ]


def _field_resolved_by_request(field: str, request: TripRequest) -> bool:
    """Mirror frontend form-resolution: filled request fields clear pending."""
    if field in {"start_date", "end_date", "travel_days"}:
        return bool(request.start_date and request.end_date and request.travel_days)
    if field == "travelers":
        return request.travelers is not None and request.travelers >= 1
    if field == "budget":
        return request.budget is not None and request.budget > 0
    if field == "origin_city":
        return bool((request.origin_city or "").strip())
    if field == "destination_city":
        return bool((request.city or "").strip())
    if field == "travel_party":
        return request.travelers is not None and request.travelers >= 1
    if field == "pace":
        # 【理由】 is generated copy — it must not resolve a pending pace on the
        # user's behalf. Only decided constraints and the user's own words count.
        text = decided_constraint_text(request.free_text_input)
        prefs = set(request.preferences or [])
        return "休闲" in prefs or bool(
            re.search(r"轻松|慢|父母|爸妈|避暑|不想太累", text)
        )
    return False


def _pending_is_hard_block(
    field: str,
    binding: FieldBinding,
    request: TripRequest,
) -> bool:
    """Optional unknowns (no budget / no origin) must not block generation."""
    if field not in CRITICAL_HARD_BLOCK_FIELDS:
        return False
    if _field_resolved_by_request(field, request):
        return False
    # Optional fields only block when a concrete value is still unconfirmed.
    if field in {"budget", "origin_city", "pace", "travel_party"}:
        return binding.is_known() and binding.pending_confirmation
    return binding.pending_confirmation or not binding.is_known()


def _request_value(request: TripRequest, field: str) -> Any:
    if field == "destination_city":
        return request.city
    return getattr(request, field, None)


def _normalize_compare(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in {"origin_city", "destination_city"}:
        # Share the planner's canonical form so "山西太原" and "太原" compare
        # equal — otherwise a province-prefixed utterance hard-blocks the form.
        from .destination_feasibility_service import (
            get_destination_feasibility_service,
        )

        return get_destination_feasibility_service().normalize_location_for_matching(
            value
        )
    if field in {"budget", "travelers", "travel_days"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def collect_free_text_form_divergences(
    request: TripRequest,
    message_contract: SemanticTripContract,
) -> list[dict[str, Any]]:
    """Hard-block when free-text user_explicit disagrees with form fields.

    Generation uses TripRequest fields. Free-text must not silently disagree
    with those fields after merge promoted free-text into the contract only.
    """
    issues: list[dict[str, Any]] = []
    pairs = [
        ("origin_city", "出发地"),
        ("budget", "预算"),
        ("travelers", "人数"),
        ("travel_days", "天数"),
        ("start_date", "开始日期"),
        ("end_date", "结束日期"),
    ]
    diverged: list[str] = []
    details: list[str] = []
    for field, label in pairs:
        binding: FieldBinding = getattr(message_contract, field)
        if not binding.is_known():
            continue
        # Only high-confidence explicit utterances force form alignment.
        if binding.source != "user_explicit" or binding.confidence != "high":
            continue
        if binding.pending_confirmation:
            continue
        req_val = _request_value(request, field)
        if req_val is None or req_val == "":
            # Form left optional empty while free-text states a value → require align/ack
            diverged.append(field)
            details.append(f"{label}: 表单未填，原文为 {binding.value!r}")
            continue
        left = _normalize_compare(field, req_val)
        right = _normalize_compare(field, binding.value)
        if left != right:
            diverged.append(field)
            details.append(f"{label}: 表单 {req_val!r} ≠ 原文 {binding.value!r}")

    if diverged:
        issues.append(
            {
                "code": "SEMANTIC_FORM_FREE_TEXT_DIVERGENCE",
                "severity": "error",
                "path": "semantic_contract",
                "message": (
                    "表单与额外要求中的明确表述不一致："
                    + "；".join(details[:4])
                ),
                "suggestion": (
                    "请改表单与原文一致，或二次确认后按当前表单生成"
                    f"（semantic_risks_acknowledged / {USER_CONTRACT_ACK_MARKER}）。"
                ),
                "auto_repaired": False,
                "fields": diverged,
                "details": details[:6],
            }
        )
    return issues


def collect_semantic_hard_block_issues(
    request: TripRequest,
) -> list[dict[str, Any]]:
    """Return structured 422 issues for unresolved critical contract risks.

    Empty list means generation may proceed. Acknowledgment (boolean flag or
    free_text marker) skips the hard block after frontend secondary confirm.
    """
    if user_acknowledged_contract_risks(request):
        return []

    service = get_semantic_contract_service()
    message_contract = service.extract_from_text(
        user_intent_text(request.free_text_input)
    )
    attached = attach_contract_to_trip_request(request)
    contract = attached.semantic_contract
    if contract is None:
        return []

    issues: list[dict[str, Any]] = []
    issues.extend(collect_free_text_form_divergences(request, message_contract))

    pending: list[str] = []
    for name in CRITICAL_HARD_BLOCK_FIELDS:
        binding: FieldBinding = getattr(contract, name, None)
        if not isinstance(binding, FieldBinding):
            continue
        listed = name in contract.pending_fields or binding.pending_confirmation
        if listed and _pending_is_hard_block(name, binding, request):
            pending.append(name)

    if pending:
        labels = "、".join(field_label(name) for name in pending[:8])
        issues.append(
            {
                "code": "SEMANTIC_CONTRACT_PENDING",
                "severity": "error",
                "path": "semantic_contract.pending_fields",
                "message": f"仍有关键字段待确认：{labels}。",
                "suggestion": (
                    "请在表单中确认这些字段，或在二次确认后继续生成"
                    f"（semantic_risks_acknowledged / {USER_CONTRACT_ACK_MARKER}）。"
                ),
                "auto_repaired": False,
                "fields": pending,
            }
        )

    unresolved = blocking_conflicts(contract)
    if unresolved:
        issues.append(
            {
                "code": "SEMANTIC_CONTRACT_CONFLICT_BLOCK",
                "severity": "error",
                "path": "semantic_contract.conflicts",
                "message": (
                    f"语义契约存在 {len(unresolved)} 条未消解冲突："
                    f"{unresolved[0]}"
                ),
                "suggestion": (
                    "请核对出发地/人数/预算等表单取值，或二次确认后继续生成"
                    f"（semantic_risks_acknowledged / {USER_CONTRACT_ACK_MARKER}）。"
                ),
                "auto_repaired": False,
                "conflicts": unresolved[:5],
            }
        )

    return issues
