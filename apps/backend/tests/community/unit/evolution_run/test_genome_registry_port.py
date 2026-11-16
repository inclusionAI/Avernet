"""生产注册表适配（01-genome ↔ 06）：快照展开与候补修订记录。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.content_store import canonical_tree_bytes
from agentclaw.community.core.bot_genome.models import (
    ProvenanceKind, RevisionStatus, RunScope,
)
from agentclaw.community.core.evolution_run.genome_registry_port import RegistryGenomePort

BOT = SimpleNamespace(owner_id="owner_1", bot_id="bot_1", to_dict=lambda: {})


class FakeSpec:
    persona = [SimpleNamespace(type="SOUL.md", digest="d_soul")]
    skills = [
        SimpleNamespace(origin="local", name="refund", digest="d_skill"),
        SimpleNamespace(origin="center", name="governed", center_version="1"),
    ]
    resources = [SimpleNamespace(path="kb/faq.md", digest="d_faq"),
                 SimpleNamespace(path="kb/", digest="d_dir")]


class FakeRegistry:
    def __init__(self) -> None:
        self.content = {
            "d_soul": b"# soul",
            "d_skill": canonical_tree_bytes(
                [("SKILL.md", b"# skill"), ("examples/demo.md", b"demo")]
            ),
            "d_faq": b"faq",
        }
        self.committed: list[dict] = []

    def get_revision(self, bot, ref):
        return SimpleNamespace(revision=SimpleNamespace(id="sha256:" + "c" * 64),
                              spec=FakeSpec())

    def read_content(self, bot, digest):
        return self.content[digest]

    async def drift(self, bot):
        return None

    async def commit_patch(self, bot, patch, *, created_by, status, run=None):
        self.committed.append({"created_by": created_by, "status": status, "run": run})
        return SimpleNamespace(revision=SimpleNamespace(id="sha256:" + "d" * 64))


@pytest.mark.asyncio
async def test_snapshot_expands_persona_local_skills_and_files() -> None:
    port = RegistryGenomePort(FakeRegistry())
    revision = await port.resolve_revision(BOT, "active")
    files = await port.snapshot_files(revision, bot=BOT)
    assert files == {
        "persona/SOUL.md": b"# soul",
        "skills/refund/SKILL.md": b"# skill",
        "skills/refund/examples/demo.md": b"demo",
        "resources/kb/faq.md": b"faq",
    }


@pytest.mark.asyncio
async def test_commit_patch_records_a_candidate_with_a_run_scope() -> None:
    registry = FakeRegistry()
    port = RegistryGenomePort(registry)
    revision = await port.commit_patch(
        BOT, SimpleNamespace(), "run_1", "acme/x@1.0.0", ("persona",), "acme/x@1.0.0")
    assert revision == "sha256:" + "d" * 64
    (recorded,) = registry.committed
    assert recorded["status"] is RevisionStatus.CANDIDATE
    assert recorded["created_by"].kind is ProvenanceKind.STRATEGY_RUN
    assert isinstance(recorded["run"], RunScope) and recorded["run"].run_id == "run_1"


def test_revision_type_is_checked() -> None:
    import asyncio

    port = RegistryGenomePort(FakeRegistry())
    with pytest.raises(TypeError):
        asyncio.run(port.snapshot_files("sha256:whatever", bot=BOT))
