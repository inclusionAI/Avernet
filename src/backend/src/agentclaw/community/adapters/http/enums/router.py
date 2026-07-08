"""
枚举说明 Router
提供系统中所有枚举类型的 code -> desc 映射关系
"""
from enum import Enum
from typing import Type
from pydantic import BaseModel
from fastapi import APIRouter

from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/enums", tags=["enums"])


class EnumItem(BaseModel):
    """枚举项说明"""
    code: str
    desc: str


class EnumGroup(BaseModel):
    """枚举分组"""
    name: str
    items: list[EnumItem]


class EnumResponse(BaseModel):
    """枚举响应"""
    success: bool
    data: dict[str, list[EnumItem]]
    message: str


# ============== 枚举注册表 ==============
_ENUM_REGISTRY: dict[str, dict[str, str]] = {}


def register_enum(name: str, enum_class: Type[Enum]) -> None:
    """注册枚举类型

    Args:
        name: 枚举分组名称，如 "publish", "device"
        enum_class: 枚举类
    """
    _ENUM_REGISTRY[name] = {e.value: _get_enum_desc(e) for e in enum_class}
    logger.info(f"[register_enum] Registered: {name} with {len(enum_class)} items")


def _get_enum_desc(enum_member: Enum) -> str:
    """获取枚举成员的描述信息

    优先级:
    1. 枚举成员的 docstring (第一行)
    2. 枚举成员 name 本身
    """
    # 尝试获取 docstring
    if enum_member.__doc__:
        return enum_member.__doc__.strip().split('\n')[0].strip()
    return enum_member.name


# ============== API Endpoints ==============

@router.get("", response_model=EnumResponse)
async def get_all_enums() -> EnumResponse:
    """获取所有枚举说明

    Returns:
        按分组组织的枚举 code -> desc 映射
    """
    result: dict[str, list[EnumItem]] = {}
    for name, mapping in _ENUM_REGISTRY.items():
        result[name] = [EnumItem(code=k, desc=v) for k, v in mapping.items()]
    return EnumResponse(success=True, data=result, message="获取成功")


@router.get("/{enum_name}", response_model=EnumResponse)
async def get_enum_by_name(enum_name: str) -> EnumResponse:
    """获取指定枚举说明

    Args:
        enum_name: 枚举分组名称，如 "publish", "device"

    Returns:
        指定枚举的 code -> desc 映射
    """
    if enum_name not in _ENUM_REGISTRY:
        return EnumResponse(success=False, data={}, message=f"枚举 '{enum_name}' 不存在")
    mapping = _ENUM_REGISTRY[enum_name]
    data = {enum_name: [EnumItem(code=k, desc=v) for k, v in mapping.items()]}
    return EnumResponse(success=True, data=data, message="获取成功")


# ============== 初始化注册 ==============

# DeviceBindingStatus, EntityType, Env 已迁移到 core/devices/models.py
from agentclaw.community.core.devices.models import DeviceBindingStatus, EntityType, Env  # noqa: E402


class DatabaseMode(Enum):
    """数据库模式（内联副本，原定义在 services/openclawserver/server/db.py）"""
    SQLITE = "sqlite"
    ZDAS = "zdas"


def _init_register():
    """初始化注册所有系统枚举"""
    register_enum("device_binding", DeviceBindingStatus)
    register_enum("entity_type", EntityType)
    register_enum("env", Env)
    register_enum("database_mode", DatabaseMode)


# 模块加载时自动初始化
_init_register()
