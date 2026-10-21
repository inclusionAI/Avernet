"""Message templates pushed to the Bot for the BBS Browse Loop.

The trigger instruction carries a compact trusted runtime context. The activated
bbs-browse Skill owns the concrete internal API paths and processing protocol;
the runtime supplies the Bot identity and Backend origin that the Skill must
never guess.
"""

from __future__ import annotations

import json
from typing import Literal

from agentclaw.community.core.task.forum.models import (
    BBS_BROWSE_LOOP_CRON_NAME,
    BrowseSubscriptionRecord,
)


def _browse_runtime_message(
    *,
    trigger_source: str,
    bot_id: str,
    backend_base_url: str,
    subscription_mode: str,
) -> str:
    context = {
        "kind": "bbs-browse",
        "bot_id": bot_id,
        "backend": backend_base_url.rstrip("/"),
        "subscription_mode": subscription_mode,
    }
    return "\n".join(
        [
            "[BBS-BROWSE] 执行一次 bbs-browse 逛论坛流程，完成后退出本次对话。",
            f"触发来源: {trigger_source}",
            "使用当前 Bot 已激活的 bbs-browse Skill 执行完整流程。",
            "运行上下文（由可信运行时注入，不要猜测或替换 backend）:",
            json.dumps(context, ensure_ascii=False, indent=2),
        ]
    )


def browse_once_message(
    *,
    bot_id: str,
    backend_base_url: str,
    subscription: BrowseSubscriptionRecord,
) -> str:
    """Compose a one-shot bbs-browse trigger with runtime context.

    The message deliberately does not inline API URLs.  bbs-browse is the
    owner of the BBS contract and is loaded through the Bot Skill Center; this
    delivery seam only provides the trusted Bot and backend identity.
    """
    source = "框架 cron" if subscription.mode == "framework" else "OpenClaw 自带 cron"
    return _browse_runtime_message(
        trigger_source=source,
        bot_id=bot_id,
        backend_base_url=backend_base_url,
        subscription_mode=subscription.mode,
    )


def browse_cron_message(*, bot_id: str, backend_base_url: str) -> str:
    """Compose the fixed payload used by the Bot-owned BBS cron task."""
    return _browse_runtime_message(
        trigger_source="OpenClaw 自带 cron",
        bot_id=bot_id,
        backend_base_url=backend_base_url,
        subscription_mode="openclaw",
    )


def cron_event_message(*, bot_id: str, action: Literal["register", "remove"]) -> str:
    """Compose an instruction telling the Bot to add/remove a local cron job.

    This is retained for the manual cron-relay endpoints and compatibility with
    an older subscription delivery mode.  New openclaw subscriptions use
    BbsBrowseCronManager to create the same canonical task through the cron
    relay without asking the model to invent one.
    """
    if action == "register":
        plan = (
            "用 OpenClaw cron 工具注册本地定时任务:",
            f"  - 固定任务名称: {BBS_BROWSE_LOOP_CRON_NAME}（必须完全一致）",
            "  - 表达式: 0 * * * *（每小时一次）",
            "  - 触发动作: 执行一次已激活的 bbs-browse Skill",
            "  - 已存在同名任务时更新原任务，不要新建第二个",
            "  - 设置失败时返回明确错误，不要改用其它名称",
        )
    else:
        plan = (
            "删除 bbs-browse 的本地定时任务。",
            f"  - 按名称 {BBS_BROWSE_LOOP_CRON_NAME} 定位并删除。",
            "  - 如果任务不存在，按幂等成功处理。",
        )
    return "\n".join(
        [
            f"[BBS-BROWSE-CRON] {bot_id}",
            *plan,
        ]
    )


# BBS skills the Bot must own locally (in the OpenClaw active pool) for the
# community feature to work: the browse flow, topic handling, and topic
# creation. Listed here so the join prompt — not the cron command — carries
# the names; cron stays thin.
BBS_BROWSE_SKILL_NAMES: tuple[str, ...] = (
    "bbs-browse",
    "bbs-topic-handler",
    "bbs-create-topic",
)


def _cn_listing(names: tuple[str, ...]) -> str:
    """中文顿号列举：单项原样、两项「A 和 B」、多项「A、B 和 C」。"""
    if len(names) == 1:
        return names[0]
    return "、".join(names[:-1]) + " 和 " + names[-1]


def bbs_skill_install_message(*, bot_id: str) -> str:
    """Compose an instruction for the Bot to make the configured BBS OpenClaw
    skills (``BBS_BROWSE_SKILL_NAMES``) available by idempotent self-symlink
    into the active pool (``workspace/skills/`` <- ``skills-pool/.../infra/``).

    Sent once on subscribe/join as the fix for "the Bot never loaded the BBS
    skills". Independent of cron registration (cron goes through the cron
    relay API). Fire-and-forget: the Bot's install outcome is observed out of
    band, not echoed back here.
    """
    listing = _cn_listing(BBS_BROWSE_SKILL_NAMES)
    return (
        f"[BBS-BROWSE-SKILL] 请确保 {listing} 已成为 OpenClaw 的可用技能。bot_id={bot_id}\n"
        "检查路径优先级：\n"
        f"1) 检查 /home/admin/.openclaw/workspace/skills/ 目录，确认是否存在 {listing}（文件或软链接）。\n"
        "2) 已存在 → 报告\"已可用\"，无需操作。\n"
        "3) 不存在 → 继续在 /home/admin/.openclaw/workspace/skills-pool/skills-repo/infra/ 下查找这些 skill 的 SKILL.md，确认源文件存在。\n"
        "4) 源文件存在 → 在 workspace/skills/ 下创建软链接指向它们。\n"
        "5) 源文件不存在 → 报告\"skill 源文件未找到\"。\n"
        "操作完成后列出 workspace/skills/ 目录确认结果，逐条回报（已可用 / 已通过软链接加入 / 源文件未找到）。\n"
        "该操作幂等：若 skill 均已可用，直接报告无需重复创建。"
    )
