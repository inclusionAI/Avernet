"""Atomic storage contract for per-engine build rules."""

from __future__ import annotations
from abc import abstractmethod
from typing import Literal, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.kernel.build_ignore import BuildIgnoreConfig


class BuildIgnoreRepositoryProtocol(Protocol):
    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, engine_type: str
    ) -> BuildIgnoreConfig | None: ...

    @abstractmethod
    def change(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        operation: Literal["add", "remove"],
        path: str,
        modifier: str,
    ) -> tuple[BuildIgnoreConfig, bool]: ...
