"""AC Bots 表的领域数据模型

字段顺序与 SQL 查询结果对应:
id, bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id, owner_id,
engine_types, status, binding_id, gmt_create, gmt_modified, modifier_id,
share_policy, is_delete, active_engine, device_id, env, owner_name, public, ext, bot_type
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class AcBotRecord:
    """AC Bots 表的领域数据模型"""

    id: int
    bot_id: str
    bot_name: str | None
    bot_desc: str | None
    entity_id: str
    entity_type: str
    creator_id: str
    owner_id: str
    engine_types: list[str] | None
    status: str
    binding_id: int | None
    gmt_create: datetime
    gmt_modified: datetime
    modifier_id: str | None
    share_policy: dict[str, Any] | None
    is_delete: int
    active_engine: str | None
    device_id: str | None
    env: str | None
    owner_name: str | None
    public: str
    ext: dict[str, Any] | None
    template_type: str | None = None
    bot_type: str = "personal"
