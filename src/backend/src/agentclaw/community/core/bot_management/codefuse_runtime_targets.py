"""Resolve the live-runtime binding ids a CodeFuse token must be written into.

背景：一个 service bot 最多有三条长期在运行的 runtime，按 binding 存放位置区分
（与 ``engine_runtime.stage.resolve_stage_bind_id`` 的三运行时模型一致）：

- draft —— ``ac_bots.binding_id``（预发布草稿容器）
- verify —— ``ac_bot_publish.ext.binding.verify``（预发/验证容器，含晋升后保留）
- online —— ``ac_bot_publish.ext.binding.online``（线上容器）

CodeFuse 重授权（HTTP ``PUT /aicoding/bots/{bot_id}/codefuse/auth``）与 token
变更后的异步刷新（``BotService._refresh_codefuse_token_on_device``）都必须把
``codefuse.json`` 写进**每一条在运行的 runtime**，否则真正承接对话的容器
（由 ``resolve_stage_bind_id`` 按 stage 选中的 verify/online）会一直缺 token。

本模块只回答"该 bot 有哪些在运行的 runtime binding_id"，**draft 直接读
``ac_bots.binding_id``，verify/online 复用 ``resolve_stage_bind_id``**（同一套
服务链路解析规则，含"晋升后 retained verify 仍 ACTIVE 即 live"的回退），避免再
造一套会与对话链路漂移的解析逻辑。是否真正写入仍由调用方按 provider 逐个 exec。
"""
from __future__ import annotations

from typing import List

from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

logger = get_logger()

SERVICE_BOT_TYPE = "service"


def resolve_codefuse_runtime_binding_ids(
    *,
    bot: dict,
    publish_repo: BotPublishRepositoryProtocol,
    binding_repo: DeviceBindingRepository,
    env: str | None = None,
) -> List[int]:
    """Return the live runtime binding ids to write codefuse.json into.

    Args:
        bot: bot 行（需含 ``bot_type``/``bot_id``/``id``(pk)/``binding_id``）。
        publish_repo: ``ac_bot_publish`` 仓库（service bot 的 verify/online 用）。
        binding_repo: ``ac_entity_device_binding`` 仓库（retained-verify ACTIVE 检查用）。
        env: 显式环境；不传则取 ``get_current_env()``，与 ``resolve_stage_bind_id`` 同口径。

    Returns:
        去重后的 binding_id 列表，draft 在前；personal 仅含 draft；找不到任何
        binding 时返回空列表（调用方据此跳过）。
    """
    bot_type = (bot.get("bot_type") or "personal") if isinstance(bot, dict) else "personal"
    draft_bid = bot.get("binding_id") if isinstance(bot, dict) else None
    ids: List[int] = []
    if draft_bid:
        try:
            ids.append(int(draft_bid))
        except (TypeError, ValueError):
            logger.warning(
                "[codefuse_runtime_targets] non-int draft binding_id=%s bot_id=%s",
                draft_bid, bot.get("bot_id") if isinstance(bot, dict) else "",
            )

    if bot_type != SERVICE_BOT_TYPE:
        return ids

    bot_pk = bot.get("id") if isinstance(bot, dict) else None
    bot_id = (bot.get("bot_id") or "") if isinstance(bot, dict) else ""
    runtime_env = env or get_current_env()

    # 惰性 import：engine_runtime 包 __init__ 会 eagerly 拉 connection →
    # device_context_resolver（BaasService 等重依赖），放模块顶部会引入重链/潜在环。
    # 仅在 service bot 真正需要解析发布态时才加载。
    from agentclaw.community.core.engine_runtime.stage import (
        STAGE_ONLINE,
        STAGE_VERIFY,
        resolve_stage_bind_id,
    )
    from agentclaw.community.core.engine_runtime.errors import (
        EngineStageNotLiveError,
    )
    from agentclaw.community.core.devices.services.device_context import (
        DeviceNotBoundError,
    )

    # draft(草稿)已加入；verify/online 复用对话链路同款解析器，逐 stage 取 live runtime。
    # 某 stage 不存在 live runtime（EngineStageNotLiveError）或缺主键
    # （DeviceNotBoundError）/draft 这种非法 stage（ValueError）→ 跳过；其余异常
    # 也按 best-effort 跳过，保证一个 stage 的查询失败不影响其它 runtime 写入。
    for stage in (STAGE_VERIFY, STAGE_ONLINE):
        try:
            bid = resolve_stage_bind_id(
                publish_repo,
                binding_repo,
                bot_pk=int(bot_pk or 0),
                bot_id=bot_id,
                stage=stage,
                env=runtime_env,
            )
        except (EngineStageNotLiveError, DeviceNotBoundError, ValueError):
            continue
        except Exception as exc:  # 仓库查询等失败：不阻断其它 runtime
            logger.warning(
                "[codefuse_runtime_targets] resolve stage=%s failed: bot_id=%s error=%s",
                stage, bot_id, exc,
            )
            continue
        if not bid:
            continue
        try:
            bid_int = int(bid)
        except (TypeError, ValueError):
            logger.warning(
                "[codefuse_runtime_targets] non-int published binding_id=%s bot_id=%s stage=%s",
                bid, bot_id, stage,
            )
            continue
        if bid_int not in ids:
            ids.append(bid_int)

    return ids


__all__ = ["resolve_codefuse_runtime_binding_ids", "SERVICE_BOT_TYPE"]
