"""``compile_spec`` and ``GenomeCompiler``: a revision as the apply engine's typed desired state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_genome.compile import genome_state
from agentclaw.community.core.apply_engine.compiler import Compiled, CompileRefused
from agentclaw.community.core.apply_engine.constructs import Category, Section
from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByName,
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
    ContentRef,
)
from agentclaw.community.core.bot_genome.compile import GenomeCompiler, compile_spec
from agentclaw.community.core.bot_genome.errors import NotAppliable, RefConflict, RevisionNotFound
from agentclaw.community.core.bot_genome.spec_models import GenomeSpec, RepoSkill
from tests.community.core.apply_engine._fakes import make_context
from tests.community.core.bot_genome.example_blobs import EXAMPLES, BlobDir, load_revisions

CASES = sorted(p for p in (EXAMPLES / "manifest-v1").iterdir() if (p / "revision.json").exists())
DELIVERED = (
    Section.SCRIPT,
    Category.IDENTITY,
    Category.RESOURCES,
    Category.SKILLS,
    Category.MCP,
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
    assert state.identity.entries == tuple(DesiredIdentityFile(e.type, ContentRef(e.digest)) for e in spec.persona)
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
    assert all(isinstance(s, (LocalSkillPackage, CenterSkillByName, RepoSkillPath)) for s in sources.values())
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
    assert state.script == Declaration(()) and Section.SCRIPT in state.declared
    assert state != DesiredState.EMPTY


# -- the compiler the run service holds ----------------------------------------------------------


REVISION = "sha256:" + "ab" * 32
FROM = "sha256:" + "cd" * 32


class _Promotions:
    def __init__(self, row) -> None:
        self.row = row
        self.calls: list[dict] = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.row


class _Registry:
    def __init__(self, state: DesiredState, active: str | None = FROM) -> None:
        self.state = state
        self.active = active
        self.compiled: list[tuple] = []

    def list_refs(self, bot):
        return [] if self.active is None else [SimpleNamespace(name="active", revision=self.active)]

    def compile(self, bot, rev):
        self.compiled.append((bot, rev))
        return self.state


def _row(status: str = "applying", ref: str = "active", from_revision: str | None = FROM):
    return SimpleNamespace(revision_id=REVISION, status=status, ref=ref, from_revision_id=from_revision)


def _compiler(row, state: DesiredState | None = None) -> tuple[GenomeCompiler, _Registry, _Promotions]:
    registry = _Registry(state or compile_spec(_spec(CASES[0]), BlobDir()))
    promotions = _Promotions(row)
    compiler = GenomeCompiler(registry=registry, promotions=promotions, env=lambda: "dev")
    return compiler, registry, promotions


def test_the_genome_compiler_resolves_the_promotion_to_its_revision_and_compiles_it() -> None:
    compiler, registry, promotions = _compiler(_row())
    ctx = make_context(engine_type="openclaw", bot_type="arca", entity_id="staff_u", owner_id="u")
    compiler.check(ctx, genome_state("p" * 32))
    compiled = asyncio.run(compiler.compile(ctx, genome_state("p" * 32)))
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
    half = asyncio.run(compiler.compile(ctx, genome_state("p" * 32), only={Section.SCRIPT})).state
    assert half.declared == (Section.SCRIPT,)
    assert half.identity is None and half.resources is None and half.skills is None and half.mcp is None


def test_only_a_missing_promotion_is_refused_and_no_status_is_checked() -> None:
    """Compile is revision → desired state and nothing else: whatever the promotion's status, its revision
    compiles (a re-run after a crash compiles the same state again). Only a promotion that does not exist
    is refused — as the genome's own error by ``check``, as ``CompileRefused`` in the worker."""
    ctx = make_context(engine_type="openclaw", bot_type="arca")
    compiler, _, _ = _compiler(None)
    with pytest.raises(RevisionNotFound):
        compiler.check(ctx, genome_state("p" * 32))
    with pytest.raises(CompileRefused, match="is not there to deliver") as refused:
        asyncio.run(compiler.compile(ctx, genome_state("p" * 32)))
    assert isinstance(refused.value.domain_error, RevisionNotFound)
    assert refused.value.__cause__ is refused.value.domain_error, "chained for the worker's log too"
    for status in ("pending", "applying", "applied", "applied_partial", "apply_failed"):
        compiler, registry, _ = _compiler(_row(status))
        compiler.check(ctx, genome_state("p" * 32))
        assert asyncio.run(compiler.compile(ctx, genome_state("p" * 32))).state is registry.state


