"""Request-scoped content context and delivery reply values."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContentAccess:
    actor_id: str
    credentials: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True)
class ContentReply:
    status: int
    body: bytes
    headers: dict[str, str]
