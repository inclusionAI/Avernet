"""Fresh runtime-layout observation normalization for Service Artifact builds."""

from __future__ import annotations

import pytest

from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
    ServiceArtifactLayoutObservation,
)
from agentclaw.community.core.skill_center.runtime_layout_probe_service_protocol import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)


def _probe(*, resolved_layout=None, **evidence) -> RuntimeLayoutProbeResult:
    return RuntimeLayoutProbeResult(
        status=RuntimeLayoutProbeStatus.READY,
        engine="openclaw",
        layout_contract_version="skills-pool-p3-v1",
        preparation_id="preparation-1",
        evidence={"resolved_layout": resolved_layout, **evidence},
    )


def _resolved_layout() -> dict[str, str]:
    return {
        "engine": "openclaw",
        "layout_contract_version": "skills-pool-p3-v1",
        "active_root": "/home/admin/.openclaw/workspace/skills",
        "local_root": ("/home/admin/.openclaw/workspace/skills-pool/skills-local"),
        "repo_root": "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
        "pool_center": ("/home/admin/.openclaw/workspace/skills-pool/skill-center"),
    }


@pytest.mark.unit
def test_ready_probe_becomes_typed_observation_without_transport_extras() -> None:
    observation = ServiceArtifactLayoutObservation.from_probe(
        _probe(
            resolved_layout=_resolved_layout(),
            supported_mapping_contract_versions=[
                "skills-pool-mapping-v2",
                "skills-pool-mapping-v3",
            ],
            center_mount={
                "status": "NOT_READY",
                "reason": "mount_pending",
                "restart_required": True,
            },
            checks={"sensitive_transport_detail": True},
        ),
        expected_engine="openclaw",
    )

    assert observation.status is RuntimeLayoutProbeStatus.READY
    assert observation.resolved_layout is not None
    assert observation.resolved_layout.center_root.endswith("/skill-center")
    assert observation.center_mount_status == "NOT_READY"
    assert observation.supported_mapping_contract_versions == frozenset(
        {"skills-pool-mapping-v2", "skills-pool-mapping-v3"}
    )
    assert not hasattr(observation, "checks")


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        {"repo_root": "relative/skills-repo"},
        {"repo_root": "//home/admin/workspace/skills-pool/skills-repo"},
        {"pool_center": "/home/admin/.openclaw/workspace/other"},
        {
            "active_root": (
                "/home/admin/.openclaw/workspace/skills-pool/skill-center/active"
            )
        },
    ],
)
def test_noncanonical_ready_paths_become_invalid(mutation) -> None:
    resolved = {**_resolved_layout(), **mutation}

    observation = ServiceArtifactLayoutObservation.from_probe(
        _probe(
            resolved_layout=resolved,
            supported_mapping_contract_versions=["skills-pool-mapping-v3"],
        ),
        expected_engine="openclaw",
    )

    assert observation.status is RuntimeLayoutProbeStatus.INVALID
    assert observation.resolved_layout is None
    assert observation.reason == "invalid_runtime_layout_probe_evidence"


@pytest.mark.unit
def test_nonready_probe_preserves_safe_status_and_reason() -> None:
    probe = RuntimeLayoutProbeResult(
        status=RuntimeLayoutProbeStatus.TRANSIENT_ERROR,
        engine="openclaw",
        layout_contract_version="skills-pool-p3-v1",
        preparation_id=None,
        evidence={
            "reason": "runtime_probe_failed",
            "error": "private endpoint detail",
        },
    )

    observation = ServiceArtifactLayoutObservation.from_probe(
        probe, expected_engine="openclaw"
    )

    assert observation.status is RuntimeLayoutProbeStatus.TRANSIENT_ERROR
    assert observation.reason == "runtime_probe_failed"
    assert observation.resolved_layout is None
    assert not hasattr(observation, "error")


@pytest.mark.unit
def test_build_request_copies_bot_mapping() -> None:
    bot = {"bot_id": "b1"}
    request = ArtifactBuildRequest.create(bot=bot, version=2)

    bot["bot_id"] = "mutated"

    assert request.bot["bot_id"] == "b1"
    with pytest.raises(TypeError):
        request.bot["bot_id"] = "forbidden"  # type: ignore[index]
