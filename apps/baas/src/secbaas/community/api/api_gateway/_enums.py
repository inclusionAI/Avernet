"""
API Key 枚举定义
"""

from enum import StrEnum


class APIKeyStatus(StrEnum):
    """API Key 状态枚举"""

    ACTIVE = "ACTIVE"  # 可用
    INACTIVE = "INACTIVE"  # 停用
    REVOKED = "REVOKED"  # 已吊销
