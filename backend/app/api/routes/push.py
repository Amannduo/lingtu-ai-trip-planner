"""Web Push public-key and authenticated subscription routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ...services.auth_service import AuthenticatedUser
from ...services.web_push_service import (
    WebPushConfigurationError,
    delete_push_subscription,
    get_vapid_public_key,
    save_push_subscription,
)
from ..auth import get_current_user

router = APIRouter(prefix="/push", tags=["Web Push"])


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=1024)
    auth: str = Field(..., min_length=1, max_length=1024)


class BrowserPushSubscription(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    endpoint: str = Field(..., min_length=1, max_length=4096)
    expiration_time: int | None = Field(default=None, alias="expirationTime", ge=0)
    keys: PushKeys


class BrowserPushSubscriptionDelete(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(..., min_length=1, max_length=4096)


class SaveSubscriptionRequest(BaseModel):
    subscription: BrowserPushSubscription


class DeleteSubscriptionRequest(BaseModel):
    subscription: BrowserPushSubscriptionDelete


class VapidPublicKeyResponse(BaseModel):
    success: bool = True
    public_key: str


class SaveSubscriptionResponse(BaseModel):
    success: bool = True
    subscription_id: str
    created: bool


class DeleteSubscriptionResponse(BaseModel):
    success: bool = True
    deleted: bool


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
async def vapid_public_key():
    try:
        return VapidPublicKeyResponse(public_key=get_vapid_public_key())
    except WebPushConfigurationError as exc:
        print(f"[push] VAPID configuration unavailable: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="桌面通知暂时不可用。",
        ) from exc


@router.post("/subscriptions", response_model=SaveSubscriptionResponse)
async def save_subscription(
    payload: SaveSubscriptionRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        saved = await run_in_threadpool(
            save_push_subscription,
            current_user.user_id,
            payload.subscription.model_dump(by_alias=True),
            request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SaveSubscriptionResponse(
        subscription_id=saved.subscription_id,
        created=saved.created,
    )


@router.delete("/subscriptions", response_model=DeleteSubscriptionResponse)
async def delete_subscription(
    payload: DeleteSubscriptionRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        deleted = await run_in_threadpool(
            delete_push_subscription,
            current_user.user_id,
            payload.subscription.endpoint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DeleteSubscriptionResponse(deleted=deleted)
