"""Operator control plane for Skills Pool admission policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from injector import inject

from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
    SkillsPoolRolloutRepositoryProtocol,
)
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
from agentclaw.community.core.skills_pool.rollout_config import (
    ENGINE_PROMOTION_ORDER,
    ROLLOUT_SCHEMA_VERSION,
    rollout_schema_version,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
    SKILLS_POOL_ROLLOUT_PARAM_CODE,
)


class SkillsPoolRolloutOperations:
    """Read and atomically mutate one environment's admission policy."""

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
        # Kept in the constructor contract for DI compatibility with the
        # already deployed service. Admission no longer mutates layout state.
        self._layouts = layout_repository
        self._repository = rollout_repository

    def get_snapshot(self, *, env: str) -> RolloutConfigSnapshot:
        snapshot, _ = self._load_snapshot(env)
        return snapshot

    def _load_snapshot(
        self,
        env: str,
    ) -> tuple[RolloutConfigSnapshot, dict[str, object]]:
        config = self._get_config(env)
        snapshot = self._parse_config(env=env, config=config)
        stored_value = (
            config.get("param_value") if isinstance(config, dict) else None
        )
        if not isinstance(stored_value, dict):
            stored_value = self._empty_value()
        return replace(
            snapshot,
            audit_log=self._audit_events(
                self._repository.list_audit_events(env=env)
            ),
        ), stored_value

    def set_feature_enabled(
        self,
        *,
        env: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        current, policy, expected_value = self._prepare_change(
            env=env,
            expected_revision=expected_revision,
            operator=operator,
            reason=reason,
        )
        if (
            current.enabled is enabled
            and current.config_id is not None
            and current.schema_version == ROLLOUT_SCHEMA_VERSION
        ):
            return current
        return self._write(
            policy=policy,
            expected_snapshot=current,
            enabled=enabled,
            operator=operator,
            reason=reason,
            action="feature_enable" if enabled else "feature_disable",
            expected_value=expected_value,
        )

    def set_engine_admission(
        self,
        *,
        env: str,
        engine: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        self._validate_engine(engine)
        current, policy, expected_value = self._prepare_change(
            env=env,
            expected_revision=expected_revision,
            operator=operator,
            reason=reason,
        )
        if (
            policy.engine_admission.get(engine) is enabled
            and current.schema_version == ROLLOUT_SCHEMA_VERSION
        ):
            return current
        engine_admission = dict(policy.engine_admission)
        engine_admission[engine] = enabled
        return self._write(
            policy=replace(policy, engine_admission=engine_admission),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            action=f"engine_admission:{engine}:{'enable' if enabled else 'disable'}",
            evidence={"engine": engine, "enabled": enabled},
            expected_value=expected_value,
        )

    def set_environment_rollout(
        self,
        *,
        env: str,
        engine: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        self._validate_engine(engine)
        current, policy, expected_value = self._prepare_change(
            env=env,
            expected_revision=expected_revision,
            operator=operator,
            reason=reason,
        )
        present = engine in policy.environment_rollouts
        if present is enabled and current.schema_version == ROLLOUT_SCHEMA_VERSION:
            return current
        selected = set(policy.environment_rollouts)
        if enabled:
            selected.add(engine)
        else:
            selected.discard(engine)
        engines = tuple(
            candidate for candidate in ENGINE_PROMOTION_ORDER if candidate in selected
        )
        return self._write(
            policy=replace(policy, environment_rollouts=engines),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            action=f"environment_rollout:{engine}:{'enable' if enabled else 'disable'}",
            evidence={"engine": engine, "enabled": enabled},
            expected_value=expected_value,
        )

    def set_owner_rollout(
        self,
        *,
        env: str,
        owner_id: str,
        engine: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        entry = self._owner_entry(owner_id=owner_id, engine=engine)
        current, policy, expected_value = self._prepare_change(
            env=env,
            expected_revision=expected_revision,
            operator=operator,
            reason=reason,
        )
        present = entry in policy.owner_rollouts
        if present is enabled and current.schema_version == ROLLOUT_SCHEMA_VERSION:
            return current
        owners = tuple(item for item in policy.owner_rollouts if item != entry)
        if enabled:
            owners = (*owners, entry)
        return self._write(
            policy=replace(policy, owner_rollouts=owners),
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            action=(
                f"owner_rollout:{entry.owner_id}:{entry.engine}:"
                f"{'enable' if enabled else 'disable'}"
            ),
            evidence={
                "owner_id": entry.owner_id,
                "engine": entry.engine,
                "enabled": enabled,
            },
            expected_value=expected_value,
        )

    def set_bot_allow(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine: str,
        present: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        return self._set_bot_rule(
            env=env,
            owner_id=owner_id,
            bot_id=bot_id,
            engine=engine,
            present=present,
            expected_revision=expected_revision,
            operator=operator,
            reason=reason,
            exclusion=False,
        )

    def set_bot_exclusion(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine: str,
        present: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot:
        return self._set_bot_rule(
            env=env,
            owner_id=owner_id,
            bot_id=bot_id,
            engine=engine,
            present=present,
            expected_revision=expected_revision,
            operator=operator,
            reason=reason,
            exclusion=True,
        )

    def _set_bot_rule(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine: str,
        present: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
        exclusion: bool,
    ) -> RolloutConfigSnapshot:
        self._validate_engine(engine)
        current, policy, expected_value = self._prepare_change(
            env=env,
            expected_revision=expected_revision,
            operator=operator,
            reason=reason,
        )
        entry = RolloutBotEntry(
            owner_id=self._identity(owner_id, label="owner"),
            bot_id=self._identity(bot_id, label="bot"),
            engine=engine,
        )
        source = policy.bot_exclusions if exclusion else policy.bot_allowlist
        existing = next(
            (
                item
                for item in source
                if item.owner_id == entry.owner_id
                and item.bot_id == entry.bot_id
                and item.engine == entry.engine
            ),
            None,
        )
        if (
            (existing is not None) is present
            and current.schema_version == ROLLOUT_SCHEMA_VERSION
        ):
            return current
        if present:
            actual_engine = self._resolve_bot_engine(
                env=env,
                owner_id=entry.owner_id,
                bot_id=entry.bot_id,
            )
            if actual_engine != engine:
                raise RolloutOperationError("bot engine does not match policy engine")
        retained = tuple(
            item
            for item in source
            if not (
                item.owner_id == entry.owner_id
                and item.bot_id == entry.bot_id
                and item.engine == entry.engine
            )
        )
        updated = (*retained, entry) if present else retained
        policy = replace(
            policy,
            bot_exclusions=updated if exclusion else policy.bot_exclusions,
            bot_allowlist=policy.bot_allowlist if exclusion else updated,
        )
        rule_name = "bot_exclusion" if exclusion else "bot_allow"
        return self._write(
            policy=policy,
            expected_snapshot=current,
            enabled=current.enabled,
            operator=operator,
            reason=reason,
            action=f"{rule_name}:{'add' if present else 'remove'}:{entry.bot_id}",
            evidence={
                "owner_id": entry.owner_id,
                "bot_id": entry.bot_id,
                "engine": engine,
                "present": present,
            },
            expected_value=expected_value,
        )

    def _prepare_change(
        self,
        *,
        env: str,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> tuple[
        RolloutConfigSnapshot,
        RolloutConfigSnapshot,
        dict[str, object],
    ]:
        self._validate_change(operator=operator, reason=reason)
        current, expected_value = self._load_snapshot(env)
        if current.config_version != expected_revision:
            raise RolloutOperationError("POLICY_REVISION_CONFLICT")
        return current, self._to_v2(current), expected_value

    def _to_v2(self, snapshot: RolloutConfigSnapshot) -> RolloutConfigSnapshot:
        if snapshot.schema_version == ROLLOUT_SCHEMA_VERSION:
            return snapshot

        def bind_engine(entry: RolloutBotEntry) -> RolloutBotEntry:
            engine = self._resolve_bot_engine(
                env=snapshot.env,
                owner_id=entry.owner_id,
                bot_id=entry.bot_id,
            )
            return RolloutBotEntry(
                owner_id=entry.owner_id,
                bot_id=entry.bot_id,
                engine=engine,
            )

        return replace(
            snapshot,
            schema_version=ROLLOUT_SCHEMA_VERSION,
            bot_allowlist=tuple(bind_engine(entry) for entry in snapshot.bot_allowlist),
            bot_exclusions=tuple(bind_engine(entry) for entry in snapshot.bot_exclusions),
            legacy_teclaw_controls=(),
        )

    def _write(
        self,
        *,
        policy: RolloutConfigSnapshot,
        expected_snapshot: RolloutConfigSnapshot,
        enabled: bool,
        operator: str,
        reason: str,
        action: str,
        evidence: dict[str, object] | None = None,
        expected_value: dict[str, object],
    ) -> RolloutConfigSnapshot:
        next_revision = uuid4().hex
        effective_at = datetime.now(UTC).isoformat()
        audit_evidence = dict(evidence or {})
        if expected_snapshot.schema_version == 1:
            # v2 deliberately drops Batch gates. Preserve the last v1 policy
            # only as immutable diagnostic evidence so the temporary Batch GET
            # remains useful without letting legacy fields back into admission.
            audit_evidence["legacy_policy"] = expected_value
        event = RolloutAuditEvent(
            env=policy.env,
            action=action,
            operator=operator.strip(),
            reason=reason.strip(),
            batch_id=None,
            based_on_config_version=expected_snapshot.config_version,
            effective_config_version=next_revision,
            effective_at=effective_at,
            evidence=audit_evidence or None,
        )
        if not self._repository.commit_change(
            env=policy.env,
            config_id=expected_snapshot.config_id,
            expected_revision=expected_snapshot.config_revision,
            expected_enable=expected_snapshot.enabled,
            expected_value=expected_value,
            next_revision=next_revision,
            enabled=enabled,
            value=self._config_value(policy),
            audit=event.to_dict(),
        ):
            raise RolloutOperationError("POLICY_REVISION_CONFLICT")
        return self.get_snapshot(env=policy.env)

    def _get_config(self, env: str) -> dict[str, object] | None:
        return self._configs.get_config(
            business_code=SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
            param_code=SKILLS_POOL_ROLLOUT_PARAM_CODE,
            env=env,
            only_enabled=False,
        )

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
                schema_version=ROLLOUT_SCHEMA_VERSION,
                engine_admission={},
                bot_allowlist=(),
                owner_rollouts=(),
                environment_rollouts=(),
                bot_exclusions=(),
                audit_log=(),
            )
        if config.get("env") != env:
            raise RolloutOperationError("rollout config environment mismatch")
        config_id = config.get("id")
        record_version = config.get("gmt_modified")
        ext_info = config.get("ext_info")
        revision = ext_info.get("revision") if isinstance(ext_info, dict) else None
        value = config.get("param_value")
        version = rollout_schema_version(value)
        if (
            isinstance(config_id, bool)
            or not isinstance(config_id, int)
            or not isinstance(record_version, str)
            or not record_version
            or (revision is not None and not isinstance(revision, str))
            or version is None
            or not isinstance(value, dict)
        ):
            raise RolloutOperationError("rollout config is invalid")

        if version == ROLLOUT_SCHEMA_VERSION:
            admission = value["engine_admission"]
            assert isinstance(admission, dict)
            return RolloutConfigSnapshot(
                env=env,
                config_id=config_id,
                config_version=revision or record_version,
                record_version=record_version,
                config_revision=revision,
                enabled=config.get("enable") == "1",
                schema_version=version,
                engine_admission={str(key): bool(item) for key, item in admission.items()},
                bot_allowlist=cls._v2_bot_entries(value["bot_allowlist"]),
                owner_rollouts=cls._owner_entries(value["owner_rollouts"]),
                environment_rollouts=tuple(value["environment_rollouts"]),
                bot_exclusions=cls._v2_bot_entries(value["bot_exclusions"]),
                audit_log=(),
            )

        promoted = value["promoted_engines"]
        assert isinstance(promoted, list)
        full_engines = value.get("full_rollout_engines", [])
        assert isinstance(full_engines, list)
        environment_rollouts = tuple(
            promoted if value["enable_all"] else full_engines
        )
        return RolloutConfigSnapshot(
            env=env,
            config_id=config_id,
            config_version=revision or record_version,
            record_version=record_version,
            config_revision=revision,
            enabled=config.get("enable") == "1",
            schema_version=1,
            engine_admission={str(engine): True for engine in promoted},
            bot_allowlist=cls._v1_bot_entries(value["whitelist"]),
            owner_rollouts=cls._owner_entries(value.get("full_rollout_owners", [])),
            environment_rollouts=environment_rollouts,
            bot_exclusions=cls._v1_bot_entries(value.get("negative_controls", [])),
            legacy_teclaw_controls=cls._v1_bot_entries(
                value.get("teclaw_controls", [])
            ),
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
    def _config_value(policy: RolloutConfigSnapshot) -> dict[str, object]:
        if policy.schema_version != ROLLOUT_SCHEMA_VERSION:
            raise RolloutOperationError("rollout policy must be schema v2")
        if any(entry.engine is None for entry in (*policy.bot_allowlist, *policy.bot_exclusions)):
            raise RolloutOperationError("rollout policy has an unbound engine")
        return {
            "schema_version": ROLLOUT_SCHEMA_VERSION,
            "engine_admission": dict(policy.engine_admission),
            "bot_allowlist": [entry.to_dict() for entry in policy.bot_allowlist],
            "owner_rollouts": [entry.to_dict() for entry in policy.owner_rollouts],
            "environment_rollouts": list(policy.environment_rollouts),
            "bot_exclusions": [entry.to_dict() for entry in policy.bot_exclusions],
        }

    @staticmethod
    def _empty_value() -> dict[str, object]:
        return {
            "schema_version": ROLLOUT_SCHEMA_VERSION,
            "engine_admission": {},
            "bot_allowlist": [],
            "owner_rollouts": [],
            "environment_rollouts": [],
            "bot_exclusions": [],
        }

    @classmethod
    def _v1_bot_entries(cls, raw: object) -> tuple[RolloutBotEntry, ...]:
        if not isinstance(raw, list):
            raise RolloutOperationError("rollout bot entries are invalid")
        return tuple(
            RolloutBotEntry(
                owner_id=cls._identity(entry["owner_id"], label="owner"),
                bot_id=cls._identity(entry["bot_id"], label="bot"),
                batch_id=(
                    str(entry["batch_id"])
                    if entry.get("batch_id") is not None
                    else None
                ),
            )
            for entry in raw
            if isinstance(entry, dict)
        )

    @classmethod
    def _v2_bot_entries(cls, raw: object) -> tuple[RolloutBotEntry, ...]:
        if not isinstance(raw, list):
            raise RolloutOperationError("rollout bot entries are invalid")
        return tuple(
            RolloutBotEntry(
                owner_id=cls._identity(entry["owner_id"], label="owner"),
                bot_id=cls._identity(entry["bot_id"], label="bot"),
                engine=str(entry["engine"]),
            )
            for entry in raw
            if isinstance(entry, dict)
        )

    @classmethod
    def _owner_entries(cls, raw: object) -> tuple[RolloutOwnerEntry, ...]:
        if not isinstance(raw, list):
            raise RolloutOperationError("rollout owner entries are invalid")
        return tuple(
            cls._owner_entry(
                owner_id=entry["owner_id"],
                engine=entry["engine"],
            )
            for entry in raw
            if isinstance(entry, dict)
        )

    @classmethod
    def _owner_entry(cls, *, owner_id: object, engine: object) -> RolloutOwnerEntry:
        owner = cls._identity(owner_id, label="owner")
        if not isinstance(engine, str):
            raise RolloutOperationError("rollout owner engine is invalid")
        cls._validate_engine(engine)
        return RolloutOwnerEntry(owner_id=owner, engine=engine)

    def _resolve_bot_engine(self, *, env: str, owner_id: str, bot_id: str) -> str:
        matches = self._bots.get_live_by_id_owner_and_env(
            bot_id=str(bot_id),
            owner_id=str(owner_id),
            env=env,
        )
        if not matches:
            raise RolloutOperationError(
                f"cannot bind engine for missing bot {owner_id}/{bot_id}"
            )
        if len(matches) != 1:
            raise RolloutOperationError("bot identity is ambiguous")
        engine = matches[0].get("active_engine")
        if not isinstance(engine, str):
            raise RolloutOperationError("bot engine is invalid")
        self._validate_engine(engine)
        return engine

    @staticmethod
    def _identity(value: object, *, label: str) -> str:
        if (
            isinstance(value, bool)
            or not isinstance(value, (str, int))
            or str(value).strip() in {"", "*"}
        ):
            raise RolloutOperationError(f"rollout {label} identity is invalid")
        return str(value)

    @staticmethod
    def _validate_engine(engine: str) -> None:
        if engine not in ENGINE_PROMOTION_ORDER:
            raise RolloutOperationError(f"unsupported engine: {engine}")

    @staticmethod
    def _validate_change(*, operator: str, reason: str) -> None:
        if not operator.strip():
            raise RolloutOperationError("operator is required")
        if not reason.strip():
            raise RolloutOperationError("change reason is required")


__all__ = [
    "BatchPromotionEvidence",
    "RolloutConfigSnapshot",
    "RolloutControlGroup",
    "RolloutOperationError",
    "WhitelistMutationResult",
    "SkillsPoolRolloutOperations",
]
