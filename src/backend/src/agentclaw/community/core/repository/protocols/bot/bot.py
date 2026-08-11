"""Repository contracts owned by the ``bot`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord
    from agentclaw.community.core.bot_management.repository.models import BotRestartLockRecord


@runtime_checkable
class BotRepository(Protocol):
    """Protocol for bot repository implementations.

    Implementation: a single unified ORM body
    (plugins.bot_repository.BotRepository) runs on both prod
    OceanBase and local SQLite via the injected DatabasePlugin.
    """

    @abstractmethod
    def insert(self, bot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new bot record."""
        ...

    @abstractmethod
    def get_by_id_and_owner(
        self,
        bot_id: str,
        owner_id: str,
        *,
        execution_options: dict | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Get bot by bot_id and owner_id.

        ``execution_options`` is forwarded to the SQLAlchemy query via
        ``Query.execution_options`` so callers can opt out of cross-cutting
        guards (e.g. ``{"skip_avernet_tenant_guard": True}`` for the
        refresh-token callback, which is served under ``/api`` and thus runs
        under the DEFAULT tenant but must resolve an external-tenant bot).
        ``None`` means no override — preserves existing behavior.
        """
        ...

    @abstractmethod
    def get_live_by_id_owner_and_env(
        self, *, bot_id: str, owner_id: str, env: str
    ) -> list[dict[str, Any]]:
        """Get all live exact matches in an explicitly selected environment."""
        ...

    @abstractmethod
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

    @abstractmethod
    def get_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get bot by bot_id only (no owner check).

        Used when reading bot metadata (like active_engine) without
        verifying ownership. For permission checks, use get_by_id_and_owner.
        """
        ...

    @abstractmethod
    def get_by_id_and_entity(
        self, bot_id: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get one live Bot by exact bot and entity identifiers in this env."""
        ...

    @abstractmethod
    def get_unique_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get one live Bot by id or raise when the caller scope is ambiguous."""
        ...

    @abstractmethod
    def list_by_owner(
        self, owner_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots by owner_id with pagination.

        Scoped by current env AND tenant (via the ``BotModel`` avernet_tenant
        guard). Callers that key semantics off this — ``is_first_bot``,
        ``delete_bot`` earliest-protection, ``create_bot_for_others`` owner
        lookup — must be aware the result reflects only the current tenant.
        """
        ...

    @abstractmethod
    def list_live_bot_ids_by_owner(self, owner_id: str) -> list[str]:
        """Every live ``bot_id`` this owner has, in one query.

        Unpaginated and id-only on purpose: it exists so a caller holding a set
        of bot ids can drop the dead ones without issuing a lookup per id.
        Returning ids rather than rows keeps that cheap even for an owner with
        many bots.

        Scoped by current env AND tenant, like its siblings, and excludes
        soft-deleted rows — so "in this list" means "live for this owner right
        now".
        """
        ...

    @abstractmethod
    def filter_live_bots(
        self, pairs: list[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        """Which of these ``(bot_id, owner_id)`` bots are live, in one query.

        The many-owner sibling of :meth:`list_live_bot_ids_by_owner`, and it
        exists because a caller may hold bots belonging to *several* owners — an
        application's grants, for instance, which cover bots the delegating user
        collaborates on rather than owns. Filtering those against one owner's
        live ids would silently drop every shared one.

        **Keyed on the pair, not the bare id.** ``ac_bots`` has no unique key on
        ``bot_id``: with the legacy ``default`` convention, an id-only query
        reports a soft-deleted bot as live whenever *any* owner still has a
        live bot of that id, so a grant whose bot is gone keeps being advertised
        as reachable. The owner is on every grant record, so there is nothing to
        discover.

        Returns a set for direct membership testing, and never more pairs than
        it was given. An empty input returns an empty set without querying.

        Scoped by current env AND tenant, like its siblings, and excludes
        soft-deleted rows.
        """
        ...

    @abstractmethod
    def list_by_owner_or_collaborator(
        self, owner_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots owned by the user or collaboratively managed by the user."""
        ...

    @abstractmethod
    def list_by_entity(
        self,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots by entity with pagination."""
        ...

    @abstractmethod
    def list_by_conditions(
        self,
        public: Optional[str] = None,
        bot_name: Optional[str] = None,
        owner_name: Optional[str] = None,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        engine: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        bot_ids: list[str] | None = None,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots by conditions with pagination.

        ``bot_ids`` restricts the result to an explicit set. ``None`` means no
        restriction; an **empty list means none**, and the two must stay
        distinguishable — collapsing them would turn "this caller may reach no
        bots" into "show everything".

        ``owner_id`` scopes to a single owner (exact), ``engine`` filters on the
        active engine (exact), ``status`` filters on lifecycle status (exact).
        All are optional and additive — omitting them reproduces the prior
        result set and count exactly.
        """
        ...

    @abstractmethod
    def list_by_search(
        self,
        public: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, List[Dict[str, Any]]]:
        """List bots with pagination and search."""
        ...

    @abstractmethod
    def list_public_bots_by_owner_bot_pairs(
        self, pairs: List[tuple[str, str]]
    ) -> List[Dict[str, Any]]:
        """Live public bots matching any ``(bot_id, owner_id)`` pair, this env."""
        ...

    @abstractmethod
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

    @abstractmethod
    def update_by_owner(
        self, bot_id: str, owner_id: str, update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update bot record by bot_id and owner_id."""
        ...

    @abstractmethod
    def compare_and_set_ext(
        self,
        *,
        bot_id: str,
        owner_id: str,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Replace ``ext`` only when the full stored value is unchanged."""
        ...

    @abstractmethod
    def soft_delete_by_owner(self, bot_id: str, owner_id: str) -> bool:
        """Soft delete a bot by bot_id and owner_id."""
        ...

    @abstractmethod
    def count_by_owner(self, owner_id: str, exclude_bot_type: str | None = None) -> int:
        """Count bots by owner_id.

        Scoped by current env AND tenant (via the ``BotModel`` avernet_tenant
        guard); see :meth:`list_by_owner`.

        Args:
            owner_id: Owner user ID.
            exclude_bot_type: If provided, exclude bots with this bot_type from the count.
                Used to exclude desktop bots from cloud bot limits.
        """
        ...

    @abstractmethod
    def exists_by_owner_and_bot_id(self, owner_id: str, bot_id: str) -> bool:
        """Check if a bot with specific bot_id exists for the owner."""
        ...

    @abstractmethod
    def exists_by_owner_and_bot_type(self, owner_id: str, bot_type: str) -> bool:
        """Check if the owner has a live Bot of the requested type."""
        ...

    @abstractmethod
    def exists_by_bot_name(self, bot_name: str) -> bool:
        """Check if a bot with specific bot_name exists globally."""
        ...

    @abstractmethod
    def get_by_bot_name(self, bot_name: str) -> Optional[Dict[str, Any]]:
        """Get bot by bot_name."""
        ...

    @abstractmethod
    def get_by_binding_id(self, binding_id: int) -> Optional[Dict[str, Any]]:
        """Get bot by binding_id."""
        ...

    @abstractmethod
    def get_device_provider_by_bot_id_and_owner(
        self, bot_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get device_provider info by bot_id and owner_id."""
        ...

    @abstractmethod
    def get_device_provider_by_bot_id(
        self, bot_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get device_provider info by bot_id (no owner_id required)."""
        ...

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
    def get(
        self, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotRestartLockRecord]:
        """Return the lock row for ``(env, entity_id, bot_id)`` if present."""
        ...

    @abstractmethod
    def get_if_stale(
        self, env: str, entity_id: str, bot_id: str, ttl_seconds: int
    ) -> Optional[BotRestartLockRecord]:
        """Return the lock row only if it is older than ``ttl_seconds``.

        Staleness is evaluated DB-side (comparing ``gmt_create`` against the
        database clock) to avoid app/DB clock-skew. Returns ``None`` when no
        row exists or the existing row is still fresh.
        """
        ...

    @abstractmethod
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


@runtime_checkable
class TemplateRepository(Protocol):
    """Protocol for template repository implementations.

    Implementation: a single unified ORM body
    (plugins.template_repository.TemplateRepository) runs on both prod
    OceanBase and local SQLite via the injected DatabasePlugin.
    """

    @abstractmethod
    def insert(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new template record.

        Args:
            template_data: Dictionary with template fields (bot_id, ext, etc.)

        Returns:
            Created template record as dictionary
        """
        ...

    @abstractmethod
    def get_by_bot_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get template by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            Template record as dictionary, or None if not found
        """
        ...

    @abstractmethod
    def update_by_bot_id(self, bot_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update template by bot_id.

        Args:
            bot_id: Bot ID
            update_data: Dictionary with fields to update

        Returns:
            Updated template record as dictionary, or None if not found
        """
        ...

    @abstractmethod
    def delete_by_bot_id(self, bot_id: str) -> bool:
        """Delete template by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            True if deleted, False if not found
        """
        ...

    @abstractmethod
    def exists_by_bot_id(self, bot_id: str) -> bool:
        """Check if a template exists for the given bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            True if template exists, False otherwise
        """
        ...

    @abstractmethod
    def list_by_bot_ids(self, bot_ids: List[str]) -> List[Dict[str, Any]]:
        """List templates by bot IDs.

        Args:
            bot_ids: Bot IDs

        Returns:
            List of template records
        """
        ...

    @abstractmethod
    def list_by_architect_bot_id(self, architect_bot_id: str) -> List[Dict[str, Any]]:
        """List templates whose ext JSON contains the given architect_bot_id.

        Used to find all application coding bots associated with a
        domain architect bot.

        Args:
            architect_bot_id: The architect bot's bot_id

        Returns:
            List of template records
        """
        ...


@runtime_checkable
class RenderScreenRepository(Protocol):
    """Render screen repository interface.

    Implementation: a single unified ORM repository
    ``plugins.render_screen_repository.RenderScreenRepository`` that
    runs on both the corp store (prod) and SQLite (local) — the only difference is
    the injected ``DatabasePlugin`` (``orm_session()``).
    """

    @abstractmethod
    def insert(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        cdn_url: str,
        creator_id: str,
    ) -> int:
        """插入一条 CDN 配置，返回新记录 id。"""
        ...

    @abstractmethod
    def list_by_bot_id(self, *, bot_id: str, owner_id: str) -> list[RenderScreenRecord]:
        """查询某 Bot 下所有未删除的 CDN 配置。"""
        ...

    @abstractmethod
    def get_by_id(self, record_id: int) -> RenderScreenRecord | None:
        """根据 id 查询单条记录。"""
        ...

    @abstractmethod
    def update_by_id(
        self,
        *,
        record_id: int,
        name: str,
        cdn_url: str,
    ) -> None:
        """更新 name 和 cdn_url。"""
        ...

    @abstractmethod
    def delete_by_id(self, *, record_id: int) -> None:
        """软删除（is_delete=1）。"""
        ...
