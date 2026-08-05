"""Operator control plane for Skills Pool rollout admission."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import (
    BotRepository,
)
from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.skills_pool.operation_models import (
    BatchPromotionEvidence,
    RolloutAuditEvent,
    RolloutBotEntry,
    RolloutConfigSnapshot,
    RolloutControlGroup,
    RolloutOperationError,
    RolloutOwnerEntry,
    WhitelistMutationResult,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.rollout_config import (
    CONTROL_KEYS,
    ENGINE_PROMOTION_ORDER,
    is_valid_rollout_config_value,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
    SKILLS_POOL_ROLLOUT_PARAM_CODE,
)
from agentclaw.community.core.skills_pool.rollout_repository import (
    SkillsPoolRolloutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
)


class SkillsPoolRolloutOperations:
    """Mutate the environment-scoped, exact-Bot rollout configuration."""

    @inject
    def __init__(
        self,
        *,
        common_config_service: CommonConfigService,
        bot_repository: BotRepository,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        rollout_repository: SkillsPoolRolloutRepositoryProtocol,
    ) -> None:
        self._configs = common_config_service
        self._bots = bot_repository
        self._layouts = layout_repository
        self._repository = rollout_repository

    def get_snapshot(self, *, env: str) -> RolloutConfigSnapshot:
        snapshot = self._parse_config(env=env, config=self._get_config(env))
        return replace(
            snapshot,
            audit_log=self._audit_events(self._repository.list_audit_events(env=env)),
        )

    def set_feature_enabled(
        self,
        *,
        env: str,
        enabled: bool,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        self._validate_change(operator=operator, reason=reason)
        current = self.get_snapshot(env=env)
        if current.enabled is enabled and current.config_id is not None:
            return current
        return self._write(
            snapshot=current,
            expected_snapshot=current,
            enabled=enabled,
            operator=operator,
            reason=reason,
            batch_id=None,
            action="enable" if enabled else "disable",
        )

    def set_full_rollout(
        self,
        *,
        env: str,
        enabled: bool,
        engine: str | None = None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        """Enable future claims for one engine or the whole environment."""

        self._validate_change(operator=operator, reason=reason)
        current = self.get_snapshot(env=env)
        if engine is not None and engine not in ENGINE_PROMOTION_ORDER:
            raise RolloutOperationError(f"unsupported engine: {engine}")
        current_enabled = (
            current.enable_all
            if engine is None
            else engine in current.full_rollout_engines
        )
        if current_enabled is enabled:
            return current
        if enabled:
            if not current.enabled:
                raise RolloutOperationError(
                    "rollout feature must be enabled before full rollout"
                )
            target_engines = current.promoted_engines if engine is None else (engine,)
            if not target_engines:
                raise RolloutOperationError(
                    "at least one engine must be promoted before full rollout"
                )
            if current.negative_controls:
                raise RolloutOperationError(
                    "negative controls must be cleared before full rollout"
                )
            for target_engine in target_engines:
                if target_engine not in current.promoted_engines:
                    raise RolloutOperationError(
                        f"{target_engine} must be promoted before full rollout"
                    )
                if self._latest_accepted_batch(current, engine=target_engine) is None:
                    raise RolloutOperationError(
                        f"an accepted {target_engine} batch is required for full rollout"
                    )
                if self._open_batches(current, env=env, engine=target_engine):
                    raise RolloutOperationError(
                        f"{target_engine} still has an unaccepted batch"
                    )
        full_rollout_engines = current.full_rollout_engines
        if engine is not None:
            full_rollout_engines = tuple(
                item for item in full_rollout_engines if item != engine
            )
            if enabled:
                full_rollout_engines = (*full_rollout_engines, engine)
        return self._write(
            snapshot=RolloutConfigSnapshot(
                **{
                    **self._snapshot_values(current),
                    "enable_all": enabled if engine is None else current.enable_all,
                    "full_rollout_engines": full_rollout_engines,
                }
            ),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            batch_id=None,
            action=(
                f"full_rollout:{engine or 'environment'}:"
                f"{'enable' if enabled else 'disable'}"
            ),
        )

    def set_owner_full_rollout(
        self,
        *,
        env: str,
        owner_id: str,
        engine: str,
        enabled: bool,
        acceptance_batch_id: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        """Enable future claims for every Bot owned by one person and engine."""

        self._validate_change(operator=operator, reason=reason)
        entry = self._owner_entry(owner_id=owner_id, engine=engine)
        current = self.get_snapshot(env=env)
        present = entry in current.full_rollout_owners
        if present is enabled:
            return current

        acceptance = None
        if enabled:
            if not current.enabled:
                raise RolloutOperationError(
                    "rollout feature must be enabled before owner full rollout"
                )
            if engine not in current.promoted_engines:
                raise RolloutOperationError(
                    f"{engine} must be promoted before owner full rollout"
                )
            acceptance = self._latest_accepted_batch(current, engine=engine)
            if acceptance is None:
                raise RolloutOperationError(
                    f"an accepted {engine} batch is required for owner full rollout"
                )
            if acceptance_batch_id != acceptance.batch_id:
                raise RolloutOperationError(
                    "owner full rollout must reference the latest accepted batch"
                )
            if self._open_batches(current, env=env, engine=engine):
                raise RolloutOperationError(f"{engine} still has an unaccepted batch")

        owners = tuple(item for item in current.full_rollout_owners if item != entry)
        if enabled:
            owners = (*owners, entry)
        return self._write(
            snapshot=RolloutConfigSnapshot(
                **{
                    **self._snapshot_values(current),
                    "full_rollout_owners": owners,
                }
            ),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            batch_id=acceptance.batch_id if acceptance else None,
            evidence=acceptance.report if acceptance else None,
            action=(
                f"owner_full_rollout:{entry.owner_id}:{entry.engine}:"
                f"{'enable' if enabled else 'disable'}"
            ),
        )

    def promote_engine(
        self,
        *,
        env: str,
        engine: str,
        operator: str,
        reason: str,
        acceptance_batch_id: str | None = None,
    ) -> RolloutConfigSnapshot:
        # Retain the established HTTP/service parameter for callers that still
        # send it.  Engine promotion is now independent, so it must not
        # validate or audit acceptance evidence from another engine.
        del acceptance_batch_id
        self._validate_change(operator=operator, reason=reason)
        if engine not in ENGINE_PROMOTION_ORDER:
            raise RolloutOperationError(f"unsupported engine: {engine}")
        current = self.get_snapshot(env=env)
        if engine in current.promoted_engines:
            return current
        if current.enable_all:
            raise RolloutOperationError(
                "disable environment full rollout before promoting another engine"
            )
        promoted = {*current.promoted_engines, engine}
        promoted_engines = tuple(
            candidate
            for candidate in ENGINE_PROMOTION_ORDER
            if candidate in promoted
        )
        return self._write(
            snapshot=RolloutConfigSnapshot(
                **{
                    **self._snapshot_values(current),
                    "promoted_engines": promoted_engines,
                }
            ),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            batch_id=None,
            evidence=None,
            action=f"promote:{engine}",
        )

    def accept_batch(
        self,
        *,
        env: str,
        acceptance: BatchPromotionEvidence,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        self._validate_change(operator=operator, reason=reason)
        if (
            acceptance.engine not in ENGINE_PROMOTION_ORDER
            or not acceptance.batch_id.strip()
            or not acceptance.promotion_ready
        ):
            raise RolloutOperationError("batch is not ready for acceptance")
        current = self.get_snapshot(env=env)
        if acceptance.report.get("rollout_config_version") != current.config_version:
            raise RolloutOperationError("batch report is stale")
        if acceptance.engine not in current.promoted_engines:
            raise RolloutOperationError("batch engine is not promoted")
        existing = self._accepted_batch(
            current,
            engine=acceptance.engine,
            batch_id=acceptance.batch_id,
        )
        if existing is not None:
            return current
        return self._write(
            snapshot=current,
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            batch_id=acceptance.batch_id,
            evidence=acceptance.report,
            action=f"accept_batch:{acceptance.engine}",
        )

    def add_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        batch_id: str,
        acceptance_batch_id: str | None,
        operator: str,
        reason: str,
    ) -> WhitelistMutationResult:
        self._validate_change(operator=operator, reason=reason)
        bot, scope = self._resolve_bot_and_scope(
            env=env,
            owner_id=owner_id,
            bot_id=bot_id,
        )
        engine = bot.get("active_engine")
        if not isinstance(engine, str) or engine not in ENGINE_PROMOTION_ORDER:
            raise RolloutOperationError("bot engine is not supported")
        state = self._layouts.get(scope)
        claimed = self._claimed(state.active_layout, state.target_layout)
        current = self.get_snapshot(env=env)
        open_batches = self._open_batches(current, env=env, engine=engine)
        if len(open_batches) > 1:
            raise RolloutOperationError(
                f"{engine} rollout has multiple unaccepted batches"
            )
        if open_batches and batch_id not in open_batches:
            raise RolloutOperationError(
                "the current engine batch must be accepted before expansion"
            )
        latest_acceptance = self._latest_accepted_batch(
            current,
            engine=engine,
        )
        if latest_acceptance is None:
            if acceptance_batch_id is not None:
                raise RolloutOperationError(
                    "initial batch cannot reference an acceptance"
                )
        elif (
            acceptance_batch_id != latest_acceptance.batch_id
            or batch_id == latest_acceptance.batch_id
        ):
            raise RolloutOperationError(
                "a new batch must reference the latest accepted batch"
            )
        entry = self._entry(owner_id=owner_id, bot_id=bot_id, batch_id=batch_id)
        existing = next(
            (
                item
                for item in current.whitelist
                if item.owner_id == entry.owner_id and item.bot_id == entry.bot_id
            ),
            None,
        )
        if existing == entry:
            return WhitelistMutationResult(False, claimed, claimed, current)
        whitelist = tuple(
            item
            for item in current.whitelist
            if not (item.owner_id == entry.owner_id and item.bot_id == entry.bot_id)
        ) + (entry,)
        updated = self._write(
            snapshot=RolloutConfigSnapshot(
                **{
                    **self._snapshot_values(current),
                    "whitelist": whitelist,
                }
            ),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            batch_id=batch_id,
            action=f"whitelist_add:{owner_id}:{bot_id}",
        )
        # Admission only persists rollout configuration. Claiming remains an
        # asynchronous reconciliation step, so this write cannot change it.
        return WhitelistMutationResult(True, claimed, claimed, updated)

    def remove_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        operator: str,
        reason: str,
    ) -> WhitelistMutationResult:
        self._validate_change(operator=operator, reason=reason)
        current = self.get_snapshot(env=env)
        removed = next(
            (
                item
                for item in current.whitelist
                if item.owner_id == str(owner_id) and item.bot_id == str(bot_id)
            ),
            None,
        )
        scope: BotSkillLayoutScope | None = None
        claimed_before = False
        try:
            scope = self._resolve_scope(
                env=env,
                owner_id=owner_id,
                bot_id=bot_id,
            )
        except RolloutOperationError:
            # Configuration cleanup must remain possible after the Bot row
            # has been deleted or otherwise becomes unresolvable.
            pass
        if scope is not None:
            state = self._layouts.get(scope)
            claimed_before = self._claimed(
                state.active_layout,
                state.target_layout,
            )
        whitelist = tuple(
            item
            for item in current.whitelist
            if not (item.owner_id == str(owner_id) and item.bot_id == str(bot_id))
        )
        if whitelist == current.whitelist:
            return WhitelistMutationResult(
                False,
                claimed_before,
                claimed_before,
                current,
            )
        updated = self._write(
            snapshot=RolloutConfigSnapshot(
                **{
                    **self._snapshot_values(current),
                    "whitelist": whitelist,
                }
            ),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            batch_id=removed.batch_id if removed else None,
            action=f"whitelist_remove:{owner_id}:{bot_id}",
        )
        claimed_after = claimed_before
        if scope is not None:
            state_after = self._layouts.get(scope)
            claimed_after = self._claimed(
                state_after.active_layout,
                state_after.target_layout,
            )
        return WhitelistMutationResult(
            True,
            claimed_before,
            claimed_after,
            updated,
        )

    def set_control_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        batch_id: str,
        group: RolloutControlGroup,
        present: bool,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        self._validate_change(operator=operator, reason=reason)
        self._resolve_scope(env=env, owner_id=owner_id, bot_id=bot_id)
        current = self.get_snapshot(env=env)
        entry = self._entry(
            owner_id=owner_id,
            bot_id=bot_id,
            batch_id=batch_id,
        )
        source = (
            current.negative_controls
            if group is RolloutControlGroup.NEGATIVE
            else current.teclaw_controls
        )
        retained = tuple(
            item
            for item in source
            if not (item.owner_id == entry.owner_id and item.bot_id == entry.bot_id)
        )
        updated_entries = (*retained, entry) if present else retained
        if updated_entries == source:
            return current
        values = self._snapshot_values(current)
        values[
            "negative_controls"
            if group is RolloutControlGroup.NEGATIVE
            else "teclaw_controls"
        ] = updated_entries
        return self._write(
            snapshot=RolloutConfigSnapshot(**values),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            batch_id=batch_id,
            action=(
                f"control_{'add' if present else 'remove'}:"
                f"{group.value}:{owner_id}:{bot_id}"
            ),
        )

    def _get_config(self, env: str) -> dict[str, object] | None:
        return self._configs.get_config(
            business_code=SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
            param_code=SKILLS_POOL_ROLLOUT_PARAM_CODE,
            env=env,
            only_enabled=False,
        )

    def _write(
        self,
        *,
        snapshot: RolloutConfigSnapshot,
        expected_snapshot: RolloutConfigSnapshot,
        enabled: bool,
        operator: str,
        reason: str,
        batch_id: str | None,
        action: str,
        evidence: dict[str, object] | None = None,
    ) -> RolloutConfigSnapshot:
        next_revision = uuid4().hex
        event = RolloutAuditEvent(
            env=snapshot.env,
            action=action,
            operator=operator.strip(),
            reason=reason.strip(),
            batch_id=batch_id,
            based_on_config_version=snapshot.config_version,
            effective_config_version=next_revision,
            effective_at=datetime.now(UTC).isoformat(),
            evidence=evidence,
        )
        next_value = self._config_value(snapshot)
        if not self._repository.commit_change(
            env=snapshot.env,
            config_id=expected_snapshot.config_id,
            expected_revision=expected_snapshot.config_revision,
            expected_enable=expected_snapshot.enabled,
            expected_value=self._config_value(expected_snapshot),
            next_revision=next_revision,
            enabled=enabled,
            value=next_value,
            audit=event.to_dict(),
        ):
            raise RolloutOperationError("rollout config changed concurrently")
        return self.get_snapshot(env=snapshot.env)

    def _resolve_scope(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
    ) -> BotSkillLayoutScope:
        _, scope = self._resolve_bot_and_scope(
            env=env,
            owner_id=owner_id,
            bot_id=bot_id,
        )
        return scope

    def _resolve_bot_and_scope(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
    ) -> tuple[dict[str, object], BotSkillLayoutScope]:
        matches = self._bots.get_live_by_id_owner_and_env(
            bot_id=str(bot_id),
            owner_id=str(owner_id),
            env=env,
        )
        if not matches:
            raise RolloutOperationError("bot not found")
        if len(matches) != 1:
            raise RolloutOperationError("bot identity is ambiguous")
        entity_id = matches[0].get("entity_id")
        if not isinstance(entity_id, (str, int)) or isinstance(entity_id, bool):
            raise RolloutOperationError("bot entity identity is invalid")
        return (
            matches[0],
            BotSkillLayoutScope(
                env=env,
                entity_id=str(entity_id),
                bot_id=str(bot_id),
            ),
        )

    @staticmethod
    def _accepted_batch(
        snapshot: RolloutConfigSnapshot,
        *,
        engine: str,
        batch_id: str | None,
    ) -> BatchPromotionEvidence | None:
        if batch_id is None:
            return None
        for event in reversed(snapshot.audit_log):
            if (
                event.action == f"accept_batch:{engine}"
                and event.batch_id == batch_id
                and event.evidence is not None
                and event.evidence.get("promotion_ready") is True
            ):
                return BatchPromotionEvidence(
                    engine=engine,
                    batch_id=batch_id,
                    promotion_ready=True,
                    report=event.evidence,
                )
        return None

    @classmethod
    def _latest_accepted_batch(
        cls,
        snapshot: RolloutConfigSnapshot,
        *,
        engine: str,
    ) -> BatchPromotionEvidence | None:
        for event in reversed(snapshot.audit_log):
            if event.action == f"accept_batch:{engine}" and event.batch_id is not None:
                return cls._accepted_batch(
                    snapshot,
                    engine=engine,
                    batch_id=event.batch_id,
                )
        return None

    @staticmethod
    def _accepted_batch_ids(
        snapshot: RolloutConfigSnapshot,
        *,
        engine: str,
    ) -> set[str]:
        return {
            event.batch_id
            for event in snapshot.audit_log
            if event.action == f"accept_batch:{engine}"
            and event.batch_id is not None
            and event.evidence is not None
            and event.evidence.get("promotion_ready") is True
        }

    def _open_batches(
        self,
        snapshot: RolloutConfigSnapshot,
        *,
        env: str,
        engine: str,
    ) -> set[str]:
        accepted = self._accepted_batch_ids(snapshot, engine=engine)
        opened: set[str] = set()
        for entry in snapshot.whitelist:
            if entry.batch_id is None or entry.batch_id in accepted:
                continue
            try:
                bot, _ = self._resolve_bot_and_scope(
                    env=env,
                    owner_id=entry.owner_id,
                    bot_id=entry.bot_id,
                )
            except RolloutOperationError:
                # Keep an orphaned member's batch open until an operator
                # explicitly removes the stale whitelist entry.
                opened.add(entry.batch_id)
                continue
            if bot.get("active_engine") == engine:
                opened.add(entry.batch_id)
        for state in self._layouts.list_states(env=env, engine=engine):
            evidence = state.rollout_evidence
            if (
                self._claimed(state.active_layout, state.target_layout)
                and evidence is not None
                and evidence.engine_type == engine
                and evidence.batch_id is not None
                and evidence.batch_id not in accepted
            ):
                opened.add(evidence.batch_id)
        return opened

    @classmethod
    def _parse_config(
        cls,
        *,
        env: str,
        config: dict[str, object] | None,
    ) -> RolloutConfigSnapshot:
        if config is None:
            return RolloutConfigSnapshot(
                env=env,
                config_id=None,
                config_version=None,
                record_version=None,
                config_revision=None,
                enabled=False,
                enable_all=False,
                full_rollout_engines=(),
                full_rollout_owners=(),
                promoted_engines=(),
                whitelist=(),
                negative_controls=(),
                teclaw_controls=(),
                audit_log=(),
            )
        if config.get("env") != env:
            raise RolloutOperationError("rollout config environment mismatch")
        config_id = config.get("id")
        record_version = config.get("gmt_modified")
        ext_info = config.get("ext_info")
        revision = ext_info.get("revision") if isinstance(ext_info, dict) else None
        value = config.get("param_value")
        if (
            isinstance(config_id, bool)
            or not isinstance(config_id, int)
            or not isinstance(record_version, str)
            or not record_version
            or (revision is not None and not isinstance(revision, str))
            or not is_valid_rollout_config_value(value)
        ):
            raise RolloutOperationError("rollout config is invalid")
        assert isinstance(value, dict)
        promoted = value.get("promoted_engines")
        whitelist = value.get("whitelist")
        assert isinstance(promoted, list)
        assert isinstance(whitelist, list)
        promoted_engines = tuple(promoted)
        return RolloutConfigSnapshot(
            env=env,
            config_id=config_id,
            config_version=revision or record_version,
            record_version=record_version,
            config_revision=revision,
            enabled=config.get("enable") == "1",
            enable_all=bool(value["enable_all"]),
            full_rollout_engines=tuple(value.get("full_rollout_engines", [])),
            full_rollout_owners=cls._owner_entries(
                value.get("full_rollout_owners", [])
            ),
            promoted_engines=promoted_engines,
            whitelist=cls._entries(whitelist),
            negative_controls=cls._entries(value.get(CONTROL_KEYS[0], [])),
            teclaw_controls=cls._entries(value.get(CONTROL_KEYS[1], [])),
            audit_log=(),
        )

    @classmethod
    def _audit_events(cls, raw: object) -> tuple[RolloutAuditEvent, ...]:
        if not isinstance(raw, list):
            raise RolloutOperationError("rollout audit log is invalid")
        events: list[RolloutAuditEvent] = []
        for item in raw:
            if not isinstance(item, dict):
                raise RolloutOperationError("rollout audit event is invalid")
            required = (
                "action",
                "env",
                "operator",
                "reason",
                "effective_config_version",
                "effective_at",
            )
            if any(
                not isinstance(item.get(key), str) or not str(item[key]).strip()
                for key in required
            ):
                raise RolloutOperationError("rollout audit event is invalid")
            events.append(
                RolloutAuditEvent(
                    env=str(item["env"]),
                    action=str(item["action"]),
                    operator=str(item["operator"]),
                    reason=str(item["reason"]),
                    batch_id=(
                        str(item["batch_id"])
                        if item.get("batch_id") is not None
                        else None
                    ),
                    based_on_config_version=(
                        str(item["based_on_config_version"])
                        if item.get("based_on_config_version") is not None
                        else None
                    ),
                    effective_config_version=str(item["effective_config_version"]),
                    effective_at=str(item["effective_at"]),
                    evidence=(
                        item.get("evidence")
                        if isinstance(item.get("evidence"), dict)
                        else None
                    ),
                )
            )
        return tuple(events)

    @staticmethod
    def _config_value(
        snapshot: RolloutConfigSnapshot,
    ) -> dict[str, object]:
        return {
            "enable_all": snapshot.enable_all,
            "full_rollout_engines": list(snapshot.full_rollout_engines),
            "full_rollout_owners": [
                item.to_dict() for item in snapshot.full_rollout_owners
            ],
            "promoted_engines": list(snapshot.promoted_engines),
            "whitelist": [item.to_dict() for item in snapshot.whitelist],
            "negative_controls": [
                item.to_dict() for item in snapshot.negative_controls
            ],
            "teclaw_controls": [item.to_dict() for item in snapshot.teclaw_controls],
        }

    @classmethod
    def _entries(cls, raw: object) -> tuple[RolloutBotEntry, ...]:
        if not isinstance(raw, list):
            raise RolloutOperationError("rollout control entries are invalid")
        entries: list[RolloutBotEntry] = []
        for value in raw:
            if not isinstance(value, dict):
                raise RolloutOperationError("rollout control entry is invalid")
            entries.append(
                cls._entry(
                    owner_id=value.get("owner_id"),
                    bot_id=value.get("bot_id"),
                    batch_id=value.get("batch_id"),
                )
            )
        return tuple(entries)

    @classmethod
    def _owner_entries(cls, raw: object) -> tuple[RolloutOwnerEntry, ...]:
        if not isinstance(raw, list):
            raise RolloutOperationError("rollout owner entries are invalid")
        entries: list[RolloutOwnerEntry] = []
        for value in raw:
            if not isinstance(value, dict):
                raise RolloutOperationError("rollout owner entry is invalid")
            entries.append(
                cls._owner_entry(
                    owner_id=value.get("owner_id"),
                    engine=value.get("engine"),
                )
            )
        return tuple(entries)

    @staticmethod
    def _owner_entry(*, owner_id: object, engine: object) -> RolloutOwnerEntry:
        if (
            isinstance(owner_id, bool)
            or not isinstance(owner_id, (str, int))
            or str(owner_id).strip() in {"", "*"}
        ):
            raise RolloutOperationError("rollout owner identity is invalid")
        if not isinstance(engine, str) or engine not in ENGINE_PROMOTION_ORDER:
            raise RolloutOperationError("rollout owner engine is invalid")
        return RolloutOwnerEntry(owner_id=str(owner_id), engine=engine)

    @staticmethod
    def _entry(
        *,
        owner_id: object,
        bot_id: object,
        batch_id: object,
    ) -> RolloutBotEntry:
        values = (owner_id, bot_id)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (str, int))
            or str(value).strip() in {"", "*"}
            for value in values
        ):
            raise RolloutOperationError("rollout bot identity is invalid")
        if batch_id is not None and (
            isinstance(batch_id, bool)
            or not isinstance(batch_id, (str, int))
            or str(batch_id).strip() in {"", "*"}
        ):
            raise RolloutOperationError("rollout batch identity is invalid")
        return RolloutBotEntry(
            owner_id=str(owner_id),
            bot_id=str(bot_id),
            batch_id=str(batch_id) if batch_id is not None else None,
        )

    @staticmethod
    def _claimed(
        active_layout: SkillLayout,
        target_layout: SkillLayout | None,
    ) -> bool:
        return active_layout is SkillLayout.POOL or target_layout is SkillLayout.POOL

    @staticmethod
    def _validate_change(*, operator: str, reason: str) -> None:
        if not operator.strip():
            raise RolloutOperationError("operator is required")
        if not reason.strip():
            raise RolloutOperationError("change reason is required")

    @staticmethod
    def _snapshot_values(
        snapshot: RolloutConfigSnapshot,
    ) -> dict[str, object]:
        return {
            "env": snapshot.env,
            "config_id": snapshot.config_id,
            "config_version": snapshot.config_version,
            "record_version": snapshot.record_version,
            "config_revision": snapshot.config_revision,
            "enabled": snapshot.enabled,
            "enable_all": snapshot.enable_all,
            "full_rollout_engines": snapshot.full_rollout_engines,
            "full_rollout_owners": snapshot.full_rollout_owners,
            "promoted_engines": snapshot.promoted_engines,
            "whitelist": snapshot.whitelist,
            "negative_controls": snapshot.negative_controls,
            "teclaw_controls": snapshot.teclaw_controls,
            "audit_log": snapshot.audit_log,
        }
