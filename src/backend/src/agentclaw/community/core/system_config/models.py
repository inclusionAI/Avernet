"""系统配置数据模型

两表设计：
- ConfigCategoryRecord: 分类目录表记录 (ac_config_category)
- ConfigItemRecord: 配置项表记录 (ac_config_item)

配置项通过 parent_id 关联分类目录。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ConfigCategory(str, Enum):
    """配置分类枚举"""

    DEVICE = "device"           # 设备配置
    MANAGEMENT = "management"   # 管理配置
    UPGRADE = "upgrade"         # 升级配置
    SECURITY = "security"       # 安全配置
    SYSTEM = "system"           # 系统配置


# ============================================================
# 分类目录表 Record
# ============================================================

@dataclass(slots=True)
class ConfigCategoryRecord:
    """配置分类目录数据库记录

    对应 ac_config_category 表结构。
    """

    id: int
    category: str               # 分类标识
    category_name: str          # 分类名称（中文）
    description: str | None     # 分类描述
    env: str                    # 环境标识
    creator: str | None         # 创建人
    modifier: str | None        # 修改人
    gmt_create: datetime | None
    gmt_modified: datetime | None


# ============================================================
# 配置项表 Record
# ============================================================

@dataclass(slots=True)
class ConfigItemRecord:
    """配置项数据库记录

    对应 ac_config_item 表结构，通过 parent_id 关联分类目录。
    """

    id: int
    parent_id: int              # 所属分类ID
    config_key: str             # 配置键
    config_value: str           # 配置值（JSON 字符串）
    description: str | None     # 配置描述
    creator: str | None         # 创建人
    modifier: str | None        # 修改人
    gmt_create: datetime | None
    gmt_modified: datetime | None
