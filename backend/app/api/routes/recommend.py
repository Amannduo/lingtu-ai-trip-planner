"""目的地推荐API路由"""

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from ...agents.destination_recommender_agent import get_destination_recommender_agent
from ...models.schemas import DestinationChatRequest, DestinationChatResponse

router = APIRouter(prefix="/recommend", tags=["目的地推荐"])


@router.post(
    "/chat",
    response_model=DestinationChatResponse,
    summary="目的地推荐对话",
    description="当用户不知道去哪时,根据预算、天数、偏好和高德地图数据推荐目的地"
)
async def recommend_destination(request: DestinationChatRequest):
    """目的地推荐对话"""
    try:
        agent = get_destination_recommender_agent()
        return await run_in_threadpool(agent.chat, request)
    except Exception as e:
        print(f"❌ 目的地推荐失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"目的地推荐失败: {str(e)}"
        )
