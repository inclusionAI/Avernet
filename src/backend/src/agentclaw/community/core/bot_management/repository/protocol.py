"""BotRepository Protocol.

Defines the abstract interface for bot persistence operations.
Implementations are provided in plugins/local and plugins/prod.
"""
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from agentclaw.community.core.bot_management.repository.models import BotRestartLockRecord


class BotLookupAmbiguousError(RuntimeError):
    """A caller-specific Bot lookup matched more than one live row."""


@runtime_checkable
class BotRepository(Protocol):
    """Protocol for bot repository implementations.

    Implementation: a single unified ORM body
    (plugins.bot_repository.BotRepository) runs on both prod
    OceanBase and local SQLite via the injected DatabasePlugin.
    """

    def insert(self, bot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new bot record."""
        ...

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        """Get bot by bot_id and owner_id."""
        ...

    def get_live_by_id_owner_and_env(
        self, *, bot_id: str, owner_id: str, env: str
    ) -> list[dict[str, Any]]:
        """Get all live exact matches in an explicitly selected environment."""
        ...

    def update_ext_by_id_owner_and_env(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        ext: dict[str, Any],
    ) -> dict[str, Any]:
        """Update ``ext`` only when exactly one live explicit-env row matches."""
        ...

    def get_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get bot by bot_id only (no owner check).

        Used when reading bot metadata (like active_engine) without
        verifying ownership. For permission checks, use get_by_id_and_owner.
        """
        ...

    def get_by_id_and_entity(
        self, bot_id: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get one live Bot by exact bot and entity identifiers in this env."""
        ...

    def get_unique_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get one live Bot by id or raise when the caller scope is ambiguous."""
        ...

    def list_by_owner(
        self, owner_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots by owner_id with pagination."""
        ...

    def list_by_owner_or_collaborator(
        self, owner_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots owned by the user or collaboratively managed by the user."""
        ...

    def list_by_entity(
        self,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots by entity with pagination."""
        ...

    def list_by_conditions(
        self,
        public: Optional[str] = None,
        bot_name: Optional[str] = None,
        owner_name: Optional[str] = None,
        bot_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots by conditions with pagination."""
        ...

    def list_by_search(
        self,
        public: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots with pagination and search."""
        ...

    def list_public_bots_by_owner_bot_pairs(
        self, pairs: List[tuple[str, str]]
    ) -> List[Dict[str, Any]]:
        """Live public bots matching any ``(bot_id, owner_id)`` pair, this env."""
        ...

    def list_domain_bots(
        self,
        page: int | None = None,
        page_size: int | None = None,
        keyword: str | None = None,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List domain bots (bots with ext.is_domain_bot=true).

        Domain bots are identified by checking the ext JSON field for is_domain_bot=true.
        Supports optional keyword search on bot_name.
        When page/page_size are omitted, returns all matching results.
        """
        ...

    def update_by_owner(
        self, bot_id: str, owner_id: str, update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update bot record by bot_id and owner_id."""
        ...

    def soft_delete_by_owner(self, bot_id: str, owner_id: str) -> bool:
        """Soft delete a bot by bot_id and owner_id."""
        ...

    def count_by_owner(self, owner_id: str, exclude_bot_type: str | None = None) -> int:
        """Count bots by owner_id.

        Args:
            owner_id: Owner user ID.
            exclude_bot_type: If provided, exclude bots with this bot_type from the count.
                Used to exclude desktop bots from cloud bot limits.
        """
        ...

    def exists_by_owner_and_bot_id(self, owner_id: str, bot_id: str) -> bool:
        """Check if a bot with specific bot_id exists for the owner."""
        ...

    def exists_by_bot_name(self, bot_name: str) -> bool:
        """Check if a bot with specific bot_name exists globally."""
        ...

    def get_by_bot_name(self, bot_name: str) -> Optional[Dict[str, Any]]:
        """Get bot by bot_name."""
        ...

    def get_by_binding_id(self, binding_id: int) -> Optional[Dict[str, Any]]:
        """Get bot by binding_id."""
        ...

    def get_device_provider_by_bot_id_and_owner(
        self, bot_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get device_provider info by bot_id and owner_id."""
        ...

    def get_device_provider_by_bot_id(
        self, bot_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get device_provider info by bot_id (no owner_id required)."""
        ...

    def search_bots(
        self,
        key: Optional[str] = None,
        bot_status: Optional[str] = None,
        public: Optional[str] = None,
        owner_id: Optional[str] = None,
        service_status_list: Optional[List[str]] = None,
        bot_type: Optional[str] = None,
        active_engine: Optional[str] = None,
        collaborator_user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        provider: Optional[str] = None,
        template_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """
        搜索 bots，关联发布记录。

        Args:
            key: 模糊搜索 bot_name 或 owner_name
            bot_status: ac_bots.status 过滤
            public: ac_bots.public 过滤
            owner_id: ac_bots.owner_id 过滤
            service_status_list: 服务状态列表过滤（不影响 bot 返回）
            bot_type: ac_bots.bot_type 过滤（如 "personal" 或 "service"）
            active_engine: ac_bots.active_engine 过滤（如 "openclaw"、"claude_code"、"aicoding"）
            collaborator_user_id: 协作者用户 ID，用于过滤该用户参与的 bot
            bot_id: ac_bots.bot_id 精确过滤
            provider: device_provider 过滤（如 "arca"、"daas"、"local"、"baas"）
            template_type: ac_bots.template_type 过滤
            page: 页码
            page_size: 每页数量

        Returns:
            (total, items) items 中每条记录包含 bot 和 publish 字段，
            当 collaborator_user_id 存在时，每条记录还包含 user_role 字段
        """
        ...

    def list_active_bots_by_entity(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
        bot_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """根据 entity_id 查询激活的 bot.

        Args:
            entity_id: 实体 ID（用户 ID）
            entity_type: 实体类型（可选）
            bot_type: bot 类型过滤（可选，如 "personal" 或 "service"）

        Returns:
            激活的 bot 列表，每项包含 bot_id, binding_id, owner_id 等字段
        """
        ...


@runtime_checkable
class BotRestartLockRepositoryProtocol(Protocol):
    """Protocol for the bot restart idempotency lock repository.

    The lock is keyed on ``(env, entity_id, bot_id)`` and backed by a
    UNIQUE constraint on ``ac_bot_restart_lock`` — that constraint is the
    guard. A single unified ORM body
    (``plugins.bot_restart_lock_repository.BotRestartLockRepository``) runs
    on both prod OceanBase and local SQLite via the injected DatabasePlugin.
    """

    def acquire(
        self, env: str, entity_id: str, bot_id: str, holder_user_id: str
    ) -> Optional[BotRestartLockRecord]:
        """Acquire the lock by inserting a row.

        Stamps a random ``lock_token`` (fencing token) on the row and returns
        the inserted record (carrying that token) on success, or ``None`` if a
        row for ``(env, entity_id, bot_id)`` already exists (UNIQUE violation).
        The caller must keep the token and pass it to ``release`` so a delete
        only ever removes the exact row it acquired.
        """
        ...

    def get(
        self, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotRestartLockRecord]:
        """Return the lock row for ``(env, entity_id, bot_id)`` if present."""
        ...

    def get_if_stale(
        self, env: str, entity_id: str, bot_id: str, ttl_seconds: int
    ) -> Optional[BotRestartLockRecord]:
        """Return the lock row only if it is older than ``ttl_seconds``.

        Staleness is evaluated DB-side (comparing ``gmt_create`` against the
        database clock) to avoid app/DB clock-skew. Returns ``None`` when no
        row exists or the existing row is still fresh.
        """
        ...

    def release(
        self, env: str, entity_id: str, bot_id: str, lock_token: str
    ) -> bool:
        """Release the lock by hard-deleting the row — only if it's still ours.

        Compare-and-delete: ``DELETE WHERE (env, entity_id, bot_id) matches AND
        lock_token = :lock_token``. The token guard prevents deleting a row that
        a different holder acquired after ours was reaped (the stale-reaper and
        late-async-release races). Returns ``True`` if a row was deleted.
        """
        ...
