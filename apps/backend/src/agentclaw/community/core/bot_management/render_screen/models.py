"""Bot Render Screen — domain record."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class RenderScreenRecord:
    """数据库记录：ac_bot_render_screen."""

    id: int
    bot_id: str
    owner_id: str
    name: str
    cdn_url: str
    env: str
    creator_id: str
    is_delete: int
    gmt_create: datetime | None
    gmt_modified: datetime | None
