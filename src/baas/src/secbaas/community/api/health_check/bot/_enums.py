"""
Provider 类型枚举

用于区分设备数据来源类型。
"""

from enum import StrEnum


class DeviceProviderType(StrEnum):
    """设备数据源类型

    用于区分设备数据的来源：
    - arca: 设备信息直接存储在 ac_entity_device_binding.device_props
    - baas: 设备信息存储在 baas_device 表，需要多表 JOIN 查询
    """

    ARCA = "arca"
    BAAS = "baas"
