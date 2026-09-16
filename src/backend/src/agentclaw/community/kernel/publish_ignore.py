"""Neutral command and target values shared by publish-ignore boundaries."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PublishIgnoreCommand:
    bot_id: str
    entity_id: str
    stage: Literal["draft", "verify", "online"]
    operation: Literal["add", "remove"]
    path: str
    request_id: str


@dataclass(frozen=True)
class PublishIgnoreBinding:
    id: int
    device_provider: str
    device_id: str


class PublishIgnoreError(ValueError):
    """Safe domain failure; code is suitable for client and audit output."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
