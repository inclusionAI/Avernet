"""Dialect-neutral relational schema bootstrap.

One place that knows how to turn an empty database into the backend's schema,
shared by every ``DatabasePlugin`` that owns its own store:

- :func:`import_all_models` — force every ORM class to be registered on the
  metadata, so ``create_all`` is complete rather than dependent on whichever
  modules the router chain happened to import first.
- :func:`create_all` — emit the DDL for both declarative bases.
- :func:`prepare_for_mysql` — MySQL-only DDL adjustments (index key lengths).

This body used to live inline in ``plugins/local/database.py``'s ``SqliteDB``,
where only the ``test`` and ``singlebox`` profiles could reach it. The community
plugin needs the same bootstrap against a real store (a deployment does not run
DDL by hand before the pods start), so it lives in ``core/`` and both plugins
call it. ``core/`` is the lowest layer; ``plugins/`` depends on it, never the
reverse.
"""
from __future__ import annotations

import logging

from sqlalchemy import Index, MetaData, String, UniqueConstraint
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def import_all_models() -> None:
    """Register every ORM class on its declarative metadata.

    Eagerly imports each model module so its ``class`` statement runs and
    SQLAlchemy's declarative metaclass attaches a ``Table`` to the shared
    ``MetaData``. Without this, ``create_all`` would only emit DDL for tables
    whose class was already transitively imported via the router chain — and the
    method-level lazy imports in many repositories make that set
    non-deterministic. The first request would hit ``no such table: ac_xxx``.

    ``noqa: F401`` throughout: the names are intentionally unused, only the
    import side effect matters.
    """
    import agentclaw.community.plugin_api.models  # noqa: F401  ac_bots / ac_resource / ac_channel_config
    import agentclaw.community.core.models  # noqa: F401  ac_skill* / ac_skill_set_mcp / ac_user_mcp_config / propagation_log / center_sync_log
    import agentclaw.community.core.skill_center.orm  # noqa: F401  ac_default_skillset_*
    import agentclaw.community.core.access.sqlite_models  # noqa: F401  ac_access_control_policy / ac_user_info
    import agentclaw.community.core.service_bot.repository.models  # noqa: F401  ac_bot_publish
    import agentclaw.community.core.bot_public.repository.models  # noqa: F401  ac_bot_friend
    import agentclaw.community.core.expert_chat.sqlite_models  # noqa: F401  ac_expert_chat_bot_sessions
    import agentclaw.community.core.devices.repository.models  # noqa: F401  ac_entity_device_binding
    import agentclaw.community.core.bot_management.repository.models  # noqa: F401  ac_templates / ac_bot_restart_lock
    import agentclaw.community.core.bot_startup_script.repository.models  # noqa: F401  ac_bot_startup_script
    import agentclaw.community.core.bot_config_manifest.repository.models  # noqa: F401  ac_bot_config_manifest
    import agentclaw.community.core.bot_config_manifest.cli_tools.models  # noqa: F401  ac_bot_cli_tool
    import agentclaw.community.core.bot_config_manifest.content.models  # noqa: F401  ac_manifest_content
    import agentclaw.community.core.bot_config_manifest.credentials.models  # noqa: F401  ac_source_credential
    import agentclaw.community.core.bot_config_manifest.repository.apply_models  # noqa: F401  ac_bot_config_manifest_apply(_lock)
    import agentclaw.community.core.bot_management.render_screen.sqlite_models  # noqa: F401  ac_bot_render_screen
    import agentclaw.community.core.system_config.orm  # noqa: F401  ac_config_*
    import agentclaw.community.core.harness.sqlite_models  # noqa: F401  ac_harness_*
    import agentclaw.community.core.bot_chat.models  # noqa: F401  bot_chat private-Base tables
    import agentclaw.community.core.bot_dormant.sqlite_models  # noqa: F401  ac_bot_dormant_*
    import agentclaw.community.core.task_queue.repository.models  # noqa: F401  ac_task_queue
    import agentclaw.community.core.task.repository.models  # noqa: F401  task_info / task_node / task_node_run_info / task_node_relation / task_callback
    import agentclaw.community.core.task.task_discovery.discovered_task_models  # noqa: F401  ac_discovered_tasks
    import agentclaw.community.core.task.task_discovery.lock_models  # noqa: F401  ac_task_discovery_lock
    import agentclaw.community.core.skills_pool.repository.models  # noqa: F401  ac_bot_skill_layout_state
    import agentclaw.community.core.session_resources.repository.models  # noqa: F401  ac_session_resource
    import agentclaw.community.core.economy.governance.orm  # noqa: F401  governance_*
    import agentclaw.community.core.caller_identity.models  # noqa: F401  caller identity tables
    import agentclaw.community.core.bot_app_grant.models  # noqa: F401  ac_bot_app_grant / ac_bot_app_grant_log
    import agentclaw.community.core.user_list.models  # noqa: F401  ac_entity_user_list
    import agentclaw.community.core.spaces.repository.models  # noqa: F401  ac_space / ac_space_member
    import agentclaw.community.core.market_favorites.repository.models  # noqa: F401  ac_market_favorite
    import agentclaw.community.core.work_orders.repository.models  # noqa: F401  ac_work_order / ac_work_order_notification


