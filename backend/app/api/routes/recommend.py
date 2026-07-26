"""目的地推荐API路由"""

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ...agents.destination_recommender_agent import get_destination_recommender_agent
from ...models.schemas import DestinationChatRequest, DestinationChatResponse
from ...services.auth_service import AuthenticatedUser
from ...services.contract_token_service import issue_contract_token
from ..auth import get_optional_current_user

router = APIRouter(prefix="/recommend", tags=["目的地推荐"])


def token_subject(current_user: AuthenticatedUser | None) -> str:
    return f"user:{current_user.user_id}" if current_user is not None else "anon"


@router.post(
    "/chat",
    response_model=DestinationChatResponse,
    summary="目的地推荐对话",
    description="当用户不知道去哪时,根据预算、天数、偏好和高德地图数据推荐目的地"
)
async def recommend_destination(
    request: DestinationChatRequest,
    current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
):
    """目的地推荐对话"""
    try:
        agent = get_destination_recommender_agent()
        response = await run_in_threadpool(agent.chat, request)
        # Stateless cross-worker handoff: the session contract travels as a
        # signed token the client returns with the generation request.
        if response.semantic_contract is not None:
            response.contract_token = issue_contract_token(
                response.semantic_contract,
                subject=token_subject(current_user),
            )
        return response
    except Exception as e:
        print(f"[recommend] request failed: {type(e).__name__}")
        raise HTTPException(
            status_code=500,
            detail="目的地推荐暂时不可用，请稍后重试。"
        )
