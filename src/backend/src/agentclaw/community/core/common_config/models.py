"""通用配置数据模型。

对应 ``ac_common_config`` 表，用于按业务 code + 配置 code + 环境
存储通用配置项。配置值与扩展属性在 service 层按 JSON 友好方式处理，
repository 层只存储字符串。
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class CommonConfigRecord:
    """``ac_common_config`` 数据库记录。"""

    id: int
    business_code: str
    param_name: str
    param_value: str | None
    business_name: str | None
    param_code: str
    enable: str
    ext_info: str | None
    env: str
    gmt_create: datetime | None
    gmt_modified: datetime | None
