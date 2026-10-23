"""``compile_spec`` and ``GenomeCompiler``: a revision as the apply engine's typed desired state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentclaw.community.core.apply_engine.compiler import Compiled, GenomeStateRef
from agentclaw.community.core.apply_engine.constructs import ManifestCategory, ManifestSection
from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillVersion,
    Declaration,
    DesiredIdentityFile,
    DesiredMcpServer,
    DesiredResourceFile,
    DesiredResourceTree,
    DesiredScript,
    DesiredState,
    LocalSkillPackage,
    McpOverride,
    RepoSkillPath,
    StoredContent,
)
from agentclaw.community.core.bot_genome.compile import GenomeCompiler, compile_spec
from agentclaw.community.core.bot_genome.errors import NotAppliable, RevisionNotFound
from agentclaw.community.core.bot_genome.spec_models import GenomeSpec, RepoSkill
from tests.community.core.apply_engine._fakes import make_context
from tests.community.core.bot_genome.example_blobs import EXAMPLES, BlobDir, load_revisions

CASES = sorted(p for p in (EXAMPLES / "manifest-v1").iterdir() if (p / "revision.json").exists())
DELIVERED = (
    ManifestSection.SCRIPT,
    ManifestCategory.IDENTITY,
    ManifestCategory.RESOURCES,
    ManifestCategory.SKILLS,
    ManifestCategory.MCP,
)


def _spec(case: Path) -> GenomeSpec:
    import json

    return GenomeSpec.from_json(json.loads((case / "revision.json").read_text(encoding="utf-8"))["spec"])


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_every_example_revision_compiles_to_a_total_state_that_mirrors_its_spec(case: Path) -> None:
    """A revision is total (README §4.3): every category it covers is declared,
    empty or not, and the two it never covers stay undeclared. Each entry is
    the spec's own digest, typed for the materialiser that delivers it."""
    spec = _spec(case)
    state = compile_spec(spec, BlobDir())
    assert state.declared == DELIVERED
    assert state.cli_tools is None and state.engine_config is None
    assert state.identity.entries == tuple(DesiredIdentityFile(e.type, StoredContent(e.digest)) for e in spec.persona)
    for entry, desired in zip(spec.resources, state.resources.entries, strict=True):
        expected = DesiredResourceTree if entry.path.endswith("/") else DesiredResourceFile
        assert isinstance(desired, expected) and desired.path == entry.path
        assert (desired.tree if isinstance(desired, DesiredResourceTree) else desired.content).digest == entry.digest
    assert [s.name for s in state.skills.entries] == [e.name for e in spec.skills]
    assert [m.server_code for m in state.mcp.entries] == [e.server_code for e in spec.tools.mcp]
    if spec.script is None:
        assert state.script == Declaration(())
    else:
        assert state.script.entries == (DesiredScript(BlobDir().read(spec.script.digest).decode("utf-8")),)
    # Every declared entry can be built into a state the engine accepts: no note, nothing failed.
    assert all(not state.declaration(construct).failures for construct in state.declared)


def test_skill_origins_and_mcp_overrides_take_their_typed_source() -> None:
    spec = next(
        GenomeSpec.from_json(r["spec"])
        for r in load_revisions().values()
        if any(s["origin"] == "repo" for s in r["spec"]["skills"])
    )
    assert any(isinstance(s, RepoSkill) for s in spec.skills)
    state = compile_spec(spec, BlobDir())
    sources = {s.name: s.source for s in state.skills.entries}
    assert sources["weather"] == RepoSkillPath("team/weather")
    assert all(isinstance(s, (LocalSkillPackage, CenterSkillVersion, RepoSkillPath)) for s in sources.values())
    for entry in spec.tools.mcp:
        desired = next(m for m in state.mcp.entries if m.server_code == entry.server_code)
        expected = McpOverride.from_config(entry.config.to_json()) if entry.config is not None else None
        assert desired == DesiredMcpServer(entry.server_code, expected)


def test_script_null_declares_no_script_rather_than_leaving_it_alone() -> None:
    """The one place a revision differs from a Manifest that omits ``script``:
    a revision is total, so ``script: null`` *declares* none, and the engine
    removes a script the bot has (the Manifest's ``script: null`` now reads the
    same way; an absent ``script`` is the only "leave it" spelling)."""
    empty = GenomeSpec.from_json(
        {"persona": [], "skills": [], "memory": None, "resources": [], "tools": {"mcp": []}, "engine_config": {}, "script": None}
    )
    state = compile_spec(empty, BlobDir())
    assert state.script == Declaration(()) and ManifestSection.SCRIPT in state.declared
    assert state != DesiredState.EMPTY


# -- the compiler the run service holds ----------------------------------------------------------


class _Promotions:
    def __init__(self, row) -> None:
        self.row = row
        self.calls: list[dict] = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.row


class _Registry:
    def __init__(self, state: DesiredState) -> None:
        self.state = state
        self.compiled: list[tuple] = []

    def compile(self, bot, rev):
        self.compiled.append((bot, rev))
        return self.state


def _row(status: str = "applying"):
    return SimpleNamespace(revision_id="sha256:" + "ab" * 32, status=status)


def _compiler(row, state: DesiredState | None = None) -> tuple[GenomeCompiler, _Registry, _Promotions]:
    registry = _Registry(state or compile_spec(_spec(CASES[0]), BlobDir()))
    promotions = _Promotions(row)
    return GenomeCompiler(registry=registry, promotions=promotions, env=lambda: "dev"), registry, promotions


def test_the_genome_compiler_resolves_the_promotion_to_its_revision_and_compiles_it() -> None:
    compiler, registry, promotions = _compiler(_row())
    ctx = make_context(engine_type="openclaw", bot_type="arca", entity_id="staff_u", owner_id="u")
    compiler.check(ctx, GenomeStateRef("p" * 32))
    compiled = asyncio.run(compiler.compile(ctx, GenomeStateRef("p" * 32)))
    assert isinstance(compiled, Compiled) and compiled.state is registry.state and compiled.sources == ()
    assert promotions.calls[-1] == {"env": "dev", "entity_id": "staff_u", "bot_id": "b_1", "promotion_id": "p" * 32}
    bot, rev = registry.compiled[-1]
    assert (bot.entity_id, bot.bot_id, bot.owner_id, bot.engine_type, bot.bot_type) == (
        "staff_u",
        "b_1",
        "u",
        "openclaw",
        "arca",
    )
    assert rev == _row().revision_id


def test_only_narrows_the_genome_state_to_a_phases_constructs() -> None:
    compiler, _registry, _ = _compiler(_row())
    ctx = make_context(engine_type="openclaw", bot_type="arca")
    half = asyncio.run(compiler.compile(ctx, GenomeStateRef("p" * 32), only={ManifestSection.SCRIPT})).state
    assert half.declared == (ManifestSection.SCRIPT,)
    assert half.identity is None and half.resources is None and half.skills is None and half.mcp is None


def test_a_missing_or_finished_promotion_is_refused() -> None:
    ctx = make_context(engine_type="openclaw", bot_type="arca")
    compiler, _, _ = _compiler(None)
    with pytest.raises(RevisionNotFound):
        compiler.check(ctx, GenomeStateRef("p" * 32))
    for status in ("applied", "apply_failed"):
        compiler, _, _ = _compiler(_row(status))
        with pytest.raises(NotAppliable) as caught:
            asyncio.run(compiler.compile(ctx, GenomeStateRef("p" * 32)))
        assert status in caught.value.reasons[0].message
