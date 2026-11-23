"""Persist Bot capability work beside desired state and confirm exact receipts."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from injector import inject
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agentclaw.community.core.models.capability_projection import BotCapabilityProjection
from agentclaw.community.core.repository.implementations.skill_center.bot_mutation_fence import (
    find_capability_bot,
    require_locked_capability_bot,
)
from agentclaw.community.core.repository.capability_projection_types import (
    CapabilityProjectionState,
    CapabilityProjectionTarget,
)
from agentclaw.community.core.repository.protocols.capability_projection import (
    CapabilityProjectionRepositoryProtocol,
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


def _covers(current: ProjectionScope, requested: ProjectionScope) -> bool:
    return (
        (current.skills or not requested.skills)
        and (current.mcp or not requested.mcp)
        and (current.mcp or current.passport or not requested.passport)
        and (current.claim_all_mcp or not requested.claim_all_mcp)
        and (current.claim_all_mcp or requested.claimed_mcp <= current.claimed_mcp)
        and (current.claim_all_mcp or requested.updated_mcp <= current.updated_mcp)
        and requested.released_mcp <= current.released_mcp
    )


def _merge_scope(current: ProjectionScope, added: ProjectionScope) -> ProjectionScope:
    return ProjectionScope(
        skills=current.skills or added.skills,
        mcp=current.mcp or added.mcp,
        claimed_mcp=current.claimed_mcp | added.claimed_mcp,
        updated_mcp=current.updated_mcp | added.updated_mcp,
        released_mcp=current.released_mcp | added.released_mcp,
        claim_all_mcp=current.claim_all_mcp or added.claim_all_mcp,
        passport=current.passport or added.passport,
    )


def _write_scope(row: BotCapabilityProjection, scope: ProjectionScope) -> None:
    row.scope_json = json.dumps({
        "skills": scope.skills,
        "mcp": scope.mcp,
        "claimed_mcp": sorted(scope.claimed_mcp),
        "updated_mcp": sorted(scope.updated_mcp),
        "released_mcp": sorted(scope.released_mcp),
        "claim_all_mcp": scope.claim_all_mcp,
        "passport": scope.passport,
    })


def _read_scope(row: BotCapabilityProjection) -> ProjectionScope:
    fields = json.loads(row.scope_json)
    passport = fields.get("passport")
    if passport is None:
        # Older rows persisted the failed authorization in the result only.
        result = _read_result(row)
        issue_codes = {issue.code for issue in result.issues}
        passport = "PASSPORT_RUNTIME_UNAVAILABLE" in issue_codes
        if (
            passport
            and result.components.get("mcp") is RuntimeProjectionStatus.CONVERGED
            and issue_codes <= {"PASSPORT_RUNTIME_UNAVAILABLE", "CAPABILITY_PROJECTION_PENDING"}
        ):
            # Legacy MCP-only receipts kept the MCP scope after a Passport
            # failure even when the device confirmed it.
            return ProjectionScope(skills=fields["skills"], passport=True)
    return ProjectionScope(
        skills=fields["skills"],
        mcp=fields["mcp"],
        claimed_mcp=frozenset(fields["claimed_mcp"]),
        updated_mcp=frozenset(fields["updated_mcp"]),
        released_mcp=frozenset(fields["released_mcp"]),
        claim_all_mcp=fields["claim_all_mcp"],
        passport=passport,
    )


def _read_mappings(row: BotCapabilityProjection) -> tuple[PoolSkillMapping, ...]:
    return tuple(
        PoolSkillMapping(relative_path=fields.pop("relative_path", None), **fields)
        for fields in json.loads(row.retired_json)
    )


def _read_result(row: BotCapabilityProjection) -> RuntimeProjectionResult:
    return RuntimeProjectionResult.from_dict(json.loads(row.result_json))


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
        retry_pending=bool(row.retry_pending),
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
        dispatches = _read_dispatches(row)
        if bot.device_id and row.device_id and row.device_id != bot.device_id:
            row.retired_json = "[]"
        row.binding_id = bot.binding_id
        row.device_id = bot.device_id
        row.engine_type = bot.active_engine
        _write_dispatches(row, dispatches)
        scope = _merge_scope(ProjectionScope.everything(), scope)
    if row.version == 0:
        scope = _merge_scope(ProjectionScope.everything(), scope)
    combined_scope = _merge_scope(_read_scope(row), scope)
    row.version += 1
    row.retry_pending = True
    row.result_json = json.dumps(_queue_result().to_dict(), ensure_ascii=False)
    _write_scope(row, combined_scope)
    if scope.skills and retired_mappings:
        candidates = dict.fromkeys((*_read_mappings(row), *retired_mappings))
        row.retired_json = json.dumps([mapping.to_dict() for mapping in candidates])


def record_capability_projection(
    session: Session, *, bot_id: str, entity_id: str, scope: ProjectionScope,
    retired_mappings: Sequence[PoolSkillMapping] = (), create: bool,
    skip_if_covered: bool = False,
) -> CapabilityProjectionState | None:
    """Append work inside the caller's transaction, after locking its Bot row."""
    if not (scope.requires_device_delivery or scope.passport):
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
    elif skip_if_covered and row.retry_pending and _same_binding(row, bot) and _covers(
        _read_scope(row), scope,
    ) and set(retired_mappings).issubset(_read_mappings(row)):
        return _snapshot(row, bot)
    _advance(row, bot, scope, retired_mappings)
    return _snapshot(row, bot)


