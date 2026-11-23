from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ManagedBotServiceProtocol(Protocol):
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

    def snapshot(self, *, bot_id: str, owner_id: str, template_key: str) -> dict[str, Any]: ...

    def snapshot_context(self, *, bot_id: str, owner_id: str, template_key: str) -> dict[str, Any]: ...

    async def restart(
        self,
        *,
        bot_id: str,
        owner_id: str,
        template_key: str,
        template_config: dict[str, Any] | None = None,
        expected_template_config_hash: str | None = None,
    ) -> dict[str, Any]: ...

    def statuses(self, *, template_key: str, targets: list[tuple[str, str]]) -> list[dict[str, Any]]: ...
