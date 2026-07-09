"""
Engine-agnostic standalone FastAPI application.

WebSocket + HTTP frontdoor for the engine adapter. Both WS endpoints dispatch
through the engine-agnostic server (`engine.community.api.transport.ws_server`) regardless of
which engine is currently active on `EngineManager`. The three debug HTTP
endpoints (/config, /test-connection, /disconnect) remain OpenClaw-specific
because they inspect gateway connection state — see `_get_openclaw_modules`.

Mounts:
  WebSocket /ws                    — Engine-agnostic, active engine is taken
                                     from EngineManager
  WebSocket /api/{engine}/ws       — Path-pinned; rejects when the pinned
                                     engine is not currently active
  /api/sessions                    — Session REST API
  /api/engine/*                    — Engine management (/status, /switch,
                                     /restart, /capabilities, /list)
  GET /health
  GET /config?engine=...           — OpenClaw gateway config (debug)
  GET /test-connection?engine=...  — OpenClaw gateway handshake test (debug)
  POST /disconnect?engine=...      — OpenClaw gateway disconnect (debug)
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import List

# ============ Trace context（供日志和业务代码使用）============
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
rpc_id_var: ContextVar[str] = ContextVar("rpc_id", default="")


class _TraceContextFilter(logging.Filter):
    """将当前请求的 trace_id / rpc_id 注入每条日志记录."""

    def filter(self, record):
        record.trace_id = trace_id_var.get("")
        record.rpc_id = rpc_id_var.get("")
        return True


# ============ 日志配置（必须在导入其他模块之前）============
# 配置日志级别（支持通过环境变量设置）
_log_level = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# 使用 force=True 强制重新配置（覆盖 uvicorn 的配置）
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s %(levelname)s %(name)s [trace=%(trace_id)s rpc=%(rpc_id)s]: %(message)s",
    force=True,  # Python 3.8+ 强制覆盖已有配置
)
_trace_filter = _TraceContextFilter()
for _h in logging.getLogger().handlers:
    _h.addFilter(_trace_filter)

# 为 intent_eval 模块启用 DEBUG 级别（用于评测系统调试）
_intent_eval_level = os.environ.get("INTENT_EVAL_LOG_LEVEL", "DEBUG").upper()
logging.getLogger("intent_eval").setLevel(getattr(logging, _intent_eval_level, logging.DEBUG))
logging.getLogger("intent_eval").debug(f"Intent evaluation logging set to {_intent_eval_level}")
logging.getLogger("intent_eval").info(f"Intent evaluation logging set to {_intent_eval_level}")

# 输出启动日志
logging.getLogger("engine-web").info(f"Logging configured: root={_log_level}, intent_eval={_intent_eval_level}")
# ============ 日志配置结束 ============

from fastapi import APIRouter, FastAPI, Query, Request, WebSocket  # noqa: E402
from fastapi_injector import attach_injector  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from engine.community.api.cron.router import router as cron  # noqa: E402
from engine.community.api.session.router import router as session_router  # noqa: E402
from engine.community.manager import EngineManager  # noqa: E402
from engine.community.di import Injected  # noqa: E402
from engine.community.plugin_api.auth_gate.protocol import AuthGateService  # noqa: E402
from engine.community.api.approvals import router as approvals  # noqa: E402
from engine.community.api.bot import router as bot_router  # noqa: E402
from engine.community.api.default_config import router as default_config_router  # noqa: E402
from engine.community.api.engine import router as engine_router  # noqa: E402
from engine.community.api.file import router as file_router  # noqa: E402
from engine.community.api.bash import router as bash_router  # noqa: E402
from engine.community.api.mcp import router as mcp  # noqa: E402
from engine.community.api.models import router as models  # noqa: E402
from engine.community.api.node import router as node  # noqa: E402
from engine.community.api.skills import router as skills_router  # noqa: E402
from engine.community.api.web_shell import router as web_shell_router  # noqa: E402
from engine.community.api.work_item import router as work_item_router  # noqa: E402
from engine.community.api.zero_check import router as zero_check_router  # noqa: E402

log = logging.getLogger("engine-web")

# ── 评测报告缓存 ──────────────────────────────────────────────────────────
# 简单的内存缓存，存储最近的评测报告
# key: session_id, value: report_data
_evaluation_report_cache: dict = {}
# 最大缓存数量
_MAX_CACHE_SIZE = 100


def cache_evaluation_report(session_id: str, report_data: dict) -> None:
    """缓存评测报告"""
    global _evaluation_report_cache
    # 如果缓存过多，清理旧数据
    if len(_evaluation_report_cache) >= _MAX_CACHE_SIZE:
        # 删除最早的一半
        keys_to_remove = list(_evaluation_report_cache.keys())[:_MAX_CACHE_SIZE // 2]
        for key in keys_to_remove:
            _evaluation_report_cache.pop(key, None)
    _evaluation_report_cache[session_id] = report_data
    log.info(f"[EvaluationCache] Cached report for session: {session_id}")
    log.info(f"[EvaluationCache] session_id format: len={len(session_id)}, has_colon={':' in session_id}")
    log.debug(f"[EvaluationCache] Current cache keys: {list(_evaluation_report_cache.keys())}")


def get_cached_evaluation_report(session_id: str) -> dict | None:
    """获取缓存的评测报告，支持模糊匹配"""
    # 先精确匹配
    report = _evaluation_report_cache.get(session_id)
    if report:
        return report

    # 模糊匹配：尝试各种可能的格式变体
    # 1. 尝试去掉前缀（如 "agent:main:" -> "main"）
    if ":" in session_id:
        simple_key = session_id.split(":")[-1]
        report = _evaluation_report_cache.get(simple_key)
        if report:
            log.info(f"[EvaluationCache] Found report with simple key: {simple_key}")
            return report

    # 2. 尝试添加前缀（如 "main" -> "agent:main:main"）
    for cached_key in _evaluation_report_cache:
        if cached_key.endswith(f":{session_id}") or cached_key == session_id:
            log.info(f"[EvaluationCache] Found report with partial match: {cached_key}")
            return _evaluation_report_cache.get(cached_key)

    return None


class TraceContextMiddleware(BaseHTTPMiddleware):
    """从请求头提取 trace context，存入 ContextVar 供日志使用.

    Engine 接收两类 HTTP 请求：
    1. 来自 backend 的内部调用（如 /api/sessions 由 expert_chat_service 发起）
       — 携带 SOFA-TraceId / SOFA-RpcId（由 backend 的 HttpxPatcher 自动注入）
    2. 来自前端的直接调用（如 /api/sessions, /api/engine/status, /api/nodes）
       — 携带 X-Request-ID（由前端 requestConfig.ts 生成）

    优先使用 SOFA 头，若不存在则回退到 X-Request-ID。
    """

    async def dispatch(self, request: Request, call_next):
        trace_id = (
            request.headers.get("SOFA-TraceId")
            or request.headers.get("sofaTraceId")
            or request.headers.get("X-Request-ID")
            or ""
        )
        rpc_id = request.headers.get("SOFA-RpcId") or request.headers.get("sofaRpcId") or ""

        trace_token = trace_id_var.set(trace_id)
        rpc_token = rpc_id_var.set(rpc_id)
        try:
            response = await call_next(request)
            if trace_id:
                response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            trace_id_var.reset(trace_token)
            rpc_id_var.reset(rpc_token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭生命周期。

    替代已废弃的 ``@app.on_event("startup"/"shutdown")``。函数体在启动时执行
    到 ``yield``，进程退出时执行 ``yield`` 之后的清理。引用的模块级全局
    （``_INJECTOR`` 等）在下方定义，但只在运行期解析，故顺序无碍。
    """
    # ── startup ──
    # Best-effort skills-repo download (VM desktop only). Runs in background so
    # engine startup is never blocked.
    try:
        import asyncio
        from engine.community.core.skills.skills_repo_download import (
            bootstrap_on_startup,
            start_background_sync,
        )
        asyncio.create_task(asyncio.to_thread(bootstrap_on_startup))
        start_background_sync()
    except Exception:
        pass

    # Bind the composition-root injector on EngineManager so get_instance()
    # resolves the DI singleton instead of the lazy global path.
    EngineManager.bind_injector(_INJECTOR)

    manager = EngineManager.get_instance()
    await manager.initialize()

    yield

    # ── shutdown ──
    manager = EngineManager.get_instance()
    await manager.shutdown()
    # Unbind the injector so the binding never leaks past this app's lifespan
    # (keeps `with TestClient(app)` blocks from polluting later tests).
    EngineManager.bind_injector(None)


