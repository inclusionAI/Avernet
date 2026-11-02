"""``capture_spec`` over a fake reader: the example revisions come back exactly; README §7 normalisation; refusals."""

from __future__ import annotations

import json

import pytest

from agentclaw.community.core.content_store import canonical_tree_bytes, decode_tree_bytes
from agentclaw.community.core.bot_genome.canonical import sha256_digest
from agentclaw.community.core.bot_genome.capture import (
    CapturedFile,
    CapturedMcpServer,
    CapturedPersonaFile,
    CapturedResource,
    CapturedSkill,
    SkillOrigin,
    capture_spec,
)
from agentclaw.community.core.bot_genome.capture.capture import agents_md_text, normalise_resource_path
from agentclaw.community.core.bot_genome.errors import CaptureRefused
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.bot_genome.spec_models import GenomeSpec, LocalSkill, RepoSkill
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from tests.community.core.bot_genome.example_blobs import EXAMPLES, BlobDir, load_revisions, spec_of

REVISIONS = load_revisions()
BOT = BotRef(
    entity_id="staff_user_owner_5", bot_id="bot_123", owner_id="user_owner_5", engine_type="openclaw", bot_type="arca"
)
VALIDATOR = SkillPackageValidator(SkillParser())


class FakeReader:
    """A reader seeded from a spec's blobs, or from explicit values."""

    def __init__(self, *, persona=None, skills=(), resources=(), mcp=(), script=None, refuse=None) -> None:
        self._persona = persona or {}
        self._skills = tuple(skills)
        self._resources = tuple(resources)
        self._mcp = tuple(mcp)
        self._script = script
        self._refuse = refuse

    @classmethod
    def from_spec(cls, spec: GenomeSpec) -> "FakeReader":
        blobs = BlobDir()
        persona = {e.type: blobs.read(e.digest).decode("utf-8") for e in spec.persona}
        skills = []
        for e in spec.skills:
            if isinstance(e, LocalSkill):
                skills.append(
                    CapturedSkill(
                        e.name,
                        SkillOrigin.LOCAL,
                        files=tuple(CapturedFile(*f) for f in decode_tree_bytes(blobs.read(e.digest))),
                    )
                )
            elif isinstance(e, RepoSkill):
                skills.append(CapturedSkill(e.name, SkillOrigin.REPO, repo_path=e.repo_path))
            else:
                skills.append(CapturedSkill(e.name, SkillOrigin.CENTER))
        resources = []
        for e in spec.resources:
            data = blobs.read(e.digest)
            if e.path.endswith("/"):
                resources.append(CapturedResource(e.path, tuple(CapturedFile(*f) for f in decode_tree_bytes(data))))
            else:
                resources.append(CapturedResource(e.path, (CapturedFile(e.path.rsplit("/", 1)[-1], data),)))
        script = blobs.read(spec.script.digest).decode("utf-8") if spec.script else None
        return cls(
            persona=persona,
            skills=skills,
            resources=resources,
            mcp=[e.to_json() for e in spec.tools.mcp],
            script=script,
        )

    async def refusal(self, bot: BotRef) -> str | None:
        return self._refuse

    async def persona_files(self, bot: BotRef) -> tuple[CapturedPersonaFile, ...]:
        return tuple(CapturedPersonaFile(file_type, text) for file_type, text in self._persona.items())

    async def skills(self, bot: BotRef) -> tuple[CapturedSkill, ...]:
        return self._skills

    async def resources(self, bot: BotRef) -> tuple[CapturedResource, ...]:
        return self._resources

    async def mcp_servers(self, bot: BotRef) -> tuple[CapturedMcpServer, ...]:
        copied = (json.loads(json.dumps(e)) for e in self._mcp)
        return tuple(CapturedMcpServer(e["server_code"], e.get("config")) for e in copied)

    async def script_body(self, bot: BotRef) -> str | None:
        return self._script


@pytest.mark.asyncio
async def _capture(reader: FakeReader):
    return await capture_spec(reader, BOT, validator=VALIDATOR)


