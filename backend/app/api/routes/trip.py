"""旅行规划API路由"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request as HttpRequest,
)
from starlette.concurrency import run_in_threadpool
from ...models.schemas import (
    TripRequest,
    TripPlan,
    TripPlanResponse,
    ErrorResponse
)
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...services.auth_service import AuthenticatedUser
from ...services.trip_email_service import deliver_trip_plan_email
from ...services.travel_plan_data_service import get_travel_plan_data_service
from ...services.web_push_service import notify_trip_plan_ready
from ..auth import get_current_user, get_optional_current_user

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
async def plan_trip(
    request: TripRequest,
    background_tasks: BackgroundTasks,
    http_request: HttpRequest,
    current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
):
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

        plan_no = None
        try:
            if current_user is None:
                print("[trip] anonymous request - generated plan will not be saved")
            else:
                plan_no = get_travel_plan_data_service().save_trip_plan(
                    request,
                    trip_plan,
                    user_id=current_user.user_id,
                    user_role=current_user.role,
                    source="generated",
                )
                print(f"[trip] travel plan saved to dataset: {plan_no}")
        except Exception as save_error:
            print(f"[trip] save travel plan skipped (not critical): {save_error}")

        if plan_no and current_user is not None:
            background_tasks.add_task(
                notify_trip_plan_ready,
                current_user.user_id,
                trip_plan.city,
                plan_no,
            )

        email_delivery = None
        if request.email_on_completion:
            recipient = str(request.delivery_email or "").strip()
            if current_user is None:
                email_delivery = {
                    "requested": True,
                    "sent": False,
                    "dry_run": False,
                    "to": recipient or None,
                    "message": "登录后才能发送旅行计划邮件。",
                }
            else:
                recipient = recipient or (current_user.email or "")
                if not recipient:
                    email_delivery = {
                        "requested": True,
                        "sent": False,
                        "dry_run": False,
                        "to": None,
                        "message": "请填写收件邮箱或先在账号中绑定邮箱。",
                    }
                else:
                    try:
                        email_delivery = await run_in_threadpool(
                            deliver_trip_plan_email,
                            recipient,
                            trip_plan,
                            plan_no,
                            user_id=current_user.user_id,
                            client_ip=(
                                http_request.client.host
                                if http_request.client
                                else "unknown"
                            ),
                        )
                    except Exception as email_error:
                        print(
                            "[trip] email delivery failed (not critical): "
                            f"{type(email_error).__name__}"
                        )
                        email_delivery = {
                            "requested": True,
                            "sent": False,
                            "dry_run": False,
                            "to": recipient,
                            "message": "邮件服务暂时不可用，行程已正常保存。",
                        }

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan,
            plan_no=plan_no,
            email_delivery=email_delivery,
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
async def trip_history(
    limit: int = 20,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """查询当前登录用户的历史行程和统计"""
    from ...services.database_service import fetch_all, fetch_one
    from ...services.schema import init_db

    init_db()
    trips = fetch_all(
        "SELECT plan_no, destination, start_date, end_date, travel_days, "
        "budget, transportation, summary, created_at, "
        "CASE WHEN plan_json IS NOT NULL AND plan_json <> '{}' THEN 1 ELSE 0 END AS has_detail "
        "FROM travel_plans WHERE user_id = :uid ORDER BY created_at DESC LIMIT :lim",
        {"uid": current_user.user_id, "lim": limit},
    )
    stats = fetch_one(
        "SELECT COUNT(*) AS total, ROUND(AVG(budget), 0) AS avg_budget, "
        "SUM(travel_days) AS total_days "
        "FROM travel_plans WHERE user_id = :uid",
        {"uid": current_user.user_id},
    )
    fav = fetch_all(
        "SELECT destination AS city, COUNT(*) AS count "
        "FROM travel_plans WHERE user_id = :uid "
        "GROUP BY destination ORDER BY count DESC LIMIT 5",
        {"uid": current_user.user_id},
    )
    return {
        "success": True,
        "user_id": current_user.user_id,
        "stats": {
            "total_trips": stats.get("total", 0) if stats else 0,
            "avg_budget": (stats.get("avg_budget") or 0) if stats else 0,
            "total_days": (stats.get("total_days") or 0) if stats else 0,
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
                "created_at": t["created_at"],
                "has_detail": bool(t["has_detail"]),
            }
            for t in trips
        ],
    }


@router.get("/history/{plan_no}", response_model=TripPlanResponse, summary="读取历史旅行计划")
async def trip_history_detail(
    plan_no: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    plan = get_travel_plan_data_service().get_trip_plan(plan_no, current_user.user_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="未找到该旅行计划，或当前用户无权访问")
    return TripPlanResponse(success=True, message="旅行计划读取成功", data=plan, plan_no=plan_no)


@router.put("/history/{plan_no}", response_model=TripPlanResponse, summary="保存旅行计划修改")
async def update_trip_history(
    plan_no: str,
    plan: TripPlan,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    updated = get_travel_plan_data_service().update_trip_plan(
        plan_no,
        current_user.user_id,
        plan,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="未找到该旅行计划，或当前用户无权修改")
    return TripPlanResponse(success=True, message="旅行计划修改已保存", data=plan, plan_no=plan_no)


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
