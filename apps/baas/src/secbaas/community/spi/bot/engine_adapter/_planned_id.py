"""Planned session id 解析 — core(bot_run) 与 plugins(engine adapter) 共用。"""

from __future__ import annotations


def extract_session_key_from_planned_id(planned_id: str) -> str:
    """从 planned session_id 中提取裸 session key（即 adapter 侧的 uuid）。

    planned id 格式：
    - openclaw → agent:main:session:{key}:user:{user_id}
    - aicoding / claude_code → user:{user_id}:session:{key}:agent:{tc_bot_id}
    - 其余（基类默认）→ agent:{tc_bot_id}:session:{key}:user:{user_id}

    adapter 的 create_session(uuid=...) 期望接收裸 key（如 c03ad14d-...），
    而非完整 planned id。本函数提取 ``session:`` 与 ``:user:`` / ``:agent:``
    之间的部分。

    如果格式不匹配（非 plan_session_id 构造的 id），原样返回。
    """
    if ":session:" not in planned_id:
        return planned_id
    _, _, rest = planned_id.partition(":session:")
    # 尝试匹配 "...session:{key}:user:{user_id}" 格式
    key, sep, _ = rest.partition(":user:")
    if not sep:
        # 尝试匹配 "...session:{key}:agent:{tc_bot_id}" 格式（user 前置）
        key, sep, _ = rest.partition(":agent:")
    if not sep:
        return planned_id
    return key
