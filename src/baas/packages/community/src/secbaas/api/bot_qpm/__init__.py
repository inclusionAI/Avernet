"""Bot QPM 配置管理 API 接口定义。"""

from __future__ import annotations

from ._models import BotQpmConfigItem, BotQpmConfigListResult
from ._protocols import BotQpmManageService

__all__ = [
    "BotQpmConfigItem",
    "BotQpmConfigListResult",
    "BotQpmManageService",
]
