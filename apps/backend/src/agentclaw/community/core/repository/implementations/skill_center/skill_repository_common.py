"""Unified Skill / SkillSet repositories (prod OceanBase + local SQLite).

Two ORM implementations behind the ``SkillRepository`` and
``SkillSetRepository`` Protocols. The only per-environment difference
is the injected :class:`DatabasePlugin`: ``orm_session()`` yields a
SQLAlchemy ``Session`` in both runtimes, so this single body runs
unchanged on OceanBase (prod) and SQLite (local), collapsing the
previous raw-SQL/ORM twins so CI exercises the prod path too.

This is a faithful ORM port of the **prod** relational-store Skill and
SkillSet repositories — prod behavior is the contract and
becomes the new local/test behavior (the old SQLite twin is
discarded, not preserved). Every method reproduces prod's exact
statement shape and predicates:

- ``create`` / ``add_skill_to_set`` / ``add_mcp_to_set`` are **plain
  INSERTs** (no upsert — these tables have no unique key in prod
  DDL). ``delete`` / ``delete_by_*`` are **hard DELETEs** (ac_skill /
  ac_skill_set have no soft-delete column). Single UPDATEs stay
  single. ``gmt_modified=func.now()`` matches prod's literal
  ``NOW()``.
- Asset hard-delete methods now deliberately supersede the historical
  cascade behavior: they lock exact Skill rows, reject durable references,
  and delete only zero-reference assets in one transaction. They never
  silently remove Installation or Membership rows.
- ``add_default_mcp_exclusion`` is the **only** genuine upsert in S4
  (prod ``INSERT ... ON DUPLICATE KEY UPDATE`` on
  ``uk_user_bot_skillset_mcp``). Implemented with the live
  dialect-aware pattern from
  ``expert_chat_repository.add_chat_bot`` (SQLite
  ``on_conflict_do_update``, MySQL ``on_duplicate_key_update``) — a
  pure dialect branch, no runtime-mode awareness.
- Env predicates are matched **per method** to prod: most are strict
  ``env == current``; a few (``SkillSet.get_by_id`` / ``list_all`` /
  ``list_all_exclude_deleted`` / ``set_active_skill_set`` /
  ``activate``/``deactivate``) are deliberately
  ``env == current OR env IS NULL`` exactly where prod is.
- ``user_id`` int<->str coercion uses prod's ``_parse_user_id``
  (None→None, "anonymous"→0, non-numeric→0) / ``_format_user_id``
  (0→"anonymous"). Return dicts reproduce prod's ``_row_to_dict``
  shapes (NOT the ORM models' ``to_dict()``).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional


from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


def _format_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _normalize_user_id(user_id: Optional[str]) -> Optional[str]:
    """Normalize user_id for string-backed persistence (dev rename of
    the old ``_parse_user_id``). The underlying column is now
    VARCHAR(128) on prod — see
    core/skill_center/sql/user_id_int_to_varchar.sql. No more
    "anonymous"→0 coercion; user_id is stored verbatim as a string.
    """
    if user_id is None:
        return None
    return str(user_id)


def _format_user_id(user_id) -> Optional[str]:
    """Return user_id from storage as a string (NULL preserved). The
    0→"anonymous" mapping is gone — callers now pass and receive
    "anonymous" as the literal user_id string."""
    if user_id is None:
        return None
    return str(user_id)


def _skill_to_dict(s) -> dict:
    """Reproduces the prod Skill repository's _row_to_dict from a Skill ORM row."""
    git_path = s.git_path
    if git_path and git_path.startswith("git://"):
        link_name = git_path[6:].replace("/", "_")
    else:
        link_name = None
    risk_tags = s.risk_tags
    mcp_deps = s.mcp_dependencies
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "git_path": git_path,
        "link_name": link_name,
        "category": s.category,
        "tags": s.tags,
        "input_schema": s.input_schema,
        "output_schema": s.output_schema,
        "is_public": bool(s.is_public),
        "is_builtin": bool(s.is_builtin),
        "user_id": _format_user_id(s.user_id),
        "gmt_created": _format_datetime(s.gmt_created),
        "gmt_modified": _format_datetime(s.gmt_modified),
        "risk_tags": json.loads(risk_tags) if risk_tags else [],
        "mcp_dependencies": json.loads(mcp_deps) if mcp_deps else [],
        "bolt_id": s.bolt_id if s.bolt_id else "default",
        "env": s.env if s.env else get_current_env(),
        "status": s.status if s.status else "DEVELOPING",
        "version": s.version if s.version is not None else 1,
        "skill_uuid": s.skill_uuid,
        "source_type": s.source_type,
        "category_path": getattr(s, "category_path", None),
        "package_url": getattr(s, "package_url", None),
        "zip_url": getattr(s, "zip_url", None),
    }


def _skill_set_to_dict(ss) -> dict:
    """Reproduces the prod SkillSet repository's _row_to_dict from a SkillSet row."""
    return {
        "id": str(ss.id),
        "name": ss.name,
        "description": ss.description,
        "is_default": bool(ss.is_default),
        "is_builtin": bool(ss.is_builtin),
        "user_id": _format_user_id(ss.user_id),
        "gmt_created": _format_datetime(ss.gmt_created),
        "gmt_modified": _format_datetime(ss.gmt_modified),
        "bolt_id": ss.bolt_id if ss.bolt_id else "default",
        "env": ss.env if ss.env else get_current_env(),
        "is_active": bool(ss.is_active) if ss.is_active is not None else False,
        "engine_type": ss.engine_type,
    }


def _mcp_assoc_to_dict(m) -> dict:
    return {
        "id": str(m.id),
        "skill_set_id": str(m.skill_set_id),
        "server_code": m.server_code,
        "name": m.name,
        "description": m.description,
        "icon": m.icon,
        "user_id": m.user_id,
        "gmt_created": _format_datetime(m.gmt_created),
        "gmt_modified": _format_datetime(m.gmt_modified),
        "env": m.env,
    }
