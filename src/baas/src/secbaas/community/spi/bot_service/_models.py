"""BotService SPI — shared data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BotBindingData:
    """GET /api/service-bot/publish/{bot_id}/binding response data."""

    bot_id: str
    owner_id: str
    bot_type: str
    engine_type: str
    publish_id: int | None = None
    publish_status: str | None = None
    binding_id: int = 0
    device_provider: str = ""
    device_id: str = ""
    template_type: str | None = None


@dataclass
class LogRelationPayload:
    """POST /api/bot-chat/log-relations 请求体"""

    biz_scene: str
    biz_task_id: str
    engine: str
    collector: str
    refs: list[dict[str, Any]] = field(default_factory=list)
    user_id: str = ""
    bot_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "biz_scene": self.biz_scene,
            "biz_task_id": self.biz_task_id,
            "engine": self.engine,
            "collector": self.collector,
            "refs": self.refs,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
        }
