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

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    FrontendUrlHolder,
)
from agentclaw.community.core.task.task_runner.integration.ports import (
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
        ensure_grant: bool = False,
    ):
        """
        Args:
            openapi_bot: BaaS Open API 适配器 (已内含 Bearer api_key 鉴权)。
            frontend_url: 前端 workbench 地址 (用于构建 session_url)。
                运行时可通过 ``FrontendUrlHolder`` (API 注入) 覆盖。
            ensure_grant: 是否对 bot 执行 allowed-bots 校验 + grant 流程。
                corp 预授权模式默认 False (OOB 预授权); 测试/联调可设 True。
        """
        self._openapi_bot = openapi_bot
        self._frontend_url = frontend_url
        self._ensure_grant = ensure_grant

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

        # ── Step 4: 构建 session_url ────────────────────────────
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

    def _build_session_url(self, session_id: str, bot_id: str, owner_id: str) -> str:
        """构建前端 workbench session URL — 生产前端路由格式。

        格式: ``{frontend_url}/workspace?tab=chat&bot={bot_id}:{owner_id}&session={encoded_session_key}``
        session_key = ``agent:main:{raw_session_id}`` URL-encoded。

        动态解析 frontend URL — 支持运行时 API 注入 (FrontendUrlHolder)。
        """
        base = (FrontendUrlHolder.get() or self._frontend_url).rstrip("/")
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
