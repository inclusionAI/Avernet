"""Shared skill_center constants.

锁占用标识：同步因"锁被占用"而跳过时写入 result["error"] 的 sentinel 值。
这不是真实失败——只是别的 worker/机器正在同步。

为什么单独成模块（而非放在 services/git_sync.py）：
HTTP 路由层（sync_market，adapters/http/skill_center/skills.py）需要据此
区分"锁占用 → 200 友好提示"与"真实失败 → 500"。但架构约束（R8）禁止
adapters/http 从 core/<m>/services/ 导入。把这些纯数据常量放在不带
``services`` 路径的模块里，route 与 service 都能共享，且不违反分层。
"""
from __future__ import annotations

# 进程内锁（GlobalSyncLock，threading.Lock）被占用。
GLOBAL_SYNC_LOCK_HELD = "GlobalSyncLock held"
# 跨机分布式锁（SET NX）被占用。
DISTRIBUTED_LOCK_HELD = "Distributed lock held"

# 路由层判定"锁占用（应友好提示）"的全集。
# 用常量替代裸字符串，避免 service↔route 的字符串契约因改字面量而静默断裂。
LOCK_HELD_ERRORS = frozenset({GLOBAL_SYNC_LOCK_HELD, DISTRIBUTED_LOCK_HELD})
