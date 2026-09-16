from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.config.common_config import CommonConfigRepository
from agentclaw.community.core.repository.implementations.skills_pool.rollout import SkillsPoolRolloutRepository


def _value(*, bots: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "enable_all": False,
        "promoted_engines": ["openclaw"],
        "whitelist": bots or [],
        "negative_controls": [],
        "teclaw_controls": [],
    }


def _audit(revision: str) -> dict[str, object]:
    return {
        "env": "pre",
        "action": "enable",
        "operator": "freddie",
        "reason": "start canary",
        "batch_id": None,
        "based_on_config_version": None,
        "effective_config_version": revision,
        "effective_at": datetime.now(UTC).isoformat(),
        "evidence": None,
    }


def _v2_value() -> dict[str, object]:
    return {
        "schema_version": 2,
        "engine_admission": {"openclaw": True},
        "bot_allowlist": [
            {"owner_id": "owner-1", "bot_id": "bot-1", "engine": "openclaw"}
        ],
        "owner_rollouts": [],
        "environment_rollouts": [],
        "bot_exclusions": [],
    }


def test_rollout_config_and_append_only_audit_commit_atomically(test_injector):
    from agentclaw.community.plugin_api.database import DatabasePlugin

    database = test_injector.get(DatabasePlugin)
    asyncio.run(database.bootstrap())
    repository = SkillsPoolRolloutRepository(database)

    assert repository.commit_change(
        env="pre",
        config_id=None,
        expected_revision=None,
        expected_enable=False,
        expected_value=_value(),
        next_revision="revision-1",
        enabled=True,
        value=_value(),
        audit=_audit("revision-1"),
    )

    config = CommonConfigRepository(database).get_by_biz_param(
        business_code="skills_pool",
        param_code="layout_rollout",
        env="pre",
    )
    assert config is not None
    events = repository.list_audit_events(env="pre")
    assert [event["effective_config_version"] for event in events] == [
        "revision-1"
    ]

    # A writer that bypasses the rollout API changes semantic content without
    # advancing revision. The full expected-value CAS still rejects overwrite.
    CommonConfigRepository(database).update_config(
        config_id=config.id,
        updates={"param_value": '{"enable_all":true}'},
    )
    assert not repository.commit_change(
        env="pre",
        config_id=config.id,
        expected_revision="revision-1",
        expected_enable=True,
        expected_value=_value(),
        next_revision="revision-2",
        enabled=True,
        value=_value(
            bots=[{"owner_id": "owner-1", "bot_id": "bot-1"}]
        ),
        audit=_audit("revision-2"),
    )
    assert len(repository.list_audit_events(env="pre")) == 1


def test_v1_expected_value_can_cas_directly_to_v2(test_injector) -> None:
    from agentclaw.community.plugin_api.database import DatabasePlugin

    database = test_injector.get(DatabasePlugin)
    asyncio.run(database.bootstrap())
    repository = SkillsPoolRolloutRepository(database)
    assert repository.commit_change(
        env="pre",
        config_id=None,
        expected_revision=None,
        expected_enable=False,
        expected_value=_value(),
        next_revision="legacy-revision",
        enabled=True,
        value=_value(bots=[{"owner_id": "owner-1", "bot_id": "bot-1"}]),
        audit=_audit("legacy-revision"),
    )
    config = CommonConfigRepository(database).get_by_biz_param(
        business_code="skills_pool",
        param_code="layout_rollout",
        env="pre",
    )
    assert config is not None

    assert repository.commit_change(
        env="pre",
        config_id=config.id,
        expected_revision="legacy-revision",
        expected_enable=True,
        expected_value=_value(
            bots=[{"owner_id": "owner-1", "bot_id": "bot-1"}]
        ),
        next_revision="policy-v2-revision",
        enabled=True,
        value=_v2_value(),
        audit=_audit("policy-v2-revision"),
    )

    updated = CommonConfigRepository(database).get_by_id(config_id=config.id)
    assert updated is not None
    assert '"schema_version": 2' in (updated.param_value or "")


def test_concurrent_first_config_insert_is_reported_as_cas_conflict() -> None:
    class ConflictingSession:
        def query(self, *_: object):
            return self

        def filter(self, *_: object):
            return self

        def with_for_update(self):
            return self

        def one_or_none(self):
            return None

        def add(self, _: object) -> None:
            return None

        def flush(self) -> None:
            raise IntegrityError(
                "INSERT INTO ac_common_config ...",
                {},
                RuntimeError("duplicate rollout config"),
            )

    class ConflictingDatabase:
        @contextmanager
        def transactional_orm_session(self):
            yield ConflictingSession()

    repository = SkillsPoolRolloutRepository(ConflictingDatabase())

    assert not repository.commit_change(
        env="pre",
        config_id=None,
        expected_revision=None,
        expected_enable=False,
        expected_value=_value(),
        next_revision="revision-1",
        enabled=True,
        value=_value(),
        audit=_audit("revision-1"),
    )
