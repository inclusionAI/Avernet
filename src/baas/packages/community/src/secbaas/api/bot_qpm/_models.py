from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BotQpmConfigItem:
    """QPM 配置返回项。"""

    id: int
    bot_id: str
    qpm: int
    env: str | None
    gmt_create: datetime | None
    gmt_modified: datetime | None


@dataclass(slots=True)
class BotQpmConfigListResult:
    """QPM 配置列表返回。"""

    items: list[BotQpmConfigItem]
    total: int