def _read_dispatches(row: BotCapabilityProjection) -> dict[str, int]:
    if row.dispatch_history_json is not None:
        return json.loads(row.dispatch_history_json)
    if row.dispatch_version is not None:
        return {row.dispatch_device_id or "": int(row.dispatch_version)}
    return {}


def _write_dispatches(row: BotCapabilityProjection, dispatches: dict[str, int]) -> None:
    row.dispatch_history_json = json.dumps(dispatches)
    # The empty key records an attempt whose actual device was unknown.
    # It may conflict with any device. Without a current device identity
    # none of the outstanding attempts can be proven unrelated either.
    target = row.device_id or ""
    if "" in dispatches:
        target = ""
    elif not target and dispatches:
        target = next(iter(dispatches))
    row.dispatch_version = dispatches.get(target)
    row.dispatch_device_id = (target or None) if row.dispatch_version is not None else None


def _mark_dispatch(row: BotCapabilityProjection, state: CapabilityProjectionState) -> None:
    dispatches = _read_dispatches(row)
    dispatches[state.device_id or ""] = state.version
    _write_dispatches(row, dispatches)


def _retry_retirement_mismatches(
    row: BotCapabilityProjection, result: RuntimeProjectionResult,
) -> RuntimeProjectionResult:
    if result.status is not RuntimeProjectionStatus.DEGRADED or any(
        issue.code == "SKILL_MAPPING_RESULT_INVALID" for issue in result.issues
    ):
        return result
    candidates = _read_mappings(row)
    alternatives = Counter(mapping.link_name for mapping in candidates)
    retry_issues = {
        issue for issue in result.issues
        if issue.name is not None
        and alternatives[issue.name] > 1
        and issue.requested_action == "RETIRE"
        and issue.code == "RETIRED_TARGET_IDENTITY_MISMATCH"
    }
    if not retry_issues:
        return result

    rejected = {issue.name for issue in retry_issues}
    remaining = []
    for candidate in candidates:
        if candidate.link_name in rejected:
            rejected.remove(candidate.link_name)
        else:
            remaining.append(candidate.to_dict())
    row.retired_json = json.dumps(remaining)

    issues = tuple(
        replace(
            issue,
            status=RuntimeProjectionStatus.PENDING,
            retryable=True,
            reason="The Runtime rejected one recorded retirement identity. "
                   "Another recorded identity remains to be checked.",
        )
        if issue in retry_issues else issue
        for issue in result.issues
    )
    if any(issue.status is RuntimeProjectionStatus.DEGRADED for issue in issues):
        return replace(result, issues=issues)
    return replace(
        result,
        status=RuntimeProjectionStatus.PENDING,
        components={**result.components, "skills": RuntimeProjectionStatus.PENDING},
        issues=issues,
    )