@pytest.mark.parametrize(
    "record_id", sorted(r for r, rec in REVISIONS.items() if rec["spec"]["memory"] is None), ids=lambda i: i[:15]
)
@pytest.mark.asyncio
async def test_the_example_revisions_capture_back_exactly(record_id: str) -> None:
    spec = spec_of(record_id)
    captured = await _capture(FakeReader.from_spec(spec))
    assert captured.spec == spec and captured.spec.to_json() == REVISIONS[record_id]["spec"]
    written = dict(captured.writes)
    for digest, data in written.items():
        assert sha256_digest(data) == digest
    referenced = {e.digest for e in spec.persona + spec.resources} | {
        e.digest for e in spec.skills if isinstance(e, LocalSkill)
    }
    if spec.script:
        referenced.add(spec.script.digest)
    assert set(written) == referenced, "every byte the spec names was produced by the capture"


@pytest.mark.asyncio
async def test_memory_is_null_and_engine_config_empty_for_a_fresh_bot() -> None:
    captured = await _capture(FakeReader())
    assert captured.spec.to_json() == {
        "persona": [],
        "skills": [],
        "memory": None,
        "resources": [],
        "tools": {"mcp": []},
        "engine_config": {},
        "script": None,
    }
    assert captured.writes == ()


# -- README §7 normalisation -----------------------------------------------------------------------


def test_agents_md_loses_the_reference_block_and_trailing_whitespace() -> None:
    owner = "# Agents\nBe kind.  \n\n"
    block = "<!-- REFERENCE_FILES_SECTION_START -->\nRead RULES.md\n<!-- REFERENCE_FILES_SECTION_END -->\n"
    assert agents_md_text(owner.rstrip() + "\n\n" + block) == "# Agents\nBe kind.\n"
    assert agents_md_text(owner) == "# Agents\nBe kind.\n"
    assert agents_md_text("") == "\n"


@pytest.mark.asyncio
async def test_captured_agents_md_is_normalised_and_other_files_are_not() -> None:
    block = "<!-- REFERENCE_FILES_SECTION_START -->\nx\n<!-- REFERENCE_FILES_SECTION_END -->\n"
    reader = FakeReader(
        persona={
            "AGENTS.md": "# A\n\n" + block,
            "SOUL.md": "# Soul  \n\n",
            "MEMORY.md": "engine memory",
            "IDENTITY.md": "x",
        }
    )
    captured = await _capture(reader)
    written = dict(captured.writes)
    by_type = {e.type: written[e.digest] for e in captured.spec.persona}
    assert by_type == {"AGENTS.md": b"# A\n", "SOUL.md": b"# Soul  \n\n"}


@pytest.mark.asyncio
async def test_local_skills_are_what_the_validator_keeps_and_repo_skills_keep_their_path() -> None:
    skill_md = b"---\nname: tidy\ndescription: Tidy things.\n---\nBody.\n"
    reader = FakeReader(
        skills=[
            CapturedSkill(
                "tidy",
                SkillOrigin.LOCAL,
                files=(
                    CapturedFile("tidy/SKILL.md", skill_md),
                    CapturedFile("tidy/.DS_Store", b"x"),
                    CapturedFile("tidy/a.py", b"print()\n"),
                ),
            ),
            CapturedSkill("weather", SkillOrigin.REPO, repo_path="team/weather"),
            CapturedSkill("refund", SkillOrigin.CENTER),
        ]
    )
    captured = await _capture(reader)
    local = captured.spec.skills[1]
    assert local.name == "tidy" and [m[0] for m in decode_tree_bytes(dict(captured.writes)[local.digest])] == [
        "SKILL.md",
        "a.py",
    ]
    assert captured.spec.skills[0].to_json() == {"name": "refund", "origin": "center"}
    assert captured.spec.skills[2].to_json() == {"name": "weather", "origin": "repo", "repo_path": "team/weather"}


