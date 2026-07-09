"""Bot QPM 配置记录。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BotQpmRecord:
    """每个 bot 的每分钟请求数（QPM）上限配置。"""

    id: int
    bot_id: str
    qpm: int
    env: str | None
    gmt_create: datetime | None
    gmt_modified: datetime | None
