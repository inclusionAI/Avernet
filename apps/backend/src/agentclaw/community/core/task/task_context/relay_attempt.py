"""relay 执行代次 fence 的 core 落点(spec §14.5,2026-10-27 定稿)。

恒时比对与空值拒绝是领域语义,归属 core;adapter 侧
``adapters/http/task/relay_guard.require_relay_attempt`` 委托本函数,既有
relay 路由零行为变化。同一校验供产物写口的 relay 分支复用
(``TaskArtifactObjectFileService.upload_object_file`` /
``TaskArtifactApiService.add_artifact``,spec §14.2)。
"""
from __future__ import annotations

import hmac

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import TaskNode


def verify_relay_attempt(node: TaskNode, relay_token: str) -> None:
    """拒绝 Backend 轮转节点代次后的过期执行(恒时比较,双空拒绝)。

    调用方语义:``node`` 须为已按 task_id/node_id 定位的图上节点;
    失配/缺失即抛 ``TaskStateError``(envelope 既有 409 映射)。
    """
    current = str(node.run_info.extend_props.get("relay_token") or "")
    if not current or not relay_token or not hmac.compare_digest(current, relay_token):
        raise TaskStateError("relay execution attempt has expired")


__all__ = ["verify_relay_attempt"]