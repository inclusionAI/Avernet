"""Unit tests for ``ArcaSnapshotProducer`` — behavior-equivalent build wrap."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
    ArcaSnapshotProducer,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    ServiceSkillsManifestBuilder,
    ServiceSkillsManifestError,
    service_skills_env_from_ext,
    service_skills_manifest_env,
    validate_service_skills_manifest_for_release,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


class _RecordingBuild:
    """Stub build service that records its args and returns a canned result."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[dict[str, Any], int]] = []
        self.snapshot_requirements: list[bool] = []

    def build(
        self,
        bot: dict[str, Any],
        version: int = 1,
        *,
        active_skills_snapshot_required: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((bot, version))
        self.snapshot_requirements.append(active_skills_snapshot_required)
        return self.result


class _NoSkillsManifestBuilder(ServiceSkillsManifestBuilder):
    """Minimal builder for tests that only exercise the producer wrapper."""

    def __init__(self) -> None:
        pass

    def capture(self, *, bot: dict[str, Any]) -> None:
        return None


@pytest.mark.unit
def test_passes_bot_and_version_through_to_build() -> None:
    stub = _RecordingBuild({"success": True})
    bot = {"bot_id": "b1", "entity_id": "u1"}
    ArcaSnapshotProducer(stub, _NoSkillsManifestBuilder()).produce_artifact(bot, 7)
    assert stub.calls == [(bot, 7)]


@pytest.mark.unit
def test_maps_success_and_both_paths_onto_ext() -> None:
    stub = _RecordingBuild(
        {
            "success": True,
            "migration_path": "/home/admin/nfs/bot-data/3/mcp",
            "build_target_path": "/data/bot/3/mcp",
            # extra build-result keys are not deployable pointers -> dropped from ext
            "bot_id": "b1",
            "version": "3",
        }
    )
    artifact = ArcaSnapshotProducer(
        stub, _NoSkillsManifestBuilder()
    ).produce_artifact({}, 3)
    assert artifact.success is True
    assert artifact.message == ""
    assert artifact.ext == {
        "migration_path": "/home/admin/nfs/bot-data/3/mcp",
        "build_target_path": "/data/bot/3/mcp",
    }


@pytest.mark.unit
def test_failed_build_propagates_success_false_and_message() -> None:
    stub = _RecordingBuild({"success": False})
    artifact = ArcaSnapshotProducer(
        stub, _NoSkillsManifestBuilder()
    ).produce_artifact({}, 1)
    assert artifact.success is False
    assert artifact.message == "构建失败"
    assert artifact.ext == {}


@pytest.mark.unit
def test_missing_paths_are_omitted_from_ext() -> None:
    # build() may legitimately omit a pointer (e.g. no device_id branch);
    # we must not invent keys.
    stub = _RecordingBuild({"success": True, "migration_path": "/only/mig"})
    artifact = ArcaSnapshotProducer(
        stub, _NoSkillsManifestBuilder()
    ).produce_artifact({}, 1)
    assert artifact.ext == {"migration_path": "/only/mig"}


@pytest.mark.unit
def test_pool_build_freezes_the_draft_layout_into_one_versioned_artifact(
    tmp_path,
) -> None:
    """The versioned manifest freezes policy, not engine-specific paths."""
    target = tmp_path / "openclaw"

    state = BotSkillLayoutState(
        scope=BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1"),
        active_layout=SkillLayout.POOL,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_ACTIVE,
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
        preparation_id="preparation-1",
    )
    layout_repository = _LayoutRepository(state)
    stub = _RecordingBuild(
        {
            "success": True,
            "migration_path": "/home/admin/nfs/bot-data/7/openclaw",
            "build_target_path": str(target),
        }
    )

    artifact = ArcaSnapshotProducer(
        stub,
        ServiceSkillsManifestBuilder(layout_repository),
    ).produce_artifact(
        {
            "bot_id": "b1",
            "entity_id": "u1",
            "env": "dev",
            "active_engine": "openclaw",
        },
        7,
    )

    assert artifact.ext["skills_manifest"] == {
        "schema_version": 1,
        "engine": "openclaw",
        "active_layout": "pool",
        "layout_contract_version": "skills-pool-p3-v1",
    }
    assert stub.snapshot_requirements == [True]
    assert layout_repository.scopes == [
        BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1"),
        BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1"),
    ]


class _LayoutRepository:
    def __init__(self, state: BotSkillLayoutState) -> None:
        self.state = state
        self.scopes: list[BotSkillLayoutScope] = []

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        self.scopes.append(scope)
        return self.state


@pytest.mark.unit
def test_layout_is_captured_before_physical_build_starts(tmp_path) -> None:
    target = tmp_path / "openclaw"
    legacy_local = target / "workspace" / "skills" / "skills-local"
    legacy_local.mkdir(parents=True)
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    repository = _LayoutRepository(BotSkillLayoutState.legacy_default(scope))
    pool_state = BotSkillLayoutState(
        scope=scope,
        active_layout=SkillLayout.POOL,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_ACTIVE,
        migration_generation="generation-2",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
    )

    class _StateChangingBuild(_RecordingBuild):
        def build(
            self,
            bot,
            version=1,
            *,
            active_skills_snapshot_required=False,
        ):
            repository.state = pool_state
            return super().build(
                bot,
                version,
                active_skills_snapshot_required=active_skills_snapshot_required,
            )

    producer = ArcaSnapshotProducer(
        _StateChangingBuild(
            {
                "success": True,
                "migration_path": "/snapshot/8/openclaw",
                "build_target_path": str(target),
            }
        ),
        ServiceSkillsManifestBuilder(repository),
    )

    with pytest.raises(
        ServiceSkillsManifestError,
        match="draft Skills layout changed during service build",
    ):
        producer.produce_artifact(
            {
                "bot_id": "b1",
                "entity_id": "u1",
                "env": "dev",
                "active_engine": "openclaw",
            },
            8,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "phase",
    [
        SkillLayoutPhase.POOL_PREPARING,
        SkillLayoutPhase.POOL_READY,
        SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
        SkillLayoutPhase.POOL_CUTOVER_FINALIZING,
        SkillLayoutPhase.POOL_CUTOVER_COMMITTED,
        SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
        SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED,
        SkillLayoutPhase.NEEDS_MANUAL_REPAIR,
    ],
)
def test_build_rejects_non_terminal_layout_before_physical_snapshot(
    phase: SkillLayoutPhase,
) -> None:
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    state = BotSkillLayoutState(
        scope=scope,
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=phase,
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
    )
    build = _RecordingBuild({"success": True})
    producer = ArcaSnapshotProducer(
        build,
        ServiceSkillsManifestBuilder(_LayoutRepository(state)),
    )

    with pytest.raises(
        ServiceSkillsManifestError,
        match="terminal Skills layout state",
    ):
        producer.produce_artifact(
            {
                "bot_id": "b1",
                "entity_id": "u1",
                "env": "dev",
                "active_engine": "openclaw",
            },
            8,
        )

    assert build.calls == []


@pytest.mark.unit
def test_build_rejects_phase_or_generation_drift_after_physical_snapshot(
    tmp_path,
) -> None:
    target = tmp_path / "openclaw"
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    initial = BotSkillLayoutState(
        scope=scope,
        active_layout=SkillLayout.POOL,
        target_layout=None,
        phase=SkillLayoutPhase.POOL_ACTIVE,
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
    )
    repository = _LayoutRepository(initial)

    class _StateChangingBuild(_RecordingBuild):
        def build(
            self,
            bot,
            version=1,
            *,
            active_skills_snapshot_required=False,
        ):
            repository.state = replace(
                initial,
                phase=SkillLayoutPhase.NEEDS_MANUAL_REPAIR,
                migration_generation="generation-2",
            )
            return super().build(
                bot,
                version,
                active_skills_snapshot_required=active_skills_snapshot_required,
            )

    producer = ArcaSnapshotProducer(
        _StateChangingBuild(
            {
                "success": True,
                "migration_path": "/snapshot/8/openclaw",
                "build_target_path": str(target),
            }
        ),
        ServiceSkillsManifestBuilder(repository),
    )

    with pytest.raises(
        ServiceSkillsManifestError,
        match="draft Skills layout changed during service build",
    ):
        producer.produce_artifact(
            {
                "bot_id": "b1",
                "entity_id": "u1",
                "env": "dev",
                "active_engine": "openclaw",
            },
            8,
        )


@pytest.mark.unit
def test_legacy_build_rejects_contract_drift_after_physical_snapshot(
    tmp_path,
) -> None:
    target = tmp_path / "openclaw"
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    initial = BotSkillLayoutState(
        scope=scope,
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.LEGACY_ACTIVE,
        migration_generation=None,
        persisted=True,
        layout_contract_version=None,
    )
    repository = _LayoutRepository(initial)

    class _StateChangingBuild(_RecordingBuild):
        def build(
            self,
            bot,
            version=1,
            *,
            active_skills_snapshot_required=False,
        ):
            repository.state = replace(
                initial,
                layout_contract_version="skills-pool-p3-v1",
            )
            return super().build(
                bot,
                version,
                active_skills_snapshot_required=active_skills_snapshot_required,
            )

    producer = ArcaSnapshotProducer(
        _StateChangingBuild(
            {
                "success": True,
                "migration_path": "/snapshot/8/openclaw",
                "build_target_path": str(target),
            }
        ),
        ServiceSkillsManifestBuilder(repository),
    )

    with pytest.raises(
        ServiceSkillsManifestError,
        match="draft Skills layout changed during service build",
    ):
        producer.produce_artifact(
            {
                "bot_id": "b1",
                "entity_id": "u1",
                "env": "dev",
                "active_engine": "openclaw",
            },
            8,
        )


@pytest.mark.unit
def test_release_rejects_live_engine_drift_from_the_frozen_artifact() -> None:
    with pytest.raises(
        ServiceSkillsManifestError,
        match="engine no longer matches",
    ):
        validate_service_skills_manifest_for_release(
            {
                "schema_version": 1,
                "engine": "openclaw",
                "active_layout": "pool",
                "layout_contract_version": "skills-pool-p3-v1",
            },
            {"active_engine": "claude_code"},
        )


@pytest.mark.unit
def test_release_translates_frozen_layout_into_container_env() -> None:
    assert service_skills_manifest_env(
        {
            "schema_version": 1,
            "engine": "openclaw",
            "active_layout": "pool",
            "layout_contract_version": "skills-pool-p3-v1",
        },
        {"active_engine": "openclaw"},
    ) == {
        "AGENTCLAW_SKILLS_LAYOUT": "pool",
        "AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION": "skills-pool-p3-v1",
    }


@pytest.mark.unit
def test_release_rejects_an_unknown_pool_contract() -> None:
    with pytest.raises(
        ServiceSkillsManifestError,
        match="unsupported layout contract",
    ):
        service_skills_manifest_env(
            {
                "schema_version": 1,
                "engine": "openclaw",
                "active_layout": "pool",
                "layout_contract_version": "skills-pool-future-v2",
            },
            {"active_engine": "openclaw"},
        )


@pytest.mark.unit
def test_legacy_draft_builds_a_legacy_artifact_without_pool_contract(
    tmp_path,
) -> None:
    target = tmp_path / "openclaw"
    (target / "workspace" / "skills" / "skills-local").mkdir(parents=True)
    (target / "workspace" / "skills").mkdir(parents=True, exist_ok=True)
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="legacy-bot")
    build = _RecordingBuild(
        {
            "success": True,
            "migration_path": "/snapshot/1/openclaw",
            "build_target_path": str(target),
        }
    )
    producer = ArcaSnapshotProducer(
        build,
        ServiceSkillsManifestBuilder(
            _LayoutRepository(BotSkillLayoutState.legacy_default(scope))
        ),
    )

    artifact = producer.produce_artifact(
        {
            "bot_id": "legacy-bot",
            "entity_id": "u1",
            "env": "dev",
            "active_engine": "openclaw",
        },
        1,
    )

    frozen = artifact.ext["skills_manifest"]
    assert frozen == {
        "schema_version": 1,
        "engine": "openclaw",
        "active_layout": "legacy",
        "layout_contract_version": None,
    }
    assert build.snapshot_requirements == [False]


