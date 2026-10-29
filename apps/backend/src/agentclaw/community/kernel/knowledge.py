"""Transport-independent values for Bot-scoped knowledge configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from unicodedata import category, normalize


# Reserved durable result: generic configuration editors must not mutate it.
KNOWLEDGE_ONLINE_CONFIG_KEY = "knowledge_online_selection"


def require_generic_bot_config_key(config_key: str) -> None:
    # Common MySQL collations are case/accent/width insensitive and may ignore
    # trailing spaces. This is a reserved namespace, not an editable spelling.
    normalized = "".join(
        char for char in normalize("NFKD", config_key)
        if category(char) not in {"Mn", "Me", "Cf", "Cc"}
    ).casefold().rstrip()
    if normalized == KNOWLEDGE_ONLINE_CONFIG_KEY:
        raise ValueError("knowledge publication summary is managed by publication completion")


KnowledgeSelectionStage = Literal["draft", "online"]


def require_selection_stage(value: str) -> KnowledgeSelectionStage:
    if value not in ("draft", "online"):
        raise ValueError("invalid selection_stage")
    return value


def require_knowledge_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError(f"invalid {name}")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"invalid {name}")
    return value


class KnowledgeError(Exception):
    """Safe, stable business failure; transport adapters map the status."""

    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class BotKnowledgeScope:
    bot_id: str
    owner_id: str
    env: str

    def __post_init__(self) -> None:
        for name in ("bot_id", "owner_id", "env"):
            require_knowledge_id(getattr(self, name), name)


@dataclass(frozen=True)
class KnowledgePublicationPolicy:
    """Publication effects; missing configuration retains the disabled fallback."""

    enabled: bool = False
