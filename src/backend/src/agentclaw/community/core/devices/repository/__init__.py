"""Core Device Repository — 业务数据访问层.

根据 README.md 规范：
- 业务 Repository 放在 core/<module>/ 内部，不放 plugin_api/
- plugin_api/ 只放纯基础设施接口（DatabasePlugin、CachePlugin 等）

本模块包含:
- DeviceBindingRecord: 设备绑定记录数据类
- DeviceBindingRepository: 设备仓库 Protocol 接口
- 统一实现 (plugins/device_repository.py): 通过注入的 DatabasePlugin
  在 OceanBase prod 与本地 SQLite 上运行同一套 ORM 代码 (S5, 2026-05-20)
"""

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository

__all__ = [
    "DeviceBindingRecord",
    "DeviceBindingRepository",
]
