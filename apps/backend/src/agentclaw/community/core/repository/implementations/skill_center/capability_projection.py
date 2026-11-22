"""Persist Bot capability work beside desired state and confirm exact receipts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from injector import inject
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from agentclaw.community.core.models.capability_projection import BotCapabilityProjection
from agentclaw.community.core.repository.implementations.skill_center.bot_mutation_fence import (
    find_capability_bot,
    require_locked_capability_bot,
)
from agentclaw.community.core.repository.protocols.capability_projection import (
    CapabilityProjectionRepositoryProtocol,
    CapabilityProjectionState,
    CapabilityProjectionTarget,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionIssue,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env


def _merge_scope(current: ProjectionScope, added: ProjectionScope) -> ProjectionScope:
    return ProjectionScope(
        skills=current.skills or added.skills,
        mcp=current.mcp or added.mcp,
        claimed_mcp=current.claimed_mcp | added.claimed_mcp,
        updated_mcp=current.updated_mcp | added.updated_mcp,
        released_mcp=current.released_mcp | added.released_mcp,
        claim_all_mcp=current.claim_all_mcp or added.claim_all_mcp,
    )


def _write_scope(row: BotCapabilityProjection, scope: ProjectionScope) -> None:
    row.scope_json = json.dumps({
        "skills": scope.skills,
        "mcp": scope.mcp,
        "claimed_mcp": sorted(scope.claimed_mcp),
        "updated_mcp": sorted(scope.updated_mcp),
        "released_mcp": sorted(scope.released_mcp),
        "claim_all_mcp": scope.claim_all_mcp,
    })


def _read_scope(row: BotCapabilityProjection) -> ProjectionScope:
    fields = json.loads(row.scope_json)
    return ProjectionScope(
        skills=fields["skills"],
        mcp=fields["mcp"],
        claimed_mcp=frozenset(fields["claimed_mcp"]),
        updated_mcp=frozenset(fields["updated_mcp"]),
        released_mcp=frozenset(fields["released_mcp"]),
        claim_all_mcp=fields["claim_all_mcp"],
    )


def _read_mappings(row: BotCapabilityProjection) -> tuple[PoolSkillMapping, ...]:
    return tuple(
        PoolSkillMapping(relative_path=fields.pop("relative_path", None), **fields)
        for fields in json.loads(row.retired_json)
    )


def _read_result(row: BotCapabilityProjection) -> RuntimeProjectionResult:
    data = json.loads(row.result_json)
    issues = []
    for issue in data["issues"]:
        issue["status"] = RuntimeProjectionStatus(issue["status"])
        issues.append(RuntimeProjectionIssue(**issue))
    return RuntimeProjectionResult(
        status=RuntimeProjectionStatus(data["status"]),
        components={
            name: RuntimeProjectionStatus(status)
            for name, status in data["components"].items()
        },
        issues=tuple(issues),
        reason=data.get("reason"),
    )


def _queue_result() -> RuntimeProjectionResult:
    return RuntimeProjectionResult.pending(
        code="CAPABILITY_PROJECTION_PENDING",
        reason="Capability configuration is committed and awaiting Runtime synchronization.",
    )


def _same_binding(row: BotCapabilityProjection, bot: BotModel) -> bool:
    return (
        row.binding_id == bot.binding_id
        and row.device_id == bot.device_id
        and row.engine_type == bot.active_engine
    )


def _snapshot(row: BotCapabilityProjection, bot: BotModel) -> CapabilityProjectionState:
    current = _same_binding(row, bot)
    return CapabilityProjectionState(
        bot_pk=int(bot.id),
        bot_id=str(bot.bot_id),
        entity_id=str(bot.entity_id),
        tenant=str(bot.avernet_tenant),
        env=str(bot.env),
        owner_id=str(bot.owner_id),
        binding_id=cast("int | None", row.binding_id),
        device_id=cast("str | None", row.device_id),
        engine_type=str(row.engine_type),
        version=int(row.version),
        scope=_read_scope(row),
        retired_mappings=_read_mappings(row),
        result=_read_result(row) if current else _queue_result(),
        write_blocked=row.dispatch_version is not None,
        is_current_binding=current,
    )


def _get_for_bot(session: Session, bot: BotModel) -> BotCapabilityProjection | None:
    return (
        session.query(BotCapabilityProjection)
        .filter(
            BotCapabilityProjection.bot_pk == bot.id,
            BotCapabilityProjection.env == bot.env,
            BotCapabilityProjection.avernet_tenant == bot.avernet_tenant,
        )
        .one_or_none()
    )


def _advance(
    row: BotCapabilityProjection,
    bot: BotModel,
    scope: ProjectionScope,
    retired_mappings: Sequence[PoolSkillMapping],
) -> None:
    if not _same_binding(row, bot):
        # An engine change alone does not prove that the previous device
        # lost write access. Retain its unknown dispatch if the target stayed.
        if row.binding_id != bot.binding_id or row.device_id != bot.device_id:
            row.dispatch_version = None
        row.binding_id = bot.binding_id
        row.device_id = bot.device_id
        row.engine_type = bot.active_engine
        scope = _merge_scope(ProjectionScope.everything(), scope)
        row.retired_json = "[]"
    if row.version == 0:
        scope = _merge_scope(ProjectionScope.everything(), scope)
    row.version += 1
    row.retry_pending = True
    row.result_json = json.dumps(_queue_result().to_dict(), ensure_ascii=False)
    _write_scope(row, _merge_scope(_read_scope(row), scope))
    if scope.skills and retired_mappings:
        candidates = dict.fromkeys((*_read_mappings(row), *retired_mappings))
        row.retired_json = json.dumps([mapping.to_dict() for mapping in candidates])


def record_capability_projection(
    session: Session, *, bot_id: str, entity_id: str, scope: ProjectionScope,
    retired_mappings: Sequence[PoolSkillMapping] = (), create: bool,
) -> CapabilityProjectionState | None:
    """Append work inside the caller's transaction, after locking its Bot row."""
    if not (scope.skills or scope.mcp):
        return None
    bot = require_locked_capability_bot(session, bot_id=bot_id, entity_id=entity_id)
    row = _get_for_bot(session, bot)
    if row is None:
        if not create:
            return None
        row = BotCapabilityProjection(
            bot_pk=bot.id, avernet_tenant=bot.avernet_tenant, env=bot.env,
            binding_id=bot.binding_id, device_id=bot.device_id, engine_type=bot.active_engine,
            version=0, retry_pending=True, retired_json="[]",
        )
        _write_scope(row, ProjectionScope())
        session.add(row)
    _advance(row, bot, scope, retired_mappings)
    return _snapshot(row, bot)


