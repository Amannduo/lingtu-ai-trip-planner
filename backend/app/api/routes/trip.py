"""旅行规划API路由"""

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    ErrorResponse
)
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...services.travel_plan_data_service import get_travel_plan_data_service

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
async def plan_trip(request: TripRequest, http_request: Request):
    """
    生成旅行计划

    Args:
        request: 旅行请求参数

    Returns:
        旅行计划响应
    """
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        # 获取Agent实例
        print("🔄 获取多智能体系统实例...")
        agent = get_trip_planner_agent()

        # 生成旅行计划
        print("🚀 开始生成旅行计划...")
        trip_plan = await run_in_threadpool(agent.plan_trip, request)

        print("✅ 旅行计划生成成功,准备返回响应\n")

        try:
            user_id = http_request.headers.get("x-user-id") or "u_current"
            raw_role = (http_request.headers.get("x-user-role") or "user").strip().lower()
            # Only allow known role values; fall back to "user" for unknown ones
            if raw_role not in ("guest", "user", "manager", "admin"):
                print(f"[trip] unknown x-user-role '{raw_role}', falling back to 'user'")
                raw_role = "user"
            if raw_role in ("admin", "manager"):
                print(f"[trip] x-user-role={raw_role} — no auth middleware, trusting header")
            user_role = raw_role
            plan_no = get_travel_plan_data_service().save_trip_plan(
                request,
                trip_plan,
                user_id=user_id,
                user_role=user_role,
                source="generated"
            )
            print(f"[trip] travel plan saved to dataset: {plan_no}")
        except Exception as save_error:
            print(f"[trip] save travel plan skipped (not critical): {save_error}")

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan
        )

    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.get(
    "/history",
    summary="查询个人旅行历史",
    description="返回当前用户的历史行程列表和统计数据"
)
async def trip_history(user_id: str = "u_current", limit: int = 20):
    """查询用户历史行程和统计"""
    from ...services.database_service import fetch_all, fetch_one
    from ...services.schema import init_db

    init_db()
    trips = fetch_all(
        "SELECT plan_no, destination, start_date, end_date, travel_days, "
        "budget, transportation, summary, created_at "
        "FROM travel_plans WHERE user_id = :uid ORDER BY created_at DESC LIMIT :lim",
        {"uid": user_id, "lim": limit},
    )
    stats = fetch_one(
        "SELECT COUNT(*) AS total, ROUND(AVG(budget), 0) AS avg_budget, "
        "SUM(travel_days) AS total_days "
        "FROM travel_plans WHERE user_id = :uid",
        {"uid": user_id},
    )
    fav = fetch_all(
        "SELECT destination AS city, COUNT(*) AS count "
        "FROM travel_plans WHERE user_id = :uid "
        "GROUP BY destination ORDER BY count DESC LIMIT 5",
        {"uid": user_id},
    )
    return {
        "success": True,
        "user_id": user_id,
        "stats": {
            "total_trips": stats.get("total", 0) if stats else 0,
            "avg_budget": stats.get("avg_budget", 0) if stats else 0,
            "total_days": stats.get("total_days", 0) if stats else 0,
        } if stats else {"total_trips": 0, "avg_budget": 0, "total_days": 0},
        "fav_cities": [{"city": r["city"], "count": r["count"]} for r in fav],
        "trips": [
            {
                "plan_no": t["plan_no"],
                "destination": t["destination"],
                "start_date": t["start_date"],
                "end_date": t["end_date"],
                "travel_days": t["travel_days"],
                "budget": t["budget"],
                "transportation": t["transportation"],
                "summary": t["summary"],
            }
            for t in trips
        ],
    }


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check():
    """健康检查"""
    try:
        # 检查Agent是否可用
        agent = get_trip_planner_agent()
        
        return {
            "status": "healthy",
            "service": "trip-planner",
            "agents": {
                "attraction": {
                    "name": agent.attraction_agent.name,
                    "tools_count": len(agent.attraction_agent.list_tools())
                },
                "weather": {
                    "name": agent.weather_agent.name,
                    "tools_count": len(agent.weather_agent.list_tools())
                },
                "hotel": {
                    "name": agent.hotel_agent.name,
                    "tools_count": len(agent.hotel_agent.list_tools())
                },
                "planner": {
                    "name": agent.planner_agent.name,
                    "tools_count": len(agent.planner_agent.list_tools())
                },
                "web_guide": agent.web_guide_agent.status()
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {str(e)}"
        )
