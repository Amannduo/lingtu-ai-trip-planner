"""FastAPI主应用"""

import re
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from ..config import get_settings, validate_config, print_config
from ..agents.trip_planner_agent import shutdown_trip_planner_agent
from ..services.database_service import database_status
from ..services.request_rate_limit_service import (
    create_request_rate_limit_service,
    get_request_rate_limit_service,
)
from ..services.amap_service import shutdown_amap_service
from ..services.schema import init_db
from ..services.trip_generation_job_service import shutdown_trip_generation_job_service
from .routes import auth, trip, poi, push, recommend, agent, map as map_routes


def _configure_stdio() -> None:
    """Avoid Windows console encoding crashes during startup logging."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(
                encoding="utf-8",
                errors="backslashreplace",
                line_buffering=True,
                write_through=True
            )
        except Exception:
            continue


_configure_stdio()

# 获取配置
settings = get_settings()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize and release process resources through FastAPI lifespan."""
    # Process-local rate limiter bound to this app instance (not a cluster store).
    _app.state.request_rate_limiter = create_request_rate_limit_service()
    print("\n" + "=" * 60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("=" * 60)
    print_config()
    try:
        validate_config()
        init_db()
        print("\n✅ 配置与数据库验证通过")
    except ValueError as exc:
        print(f"\n❌ 配置验证失败：{type(exc).__name__}")
        raise

    print("\n" + "=" * 60)
    print("📚 API文档: http://localhost:8000/docs")
    print("📖 ReDoc文档: http://localhost:8000/redoc")
    print("=" * 60 + "\n")
    try:
        yield
    finally:
        limiter = getattr(_app.state, "request_rate_limiter", None)
        if limiter is not None:
            limiter.clear()
        _app.state.request_rate_limiter = None
        shutdown_trip_generation_job_service()
        shutdown_trip_planner_agent()
        shutdown_amap_service()
        print("\n👋 应用正在关闭...")


# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="灵途 AI 旅行规划师 API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag", "Retry-After"],
)


_MAX_VALIDATION_ISSUES = 20


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Preserve FastAPI detail while adding the unified error vocabulary."""
    errors = jsonable_encoder(exc.errors())
    issues: list[dict] = []
    for error in errors[:_MAX_VALIDATION_ISSUES]:
        loc = error.get("loc") or ()
        path = ".".join(str(part) for part in loc) if loc else "body"
        issues.append(
            {
                "code": "REQUEST_VALIDATION",
                "severity": "error",
                "path": path,
                "message": str(error.get("msg") or "参数无效"),
            }
        )
    summary = "；".join(
        f"{issue['path']}: {issue['message']}" for issue in issues[:3]
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": errors,
            "message": f"请求参数无效：{summary}" if summary else "请求参数无效。",
            "issues": issues,
        },
    )

def _rate_limit_rule(method: str, path: str) -> tuple[str, int, int] | None:
    """Middleware rate rules for coarse public endpoints.

    Trip generation create endpoints (/api/trip/plan, /api/trip/plan-jobs) are
    intentionally NOT listed here. They are limited in the trip routes after
    auth + request validation, using user-or-IP identity keys so tests and
    multi-user NAT clients do not share a single peer-IP bucket.
    """
    if method == "POST" and path == "/api/auth/register":
        return ("auth-register", 10, 3600)
    if method == "POST" and path == "/api/auth/login":
        return ("auth-login", 20, 300)
    if path.startswith(("/api/map/", "/api/poi/")):
        return ("external-data", 120, 60)
    if method == "POST" and path.startswith("/api/recommend"):
        return ("recommendation", 30, 60)
    if method == "POST" and path.startswith("/api/agent/"):
        return ("analysis-agent", 20, 60)
    if method in {"POST", "DELETE"} and path == "/api/push/subscriptions":
        return ("push-subscription", 30, 60)
    return None


MAX_JSON_REQUEST_BODY_BYTES = 1 * 1024 * 1024
MAX_UPLOAD_REQUEST_BODY_BYTES = 25 * 1024 * 1024


class RequestBodyTooLarge(StarletteHTTPException):
    def __init__(self, limit: int) -> None:
        label = "25 MB" if limit == MAX_UPLOAD_REQUEST_BODY_BYTES else "1 MB"
        super().__init__(status_code=413, detail=f"请求体不能超过{label}。")


class RequestBodyLimitMiddleware:
    """Enforce actual ASGI body bytes, including chunked/no-length requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def body_limit(scope: Scope) -> int:
        if scope.get("path") == "/api/agent/analyze-file":
            return MAX_UPLOAD_REQUEST_BODY_BYTES
        return MAX_JSON_REQUEST_BODY_BYTES

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = self.body_limit(scope)
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                await JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Length 无效。"},
                )(scope, receive, send)
                return
            if declared_length < 0:
                await JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Length 无效。"},
                )(scope, receive, send)
                return
            if declared_length > limit:
                await self._reject(scope, receive, send, limit)
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge(limit)
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._reject(scope, receive, send, limit)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        limit: int,
    ) -> None:
        label = "25 MB" if limit == MAX_UPLOAD_REQUEST_BODY_BYTES else "1 MB"
        await JSONResponse(
            status_code=413,
            content={"detail": f"请求体不能超过{label}。"},
        )(scope, receive, send)


app.add_middleware(RequestBodyLimitMiddleware)


@app.middleware("http")
async def enforce_public_rate_limits(request: Request, call_next):
    rule = _rate_limit_rule(request.method.upper(), request.url.path)
    if rule is not None:
        scope, limit, window = rule
        peer = request.client.host if request.client else "unknown"
        retry_after = get_request_rate_limit_service(request).check(
            scope,
            f"ip:{peer}",
            limit=limit,
            window_seconds=window,
        )
        if retry_after:
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后重试。"},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


def _append_vary(response: Response, value: str) -> None:
    existing = {
        item.strip().lower()
        for item in response.headers.get("Vary", "").split(",")
        if item.strip()
    }
    if value.lower() not in existing:
        values = [
            item.strip()
            for item in response.headers.get("Vary", "").split(",")
            if item.strip()
        ]
        response.headers["Vary"] = ", ".join(values + [value])


@app.middleware("http")
async def add_response_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    if (
        request.url.path.startswith("/api/")
        and request.method.upper() in {"GET", "HEAD"}
    ):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
        _append_vary(response, "Cookie")
    return response


@app.middleware("http")
async def validate_cookie_request_origin(request: Request, call_next):
    """Reject cross-origin state changes authenticated by browser cookies."""
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin", "").rstrip("/")
        has_auth_cookie = bool(request.cookies.get(settings.auth_cookie_name))
        if origin and has_auth_cookie:
            allowed = {item.rstrip("/") for item in settings.get_cors_origins_list()}
            regex_allowed = bool(
                settings.cors_origin_regex
                and re.fullmatch(settings.cors_origin_regex, origin)
            )
            if origin not in allowed and not regex_allowed:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "请求来源未通过安全校验。"},
                )
    return await call_next(request)


# 注册路由
app.include_router(auth.router, prefix="/api")
app.include_router(push.router, prefix="/api")
app.include_router(trip.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(recommend.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(map_routes.router, prefix="/api")



@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
def health(response: Response):
    """健康检查"""
    db_status = database_status()
    healthy = db_status["status"] == "healthy"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "healthy" if healthy else "unavailable",
        "service": settings.app_name,
        "version": settings.app_version,
        "database": db_status,
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )
