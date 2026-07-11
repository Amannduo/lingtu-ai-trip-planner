"""Seed the local SQLite travel dataset with realistic synthetic records.

The dataset is synthetic, but it follows plausible travel behavior:
- most users have only 1-3 trip plans;
- destination choices vary by season;
- budget depends on city cost, travelers, days, transport and accommodation;
- preferences are correlated with destination and traveler profile.

Usage:
  python backend/scripts/init_travel_sqlite.py --rows 10000
  python backend/scripts/init_travel_sqlite.py --rows 10000 --reset
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "backend" / "data" / "travel.db"
sys.path.insert(0, str(ROOT_DIR))


DESTINATIONS = {
    "北京": {
        "tags": ["历史文化", "博物馆", "亲子"],
        "cost": 1.28,
        "seasons": [4, 5, 9, 10],
        "transport": ["公共交通", "混合"],
        "stay": ["舒适型酒店", "亲子酒店", "经济型酒店"],
    },
    "上海": {
        "tags": ["购物", "艺术", "城市漫步"],
        "cost": 1.35,
        "seasons": [3, 4, 5, 10, 11],
        "transport": ["公共交通", "混合"],
        "stay": ["舒适型酒店", "豪华酒店", "经济型酒店"],
    },
    "成都": {
        "tags": ["美食", "休闲", "历史文化"],
        "cost": 0.96,
        "seasons": [3, 4, 5, 9, 10, 11],
        "transport": ["公共交通", "混合"],
        "stay": ["舒适型酒店", "民宿", "经济型酒店"],
    },
    "重庆": {
        "tags": ["美食", "夜景", "城市漫步"],
        "cost": 0.92,
        "seasons": [3, 4, 5, 9, 10, 11],
        "transport": ["公共交通", "步行", "混合"],
        "stay": ["舒适型酒店", "民宿", "经济型酒店"],
    },
    "杭州": {
        "tags": ["自然风光", "休闲", "艺术"],
        "cost": 1.12,
        "seasons": [3, 4, 5, 9, 10],
        "transport": ["公共交通", "步行", "混合"],
        "stay": ["舒适型酒店", "民宿", "豪华酒店"],
    },
    "南京": {
        "tags": ["历史文化", "美食", "博物馆"],
        "cost": 1.02,
        "seasons": [3, 4, 5, 9, 10, 11],
        "transport": ["公共交通", "混合"],
        "stay": ["舒适型酒店", "经济型酒店"],
    },
    "西安": {
        "tags": ["历史文化", "美食", "深度体验"],
        "cost": 0.9,
        "seasons": [3, 4, 5, 9, 10],
        "transport": ["公共交通", "混合"],
        "stay": ["经济型酒店", "舒适型酒店", "民宿"],
    },
    "厦门": {
        "tags": ["海滨休闲", "摄影", "轻松低强度"],
        "cost": 1.08,
        "seasons": [3, 4, 5, 10, 11, 12],
        "transport": ["公共交通", "步行", "混合"],
        "stay": ["民宿", "舒适型酒店", "豪华酒店"],
    },
    "苏州": {
        "tags": ["园林", "历史文化", "轻松低强度"],
        "cost": 1.0,
        "seasons": [3, 4, 5, 9, 10],
        "transport": ["步行", "公共交通", "混合"],
        "stay": ["舒适型酒店", "民宿"],
    },
    "长沙": {
        "tags": ["美食", "夜生活", "购物"],
        "cost": 0.88,
        "seasons": [3, 4, 5, 9, 10, 11],
        "transport": ["公共交通", "混合"],
        "stay": ["经济型酒店", "舒适型酒店"],
    },
    "广州": {
        "tags": ["美食", "购物", "城市漫步"],
        "cost": 1.02,
        "seasons": [1, 2, 3, 10, 11, 12],
        "transport": ["公共交通", "混合"],
        "stay": ["舒适型酒店", "经济型酒店", "豪华酒店"],
    },
    "桂林": {
        "tags": ["自然风光", "摄影", "轻户外"],
        "cost": 0.86,
        "seasons": [4, 5, 6, 9, 10],
        "transport": ["自驾", "混合"],
        "stay": ["民宿", "舒适型酒店"],
    },
    "青岛": {
        "tags": ["海滨休闲", "美食", "摄影"],
        "cost": 1.05,
        "seasons": [6, 7, 8, 9],
        "transport": ["公共交通", "自驾", "混合"],
        "stay": ["舒适型酒店", "民宿", "豪华酒店"],
    },
    "大理": {
        "tags": ["自然风光", "休闲", "摄影"],
        "cost": 0.98,
        "seasons": [3, 4, 5, 9, 10, 11],
        "transport": ["自驾", "混合"],
        "stay": ["民宿", "舒适型酒店"],
    },
}

ORIGIN_CITIES = ["北京", "上海", "广州", "深圳", "杭州", "南京", "武汉", "成都", "西安", "长沙", "苏州", "天津", "郑州", "合肥"]
DESTINATIONS.update(
    {
        "深圳": {
            "tags": ["城市漫步", "购物", "艺术"],
            "cost": 1.22,
            "seasons": [1, 2, 3, 10, 11, 12],
            "transport": ["公共交通", "混合"],
            "stay": ["舒适型酒店", "豪华酒店", "经济型酒店"],
        },
        "武汉": {
            "tags": ["美食", "历史文化", "城市漫步"],
            "cost": 0.94,
            "seasons": [3, 4, 5, 9, 10, 11],
            "transport": ["公共交通", "混合"],
            "stay": ["经济型酒店", "舒适型酒店", "民宿"],
        },
        "天津": {
            "tags": ["历史文化", "美食", "城市漫步"],
            "cost": 0.98,
            "seasons": [4, 5, 9, 10],
            "transport": ["公共交通", "步行", "混合"],
            "stay": ["经济型酒店", "舒适型酒店"],
        },
        "哈尔滨": {
            "tags": ["冰雪体验", "美食", "摄影"],
            "cost": 1.04,
            "seasons": [1, 2, 12],
            "transport": ["公共交通", "混合"],
            "stay": ["舒适型酒店", "经济型酒店"],
        },
        "沈阳": {
            "tags": ["历史文化", "美食", "博物馆"],
            "cost": 0.86,
            "seasons": [5, 6, 9, 10],
            "transport": ["公共交通", "混合"],
            "stay": ["经济型酒店", "舒适型酒店"],
        },
        "济南": {
            "tags": ["历史文化", "城市漫步", "美食"],
            "cost": 0.9,
            "seasons": [4, 5, 9, 10],
            "transport": ["公共交通", "步行"],
            "stay": ["经济型酒店", "舒适型酒店"],
        },
        "郑州": {
            "tags": ["历史文化", "美食", "高性价比"],
            "cost": 0.84,
            "seasons": [4, 5, 9, 10],
            "transport": ["公共交通", "混合"],
            "stay": ["经济型酒店", "舒适型酒店"],
        },
        "合肥": {
            "tags": ["城市漫步", "美食", "高性价比"],
            "cost": 0.82,
            "seasons": [3, 4, 5, 9, 10, 11],
            "transport": ["公共交通", "混合"],
            "stay": ["经济型酒店", "舒适型酒店"],
        },
        "福州": {
            "tags": ["美食", "历史文化", "轻松低强度"],
            "cost": 0.95,
            "seasons": [3, 4, 5, 10, 11, 12],
            "transport": ["公共交通", "步行", "混合"],
            "stay": ["舒适型酒店", "民宿", "经济型酒店"],
        },
        "泉州": {
            "tags": ["历史文化", "美食", "深度体验"],
            "cost": 0.9,
            "seasons": [3, 4, 5, 10, 11, 12],
            "transport": ["步行", "公共交通", "混合"],
            "stay": ["民宿", "经济型酒店", "舒适型酒店"],
        },
        "三亚": {
            "tags": ["海滨休闲", "品质住宿", "摄影"],
            "cost": 1.42,
            "seasons": [1, 2, 3, 11, 12],
            "transport": ["自驾", "混合"],
            "stay": ["豪华酒店", "舒适型酒店", "民宿"],
        },
        "海口": {
            "tags": ["海滨休闲", "美食", "轻松低强度"],
            "cost": 1.08,
            "seasons": [1, 2, 3, 11, 12],
            "transport": ["公共交通", "自驾", "混合"],
            "stay": ["舒适型酒店", "民宿", "经济型酒店"],
        },
        "昆明": {
            "tags": ["自然风光", "美食", "休闲"],
            "cost": 0.96,
            "seasons": [3, 4, 5, 9, 10, 11],
            "transport": ["公共交通", "混合"],
            "stay": ["民宿", "舒适型酒店", "经济型酒店"],
        },
        "丽江": {
            "tags": ["自然风光", "摄影", "休闲"],
            "cost": 1.0,
            "seasons": [3, 4, 5, 9, 10, 11],
            "transport": ["自驾", "混合"],
            "stay": ["民宿", "舒适型酒店"],
        },
        "贵阳": {
            "tags": ["自然风光", "美食", "高性价比"],
            "cost": 0.84,
            "seasons": [5, 6, 7, 8, 9],
            "transport": ["公共交通", "混合"],
            "stay": ["经济型酒店", "舒适型酒店", "民宿"],
        },
        "张家界": {
            "tags": ["自然风光", "摄影", "轻户外"],
            "cost": 0.98,
            "seasons": [4, 5, 6, 9, 10],
            "transport": ["自驾", "混合"],
            "stay": ["民宿", "舒适型酒店"],
        },
        "黄山": {
            "tags": ["自然风光", "摄影", "轻户外"],
            "cost": 0.96,
            "seasons": [4, 5, 9, 10, 11],
            "transport": ["自驾", "混合"],
            "stay": ["民宿", "舒适型酒店", "经济型酒店"],
        },
        "洛阳": {
            "tags": ["历史文化", "博物馆", "美食"],
            "cost": 0.82,
            "seasons": [4, 5, 9, 10],
            "transport": ["公共交通", "混合"],
            "stay": ["经济型酒店", "舒适型酒店"],
        },
        "乌鲁木齐": {
            "tags": ["自然风光", "美食", "深度体验"],
            "cost": 1.18,
            "seasons": [6, 7, 8, 9],
            "transport": ["自驾", "混合"],
            "stay": ["舒适型酒店", "经济型酒店"],
        },
        "拉萨": {
            "tags": ["自然风光", "深度体验", "摄影"],
            "cost": 1.25,
            "seasons": [5, 6, 7, 8, 9, 10],
            "transport": ["自驾", "混合"],
            "stay": ["舒适型酒店", "民宿"],
        },
        "呼和浩特": {
            "tags": ["自然风光", "美食", "轻户外"],
            "cost": 0.92,
            "seasons": [6, 7, 8, 9],
            "transport": ["自驾", "混合"],
            "stay": ["经济型酒店", "舒适型酒店", "民宿"],
        },
    }
)

ORIGIN_CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "南京", "武汉", "成都", "西安", "长沙",
    "苏州", "天津", "郑州", "合肥", "重庆", "青岛", "厦门", "济南", "福州", "昆明",
    "宁波", "无锡", "南昌", "南宁", "太原", "石家庄", "兰州", "贵阳", "沈阳", "哈尔滨",
]
USER_TYPES = {
    "美食休闲型": ["美食", "休闲", "轻松低强度"],
    "文化探索型": ["历史文化", "博物馆", "深度体验"],
    "风光摄影型": ["自然风光", "摄影", "轻户外"],
    "亲子陪伴型": ["亲子", "轻松低强度", "品质住宿"],
    "高性价比型": ["高性价比", "美食", "城市漫步"],
    "品质度假型": ["品质住宿", "休闲", "海滨休闲"],
    "城市漫游型": ["城市漫步", "购物", "艺术"],
}
ROLE_WEIGHTS = [("guest", 0.05), ("user", 0.78), ("manager", 0.13), ("admin", 0.04)]
ACCOMMODATION_FACTOR = {
    "经济型酒店": 0.75,
    "舒适型酒店": 1.0,
    "豪华酒店": 1.65,
    "民宿": 0.9,
    "亲子酒店": 1.28,
}
TRANSPORT_FACTOR = {
    "步行": 0.62,
    "公共交通": 0.82,
    "混合": 1.0,
    "自驾": 1.18,
}


@dataclass(frozen=True)
class UserProfileSeed:
    user_id: str
    user_role: str
    traveler_type: str
    base_tags: list[str]
    home_city: str


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    from backend.app.services.schema import SCHEMA_SQL

    conn.executescript(SCHEMA_SQL)
    conn.commit()


def weighted_choice(items: list[tuple[str, float]], rng: random.Random) -> str:
    values = [item[0] for item in items]
    weights = [item[1] for item in items]
    return rng.choices(values, weights=weights, k=1)[0]


def make_users(target_rows: int, rng: random.Random) -> list[UserProfileSeed]:
    # Most users have 1-3 trips, so rows / ~2.2 gives a plausible user count.
    user_count = max(320, int(target_rows / 2.2))
    user_types = list(USER_TYPES.keys())
    type_weights = [0.2, 0.18, 0.16, 0.11, 0.14, 0.12, 0.09]
    users: list[UserProfileSeed] = []
    for index in range(1, user_count + 1):
        traveler_type = rng.choices(user_types, weights=type_weights, k=1)[0]
        role = weighted_choice(ROLE_WEIGHTS, rng)
        users.append(
            UserProfileSeed(
                user_id=f"u_{index:04d}",
                user_role=role,
                traveler_type=traveler_type,
                base_tags=list(USER_TYPES[traveler_type]),
                home_city=rng.choice(ORIGIN_CITIES),
            )
        )
    return users


def choose_trip_count(rng: random.Random) -> int:
    return rng.choices([1, 2, 3, 4, 5], weights=[0.48, 0.29, 0.15, 0.06, 0.02], k=1)[0]


def choose_destination(user: UserProfileSeed, rng: random.Random, month: int) -> str:
    weighted: list[tuple[str, float]] = []
    for city, profile in DESTINATIONS.items():
        if city == user.home_city:
            base = 0.18
        else:
            base = 1.0
        overlap = len(set(user.base_tags) & set(profile["tags"]))
        season_bonus = 1.45 if month in profile["seasons"] else 0.78
        weighted.append((city, base * season_bonus * (1 + overlap * 0.55)))
    return weighted_choice(weighted, rng)


def choose_start_date(rng: random.Random) -> date:
    today = date.today()
    # Use the last 18 months, with stronger travel periods around holidays.
    base = today - timedelta(days=rng.randint(0, 540))
    if rng.random() < 0.26:
        month_day = rng.choice([(1, 1), (4, 5), (5, 1), (6, 10), (10, 1)])
        year = rng.choice([today.year, today.year - 1])
        try:
            holiday = date(year, month_day[0], month_day[1])
            return holiday + timedelta(days=rng.randint(0, 5))
        except ValueError:
            return base
    return base


def choose_days(rng: random.Random, destination: str, month: int) -> int:
    if month in (1, 2, 7, 8, 10):
        weights = [0.04, 0.15, 0.28, 0.24, 0.16, 0.08, 0.05]
    elif destination in ("大理", "桂林", "青岛"):
        weights = [0.03, 0.12, 0.25, 0.27, 0.2, 0.09, 0.04]
    else:
        weights = [0.07, 0.22, 0.36, 0.21, 0.09, 0.04, 0.01]
    return rng.choices([1, 2, 3, 4, 5, 6, 7], weights=weights, k=1)[0]


def choose_travelers(user: UserProfileSeed, rng: random.Random) -> int:
    if user.traveler_type == "亲子陪伴型":
        return rng.choices([2, 3, 4, 5], weights=[0.18, 0.32, 0.38, 0.12], k=1)[0]
    return rng.choices([1, 2, 3, 4], weights=[0.25, 0.48, 0.18, 0.09], k=1)[0]


def choose_preferences(user: UserProfileSeed, destination: str, rng: random.Random) -> list[str]:
    pool = list(dict.fromkeys(user.base_tags + DESTINATIONS[destination]["tags"] + [
        "美食",
        "历史文化",
        "自然风光",
        "购物",
        "休闲",
        "摄影",
        "城市漫步",
        "轻松低强度",
        "深度体验",
        "高性价比",
    ]))
    count = rng.choices([2, 3, 4], weights=[0.35, 0.48, 0.17], k=1)[0]
    prefs: list[str] = []
    for tag in user.base_tags + DESTINATIONS[destination]["tags"]:
        if tag not in prefs and rng.random() < 0.72:
            prefs.append(tag)
        if len(prefs) >= count:
            break
    while len(prefs) < count:
        tag = rng.choice(pool)
        if tag not in prefs:
            prefs.append(tag)
    return prefs


def choose_accommodation(user: UserProfileSeed, destination: str, rng: random.Random) -> str:
    options = DESTINATIONS[destination]["stay"]
    if user.traveler_type == "高性价比型":
        options = ["经济型酒店", "民宿", "舒适型酒店"]
    elif user.traveler_type == "品质度假型":
        options = ["豪华酒店", "舒适型酒店", "民宿"]
    elif user.traveler_type == "亲子陪伴型":
        options = ["亲子酒店", "舒适型酒店", "豪华酒店"]
    return rng.choice(options)


def compute_budget(
    destination: str,
    days: int,
    travelers: int,
    accommodation: str,
    transportation: str,
    user: UserProfileSeed,
    rng: random.Random,
) -> tuple[float, float]:
    city_cost = DESTINATIONS[destination]["cost"]
    hotel_factor = ACCOMMODATION_FACTOR.get(accommodation, 1.0)
    transport_factor = TRANSPORT_FACTOR.get(transportation, 1.0)
    base_daily = rng.uniform(360, 760) * city_cost * hotel_factor
    if user.traveler_type == "高性价比型":
        base_daily *= rng.uniform(0.72, 0.88)
    elif user.traveler_type == "品质度假型":
        base_daily *= rng.uniform(1.18, 1.46)
    elif user.traveler_type == "亲子陪伴型":
        base_daily *= rng.uniform(1.05, 1.22)

    intercity_cost = rng.uniform(260, 950) * travelers * transport_factor
    budget = base_daily * days * travelers + intercity_cost
    budget = round(budget / 50) * 50
    actual_cost = budget * rng.uniform(0.86, 1.16)
    return float(round(budget, 2)), float(round(actual_cost, 2))


def classify_profile(tags: list[str], avg_budget: float, avg_days: float) -> str:
    tag_text = " ".join(tags)
    if "亲子" in tag_text:
        return "亲子陪伴型"
    if "历史文化" in tag_text or "博物馆" in tag_text:
        return "文化探索型"
    if "自然风光" in tag_text or "摄影" in tag_text:
        return "风光摄影型"
    if "美食" in tag_text and ("休闲" in tag_text or "轻松低强度" in tag_text):
        return "美食休闲型"
    if "高性价比" in tag_text or (avg_budget and avg_days and avg_budget / max(avg_days, 1) < 800):
        return "高性价比型"
    if avg_budget and avg_days and avg_budget / max(avg_days, 1) > 1400:
        return "品质度假型"
    return "城市漫游型"


def refresh_profiles(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT * FROM travel_plans ORDER BY created_at").fetchall()
    by_user: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_user[row["user_id"]].append(row)

    conn.execute("DELETE FROM user_profiles")
    for user_id, plans in by_user.items():
        tags: list[str] = []
        cities = []
        budgets = []
        days = []
        for plan in plans:
            cities.append(plan["destination"])
            if plan["budget"]:
                budgets.append(float(plan["budget"]))
            if plan["travel_days"]:
                days.append(int(plan["travel_days"]))
            try:
                tags.extend(json.loads(plan["preferences"] or "[]"))
            except json.JSONDecodeError:
                pass

        top_tags = [item for item, _ in Counter(tags).most_common(5)]
        fav_cities = [item for item, _ in Counter(cities).most_common(5)]
        avg_budget = sum(budgets) / len(budgets) if budgets else 0
        avg_days = sum(days) / len(days) if days else 0
        conn.execute(
            """
            INSERT OR REPLACE INTO user_profiles
            (user_id, plan_count, top_tags, fav_cities, avg_budget, avg_days, traveler_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                user_id,
                len(plans),
                json.dumps(top_tags, ensure_ascii=False),
                json.dumps(fav_cities, ensure_ascii=False),
                round(avg_budget, 2),
                round(avg_days, 2),
                classify_profile(top_tags, avg_budget, avg_days),
            ),
        )
    conn.commit()
    return len(by_user)