def _write_result(
    row: BotCapabilityProjection, result: RuntimeProjectionResult,
) -> None:
    previous = _read_result(row)
    result = replace(result, components={**previous.components, **result.components})
    row.result_json = json.dumps(result.to_dict(), ensure_ascii=False)
    row.retry_pending = (
        result.status is RuntimeProjectionStatus.PENDING
        or any(issue.retryable for issue in result.issues)
    )
    scope = _read_scope(row)
    if result.status is RuntimeProjectionStatus.CONVERGED:
        scope = ProjectionScope()
    else:
        if result.components.get("skills") is RuntimeProjectionStatus.CONVERGED:
            scope = replace(scope, skills=False)
        if result.components.get("mcp") is RuntimeProjectionStatus.CONVERGED:
            scope = ProjectionScope(skills=scope.skills)
    if not scope.skills:
        row.retired_json = "[]"
    _write_scope(row, scope)


class CapabilityProjectionRepository(CapabilityProjectionRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def read(self, *, bot_id: str, entity_id: str) -> CapabilityProjectionState | None:
        with self._db.orm_session() as session:
            bot = find_capability_bot(session, bot_id=bot_id, entity_id=entity_id)
            row = _get_for_bot(session, bot) if bot is not None else None
            return _snapshot(row, bot) if row is not None and bot is not None else None

    def require(
        self, *, bot_id: str, entity_id: str, scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> CapabilityProjectionState:
        with self._db.transactional_orm_session() as session:
            state = record_capability_projection(
                session, bot_id=bot_id, entity_id=entity_id, scope=scope,
                retired_mappings=retired_mappings, create=True,
            )
            if state is None:
                raise ValueError("At least one capability projection domain is required")
            return state

    def prepare(self, *, bot_id: str, entity_id: str) -> CapabilityProjectionState | None:
        with self._db.transactional_orm_session() as session:
            bot = find_capability_bot(
                session, bot_id=bot_id, entity_id=entity_id, lock=True,
            )
            row = _get_for_bot(session, bot) if bot is not None else None
            if row is None or bot is None:
                return None
            if not _same_binding(row, bot):
                _advance(row, bot, ProjectionScope.everything(), ())
            return _snapshot(row, bot)

    def begin(self, state: CapabilityProjectionState) -> bool:
        with self._db.transactional_orm_session() as session:
            row = self._matching_target(session, state)
            if row is None or row.version != state.version or row.dispatch_version is not None:
                return False
            row.dispatch_version = state.version
            return True

    def complete(
        self, state: CapabilityProjectionState, result: RuntimeProjectionResult, *,
        write_blocked: bool = False,
    ) -> bool:
        with self._db.transactional_orm_session() as session:
            row = self._matching_target(session, state)
            if row is None:
                return False
            if row.dispatch_version == state.version and not write_blocked:
                row.dispatch_version = None
            if row.version != state.version:
                return False
            if write_blocked:
                row.dispatch_version = state.version
            _write_result(row, result)
            return True

    @staticmethod
    def _matching_target(
        session: Session, state: CapabilityProjectionState,
    ) -> BotCapabilityProjection | None:
        bot = find_capability_bot(
            session, bot_id=state.bot_id, entity_id=state.entity_id, lock=True,
        )
        if bot is None or (
            bot.id != state.bot_pk
            or bot.avernet_tenant != state.tenant
            or bot.env != state.env
            or bot.binding_id != state.binding_id
            or bot.device_id != state.device_id
            or bot.active_engine != state.engine_type
        ):
            return None
        return _get_for_bot(session, bot)

    def due(
        self, *, env: str, limit: int, after_bot_pk: int = 0,
    ) -> tuple[CapabilityProjectionTarget, ...]:
        if env != get_current_env():
            raise ValueError("Capability recovery must use the worker's data environment")
        # Only these identifiers are needed to establish tenant scope for each
        # work item. Never read configuration or results across tenants.
        state = BotCapabilityProjection.__table__.c
        bot = BotModel.__table__.c
        query = (
            select(bot.id, bot.bot_id, bot.entity_id, bot.avernet_tenant, bot.env)
            .join_from(BotCapabilityProjection.__table__, BotModel.__table__, and_(
                state.bot_pk == bot.id,
                state.env == bot.env,
                state.avernet_tenant == bot.avernet_tenant,
            ))
            .where(
                state.env == env,
                state.bot_pk > after_bot_pk,
                bot.is_delete == 0,
                or_(
                    and_(state.retry_pending.is_(True), state.dispatch_version.is_(None)),
                    state.binding_id.is_distinct_from(bot.binding_id),
                    state.device_id.is_distinct_from(bot.device_id),
                    state.engine_type != bot.active_engine,
                ),
            )
            .order_by(state.bot_pk)
            .limit(limit)
        )
        with self._db.orm_session() as session:
            return tuple(
                CapabilityProjectionTarget(
                    row.id, row.bot_id, row.entity_id, row.avernet_tenant, row.env,
                )
                for row in session.execute(query)
            )
