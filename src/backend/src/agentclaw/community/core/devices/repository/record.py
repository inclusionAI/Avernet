"""Device Binding Record — 设备绑定记录数据类.

对应数据库表 ac_entity_device_binding 的一条记录。
从 plugins/device_repository.py 移入 core/ 层，因为它是业务数据模型。
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


@dataclass
class DeviceBindingRecord:
    """设备绑定记录数据类.

    对应数据库表 ac_entity_device_binding 的一条记录。
    """

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

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict — compatible with Pydantic model_dump()."""
        result = asdict(self)
        # Convert datetime objects to ISO format strings for JSON serialization
        for key in ("released_at", "last_alive_at", "gmt_create", "gmt_modified"):
            val = result.get(key)
            if val is not None and hasattr(val, "isoformat"):
                result[key] = val.isoformat()
        return result