def seed(conn: sqlite3.Connection, rows: int, seed_value: int, reset: bool) -> None:
    rng = random.Random(seed_value)
    init_schema(conn)
    if reset:
        conn.executescript(
            """
            DELETE FROM query_logs;
            DELETE FROM audit_logs;
            DELETE FROM user_profiles;
            DELETE FROM travel_plans;
            """
        )
        conn.commit()
        print("[seed] existing SQLite dataset cleared")

    existing = conn.execute("SELECT COUNT(*) FROM travel_plans").fetchone()[0]
    if existing >= rows:
        profile_count = refresh_profiles(conn)
        print(f"[seed] SQLite already has {existing} rows; refreshed {profile_count} profiles")
        return

    users = make_users(rows, rng)
    inserted = 0
    user_cursor = 0
    while existing + inserted < rows:
        user = users[user_cursor % len(users)]
        user_cursor += 1
        for _ in range(choose_trip_count(rng)):
            if existing + inserted >= rows:
                break
            start = choose_start_date(rng)
            destination = choose_destination(user, rng, start.month)
            days = choose_days(rng, destination, start.month)
            end = start + timedelta(days=days - 1)
            travelers = choose_travelers(user, rng)
            transportation = rng.choice(DESTINATIONS[destination]["transport"])
            accommodation = choose_accommodation(user, destination, rng)
            preferences = choose_preferences(user, destination, rng)
            budget, actual_cost = compute_budget(destination, days, travelers, accommodation, transportation, user, rng)
            created_at = datetime.combine(start - timedelta(days=rng.randint(7, 60)), datetime.min.time()) + timedelta(
                hours=rng.randint(8, 23),
                minutes=rng.randint(0, 59),
            )
            plan_no = f"LS{created_at.strftime('%Y%m%d')}-{existing + inserted + 1:06d}-{uuid.uuid4().hex[:4].upper()}"
            pace = rng.choice(["不要太赶", "适合拍照", "晚上安排少一点", "景点之间交通方便", "留出自由活动时间", "适合第一次去"])
            purpose = rng.choice(["周末放松", "年假旅行", "亲友同行", "错峰出游", "短途散心", "城市探索", "假期打卡"])
            meal_note = rng.choice(["想多安排本地小吃", "餐饮不要太贵", "希望有特色餐厅", "饮食清淡一点", "不特别安排正餐"])
            free_text = f"{purpose}，偏好{','.join(preferences[:3])}，{pace}，{meal_note}。"
            summary = (
                f"{destination}{days}天旅行，{travelers}人出行，"
                f"{accommodation}，{transportation}为主，预算约{int(budget)}元，"
                f"关注{','.join(preferences[:2])}。"
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO travel_plans
                (plan_no, user_id, user_role, origin_city, destination, start_date, end_date,
                 travel_days, travelers, budget, actual_cost, transportation, accommodation,
                 preferences, free_text, summary, status, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_no,
                    user.user_id,
                    user.user_role,
                    user.home_city if user.home_city != destination else rng.choice([c for c in ORIGIN_CITIES if c != destination]),
                    destination,
                    start.isoformat(),
                    end.isoformat(),
                    days,
                    travelers,
                    budget,
                    actual_cost,
                    transportation,
                    accommodation,
                    json.dumps(preferences, ensure_ascii=False),
                    free_text,
                    summary,
                    rng.choices(["completed", "completed", "completed", "draft", "cancelled"], weights=[0.84, 0.06, 0.1, 0.04, 0.02], k=1)[0],
                    "realistic_synthetic",
                    created_at.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            inserted += 1
    conn.commit()
    profile_count = refresh_profiles(conn)
    print(f"[seed] inserted {inserted} realistic synthetic rows")
    print(f"[seed] total travel_plans={existing + inserted}, user_profiles={profile_count}")


def print_summary(conn: sqlite3.Connection) -> None:
    plan_count = conn.execute("SELECT COUNT(*) FROM travel_plans").fetchone()[0]
    user_count = conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
    print(f"[summary] travel_plans={plan_count}, user_profiles={user_count}")
    print("[summary] top destinations:")
    for row in conn.execute(
        """
        SELECT destination, COUNT(*) AS count, ROUND(AVG(budget), 0) AS avg_budget
        FROM travel_plans
        GROUP BY destination
        ORDER BY count DESC
        LIMIT 8
        """
    ):
        print(f"  - {row['destination']}: {row['count']} plans, avg_budget={row['avg_budget']}")
    print("[summary] traveler types:")
    for row in conn.execute(
        "SELECT traveler_type, COUNT(*) AS count FROM user_profiles GROUP BY traveler_type ORDER BY count DESC"
    ):
        print(f"  - {row['traveler_type']}: {row['count']} users")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SQLite travel dataset with realistic synthetic data.")
    parser.add_argument("--rows", type=int, default=10000, help="Target number of travel plan rows.")
    parser.add_argument("--seed", type=int, default=20260711, help="Random seed.")
    parser.add_argument("--reset", action="store_true", help="Clear existing travel data before seeding.")
    args = parser.parse_args()

    with connect() as conn:
        seed(conn, max(args.rows, 10000), args.seed, args.reset)
        print_summary(conn)


if __name__ == "__main__":
    main()