def _write_result(
    row: BotCapabilityProjection, result: RuntimeProjectionResult, *, mcp_only: bool,
) -> None:
    scope = _read_scope(row)
    completed = result.status is RuntimeProjectionStatus.CONVERGED
    if result.passport_updated is not None:
        scope = replace(scope, passport=not result.passport_updated)
    if mcp_only:
        components = {name: status for name, status in result.components.items() if name != "skills"}
        result = replace(result, components=components)
    elif completed or result.components.get("skills") is RuntimeProjectionStatus.CONVERGED:
        scope = replace(scope, skills=False)

    if completed or result.components.get("mcp") is RuntimeProjectionStatus.CONVERGED:
        scope = ProjectionScope(skills=scope.skills, passport=scope.passport)
    if (mcp_only and scope.skills) or (completed and scope.passport):
        result = RuntimeProjectionResult.combine(_queue_result(), result)
    previous = _read_result(row)
    result = replace(result, components={**previous.components, **result.components})
    row.result_json = json.dumps(result.to_dict(), ensure_ascii=False)
    row.retry_pending = (
        scope.passport
        or result.status is RuntimeProjectionStatus.PENDING
        or any(issue.retryable for issue in result.issues)
    )
    if not scope.skills:
        row.retired_json = "[]"
    _write_scope(row, scope)


class CapabilityProjectionRepository(CapabilityProjectionRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def validate_schema(self) -> None:
        try:
            with self._db.orm_session() as session:
                session.execute(select(BotCapabilityProjection.__table__).limit(0))
        except SQLAlchemyError as error:
            raise RuntimeError(
                "Capability projection schema unavailable. Apply the "
                "ac_bot_capability_projection migration before Backend startup."
            ) from error

    def read(self, *, bot_id: str, entity_id: str) -> CapabilityProjectionState | None:
        with self._db.orm_session() as session:
            bot = find_capability_bot(session, bot_id=bot_id, entity_id=entity_id)
            row = _get_for_bot(session, bot) if bot is not None else None
            return _snapshot(row, bot) if row is not None and bot is not None else None

    def require(
        self, *, bot_id: str, entity_id: str, scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (), skip_if_covered: bool = False,
    ) -> CapabilityProjectionState:
        with self._db.transactional_orm_session() as session:
            state = record_capability_projection(
                session, bot_id=bot_id, entity_id=entity_id, scope=scope,
                retired_mappings=retired_mappings, create=True, skip_if_covered=skip_if_covered,
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

    def begin(self, state: CapabilityProjectionState, *, device_delivery: bool = True) -> bool:
        with self._db.transactional_orm_session() as session:
            current = self._locked_state(session, state)
            if current is None:
                return False
            row, bot = current
            if (
                not self._same_target(state, bot)
                or row.version != state.version
                or row.dispatch_version is not None
            ):
                return False
            if device_delivery:
                _mark_dispatch(row, state)
            return True

    def is_dispatch_current(self, state: CapabilityProjectionState) -> bool:
        with self._db.transactional_orm_session() as session:
            current = self._locked_state(session, state)
            if current is None:
                return False
            row, bot = current
            return (
                self._same_target(state, bot)
                and row.version == state.version
                and row.dispatch_version in (None, state.version)
            )

    def complete(
        self, state: CapabilityProjectionState, result: RuntimeProjectionResult, *,
        write_blocked: bool = False, mcp_only: bool = False,
    ) -> bool:
        with self._db.transactional_orm_session() as session:
            current = self._locked_state(session, state)
            if current is None:
                return False
            row, bot = current
            dispatches = _read_dispatches(row)
            target = state.device_id or ""
            if not write_blocked and dispatches.get(target) == state.version:
                del dispatches[target]
                _write_dispatches(row, dispatches)
            if not self._same_target(state, bot):
                return False
            if row.dispatch_version not in (None, state.version):
                return False
            if row.version != state.version:
                return False
            if write_blocked:
                _mark_dispatch(row, state)
            if not write_blocked and not mcp_only:
                result = _retry_retirement_mismatches(row, result)
            _write_result(row, result, mcp_only=mcp_only)
            return True

    @staticmethod
    def _same_target(state: CapabilityProjectionState, bot: BotModel) -> bool:
        return (
            bot.binding_id == state.binding_id
            and bot.device_id == state.device_id
            and bot.active_engine == state.engine_type
            and bot.owner_id == state.owner_id
        )

    @staticmethod
    def _locked_state(
        session: Session, state: CapabilityProjectionState,
    ) -> tuple[BotCapabilityProjection, BotModel] | None:
        bot = find_capability_bot(
            session, bot_id=state.bot_id, entity_id=state.entity_id, lock=True,
        )
        if bot is None or (
            bot.id != state.bot_pk
            or bot.avernet_tenant != state.tenant
            or bot.env != state.env
        ):
            return None
        row = _get_for_bot(session, bot)
        return (row, bot) if row is not None else None

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
