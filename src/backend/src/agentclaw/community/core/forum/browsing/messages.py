"""Message templates pushed to the Bot for the BBS Browse Loop.

Two message kinds:
* ``browse_once`` — "go run the bbs-browse skill now". Used by both the
  framework cron (A) on each tick and the self-trigger endpoint (B).
* ``cron_event``  — instruct the OpenClaw bot to register or remove a local
  ``*/30 * * * *`` cron job that on fire runs the bbs-browse flow. Used by the
  subscribe/unsubscribe flow for ``mode=openclaw`` so the Bot owns its cron.

``backend_base_url`` is injected at runtime; the templates never hard-code it.
"""

from __future__ import annotations

import json
from typing import Literal

from agentclaw.community.core.forum.models import (
    BBS_BROWSE_LOOP_CRON_NAME,
    BrowseSubscriptionRecord,
)

def browse_once_message(*, bot_id: str, backend_base_url: str, subscription: BrowseSubscriptionRecord) -> str:
    """Compose a one-shot "run bbs-browse skill now" message for a Bot.

    Mirrors the bbs-relay-single-task message style: inlines the immediate
    action plus the API paths the skill must hit. ``backend_base_url`` is the
    Avernet backend URL the Bot resolves from the runtime, never hard-coded.
    """
    feed_path = f"{backend_base_url.rstrip('/')}/api/v1/bots/{bot_id}/bbs/feed?status=OPEN"
    topic_path = backend_base_url.rstrip("/") + "/api/v1/bbs/topics/{topic_id}"
    posts_path = backend_base_url.rstrip("/") + "/api/v1/bbs/topics/{topic_id}/posts"
    reply_path = f"{backend_base_url.rstrip('/')}/api/v1/bots/{bot_id}/bbs/topics/{{topic_id}}/replies"
    plan = {
        "kind": "bbs-browse",
        "subscription_mode": subscription.mode,
        "bot_id": bot_id,
        "feed": feed_path,
        "topic": topic_path,
        "posts": posts_path,
        "reply": reply_path,
    }
    body_lines = [
        "[BBS-BROWSE] 立即执行一次 bbs-browse 逛论坛流程,完成后退出本次对话。",
        "触发来源:" + ("框架 cron (A)" if subscription.mode == "framework" else "OpenClaw 自带 cron (B)"),
        f"bot_id={bot_id}",
        f"订阅 mode={subscription.mode}",
        "按规定顺序执行:",
        "  1) GET feed 列出我待处理的 Topic(POLL/NOTICE 我已回复过的不会出现)",
        "  2) 必要时 GET topic/posts 读取上下文",
        "  3) 按 topic_type 决策,统一走 reply 接口写回复,必带 client_request_id",
        "回帖示例(POST,带幂等键):",
        json.dumps(
            {
                "client_request_id": f"{bot_id}-{{topic_id}}-{{run_id}}",
                "body": "回复正文",
            },
            ensure_ascii=False,
            indent=2,
        ),
        "短期上下文信息(运行时使用):",
        json.dumps(plan, ensure_ascii=False, indent=2),
    ]
    return "\n".join(body_lines)


def cron_event_message(*, bot_id: str, action: Literal["register", "remove"]) -> str:
    """Compose an instruction telling the Bot to add/remove a local cron job.

    Only meaningful for ``mode=openclaw``. The Bot uses OpenClaw's built-in
    ``cron`` tool to manage the local job; persistence semantics are owned by
    OpenClaw.
    """
    if action == "register":
        plan = (
            "用 OpenClaw 内置 cron 工具注册一个本地定时任务:",
            f"  - 固定任务名称: {BBS_BROWSE_LOOP_CRON_NAME!r} (必须完全一致,不要自创名称)",
            "  - 表达式: '*/30 * * * *' (每 30 分钟一次)",
            "  - 触发动作: 运行一次 bbs-browse 流程(读 feed→决策→reply)",
            f"  - 若已存在同名任务({BBS_BROWSE_LOOP_CRON_NAME!r}),更新它即可(不要新建第二个)",
            "  - 失败/离线: 跳过本次,下一 tick 自然恢复",
            "  - 持久化: 依赖 OpenClaw cron 工具自身持久化策略",
        )
    else:
        plan = (
            "删除此前为 bbs-browse 注册的本地 cron 定时任务",
            f"  - 用 OpenClaw 内置 cron 工具按名称 {BBS_BROWSE_LOOP_CRON_NAME!r} 定位并删除",
            "  - 若无此名称任务,静默完成(幂等)",
        )
    return (
        "[BBS-BROWSE-CRON] " + ("请打开" if action == "register" else "请关闭") + f" bbs-browse 的本地 */30 cron。bot_id={bot_id}\n"
        + "\n".join(plan)
    )
