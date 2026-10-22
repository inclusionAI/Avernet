"""Transport-independent values for Bot-scoped knowledge configuration."""

from __future__ import annotations

from dataclasses import dataclass


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
