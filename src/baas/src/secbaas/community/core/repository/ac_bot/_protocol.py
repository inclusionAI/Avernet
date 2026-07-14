"""AC Bots Repository Protocol — 定义对 ac_bots 表的访问接口。"""

from typing import Protocol, runtime_checkable

from ._record import AcBotRecord


@runtime_checkable
class AcBotRepository(Protocol):
    """AC Bots Repository Protocol

    定义对 ac_bots 表的访问接口。
    """

    def get_by_entity_id_bot_id_env(
        self,
        *,
        entity_id: str,
        bot_id: str,
        env: str,
    ) -> AcBotRecord | None: ...

    def get_by_bot_id_env_exclude_default(
        self,
        *,
        bot_id: str,
        env: str,
    ) -> AcBotRecord | None: ...

    def get_active_by_entity_id_bot_id_env(
        self,
        *,
        entity_id: str,
        bot_id: str,
        env: str,
    ) -> AcBotRecord | None:
        """Query ACTIVE bot by entity_id, bot_id, and env.

        Like get_by_entity_id_bot_id_env but also filters status='ACTIVE'.
        Used by health-check draft queries to match the SQL:
            WHERE is_delete=0 AND status='ACTIVE'
        """
        ...

    def list_active_bots(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str = "prod",
        bot_type: str | None = None,
    ) -> tuple[int, list[AcBotRecord]]: ...
