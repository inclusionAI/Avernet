from abc import ABC, abstractmethod
from typing import Any


class TemplateBotRepositoryProtocol(ABC):
    @abstractmethod
    def query(
        self,
        *,
        template_key: str,
        offset: int,
        limit: int,
        search: str = '',
        stage: str = '',
        outdated_before: int | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def snapshot(self, *, bot_id: str, owner_id: str, template_key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def statuses(self, *, template_key: str, targets: list[tuple[str, str]]) -> list[dict[str, Any]]: ...