@pytest.mark.parametrize(
    ("given", "expected"),
    [("./faq.csv", "faq.csv"), ("data//kb/", "data/kb/"), ("a/./b.md", "a/b.md"), ("data/", "data/")],
)
def test_resource_paths_are_normalised(given: str, expected: str) -> None:
    assert normalise_resource_path(given) == expected


@pytest.mark.parametrize("given", ["./", ".", "", "a/../b.md", "how to.md", "data/图.md", "~/x.md"])
def test_resource_paths_the_manifest_rule_refuses(given: str) -> None:
    with pytest.raises(ValueError):
        normalise_resource_path(given)


@pytest.mark.asyncio
async def test_resources_file_and_directory() -> None:
    reader = FakeReader(
        resources=[
            CapturedResource("docs/", (CapturedFile("b.md", b"b"), CapturedFile("a.md", b"a"))),
            CapturedResource("./faq.csv", (CapturedFile("faq.csv", b"q,a\n"),)),
        ]
    )
    captured = await _capture(reader)
    written = dict(captured.writes)
    assert [e.path for e in captured.spec.resources] == ["docs/", "faq.csv"]
    assert written[captured.spec.resources[0].digest] == canonical_tree_bytes([("a.md", b"a"), ("b.md", b"b")])
    assert written[captured.spec.resources[1].digest] == b"q,a\n"


@pytest.mark.asyncio
async def test_mcp_entries_carry_only_an_explicit_override_and_script_placeholders_are_the_readers_job() -> None:
    reader = FakeReader(
        mcp=[{"server_code": "b", "config": {}}, {"server_code": "a", "config": {"url": "https://x.example.com/mcp"}}],
        script="echo hi\n",
    )
    captured = await _capture(reader)
    assert [e.to_json() for e in captured.spec.tools.mcp] == [
        {"server_code": "a", "config": {"url": "https://x.example.com/mcp"}},
        {"server_code": "b"},
    ]
    assert dict(captured.writes)[captured.spec.script.digest] == b"echo hi\n"
    assert (await _capture(FakeReader(script=""))).spec.script is None


# -- refusals --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_resource_the_manifest_rule_rejects_is_refused_by_name() -> None:
    reader = FakeReader(
        resources=[
            CapturedResource("how to.md", (CapturedFile("how to.md", b"x"),)),
            CapturedResource("ok.md", (CapturedFile("ok.md", b"x"),)),
            CapturedResource("./ok.md", (CapturedFile("ok.md", b"y"),)),
        ]
    )
    with pytest.raises(CaptureRefused) as caught:
        await _capture(reader)
    assert [(r.code, r.location) for r in caught.value.reasons] == [
        ("resource_path", "resources/how to.md"),
        ("resource_path", "resources/./ok.md"),
    ]


@pytest.mark.asyncio
async def test_a_platform_managed_bot_is_refused() -> None:
    with pytest.raises(CaptureRefused) as caught:
        await _capture(FakeReader(refuse="a platform-managed teclaw bot has no device to read"))
    assert caught.value.reasons[0].code == "not_capturable" and "teclaw" in caught.value.reasons[0].message


@pytest.mark.asyncio
async def test_a_persona_type_outside_the_contract_is_refused_as_schema() -> None:
    with pytest.raises(CaptureRefused) as caught:
        await _capture(FakeReader(persona={"LESSONS.md": "x"}))
    assert [(r.code, r.location) for r in caught.value.reasons] == [("schema", "persona/LESSONS.md.type")]


@pytest.mark.asyncio
async def test_an_invalid_local_skill_is_refused_and_a_center_skill_needs_only_its_name() -> None:
    reader = FakeReader(
        skills=[
            CapturedSkill("bad", SkillOrigin.LOCAL, files=(CapturedFile("README.md", b"no skill"),)),
            CapturedSkill("refund", SkillOrigin.CENTER),
        ]
    )
    with pytest.raises(CaptureRefused) as caught:
        await _capture(reader)
    assert [(r.code, r.location) for r in caught.value.reasons] == [("skill_package", "skills/bad")]


def test_example_blob_directory_is_the_fixture_root() -> None:
    assert (EXAMPLES / "blobs").is_dir()
