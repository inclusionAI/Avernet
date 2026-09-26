"""Bot QPM 配置管理 service 包。"""

from secbaas.community.api.bot_qpm import (
    BotQpmConfigItem,
    BotQpmConfigListResult,
    BotQpmManageService,
)

from ._bot_qpm_service import DefaultBotQpmManageService

__all__ = [
    "BotQpmConfigItem",
    "BotQpmConfigListResult",
    "BotQpmManageService",
    "DefaultBotQpmManageService",
]
