"""
FastAPI Application

主应用入口。

M1: Worker Registry API skeleton。
G1: Fusion Entry Layer。
Profile API MVP: Worker Profile 内容管理。
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

# 首先配置统一日志系统，确保在导入其他模块前日志配置就绪
from src.infra.logging import configure_logging
configure_logging()

from src.interfaces.api.worker_routes import router as worker_router
from src.interfaces.api.fusion_routes import router as fusion_router
from src.interfaces.api.profile_routes import router as profile_router
from src.interfaces.api.recommend_routes import router as recommend_router
from src.interfaces.api.verify_routes import router as verify_router
from src.interfaces.api.cors_middleware import RegexCORSMiddleware
from src.interfaces.api.trace_middleware import TraceIdMiddleware
from src.interfaces.api.server_ip_middleware import ServerIpMiddleware
from src.utils.env_utils import is_dev
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize capability verify service (subscribes to EventBus)
    verify_service = None
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import get_capability_verify_service
        verify_service = get_capability_verify_service()
        if verify_service is not None:
            await verify_service.start()
            logger.info("[Startup] CapabilityVerifyService started")
    except Exception as e:
        logger.warning("[Startup] Capability verify service init skipped: %s", e)

    # Open-Core: No ZDAS mode detection or scheduler initialization
    # Internal runtime handles ZDAS scheduler in bcsfuse_internal

    yield

    # Shutdown: stop capability verify service
    if verify_service is not None:
        try:
            await verify_service.stop()
            logger.info("[Shutdown] CapabilityVerifyService stopped")
        except Exception as e:
            logger.warning("[Shutdown] CapabilityVerifyService stop failed: %s", e)


app = FastAPI(
    title="Collaboration Control Plane API",
    version="0.1.0",
    description="Worker 管理、任务理解、研究规划、统一检索、组队与 OpenClaw 交接接口",
    lifespan=lifespan,
)

# 配置 CORS — 由环境变量 CORS_ALLOWED_ORIGINS 驱动
# 本地开发地址始终允许
_LOCAL_ORIGINS = [
    r"http://localhost:.*",
    r"http://127\.0\.0\.1:.*",
]

_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if _cors_env:
    # 生产/预发：从环境变量解析逗号分隔的 origin 模式
    _ALLOW_ORIGIN_REGEX = [p.strip() for p in _cors_env.split(",") if p.strip()]
elif is_dev():
    # 开发环境：不限制来源
    _ALLOW_ORIGIN_REGEX = [r"*"]
else:
    # 非开发环境且未配置：仅允许本地地址
    _ALLOW_ORIGIN_REGEX = []

# 始终追加本地开发地址
_ALLOW_ORIGIN_REGEX = _ALLOW_ORIGIN_REGEX + _LOCAL_ORIGINS

app.add_middleware(
    RegexCORSMiddleware,
    allow_origins=[],
    allow_origin_regex=_ALLOW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册中间件：TraceIdMiddleware 统一管理请求级 trace_id
# 优先级：X-Trace-ID > X-Request-ID > 自动生成
app.add_middleware(TraceIdMiddleware)
# 注册中间件：ServerIpMiddleware 统一注入 server_ip 到所有 JSON 响应
app.add_middleware(ServerIpMiddleware)

# 注册路由
app.include_router(worker_router, prefix="/v1", tags=["Workers"])
app.include_router(profile_router, prefix="/v1", tags=["Profiles"])
app.include_router(fusion_router, prefix="/api/v1", tags=["Fusion"])
app.include_router(recommend_router, prefix="/api/v1", tags=["Recommend"])
app.include_router(verify_router, prefix="/api/v1", tags=["Verify"])


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy"}


__all__ = ["app"]