@pytest.mark.unit
def test_historical_publish_without_manifest_is_explicitly_legacy() -> None:
    assert service_skills_env_from_ext(
        {},
        {"active_engine": "openclaw"},
    ) == {
        "AGENTCLAW_SKILLS_LAYOUT": "legacy",
    }


@pytest.mark.unit
def test_aicoding_service_engine_remains_closed() -> None:
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    build = _RecordingBuild({"success": True})
    producer = ArcaSnapshotProducer(
        build,
        ServiceSkillsManifestBuilder(
            _LayoutRepository(BotSkillLayoutState.legacy_default(scope))
        ),
    )

    with pytest.raises(ServiceSkillsManifestError):
        producer.produce_artifact(
            {
                "bot_id": "b1",
                "entity_id": "u1",
                "env": "dev",
                "active_engine": "aicoding",
            },
            1,
        )

    assert build.calls == []


@pytest.mark.unit
def test_hermes_pool_service_manifest_is_closed_before_physical_build() -> None:
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    pool_state = BotSkillLayoutState(
        scope=scope,
        active_layout=SkillLayout.POOL,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_ACTIVE,
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
    )
    build = _RecordingBuild({"success": True})
    producer = ArcaSnapshotProducer(
        build,
        ServiceSkillsManifestBuilder(_LayoutRepository(pool_state)),
    )

    with pytest.raises(
        ServiceSkillsManifestError,
        match="Hermes Pool service manifest is disabled",
    ):
        producer.produce_artifact(
            {
                "bot_id": "b1",
                "entity_id": "u1",
                "env": "dev",
                "active_engine": "hermes",
            },
            1,
        )

    assert build.calls == []


@pytest.mark.unit
def test_hermes_legacy_publish_keeps_pre_pool_compatibility() -> None:
    scope = BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1")
    build = _RecordingBuild(
        {
            "success": True,
            "migration_path": "/snapshot/1/openclaw",
            "build_target_path": "/host/snapshot/1/openclaw",
        }
    )
    producer = ArcaSnapshotProducer(
        build,
        ServiceSkillsManifestBuilder(
            _LayoutRepository(BotSkillLayoutState.legacy_default(scope))
        ),
    )

    artifact = producer.produce_artifact(
        {
            "bot_id": "b1",
            "entity_id": "u1",
            "env": "dev",
            "active_engine": "hermes",
        },
        1,
    )

    assert artifact.success is True
    assert "skills_manifest" not in artifact.ext
    assert build.calls
