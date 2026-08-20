"""
核心 ORM 模型定义 — 技能中心使用的统一入口。

当前实现：从 services/ 旧架构 re-export，因为 SQLAlchemy 要求
所有 ORM 模型类在同一个 Base (declarative_base) 上只能注册一次。
旧架构的模型仍被其他模块（bot、chat 等）间接加载，
如果在新架构中重新定义同表名类，会导致 mapper 冲突。

未来计划：当所有模块都完成迁移后，将模型定义源码移到此处，
旧路径反向 re-export。
"""

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill, UserDefaultSkillSet  # noqa: F401
from agentclaw.community.core.models.mcp import (  # noqa: F401
    SkillSetMCPServer,
    UserMCPConfig,
)
from agentclaw.community.core.models.skill_propagation_log import SkillPropagationLog  # noqa: F401
from agentclaw.community.core.models.skill_center_sync_log import SkillCenterSyncLog  # noqa: F401