def test_check_refuses_a_promotion_whose_from_revision_is_no_longer_active() -> None:
    """``check`` runs under the bot's apply lock before the apply is enqueued: a promotion that another
    promotion's settle overtook is refused there, before its revision reaches the bot. ``compile`` maps
    the revision whatever ``active`` says."""
    ctx = make_context(engine_type="openclaw", bot_type="arca")
    compiler, registry, _ = _compiler(_row())
    registry.active = "sha256:" + "ef" * 32
    with pytest.raises(RefConflict) as moved:
        compiler.check(ctx, genome_state("p" * 32))
    assert (moved.value.expected, moved.value.current) == (FROM, registry.active)
    assert asyncio.run(compiler.compile(ctx, genome_state("p" * 32))).state is registry.state
    first, registry, _ = _compiler(_row(from_revision=None))
    registry.active = None
    first.check(ctx, genome_state("p" * 32))  # a bot's first promotion: no active either side
    registry.active = FROM
    with pytest.raises(RefConflict):
        first.check(ctx, genome_state("p" * 32))


def test_a_genome_reference_without_a_promotion_id_is_refused_before_the_repository_is_asked() -> None:
    """``genome_state`` never builds one; a hand-built or mis-decoded ``StateRef("genome")`` is refused as
    ``RevisionNotFound`` up front rather than queried as ``promotion_id=None``."""
    from agentclaw.community.core.apply_engine.compiler import StateRef

    ctx = make_context(engine_type="openclaw", bot_type="arca")
    compiler, _, promotions = _compiler(_row())
    with pytest.raises(RevisionNotFound, match="reference missing"):
        compiler.check(ctx, StateRef("genome"))
    with pytest.raises(CompileRefused, match="is not there to deliver") as refused:
        asyncio.run(compiler.compile(ctx, StateRef("genome", "")))
    assert isinstance(refused.value.domain_error, RevisionNotFound)
    assert promotions.calls == []


def test_a_revision_the_state_model_refuses_is_not_appliable_rather_than_a_crash() -> None:
    """A revision recorded before the resources-root rule names ``docs/``; the
    model refuses the path, and the compiler says so as a reason. A script
    blob that is not UTF-8 is the same kind of refusal."""
    spec = GenomeSpec.from_json(
        {
            "persona": [],
            "skills": [],
            "memory": None,
            "resources": [{"path": "SOUL.md", "digest": "sha256:" + "ab" * 32}],
            "tools": {"mcp": []},
            "engine_config": {},
            "script": None,
        }
    )
    with pytest.raises(NotAppliable) as caught:
        compile_spec(spec, BlobDir())
    assert "'SOUL.md' is an identity file" in caught.value.reasons[0].message
    assert caught.value.reasons[0].location == "spec"

    class _StaleRegistry:
        def compile(self, bot, rev):
            return compile_spec(spec, BlobDir())

    compiler = GenomeCompiler(registry=_StaleRegistry(), promotions=_Promotions(_row()), env=lambda: "dev")
    with pytest.raises(CompileRefused, match="is an identity file") as refused:
        asyncio.run(compiler.compile(make_context(engine_type="openclaw", bot_type="arca"), genome_state("p" * 32)))
    assert isinstance(refused.value.domain_error, NotAppliable)
    blobs = BlobDir()
    binary = blobs.put(b"\xff\xfe not text")
    scripted = GenomeSpec.from_json({**spec.to_json(), "resources": [], "script": {"digest": binary}})
    with pytest.raises(NotAppliable):
        compile_spec(scripted, blobs)
