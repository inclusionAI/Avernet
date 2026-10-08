"""Fresh layout evidence and Legacy/Pool Center manifest contracts."""

from __future__ import annotations

import pytest

from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ServiceArtifactBuildErrorCode,
    ServiceArtifactLayoutObservation,
    ServiceArtifactResolvedLayout,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    ServiceSkillsManifestBuilder,
    ServiceSkillsManifestError,
    validate_service_skills_manifest_for_release,
)
from agentclaw.community.core.skill_center.runtime_layout_probe_service_protocol import (
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    MAPPING_CONTRACT_VERSION,
    MAPPING_V3_CONTRACT_VERSION,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


_CENTER_PREFIX = "aidesktop/aidesktop_dev/bolt_shared/skills-center"
_REPO_PREFIX = "skills-repo/b1"
_CENTER_ROOT = "/home/admin/.openclaw/workspace/skills-pool/skill-center"


class _LayoutRepository:
    def __init__(self, state: BotSkillLayoutState) -> None:
        self.state = state

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        return self.state


class _Reader:
    def __init__(self, *snapshots) -> None:
        self._snapshots = iter(snapshots or ((), ()))

    def active_skill_assets(self, **_kwargs):
        return tuple(next(self._snapshots))


class _Store:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def verify_version(self, _ref) -> bool:
        return self.ready


def _legacy_state(bot_id: str = "b1") -> BotSkillLayoutState:
    return BotSkillLayoutState.legacy_default(
        BotSkillLayoutScope(env="dev", entity_id="u1", bot_id=bot_id)
    )


def _pool_state() -> BotSkillLayoutState:
    return BotSkillLayoutState(
        scope=BotSkillLayoutScope(env="dev", entity_id="u1", bot_id="b1"),
        active_layout=SkillLayout.POOL,
        target_layout=None,
        phase=SkillLayoutPhase.POOL_ACTIVE,
        migration_generation="generation-1",
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
        # Deliberately unusable: Service Artifact must consume the fresh
        # observation, not this persisted migration/recovery evidence.
        last_probe_result="INVALID",
        last_probe_evidence={"reason": "stale"},
    )


def _observation(
    *,
    status: RuntimeLayoutProbeStatus = RuntimeLayoutProbeStatus.READY,
    mappings: frozenset[str] | None = None,
    center_mount_status: str = "READY",
) -> ServiceArtifactLayoutObservation:
    resolved = None
    if status is RuntimeLayoutProbeStatus.READY:
        resolved = ServiceArtifactResolvedLayout(
            engine="openclaw",
            layout_contract_version="skills-pool-p3-v1",
            active_root="/home/admin/.openclaw/workspace/skills",
            local_root=(
                "/home/admin/.openclaw/workspace/skills-pool/skills-local"
            ),
            repo_root="/home/admin/.openclaw/workspace/skills-pool/skills-repo",
            center_root=_CENTER_ROOT,
        )
    return ServiceArtifactLayoutObservation(
        status=status,
        engine="openclaw",
        layout_contract_version="skills-pool-p3-v1",
        resolved_layout=resolved,
        supported_mapping_contract_versions=(
            mappings
            if mappings is not None
            else frozenset(
                {MAPPING_CONTRACT_VERSION, MAPPING_V3_CONTRACT_VERSION}
            )
        ),
        center_mount_status=center_mount_status,
        reason=None if resolved is not None else "runtime_probe_failed",
    )


def _center_asset(
    *,
    name: str = "pdf",
    skill_uuid: str = "00000000-0000-4000-8000-000000000001",
    version: str = "1.0.0",
) -> RegisteredSkillAsset:
    return RegisteredSkillAsset(
        skill_id=1,
        name=name,
        git_path=f"center://{name}",
        skill_uuid=skill_uuid,
        sc_version_number=version,
    )


def _builder(state, reader, store=None) -> ServiceSkillsManifestBuilder:
    return ServiceSkillsManifestBuilder(
        _LayoutRepository(state),
        reader,
        _CENTER_PREFIX,
        store or _Store(),
        _REPO_PREFIX,
    )


def _bot() -> dict[str, str]:
    return {
        "bot_id": "b1",
        "owner_id": "u1",
        "entity_id": "u1",
        "env": "dev",
        "active_engine": "openclaw",
    }


@pytest.mark.unit
def test_legacy_center_uses_fresh_v3_evidence_and_center_delivery_only() -> None:
    asset = _center_asset()
    builder = _builder(_legacy_state(), _Reader((asset,), (asset,)))

    captured = builder.capture(
        bot=_bot(),
        layout_observation=_observation(center_mount_status="NOT_READY"),
    )
    manifest = builder.finalize(captured=captured, bot=_bot())

    assert captured.active_runtime_path.endswith("/workspace/skills")
    assert [item.corpus for item in captured.shared_corpora] == ["center"]
    assert manifest["active_layout"] == "legacy"
    assert manifest["layout_contract_version"] is None
    assert [item["corpus"] for item in manifest["shared_corpora"]] == ["center"]
    assert manifest["center_skills"][0]["sc_version_number"] == "1.0.0"


@pytest.mark.unit
def test_pool_ignores_stale_persisted_probe_and_freezes_repo_then_center() -> None:
    builder = _builder(_pool_state(), _Reader((), ()))

    captured = builder.capture(bot=_bot(), layout_observation=_observation())
    manifest = builder.finalize(captured=captured, bot=_bot())

    assert [item.corpus for item in captured.shared_corpora] == ["repo", "center"]
    assert [item["corpus"] for item in manifest["shared_corpora"]] == [
        "repo",
        "center",
    ]


@pytest.mark.unit
def test_center_requires_mapping_v3() -> None:
    asset = _center_asset()
    builder = _builder(_legacy_state(), _Reader((asset,)))

    with pytest.raises(ServiceSkillsManifestError) as raised:
        builder.capture(
            bot=_bot(),
            layout_observation=_observation(
                mappings=frozenset({MAPPING_CONTRACT_VERSION})
            ),
        )

    assert raised.value.code is (
        ServiceArtifactBuildErrorCode.LAYOUT_EVIDENCE_UNAVAILABLE
    )


@pytest.mark.unit
def test_pool_requires_ready_observation_but_legacy_without_center_does_not() -> None:
    unavailable = _observation(status=RuntimeLayoutProbeStatus.TRANSIENT_ERROR)

    with pytest.raises(ServiceSkillsManifestError) as raised:
        _builder(_pool_state(), _Reader(())).capture(
            bot=_bot(), layout_observation=unavailable
        )
    assert raised.value.code is (
        ServiceArtifactBuildErrorCode.LAYOUT_EVIDENCE_UNAVAILABLE
    )

    captured = _builder(_legacy_state(), _Reader(())).capture(
        bot=_bot(), layout_observation=unavailable
    )
    assert captured.shared_corpora == ()
    assert captured.active_runtime_path is None


@pytest.mark.unit
def test_center_capability_drift_fails_finalize() -> None:
    first = _center_asset()
    second = _center_asset(
        name="writer",
        skill_uuid="00000000-0000-4000-8000-000000000002",
        version="2.0.0",
    )
    builder = _builder(_legacy_state(), _Reader((first,), (second,)))
    captured = builder.capture(bot=_bot(), layout_observation=_observation())

    with pytest.raises(ServiceSkillsManifestError) as raised:
        builder.finalize(captured=captured, bot=_bot())

    assert raised.value.code is ServiceArtifactBuildErrorCode.CAPABILITY_CHANGED


@pytest.mark.unit
def test_legacy_manifest_requires_center_delivery_iff_center_skills_exist() -> None:
    valid = {
        "schema_version": 1,
        "engine": "openclaw",
        "active_layout": "legacy",
        "layout_contract_version": None,
        "center_skills": [
            {
                "runtime_name": "pdf",
                "skill_uuid": "00000000-0000-4000-8000-000000000001",
                "sc_version_number": "1.0.0",
                "mcp_dependencies": [],
            }
        ],
        "shared_corpora": [
            {
                "corpus": "center",
                "runtime_path": _CENTER_ROOT,
                "store_prefix": _CENTER_PREFIX,
                "layout_contract_version": "skills-pool-p3-v1",
                "permission": "read_only",
                "snapshot_policy": "exclude",
            }
        ],
    }
    validate_service_skills_manifest_for_release(valid, _bot())

    for invalid in (
        {**valid, "shared_corpora": None},
        {**valid, "center_skills": None},
        {
            **valid,
            "shared_corpora": [
                {
                    **valid["shared_corpora"][0],
                    "corpus": "repo",
                    "runtime_path": (
                        "/home/admin/.openclaw/workspace/skills-pool/skills-repo"
                    ),
                    "store_prefix": _REPO_PREFIX,
                }
            ],
        },
    ):
        with pytest.raises(ServiceSkillsManifestError):
            validate_service_skills_manifest_for_release(invalid, _bot())
