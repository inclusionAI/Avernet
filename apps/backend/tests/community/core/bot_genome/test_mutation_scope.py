"""Registry independently refuses forged scoped updates before filing bytes or rows."""

import pytest
from agentclaw.community.core.bot_genome.errors import PatchRejected
from agentclaw.community.core.bot_genome.models import RevisionStatus, RunScope
from agentclaw.community.core.bot_genome.patch_models import GenomePatch
from avernet_genome.mutation_scope import FrozenMutationScope

from tests.community.core.bot_genome.example_blobs import SUPPORT_BOT, spec_of
from tests.community.core.bot_genome.service_fixtures import BOT, OWNER, RUN, World
from tests.community.core.bot_genome.test_capture import FakeReader


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op,targets,pins",
    [
        (
            {"op": "skill.add", "name": "quality-check", "files": {"SKILL.md": "new"}},
            ("skills/quality-check",),
            (),
        ),
        (
            {"op": "skill.remove", "name": "quality-check"},
            ("skills/quality-check",),
            (),
        ),
        ({"op": "persona.remove", "type": "SOUL.md"}, ("persona/SOUL.md",), ()),
        (
            {
                "op": "skill.update",
                "name": "forged",
                "files": {"SKILL.md": "overwrite"},
            },
            ("skills/forged",),
            (),
        ),
        (
            {
                "op": "skill.update",
                "name": "quality-check",
                "files": {"SKILL.md": "ok", "scripts/check.py": "bad"},
            },
            ("skills/quality-check/SKILL.md",),
            (),
        ),
        (
            {
                "op": "skill.update",
                "name": "quality-check",
                "files": {"SKILL.md": "ok", "scripts/check.py": "bad"},
            },
            ("skills/quality-check",),
            ("skills/quality-check/scripts",),
        ),
        (
            {
                "op": "skill.update",
                "name": "refund-policy",
                "files": {"SKILL.md": "overwrite center"},
            },
            ("skills/refund-policy",),
            (),
        ),
    ],
)
async def test_rejected_scope_is_not_a_content_or_revision_write(
    tmp_path, op, targets, pins
):
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    captured = await world.registry.capture(BOT, created_by=OWNER)
    before = world.registry.list_revisions(BOT).total
    files = {
        str(p): p.read_bytes() for p in (tmp_path / "store").rglob("*") if p.is_file()
    }
    patch = GenomePatch.from_json(
        {
            "patch_schema": 1,
            "base": captured.revision.revision.id,
            "ops": [op],
            "rationale": "test independent authorization",
            "evidence": ["episode:e1"],
        }
    )
    scope = FrozenMutationScope(targets=targets, pins=pins)
    run = RunScope(
        RUN.run_id,
        RUN.actor,
        ("persona", "skills"),
        step=RUN.step,
        mutation_scope=scope,
    )
    with pytest.raises(PatchRejected):
        await world.registry.commit_patch(
            BOT, patch, created_by=RUN, status=RevisionStatus.CANDIDATE, run=run
        )
    assert world.registry.list_revisions(BOT).total == before
    assert world.registry.list_refs(BOT) == []
    assert {
        str(p): p.read_bytes() for p in (tmp_path / "store").rglob("*") if p.is_file()
    } == files
    assert not world.applier.starts