def _metadatas() -> list[MetaData]:
    """Both declarative registries the schema spans.

    ``core/bot_chat/models.py`` declares a private ``Base`` instead of the
    canonical ``core/base.py`` one, so its tables (``aw_langfuse_traces``, and a
    second ``ac_bots``) live on a separate ``MetaData``. SQLAlchemy permits the
    same table name across different registries and ``create_all`` is idempotent
    (``checkfirst=True``), so emitting both is safe in either order. Without the
    private one, ``/api/v1/bot-chats`` crashes with ``no such table:
    aw_langfuse_traces``.
    """
    import agentclaw.community.core.bot_chat.models as bot_chat_models
    from agentclaw.community.core.base import Base

    return [Base.metadata, bot_chat_models.Base.metadata]


# InnoDB caps an index key at 3072 bytes (MySQL 8, DYNAMIC row format), and
# utf8mb4 bills 4 bytes per character — so a single VARCHAR(1024) column blows
# the budget on its own.
_INNODB_MAX_KEY_BYTES = 3072
_UTF8MB4_BYTES_PER_CHAR = 4


def _effective_widths(columns, existing: dict[str, int] | None) -> dict[str, int]:
    """Each string column's indexed width, honouring any prefix already applied."""
    existing = existing or {}
    return {
        col.name: existing.get(col.name, col.type.length)
        for col in columns
        if isinstance(col.type, String) and col.type.length
    }


def _prefix_lengths(
    columns, existing: dict[str, int] | None = None
) -> dict[str, int] | None:
    """Shrink the widest string columns until the key fits InnoDB's cap.

    Returns a ``{column_name: prefix_chars}`` map for ``mysql_length``, or
    ``None`` if the key already fits. Narrow columns are left whole and the
    widest are cut first, so a key made of one wide column and several small
    ones only loses precision on the wide one.

    ``existing`` is the prefix map already on the index, so a second pass over
    the same metadata is a no-op rather than halving the prefix again.
    """
    widths = _effective_widths(columns, existing)
    budget_chars = _INNODB_MAX_KEY_BYTES // _UTF8MB4_BYTES_PER_CHAR
    if sum(widths.values()) <= budget_chars:
        return None

    prefixes = dict(widths)
    # Repeatedly halve the widest column until the total fits. Converges quickly
    # and keeps the result deterministic (no float arithmetic, no ordering
    # dependence beyond "widest first, ties by name").
    while sum(prefixes.values()) > budget_chars:
        widest = max(sorted(prefixes), key=lambda name: prefixes[name])
        prefixes[widest] = max(1, prefixes[widest] // 2)
    return {name: length for name, length in prefixes.items() if length < widths[name]}


def prepare_for_mysql(metadata: MetaData) -> list[str]:
    """Make ``metadata``'s DDL emittable on MySQL. Returns what was adjusted.

    Every index whose key exceeds InnoDB's 3072-byte cap gets per-column prefix
    lengths. A ``UniqueConstraint`` cannot carry prefix lengths in SQLAlchemy, so
    an over-long one is replaced by an equivalent unique ``Index``, which can —
    the two are the same object in MySQL.

    **Semantic caveat for the unique keys.** A prefixed unique index enforces
    uniqueness on the prefix, which is *stricter* than on the whole value: two
    rows whose ids agree for the first N characters and differ later would now
    collide. The columns involved hold ids and short type discriminators, so this
    is not reachable in practice — but it is a real narrowing, and the reason the
    convention for *new* tables (see ``bot_startup_script``) is a bounded sha256
    surrogate column instead. Retrofitting a surrogate onto these six tables
    means changing their write paths, which is a bigger change than making the
    schema deployable.

    MySQL-only, and applied in-process at bootstrap: SQLite ignores prefix
    lengths, and corp never runs this code (its DDL is applied out of band).
    """
    adjusted: list[str] = []

    for table in metadata.sorted_tables:
        for constraint in list(table.constraints):
            if not isinstance(constraint, UniqueConstraint):
                continue
            prefixes = _prefix_lengths(list(constraint.columns))
            if prefixes is None:
                continue
            columns = list(constraint.columns)
            name = constraint.name or f"uk_{table.name}_{'_'.join(c.name for c in columns)}"
            table.constraints.discard(constraint)
            Index(name, *columns, unique=True, mysql_length=prefixes)
            adjusted.append(f"{table.name}.{name} (unique constraint → prefixed unique index)")

        for index in table.indexes:
            already = index.dialect_options["mysql"].get("length") or {}
            prefixes = _prefix_lengths(list(index.columns), already)
            if prefixes is None:
                continue
            index.dialect_options["mysql"]["length"] = {**already, **prefixes}
            adjusted.append(f"{table.name}.{index.name} (prefixed)")

    return adjusted


def create_all(engine: Engine, *, mysql: bool = False) -> None:
    """Create every table on ``engine``. Idempotent (``checkfirst=True``)."""
    import_all_models()
    for metadata in _metadatas():
        if mysql:
            adjusted = prepare_for_mysql(metadata)
            for entry in adjusted:
                logger.info("schema: MySQL index key capped — %s", entry)
        metadata.create_all(engine)
    logger.info("schema: create_all complete")
