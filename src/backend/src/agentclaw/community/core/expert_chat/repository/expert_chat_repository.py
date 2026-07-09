"""
ExpertChat Repository Protocol.

Defines the abstract interface for expert chat data persistence.
Implementations are provided in plugins/local and plugins/prod.
"""
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ExpertChatRepository(Protocol):
    """Protocol for expert chat repository implementations.

    Implementation: a single unified ORM body at
    ``plugins.expert_chat_repository.ExpertChatRepository`` (runs on
    both the corp store and SQLite via the injected ``DatabasePlugin``).
    """

    def add_chat_bot(self, user_id: str, bot_id: str, owner_id: str) -> Dict[str, Any]:
        """添加专家Bot到用户对话列表

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            创建的记录
        """
        ...

    def remove_chat_bot(self, user_id: str, bot_id: str, owner_id: str) -> bool:
        """从用户对话列表移除专家Bot（软删除）

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            是否成功
        """
        ...

    def list_chat_bots(self, user_id: str) -> List[Dict[str, str]]:
        """获取用户对话列表中的Bot信息列表

        Args:
            user_id: 用户ID

        Returns:
            包含 bot_id 和 owner_id 的字典列表
        """
        ...

    def get_session(self, user_id: str, bot_id: str, owner_id: str) -> Optional[str]:
        """获取 user-bot 的 session_key

        Args:
            user_id: 用户ID
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            session_key 或 None
        """
        ...

    def save_session(self, user_id: str, bot_id: str, owner_id: str, session_key: str) -> None:
        """保存 user-bot 的 session_key

        Args:
            user_id: 用户ID
            bot_id: Bot ID
            owner_id: Bot 所有者ID
            session_key: session:uuid 格式
        """
        ...

    def delete_session(self, user_id: str, bot_id: str, owner_id: str) -> bool:
        """删除 user-bot 的 session（只清空 session_key，不删除记录）

        Args:
            user_id: 用户ID
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            是否成功
        """
        ...
