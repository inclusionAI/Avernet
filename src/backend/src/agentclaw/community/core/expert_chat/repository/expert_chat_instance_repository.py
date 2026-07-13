"""ExpertChatInstance Repository Protocol.

Defines the abstract interface for the ``ac_expert_chat_instance``
persistence layer — the per-caller baas container lifecycle ledger,
distinct from the chat-session/​bot-list ledger in
``expert_chat_repository.py``.

Implementation: a single unified ORM body at
``plugins.expert_chat_repository.ExpertChatInstanceRepository`` (runs on
both the corp store and SQLite via the injected ``DatabasePlugin``).
"""
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class ExpertChatInstanceRepository(Protocol):
    """Protocol for the ``ac_expert_chat_instance`` persistence layer.

    Separate from :class:`ExpertChatRepository` — the instance table is
    the per-caller baas container lifecycle ledger, distinct from the
    chat-session/​bot-list ledger. A single unified ORM body under
    ``plugins.expert_chat_repository.ExpertChatInstanceRepository`` runs
    on both the corp store and SQLite.

    ``ext`` is round-tripped as JSON: callers hand in plain dicts, the
    repo serializes; getters return plain dicts (``None`` absent).
    """

    def get_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
    ) -> Optional[Dict[str, Any]]:
        """按 (bot_id, owner_id, user_id, env) 取 caller 实例记录。

        Returns:
            实例记录字典（``ext`` 已反序列化为 dict），不存在返回 None。
        """
        ...

    def upsert_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        status: str,
        ext: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """按唯一键 (bot_id, owner_id, user_id, env) 原子 insert-or-update。

        不存在则插入 ``status`` / ``ext``；已存在则整体覆盖 ``status`` /
        ``ext``（caller 持有实例全量 ext 时调用；局部更新走
        ``update_instance``）。返回 upsert 后的实例记录。

        Args:
            status: ``init`` / ``active`` / ``release``
            ext: 关联键 JSON（``bot_uuid`` / ``service_bot_publish_id`` /
                ``version`` / ``binding_id`` / ``baas_publish``）；None 时落空。

        Returns:
            upsert 后的实例记录字典。
        """
        ...

    def update_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        status: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """局部更新实例记录的 ``status`` 和/或 ``ext``。

        只更新非 None 的字段；``ext`` 为整体覆盖（非 merge）。记录不存在
        时为 no-op，返回 False（与 ``save_session`` 的 blind update 语义一致）。

        Returns:
            是否命中并更新了行。
        """
        ...