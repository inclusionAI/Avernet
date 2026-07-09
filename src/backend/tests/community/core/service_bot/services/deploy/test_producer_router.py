"""Unit tests for the DeployArtifactProducer selector (provider-keyed dispatch)."""
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


@pytest.mark.unit
def test_resolve_known_provider_returns_that_producer() -> None:
    arca, teclaw = _StubProducer("arca"), _StubProducer("teclaw")
    router = DeployArtifactProducerRouter(
        providers={"arca": arca, "teclaw": teclaw}, default_provider_key="arca"
    )
    assert router.resolve("teclaw") is teclaw
    assert router.resolve("arca") is arca


@pytest.mark.unit
def test_resolve_unknown_provider_falls_back_to_default() -> None:
    arca = _StubProducer("arca")
    router = DeployArtifactProducerRouter(
        providers={"arca": arca}, default_provider_key="arca"
    )
    assert router.resolve("does-not-exist") is arca


@pytest.mark.unit
def test_resolve_none_provider_falls_back_to_default() -> None:
    baas = _StubProducer("baas")
    router = DeployArtifactProducerRouter(
        providers={"baas": baas}, default_provider_key="baas"
    )
    assert router.resolve(None) is baas


@pytest.mark.unit
def test_default_key_must_be_present() -> None:
    with pytest.raises(ValueError, match="default_provider_key"):
        DeployArtifactProducerRouter(
            providers={"arca": _StubProducer("arca")}, default_provider_key="baas"
        )


@pytest.mark.unit
def test_arca_snapshot_producer_delegates_to_build() -> None:
    from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
        ArcaSnapshotProducer,
    )

    class _StubBuild:
        def build(self, bot, version=1):
            return {
                "success": True,
                "migration_path": "/home/admin/nfs/bot-data/3/mig",
                "build_target_path": "/data/3/target",
            }

    artifact = ArcaSnapshotProducer(_StubBuild()).produce_artifact({"bot_id": "b"}, 3)
    assert artifact.success is True
    assert artifact.ext == {
        "migration_path": "/home/admin/nfs/bot-data/3/mig",
        "build_target_path": "/data/3/target",
    }
