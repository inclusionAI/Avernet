"""Behavior equivalence — arca/baas paths unchanged; teclaw is purely additive.

The spec's "ARCA notify unchanged after the edit-path refactor" goal is, in the
landed design, a *no-regression* guarantee: Task 14's change→compose→notify
refactor was **abandoned** (2026-06-07) in favor of "teclaw = transport-layer
plugin variant; arca/baas edit/activation paths get zero changes". So the
invariant to guard is that the teclaw work only ever *adds a branch* — it never
alters how arca/baas are routed or delivered.

This suite pins the one seam the publish **build** path touches — the producer
router's provider→producer map. The runtime-edit (notify) seams are guarded
elsewhere and intentionally not duplicated here:

* arca/baas MCP edit keeps the per-MCP push (teclaw never consulted) —
  ``tests/core/mcp/services/test_sync_service.py::test_non_teclaw_bot_uses_per_mcp_path``.
* arca/baas device-fs routing unchanged (teclaw is an added branch) —
  ``tests/di/modules/test_dispatcher_three_state_routing.py``.
* ARCA build output byte-equivalence (migration_path/build_target_path) —
  ``test_producer_router.py::test_arca_snapshot_producer_delegates_to_build``.
"""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
    DeployArtifactProducerRouter,
)


class _StubProducer(DeployArtifactProducer):
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def produce_artifact(self, bot: dict[str, Any], version: int) -> DeployArtifact:
        return DeployArtifact(success=True, ext={"by": self.tag})


def _router_as_wired() -> tuple[DeployArtifactProducerRouter, _StubProducer, _StubProducer]:
    """Mirror ``ServiceBotModule.deploy_artifact_producer_router`` wiring exactly:
    arca AND baas → the same ARCA snapshot producer; teclaw → the external producer;
    default = baas."""
    arca = _StubProducer("arca-snapshot")
    external = _StubProducer("external-compose")
    router = DeployArtifactProducerRouter(
        providers={"arca": arca, "baas": arca, "teclaw": external},
        default_provider_key="baas",
    )
    return router, arca, external


def test_arca_and_baas_resolve_to_the_same_unchanged_producer() -> None:
    """baas reuses ARCA's build producer — both get byte-identical build behavior;
    teclaw is the ONLY provider that diverges."""
    router, arca, external = _router_as_wired()
    assert router.resolve("arca") is arca
    assert router.resolve("baas") is arca       # baas == arca build (unchanged)
    assert router.resolve("arca") is router.resolve("baas")
    assert router.resolve("teclaw") is external
    assert router.resolve("teclaw") is not router.resolve("arca")


def test_unknown_and_missing_provider_fall_back_to_arca_behavior() -> None:
    """An unknown / missing provider falls back to default=baas → ARCA build
    behavior, so legacy bots with no explicit provider are unaffected."""
    router, arca, _ = _router_as_wired()
    assert router.resolve(None) is arca
    assert router.resolve("nonexistent") is arca