app = FastAPI(
    title="OpenClaw Engine Adapter", version="1.0.0", lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(TraceContextMiddleware)

# Composition root: construct application-wide dependencies once and bind
# FastAPI dependency tokens. API endpoints receive dependencies through
# function parameters and never resolve the injector themselves.
from engine.community.di.container import build_injector  # noqa: E402
from engine.community.di.runtime_mode import RuntimeConfig  # noqa: E402

_RUNTIME_CONFIG = RuntimeConfig.detect()
_INJECTOR = build_injector(config=_RUNTIME_CONFIG)
attach_injector(app, _INJECTOR)

app.include_router(engine_router)
app.include_router(session_router)
app.include_router(models)
app.include_router(cron)
app.include_router(approvals)
app.include_router(mcp)
app.include_router(node)
app.include_router(skills_router)
app.include_router(work_item_router)
app.include_router(file_router)
app.include_router(bash_router)
app.include_router(web_shell_router)
app.include_router(default_config_router)
app.include_router(bot_router)
app.include_router(zero_check_router)
# Neutral delivery routers — mounted unconditionally on every profile.
# openclaw HTTP (/api/openclaw/{test-connection,disconnect,config}) and the
# OSS WS endpoints (/api/openclaw/ws, /api/claude_code/ws) are OSS-safe: their
# deps are injected through Protocols/ports and profile DI modules.
# AICoding is intentionally not part of this open-source router surface.
from engine.community.api.routers import openclaw_http_router, ws_router  # noqa: E402
app.include_router(openclaw_http_router)
app.include_router(ws_router)
# Router surface is profile-invariant and OSS-scoped. The base
# SharedRoutersModule contributes only the OpenClaw/Claude Code delivery surface
# for every profile; community/corp differences must be expressed behind router
# dependencies (ports/services) and DI bindings, never by mounting different
# routers.
for _collected_router in _INJECTOR.get(List[APIRouter]):
    app.include_router(_collected_router)


def _get_openclaw_modules(engine: str | None = None):
    """Resolve OpenClaw-specific gateway plumbing for the debug HTTP endpoints.

    Used by `/config`, `/test-connection`, `/disconnect` only — these expose
    OpenClaw gateway connection state and are engine-bound. WS endpoints go
    through `engine.community.api.transport.ws_server.get_server` directly, so this helper no
    longer needs to hand back a server.
    """
    manager = EngineManager.get_instance()
    engine_type = engine or manager.engine
    if engine_type != "openclaw":
        raise ValueError(
            f"/config, /test-connection, /disconnect only support 'openclaw' "
            f"(active engine: {engine_type!r})"
        )
    from engine.community.openclaw.client.gateway_client import close_client, get_client
    from engine.community.openclaw.config import get_config
    return get_client, close_client, get_config, engine_type


@app.websocket("/ws")
async def websocket_endpoint_default(
    websocket: WebSocket,
    auth_gate_service: AuthGateService = Injected(AuthGateService),
):
    """WebSocket entrypoint — dispatches through the engine-agnostic server."""
    from engine.community.api.transport.ws_server import get_server
    server = get_server()
    log.info(f"[ws] Using engine: {EngineManager.get_instance().engine}")
    await server.handle_connection(websocket, auth_gate_service=auth_gate_service)


@app.websocket("/api/{engine}/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    engine: str,
    auth_gate_service: AuthGateService = Injected(AuthGateService),
):
    """WebSocket entrypoint — path-pinned to a specific engine. Rejects if the
    pinned engine isn't currently active on the manager."""
    manager = EngineManager.get_instance()
    if engine != manager.engine:
        await websocket.accept()
        await websocket.close(
            code=4001,
            reason=f"Engine '{engine}' not active, current: '{manager.engine}'"
        )
        return
    from engine.community.api.transport.ws_server import get_server
    server = get_server()
    log.info(f"[ws] Using engine from path: {engine}")
    await server.handle_connection(websocket, auth_gate_service=auth_gate_service)


@app.get("/health")
async def health():
    manager = EngineManager.get_instance()
    return {"status": "ok", "engine": manager.engine}


@app.get("/readiness")
async def readiness():
    """Engine readiness — composes activation phase with subprocess liveness.

    Distinct from /health (a pure liveness probe). Backend's
    /api/system/readiness consumes this to derive per-bot frontend state.
    """
    manager = EngineManager.get_instance()
    return await manager.readiness()


# ── Engine management endpoints live in api/engine.py (included above) ──────


@app.get("/api/evaluation/report")
async def get_evaluation_report(session_id: str = Query(..., description="会话 ID")):
    """获取评测报告"""

    log.info(f"[EvaluationReport] Query received - session_id: {session_id}")
    log.info(f"[EvaluationReport] session_id format: len={len(session_id)}, has_colon={':' in session_id}")
    log.debug(f"[EvaluationReport] Current cache keys: {list(_evaluation_report_cache.keys())}")

    # 尝试从缓存获取
    report = get_cached_evaluation_report(session_id)

    if report:
        log.info(f"[EvaluationReport] Returning cached report for session: {session_id}")
        return {"success": True, "data": report}

    # 如果没有缓存，返回空
    log.info(f"[EvaluationReport] No cached report for session: {session_id}")
    return {"success": False, "data": None, "message": "No evaluation report found"}


@app.get("/config")
async def engine_config(engine: str | None = Query(None)):
    """获取引擎配置"""
    _, _, get_config, engine_type = _get_openclaw_modules(engine)
    cfg = get_config()
    result = {
        "engine": engine_type,
        "gateway_url": cfg.gateway_url,
        "connection_timeout": cfg.connection_timeout,
    }
    if hasattr(cfg, "gateway_password"):
        result["has_password"] = cfg.gateway_password is not None
    return result


@app.get("/test-connection")
async def test_connection(engine: str | None = Query(None)):
    """测试与引擎 Gateway 的连接"""
    get_client, _, get_config, engine_type = _get_openclaw_modules(engine)
    cfg = get_config()
    try:
        client = await get_client()
        hello = client.hello
        return {
            "success": True,
            "engine": engine_type,
            "connected": client.connected,
            "gateway_url": cfg.gateway_url,
            "server": {
                "version": hello.server.version if hello else None,
                "conn_id": hello.server.conn_id if hello else None,
                "host": hello.server.host if hello else None,
            } if hello else None,
            "protocol": hello.protocol if hello else None,
        }
    except Exception as e:
        log.error(f"Connection test failed: {e}")
        return {
            "success": False,
            "engine": engine_type,
            "connected": False,
            "gateway_url": cfg.gateway_url,
            "error": str(e),
        }


@app.post("/disconnect")
async def disconnect(engine: str | None = Query(None)):
    """断开与引擎 Gateway 的连接"""
    _, close_client, _, engine_type = _get_openclaw_modules(engine)
    try:
        await close_client()
        return {"success": True, "message": f"Disconnected from {engine_type} Gateway"}
    except Exception as e:
        log.error(f"Disconnect failed: {e}")
        return {"success": False, "error": str(e)}
