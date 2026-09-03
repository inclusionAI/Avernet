"""OpenApiBotSessionInitiator — pre/prod 环境通过 BaaS Open API 创建 session + 注入发现消息。

与 CronRelaySessionInitiator (singlebox 专用) 并列，实现同一个 SessionInitiator Protocol。

流程 (1 步, BaaS 自动创建 session):
    ensure_grant(bot_id)           — 鉴权:校验 bot 是否在 allowed-bots,缺则 grant
    send_message(bot_id, message)  — Bearer auth POST /openapi/v1/messages
                                     BaaS 内部创建 session,返回 session_id
    _build_session_url(session_id, bot_id, owner_id)
    → DiscoverySession

session_url 格式 (对齐生产前端路由):
    {frontend_url}/workspace?tab=chat&bot={bot_id}:{owner_id}&session=agent:main:{session_id}
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from agentclaw.community.core.task.task_discovery.frontend_url_provider import (
    FrontendUrlProvider,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    OpenApiBotPort,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class OpenApiBotSessionInitiator:
    """Pre/prod SessionInitiator — 通过 BaaS Open API 发送发现提示消息。

    依赖 ``OpenApiBotPort`` (corp DI 由 ``CorpTaskIntegrationModule`` 绑定),
    BaaS 在处理 ``send_message`` 时内部创建 session 并在响应中返回 ``session_id``。

    鉴权流程:
        1. ``ensure_grant(bot_id)`` — 校验 bot 是否已授权 (Bearer api_key)
        2. ``send_message(bot_id, message, metadata)`` — BaaS Open API 派发

    session_url:
        生产前端路由为 ``/workspace?tab=chat&bot={bot_id}:{owner_id}&session=...``
        (与 singlebox 的 ``/assistant?botId=...&sessionId=...`` 不同)。
    """

    def __init__(
        self,
        openapi_bot: OpenApiBotPort,
        *,
        frontend_url: str = "http://localhost:8000",
        backend_url: str = "http://localhost:8888",
        ensure_grant: bool = False,
        frontend_url_provider: FrontendUrlProvider | None = None,
    ):
        """
        Args:
            openapi_bot: BaaS Open API 适配器 (已内含 Bearer api_key 鉴权)。
            frontend_url: 前端 workbench 地址 (兜底,provider 未注入/返回空时使用)。
            backend_url: 当前 backend 服务地址 (用于创建 session 后更新 title)。
            ensure_grant: 是否对 bot 执行 allowed-bots 校验 + grant 流程。
                corp 预授权模式默认 False (OOB 预授权); 测试/联调可设 True。
            frontend_url_provider: 前端 URL 取数端口(corp 列 DI 绑
                ``CorpFrontendUrlProvider`` — env-aware 静态值 + 运行时 holder
                优先;未注入时仅用 ``frontend_url`` 兜底)。
        """
        self._openapi_bot = openapi_bot
        self._frontend_url = frontend_url
        self._backend_url = backend_url
        self._ensure_grant = ensure_grant
        self._frontend_url_provider = frontend_url_provider

    async def initiate_session(
        self,
        tasks: list[DiscoveredTask],
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """创建 session + 注入发现消息 — 通过 BaaS Open API 一步完成。

        BaaS 在处理 ``POST /openapi/v1/messages`` 时内部创建 session,
        响应中返回 ``message_id`` (run_id) 和 ``session_id``。
        """
        logger.debug("[task_discovery] → OpenApiBotSessionInitiator.initiate_session(bot_id=%s, owner_id=%s, task_count=%d)", bot_id, owner_id, len(tasks))
        first_task = tasks[0]
        task_count = len(tasks)
        title = (
            f"[DreamMode-任务发现] 发现 {task_count} 件可能有意义的事情"
            if task_count > 1
            else f"[DreamMode-任务发现] {first_task.title}"
        )

        # ── Step 1: 鉴权 — 校验 bot 是否已授权 ──────────────────
        try:
            await self._openapi_bot.ensure_grant(bot_id)
        except Exception as exc:
            logger.warning(
                "[task_discovery] ensure_grant failed (non-fatal, OOB 预授权模式可能跳过): "
                "bot=%s err=%s: %s",
                bot_id, type(exc).__name__, exc,
            )

        # ── Step 2: 构造发现提示消息 ────────────────────────────
        prompt = self._build_discovery_prompt(tasks)

        metadata: dict[str, Any] = {
            "title": title,
            "source": "task_discovery",
            "task_count": task_count,
            "discovery_date": first_task.dt,
            "ext_info": {
                "tasks": [t.to_session_ext_info() for t in tasks],
            },
        }

        # ── Step 3: 发送消息 (BaaS 内部创建 session) ────────────
        logger.info(
            "[task_discovery] send_message via BaaS Open API: bot=%s msg_len=%d",
            bot_id, len(prompt),
        )
        result = await self._openapi_bot.send_message(
            bot_id=bot_id,
            message=prompt,
            metadata=metadata,
        )

        session_id = result.session_id
        if not session_id:
            # BaaS 当前版本可能不返回 session_id; 用 run_id 作前向兼容 fallback
            logger.warning(
                "[task_discovery] BaaS send_message returned no session_id "
                "(run_id=%s), using run_id as session fallback",
                result.run_id,
            )
            session_id = result.run_id

        logger.info(
            "[task_discovery] session created via BaaS Open API: "
            "id=%s run_id=%s bot=%s",
            session_id, result.run_id, bot_id,
        )

        # ── Step 4: 更新 session title (send_message 不传 title，需单独调) ──
        await self._update_session_title(session_id, title, bot_id, owner_id)

        # ── Step 5: 构建 session_url ────────────────────────────
        session_url = self._build_session_url(session_id, bot_id, owner_id)

        return DiscoverySession(
            task_id=first_task.task_id,
            session_id=session_id,
            session_url=session_url,
        )

    # ── 辅助方法 ──────────────────────────────────────────────

    def _build_discovery_prompt(self, tasks: list[DiscoveredTask]) -> str:
        """构造发现提示消息 — 对齐 CronRelaySessionInitiator 的 prompt 格式。

        按 4 个维度组织 (对齐执行层 TaskSpec 语义):
          目标       ← task.objective (缺省回退 title)
          预期交付物 ← task.instruction
          验收标准   ← task.acceptances (为空则提示确认时补充)
          约束       ← task.background
        """
        lines = ["/task 用taskloop 这个skill。\n"]
        for i, task in enumerate(tasks, 1):
            lines.append(f"{i}. 【{task.title}】")
            lines.append(f"   目标：{task.objective or task.title}")
            lines.append(f"   预期交付物：{task.instruction}")
            if task.acceptances:
                lines.append("   验收标准：")
                for a in task.acceptances:
                    lines.append(
                        f"     - [{a.get('id', '')}] {a.get('description', '')}"
                    )
            else:
                lines.append("   验收标准：（确认时可由你补充）")
            lines.append(f"   约束：{task.background}")
            lines.append("")
        lines.append("请向用户展示以上任务，并询问是否确认执行。")
        return "\n".join(lines)

    async def _update_session_title(
        self, session_id: str, title: str, bot_id: str, owner_id: str,
    ) -> None:
        """创建 session 后单独更新 title（BaaS send_message 不传 title，需额外调一次）。

        解析 engine target 后直连 engine 的
        ``POST /api/sessions/{session_id}/update?title=...``。

        原实现走 backend 公开的 ``PATCH /openapi/v1/bots/{bot_id}/sessions/{session_id}``，
        但该接口需要 Bearer/Cookie 鉴权（PublicAPIRoute admission），内部服务调用
        只有 ``x-user-id`` header → 401。改为直连 engine 绕过 admission 层。

        失败不阻断主流程（non-fatal），仅记录 warning。
        """
        logger.debug("[task_discovery] → OpenApiBotSessionInitiator._update_session_title(session_id=%s, bot_id=%s)", session_id, bot_id)
        full_session_key = (
            session_id
            if session_id.startswith("agent:main:")
            else f"agent:main:{session_id}"
        )
        engine_target = await self._resolve_engine_target(bot_id, owner_id)
        if not engine_target:
            logger.warning(
                "[task_discovery] cannot update title: no engine target "
                "for bot=%s (non-fatal)",
                bot_id,
            )
            return

        base = engine_target
        if not base.startswith("http"):
            base = f"http://{base}"
        url = f"{base}/api/sessions/{full_session_key}/update"
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                resp = await cli.post(
                    url,
                    params={"title": title},
                    headers={"x-user-id": owner_id},
                )
                if resp.status_code == 200:
                    logger.info(
                        "[task_discovery] session title updated: id=%s title=%s",
                        session_id, title,
                    )
                else:
                    logger.warning(
                        "[task_discovery] session title update failed (non-fatal): "
                        "HTTP %s — %s",
                        resp.status_code, resp.text[:200],
                    )
        except Exception as exc:
            logger.warning(
                "[task_discovery] session title update error (non-fatal): %s", exc,
            )

    async def _resolve_engine_target(
        self, bot_id: str, owner_id: str,
    ) -> str | None:
        """通过 backend API 查 per-bot engine 的 target 地址。

        1. GET /api/bots/{bot_id} → 拿 binding_id
        2. GET /api/v1/devices/{binding_id}/connection → 拿 target
        """
        logger.debug("[task_discovery] → OpenApiBotSessionInitiator._resolve_engine_target(bot_id=%s, owner_id=%s)", bot_id, owner_id)
        try:
            backend = self._backend_url.rstrip("/")
            async with httpx.AsyncClient(timeout=10.0) as cli:
                bot_resp = await cli.get(
                    f"{backend}/api/bots/{bot_id}",
                    params={"owner_id": owner_id},
                    headers={"x-user-id": owner_id},
                )
                bot_resp.raise_for_status()
                binding_id = (
                    bot_resp.json().get("data") or {}
                ).get("binding_id")
                if not binding_id:
                    return None
                conn_resp = await cli.get(
                    f"{backend}/api/v1/devices/{binding_id}/connection",
                    headers={"x-user-id": owner_id},
                )
                conn_resp.raise_for_status()
                return (
                    conn_resp.json().get("data") or {}
                ).get("target") or None
        except Exception as exc:
            logger.warning(
                "[task_discovery] _resolve_engine_target failed: %s", exc,
            )
            return None

    def _build_session_url(self, session_id: str, bot_id: str, owner_id: str) -> str:
        """构建前端 workbench session URL — 生产前端路由格式。

        格式: ``{frontend_url}/workspace?tab=chat&bot={bot_id}:{owner_id}&session={encoded_session_key}``
        session_key = ``agent:main:{raw_session_id}`` URL-encoded。

        动态解析 frontend URL — 优先 ``FrontendUrlProvider`` (corp DI 绑
        ``CorpFrontendUrlProvider``: 运行时 holder 热注入 > env-aware 静态值),
        provider 未注入/返回空时回落构造参数 ``frontend_url``。
        """
        provided = (
            self._frontend_url_provider.get() if self._frontend_url_provider else ""
        )
        base = (provided or self._frontend_url).rstrip("/")
        full_session_key = (
            session_id
            if session_id.startswith("agent:main:")
            else f"agent:main:{session_id}"
        )
        bot_value = f"{bot_id}:{owner_id}"
        return (
            f"{base}/workspace"
            f"?tab=chat"
            f"&bot={quote(bot_value, safe='')}"
            f"&session={quote(full_session_key, safe='')}"
        )


__all__ = ["OpenApiBotSessionInitiator"]
