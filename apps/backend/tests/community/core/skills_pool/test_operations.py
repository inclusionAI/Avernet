"""Skills Pool admission policy operations."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skills_pool.operations import (
    RolloutOperationError,
    SkillsPoolRolloutOperations,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


ENV = "pre"
OWNER = "owner-1"
BOT_ID = "bot-1"


class FakeCommonConfig:
    def __init__(self, config: dict[str, object] | None = None) -> None:
        self.config = config

    def get_config(self, **_: object) -> dict[str, object] | None:
        return self.config


class FakeRolloutRepository:
    def __init__(self, configs: FakeCommonConfig) -> None:
        self.configs = configs
        self.audit: list[dict[str, object]] = []
        self.cas_succeeds = True

    def list_audit_events(self, *, env: str) -> list[dict[str, object]]:
        return [event for event in self.audit if event["env"] == env]

    def commit_change(self, **kwargs: object) -> bool:
        if not self.cas_succeeds:
            return False
        current = self.configs.config
        if current is not None:
            ext = current.get("ext_info")
            revision = ext.get("revision") if isinstance(ext, dict) else None
            if revision != kwargs["expected_revision"]:
                return False
        audit = kwargs["audit"]
        value = kwargs["value"]
        assert isinstance(audit, dict)
        assert isinstance(value, dict)
        self.audit.append(audit)
        self.configs.config = {
            "id": 42,
            "enable": "1" if kwargs["enabled"] else "0",
            "env": kwargs["env"],
            "param_value": value,
            "ext_info": {"revision": kwargs["next_revision"]},
            "gmt_modified": "2026-09-16T12:00:00+00:00",
        }
        return True


class FakeBots:
    def __init__(self) -> None:
        self.matches: list[dict[str, object]] = [
            {
                "bot_id": BOT_ID,
                "owner_id": OWNER,
                "entity_id": OWNER,
                "env": ENV,
                "active_engine": "openclaw",
            },
            {
                "bot_id": "excluded-bot",
                "owner_id": OWNER,
                "entity_id": OWNER,
                "env": ENV,
                "active_engine": "openclaw",
            },
        ]

    def get_live_by_id_owner_and_env(
        self,
        **identity: object,
    ) -> list[dict[str, object]]:
        return [
            bot
            for bot in self.matches
            if str(bot["bot_id"]) == str(identity["bot_id"])
            and str(bot["owner_id"]) == str(identity["owner_id"])
            and bot["env"] == identity["env"]
        ]


class FakeLayouts:
    def get(self, _: BotSkillLayoutScope) -> object:
        raise AssertionError("admission policy must not mutate layout state")


def v1_config() -> dict[str, object]:
    return {
        "id": 7,
        "enable": "1",
        "env": ENV,
        "gmt_modified": "legacy-record-version",
        "ext_info": {},
        "param_value": {
            "enable_all": False,
            "full_rollout_engines": ["openclaw"],
            "full_rollout_owners": [
                {"owner_id": OWNER, "engine": "openclaw"}
            ],
            "promoted_engines": ["openclaw"],
            "whitelist": [
                {"owner_id": OWNER, "bot_id": BOT_ID, "batch_id": "old-batch"}
            ],
            "negative_controls": [
                {
                    "owner_id": OWNER,
                    "bot_id": "excluded-bot",
                    "batch_id": "old-batch",
                }
            ],
            "teclaw_controls": [],
        },
    }


def v2_config() -> dict[str, object]:
    return {
        "id": 7,
        "enable": "1",
        "env": ENV,
        "gmt_modified": "record-version",
        "ext_info": {"revision": "revision-1"},
        "param_value": {
            "schema_version": 2,
            "engine_admission": {"openclaw": True},
            "bot_allowlist": [],
            "owner_rollouts": [],
            "environment_rollouts": [],
            "bot_exclusions": [],
        },
    }


def build_operations(
    config: dict[str, object] | None = None,
) -> tuple[SkillsPoolRolloutOperations, FakeCommonConfig, FakeRolloutRepository]:
    configs = FakeCommonConfig(config)
    repository = FakeRolloutRepository(configs)
    return (
        SkillsPoolRolloutOperations(
            common_config_service=configs,
            bot_repository=FakeBots(),
            layout_repository=FakeLayouts(),
            rollout_repository=repository,
        ),
        configs,
        repository,
    )


def test_first_mutation_creates_safe_v2_policy() -> None:
    operations, configs, _ = build_operations()

    snapshot = operations.set_feature_enabled(
        env=ENV,
        enabled=True,
        expected_revision=None,
        operator="freddie",
        reason="start controlled rollout",
    )

    assert snapshot.enabled is True
    assert snapshot.schema_version == 2
    assert configs.config is not None
    assert configs.config["param_value"] == {
        "schema_version": 2,
        "engine_admission": {},
        "bot_allowlist": [],
        "owner_rollouts": [],
        "environment_rollouts": [],
        "bot_exclusions": [],
    }


def test_every_mutation_rejects_a_stale_policy_revision_even_if_idempotent() -> None:
    operations, _, _ = build_operations(v2_config())

    with pytest.raises(RolloutOperationError, match="POLICY_REVISION_CONFLICT"):
        operations.set_engine_admission(
            env=ENV,
            engine="openclaw",
            enabled=True,
            expected_revision="stale",
            operator="freddie",
            reason="stale request",
        )


def test_first_v2_write_converts_v1_without_batch_gates() -> None:
    operations, configs, repository = build_operations(v1_config())

    snapshot = operations.set_engine_admission(
        env=ENV,
        engine="openclaw",
        enabled=True,
        expected_revision="legacy-record-version",
        operator="freddie",
        reason="cut policy to v2",
    )

    assert snapshot.schema_version == 2
    assert snapshot.bot_allowlist[0].engine == "openclaw"
    assert snapshot.bot_exclusions[0].engine == "openclaw"
    assert snapshot.environment_rollouts == ("openclaw",)
    assert configs.config is not None
    value = configs.config["param_value"]
    assert isinstance(value, dict)
    assert "promoted_engines" not in value
    assert "batch_id" not in str(value)
    assert repository.audit[-1]["batch_id"] is None
    evidence = repository.audit[-1]["evidence"]
    assert isinstance(evidence, dict)
    legacy_policy = evidence["legacy_policy"]
    assert isinstance(legacy_policy, dict)
    assert legacy_policy["whitelist"][0]["batch_id"] == "old-batch"


def test_allow_rules_can_be_preconfigured_while_engine_admission_is_paused() -> None:
    config = v2_config()
    value = config["param_value"]
    assert isinstance(value, dict)
    value["engine_admission"] = {"openclaw": False}
    operations, _, _ = build_operations(config)

    snapshot = operations.set_bot_allow(
        env=ENV,
        owner_id=OWNER,
        bot_id=BOT_ID,
        engine="openclaw",
        present=True,
        expected_revision="revision-1",
        operator="freddie",
        reason="prepare canary before opening engine admission",
    )

    assert snapshot.engine_admission == {"openclaw": False}
    assert snapshot.bot_allowlist[0].bot_id == BOT_ID


def test_exact_rule_rejects_a_mismatched_engine() -> None:
    config = v2_config()
    value = config["param_value"]
    assert isinstance(value, dict)
    value["engine_admission"] = {"openclaw": True, "hermes": True}
    operations, _, _ = build_operations(config)

    with pytest.raises(
        RolloutOperationError,
        match="bot engine does not match policy engine",
    ):
        operations.set_bot_allow(
            env=ENV,
            owner_id=OWNER,
            bot_id=BOT_ID,
            engine="hermes",
            present=True,
            expected_revision="revision-1",
            operator="freddie",
            reason="wrong engine",
        )


def test_owner_environment_and_exclusion_rules_are_independent() -> None:
    operations, _, _ = build_operations(v2_config())

    owner = operations.set_owner_rollout(
        env=ENV,
        owner_id=OWNER,
        engine="openclaw",
        enabled=True,
        expected_revision="revision-1",
        operator="freddie",
        reason="owner rollout",
    )
    environment = operations.set_environment_rollout(
        env=ENV,
        engine="openclaw",
        enabled=True,
        expected_revision=owner.config_version,
        operator="freddie",
        reason="environment rollout",
    )
    excluded = operations.set_bot_exclusion(
        env=ENV,
        owner_id=OWNER,
        bot_id=BOT_ID,
        engine="openclaw",
        present=True,
        expected_revision=environment.config_version,
        operator="freddie",
        reason="exclude one bot",
    )

    assert owner.owner_rollouts[0].owner_id == OWNER
    assert environment.environment_rollouts == ("openclaw",)
    assert excluded.bot_exclusions[0].bot_id == BOT_ID


def test_repository_cas_conflict_is_reported_with_stable_code() -> None:
    operations, _, repository = build_operations(v2_config())
    repository.cas_succeeds = False

    with pytest.raises(RolloutOperationError, match="POLICY_REVISION_CONFLICT"):
        operations.set_engine_admission(
            env=ENV,
            engine="openclaw",
            enabled=False,
            expected_revision="revision-1",
            operator="freddie",
            reason="pause rollout",
        )
