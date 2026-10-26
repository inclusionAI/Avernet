"""``PlatformBotStateReader`` over fakes of each service it reads through, one test per category."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.bot_genome.capture import CapturedMcpServer, CapturedPersonaFile
from agentclaw.community.core.bot_genome.capture.bot_state import PlatformBotStateReader
from agentclaw.community.core.bot_genome.errors import CaptureRefused
from agentclaw.community.core.bot_genome.models import BotRef

#: ``entity_id`` + ``bot_id`` identify the bot; ``owner_id`` is the owner's user id: the reader must not mix them up.
BOT = BotRef(entity_id="staff_u1", bot_id="bot_1", owner_id="u1", engine_type="openclaw", bot_type="arca")


class FakeIdentity:
    def __init__(self, files: dict[str, str | None]) -> None:
        self.files, self.calls = files, []

    async def list_bot_files(self, entity_type, entity_id, bot_id, owner_id, *, engine_type=None, stage="draft"):
        self.calls.append(("list", entity_type, entity_id, bot_id, owner_id, engine_type))
        return [(name, text is not None) for name, text in self.files.items()]

    async def read_identity_file(
        self, entity_type, entity_id, bot_id, file_type, owner_id, *, engine_type=None, stage="draft"
    ):
        self.calls.append(("read", file_type))
        return self.files[file_type]


class FakeStorage:
    def __init__(self, files):
        self.files = files

    async def read_package_files(self):
        return list(self.files)


class FakeSkillFactory:
    def __init__(self, packages: dict[str, list[tuple[str, bytes]]]) -> None:
        self.packages, self.calls = packages, []

    def local_skill_package_storage_for_locator(self, **kw):
        self.calls.append(kw)
        return "/dev/skills-local/" + kw["skill_name"], FakeStorage(self.packages[kw["locator"]])


class FakeCapabilities:
    def __init__(self, assets):
        self.assets = assets

    def active_skill_assets(self, *, bot_id, owner_id, bot=None):
        self.asked_for = owner_id
        return tuple(self.assets)


class FakeResourceServices:
    def __init__(self, rows):
        self.rows = rows

    def create(self, *, bot_id):
        rows = self.rows
        return SimpleNamespace(list_resources=lambda **kw: list(rows))


class FakeResourceFiles:
    def __init__(self, files: dict[str, bytes | None]):
        self.files, self.calls = files, []

    async def read_file(self, **kw):
        self.calls.append(kw)
        return self.files.get(kw["path"])


class FakeActivation:
    def __init__(self, installed, defaults):
        self.installed, self.defaults = installed, defaults

    def list_installed_mcps(self, *, bot_id, owner_id, actor_id):
        self.asked_for = (owner_id, actor_id)
        return set(self.installed)

    def platform_default_mcp_codes(self, *, bot_id, owner_id, actor_id):
        return frozenset(self.defaults)


class FakeMcpConfig:
    def __init__(self, overrides):
        self.overrides = overrides

    def get_bot_override(self, *, bot_id, owner_id, server_code):
        return self.overrides.get(server_code)


class FakeScripts:
    def __init__(self, body):
        self.body = body

    def get_body(self, *, entity_id, bot_id):
        return self.body


def _reader(**overrides: Any) -> PlatformBotStateReader:
    deps = dict(
        identity=FakeIdentity({}),
        capabilities=FakeCapabilities(()),
        skill_factory=FakeSkillFactory({}),
        resource_services=FakeResourceServices([]),
        resource_files=FakeResourceFiles({}),
        activation=FakeActivation(set(), set()),
        mcp_config=FakeMcpConfig({}),
        scripts=FakeScripts(""),
        is_teclaw=lambda engine: engine == "teclaw",
        teclaw_platform_managed=False,
    )
    deps.update(overrides)
    return PlatformBotStateReader(**deps)


@pytest.mark.asyncio
async def test_platform_managed_teclaw_is_refused_and_nothing_else_is() -> None:
    teclaw = BotRef(entity_id="staff_u1", bot_id="b", owner_id="u1", engine_type="teclaw", bot_type="personal")
    assert await _reader(teclaw_platform_managed=True).refusal(teclaw) is not None
    assert await _reader(teclaw_platform_managed=False).refusal(teclaw) is None
    assert await _reader(teclaw_platform_managed=True).refusal(BOT) is None


@pytest.mark.asyncio
async def test_persona_reads_only_the_files_that_exist() -> None:
    identity = FakeIdentity({"SOUL.md": "# Soul\n", "OKR.md": None, "AGENTS.md": "# A\n"})
    assert await _reader(identity=identity).persona_files(BOT) == (
        CapturedPersonaFile("SOUL.md", "# Soul\n"),
        CapturedPersonaFile("AGENTS.md", "# A\n"),
    )
    assert identity.calls[0] == ("list", "staff", "staff_u1", "bot_1", "u1", "openclaw"), (
        "entity for the files' home, owner for the user"
    )
    assert ("read", "OKR.md") not in identity.calls


@pytest.mark.asyncio
async def test_skills_are_classified_by_locator() -> None:
    assets = [
        SimpleNamespace(name="tidy", git_path="local://skills-local/tidy", skill_uuid=None, sc_version_number=None),
        SimpleNamespace(name="refund", git_path="center://sc/refund", skill_uuid="uuid-1", sc_version_number="v7"),
        SimpleNamespace(name="weather", git_path="git://team/weather", skill_uuid=None, sc_version_number=None),
    ]
    factory = FakeSkillFactory({"local://skills-local/tidy": [("SKILL.md", b"---\nname: tidy\n---\n")]})
    capabilities = FakeCapabilities(assets)
    skills = await _reader(capabilities=capabilities, skill_factory=factory).skills(BOT)
    assert capabilities.asked_for == "u1", "active skills are the owner's rows"
    assert [(s.name, s.origin, s.center_version, s.repo_path, s.files) for s in skills] == [
        ("tidy", "local", None, None, (("SKILL.md", b"---\nname: tidy\n---\n"),)),
        ("refund", "center", "v7", None, ()),
        ("weather", "repo", None, "team/weather", ()),
    ]
    call = factory.calls[0]
    assert (call["entity_id"], call["owner_id"], call["bot_id"], call["engine_type"], call["entity_type"]) == (
        "staff_u1",
        "u1",
        "bot_1",
        "openclaw",
        "staff",
    )
    assert (call["is_desktop"], call["is_teclaw"], call["locator"], call["skill_name"]) == (
        False,
        False,
        "local://skills-local/tidy",
        "tidy",
    )


@pytest.mark.asyncio
async def test_an_unknown_skill_locator_is_refused() -> None:
    assets = [SimpleNamespace(name="odd", git_path="oss://x", skill_uuid=None, sc_version_number=None)]
    with pytest.raises(CaptureRefused) as caught:
        await _reader(capabilities=FakeCapabilities(assets)).skills(BOT)
    assert caught.value.reasons[0].location == "skills/odd"


@pytest.mark.asyncio
async def test_resources_are_the_bots_file_rows_read_from_the_device() -> None:
    rows = [
        SimpleNamespace(path="faq.csv", is_directory=False),
        SimpleNamespace(path=None, is_directory=False),  # a URL resource
        SimpleNamespace(path="kb", is_directory=True),  # a folder row has no bytes
        SimpleNamespace(path="kb/notes.md", is_directory=False),
    ]
    files = FakeResourceFiles({"faq.csv": b"q,a\n", "data/kb/notes.md": b"# n\n"})
    resources = await _reader(resource_services=FakeResourceServices(rows), resource_files=files).resources(BOT)
    # A row's path is workspace-relative (the Open API's); a row the deprecated upload wrote is relative to
    # ``workspace/data``, so a workspace-relative miss is looked for there and the path captured is the real one.
    assert [(r.path, r.files) for r in resources] == [
        ("faq.csv", (("faq.csv", b"q,a\n"),)),
        ("data/kb/notes.md", (("notes.md", b"# n\n"),)),
    ]
    assert [c["path"] for c in files.calls] == ["faq.csv", "kb/notes.md", "data/kb/notes.md"]
    assert files.calls[0] == {
        "entity_type": "staff",
        "entity_id": "staff_u1",
        "bot_id": "bot_1",
        "engine_type": "openclaw",
        "path": "faq.csv",
    }


@pytest.mark.asyncio
async def test_a_listed_file_that_cannot_be_read_is_refused() -> None:
    rows = [SimpleNamespace(path="gone.md", is_directory=False)]
    with pytest.raises(CaptureRefused) as caught:
        await _reader(resource_services=FakeResourceServices(rows), resource_files=FakeResourceFiles({})).resources(BOT)
    assert caught.value.reasons[0].code == "resource_unreadable"


@pytest.mark.asyncio
async def test_mcp_servers_exclude_platform_defaults_and_carry_the_bot_override() -> None:
    activation = FakeActivation({"mcp.b", "mcp.a", "mcp.default"}, {"mcp.default"})
    reader = _reader(
        activation=activation,
        mcp_config=FakeMcpConfig({"mcp.a": {"url": "https://a.example.com/mcp"}, "mcp.b": {}}),
    )
    assert await reader.mcp_servers(BOT) == (
        CapturedMcpServer("mcp.a", {"url": "https://a.example.com/mcp"}),
        CapturedMcpServer("mcp.b", None),
    )
    assert activation.asked_for == ("u1", "u1"), "MCP activation is the owner's"


@pytest.mark.asyncio
async def test_script_body_gets_the_placeholder_substitution_apply_performs(monkeypatch) -> None:
    monkeypatch.setattr("agentclaw.community.core.bot_genome.capture.bot_state.get_current_env", lambda: "pre")
    monkeypatch.setattr(
        "agentclaw.community.core.bot_genome.capture.bot_state.get_current_avernet_tenant", lambda: "acme"
    )
    reader = _reader(scripts=FakeScripts("echo ${BOT_ENV} ${BOT_TENANT} ${BOT_ENGINE_TYPE}\n"))
    assert await reader.script_body(BOT) == "echo pre acme openclaw\n"
    assert await _reader(scripts=FakeScripts("")).script_body(BOT) is None
