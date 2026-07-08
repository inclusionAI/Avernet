"""
设备绑定数据访问层

参考 agentclaw 项目实现，使用 @with_db_session 装饰器管理数据库连接
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class DeviceBindingStatus(StrEnum):
    """设备绑定状态枚举"""

    PENDING = "PENDING"  # 待激活
    ACTIVE = "ACTIVE"  # 已激活
    RELEASED = "RELEASED"  # 已释放


@dataclass(slots=True)
class DeviceBindingRecord:
    """Database record for device binding."""

    id: int
    entity_id: str
    entity_type: str
    device_id: str
    device_provider: str
    env: str
    device_props: dict[str, Any]
    status: str
    apply_reason: str | None
    applied_by: str
    release_reason: str | None
    released_by: str | None
    released_at: datetime | None
    last_alive_at: datetime | None
    gmt_create: datetime | None
    gmt_modified: datetime | None
