"""SkillQueryService: flush-first listing/detail, type-resolved assets.

Ports the behavioural pins of the two seams it replaced — the
``LocalSkillQueryService`` listing (repair-before-page, engine scoping,
refusal-before-write) and the ``BotSkillAssetService`` read half (one
resolver for content and parameters, validation, Installation-projected
``active``) — and adds the merged seam's own promise: detail answers
``active`` from Installation *after* the flush, so a SkillSet-bridged
Skill is visible before any listing ran.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tests.community.skill_version_fakes import PassthroughSkillVersionResolver

from agentclaw.community.core.models.mcp import (
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.skill_query_service import (
    SkillQueryService,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env

_EMPTY = InstallationFlushPlan(
    member_skill_ids=frozenset(),
    skills_to_install=frozenset(),
    skills_to_uninstall=frozenset(),
)

_BOT = {
    "bot_id": "bot",
    "owner_id": "owner",
    "env": "dev",
    "active_engine": "openclaw",
    "template_type": "",
}


class _Bots:
    def __init__(self, bot: dict | None = _BOT) -> None:
        self._bot = bot
        self.reads = 0

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        self.reads += 1
        return self._bot


class _SkillSets:
    """Stands in for the repository seam that owns resolution *and* repair."""

    def __init__(self, bridge: InstallationFlushPlan) -> None:
        self._bridge = bridge
        self.calls: list[dict] = []

    def flush_installations(self, **kwargs) -> InstallationFlushPlan:
        self.calls.append(kwargs)
        return self._bridge


class _Skills:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_bot_skills(self, **kwargs):
        self.calls.append(kwargs)
        return 0, []


class _Collaborators:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def check_collaborator_permission(self, *_args) -> dict:
        return {"has_permission": self._allowed}


def _listing_service(
    *,
    bridge: InstallationFlushPlan,
    bot: dict | None = _BOT,
    allowed: bool = True,
):
    skills, sets, bots = _Skills(), _SkillSets(bridge), _Bots(bot)
    reader = BotCapabilityStateReader(
        sets, bots, object(), PassthroughSkillVersionResolver()
    )
    service = SkillQueryService(
        skills,
        bots,
        _Collaborators(allowed),
        reader,
        object(),
        object(),
        lambda: object(),
    )
    return service, skills, sets, bots


def _list(service, *, actor_id: str = "owner", source: str | None = None):
    return service.list_bot_skills(
        bot_id="bot",
        owner_id="owner",
        actor_id=actor_id,
        page=1,
        page_size=20,
        active=None,
        keyword=None,
        source=source,
    )


# ── Listing: flush-first, engine scoping, refusal-before-write ──────


def test_the_repair_runs_before_the_page_is_cut():
    """`active` is a filter, so the repair cannot happen after the query."""
    service, skills, sets, _bots = _listing_service(
        bridge=InstallationFlushPlan(
            member_skill_ids=frozenset({1, 2}),
            skills_to_install=frozenset({1}),
            skills_to_uninstall=frozenset({2}),
        ),
    )

    _list(service)

    assert len(sets.calls) == 1
    assert skills.calls[0]["skill_set_member_ids"] == frozenset({1, 2})
    assert skills.calls[0]["bot_id"] == "bot"
    assert skills.calls[0]["user_id"] == "owner"
    assert skills.calls[0]["source"] is None


def test_local_source_filter_is_forwarded_after_the_standard_flush():
    service, skills, sets, _bots = _listing_service(bridge=_EMPTY)

    _list(service, source="LOCAL")

    assert len(sets.calls) == 1
    assert skills.calls[0]["source"] == "LOCAL"


def test_the_skillset_scope_uses_the_bots_engine_and_layout_precedence():
    """Same Default-Set precedence the SkillSet surface applies."""
    service, _skills, sets, bots = _listing_service(
        bridge=_EMPTY,
        bot={**_BOT, "active_engine": "claude_code", "template_type": "personalCoding"},
    )

    _list(service)

    assert sets.calls[0]["env"] == "dev"
    assert sets.calls[0]["engine_type"] == "claude_code"
    assert sets.calls[0]["default_engine_types"] == ("aicoding", "claude_code")
    # One Bot read for the whole listing: the engine comes off it.
    assert bots.reads == 1


def test_a_bot_with_no_recorded_engine_does_not_scope_to_a_literal_none():
    """A legacy null engine must widen the scope, not empty it.

    Formatting the column blindly yields the string "None", which matches no
    SkillSet at all — so every bridged Skill would vanish from the listing and
    every repair would be skipped, silently.
    """
    service, _skills, sets, _bots = _listing_service(
        bridge=_EMPTY, bot={**_BOT, "active_engine": None}
    )

    _list(service)

    assert sets.calls[0]["engine_type"] is None
    assert sets.calls[0]["default_engine_types"] == ()


def test_an_invisible_bot_is_refused_before_anything_is_written():
    """An actor who cannot see the Bot cannot cause a write against it."""
    service, skills, sets, _bots = _listing_service(bridge=_EMPTY, bot=None)

    with pytest.raises(LocalSkillNotFoundError):
        _list(service)

    assert sets.calls == [] and skills.calls == []


def test_a_collaborator_without_permission_is_refused_before_any_write():
    service, skills, sets, _bots = _listing_service(bridge=_EMPTY, allowed=False)

    with pytest.raises(LocalSkillNotFoundError):
        _list(service, actor_id="someone-else")

    assert sets.calls == [] and skills.calls == []


# ── Detail, content, parameters: one resolver, validated writes ─────


class _AssetSkills:
    def get_by_id(self, skill_id: str):
        if skill_id != "42":
            return None
        return {
            "id": "42",
            "name": "weekly-report",
            "git_path": "local://weekly-report",
            "user_id": "owner",
            "bolt_id": "bot",
        }


class _AssetBots:
    def get_unique_by_id(self, bot_id: str):
        if bot_id != "bot":
            return None
        return {
            "bot_id": "bot",
            "owner_id": "owner",
            "entity_id": "owner",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "env": "pre",
        }

    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        if (bot_id, owner_id) != ("bot", "owner"):
            return None
        return {
            "entity_id": "owner",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "env": "pre",
        }


class _RecordingReader:
    """The reader's read surface: flush happens inside, callers only read."""

    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []

    def active_skill_assets(self, *, bot_id, owner_id, bot=None):
        assert bot is not None
        self.reads.append((bot_id, owner_id))
        return (SimpleNamespace(skill_id=42),)


class _Storage:
    async def read_file(self, path: str):
        assert path == "SKILL.md"
        return b"---\nname: weekly-report\ndescription: weekly\nconfig:\n  - name: region\n    required: true\n---\n# Report"


class _Factory:
    def local_skill_package_storage_for_locator(self, **kwargs):
        assert kwargs["locator"] == "weekly-report"
        return _Storage()


class _Parameters:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.saved = None

    def get_skill_parameters(self, name: str):
        assert name == "weekly-report"
        return {"region": "cn"}

    async def save_skill_parameters(self, name: str, values: dict):
        self.saved = (name, values)
        return self.result


class _ParameterFactory:
    def __init__(self, result: bool = True) -> None:
        self.parameters = _Parameters(result)

    async def create(self, **kwargs):
        assert kwargs == {"bot_id": "bot", "user_id": "owner"}
        return self.parameters


class _Resolver:
    def resolve_for_bot(self, *_args):
        return type("Context", (), {"provider": "local"})()


def _asset_service(*, save_result: bool = True):
    parameters = _ParameterFactory(save_result)
    reader = _RecordingReader()
    service = SkillQueryService(
        _AssetSkills(),
        _AssetBots(),
        object(),
        reader,
        _Factory(),
        parameters,
        lambda: _Resolver(),
    )
    return service, parameters, reader


@pytest.mark.asyncio
async def test_local_content_and_parameters_use_one_skill_id_resolver() -> None:
    service, parameters, _reader = _asset_service()

    assert "# Report" in await service.get_content(
        skill_id="42", bot_id="bot", owner_id="owner", user_id="owner"
    )
    assert await service.get_parameters(
        skill_id="42", bot_id="bot", owner_id="owner", user_id="owner"
    ) == {"region": "cn"}
    assert await service.replace_parameters(
        skill_id="42",
        bot_id="bot",
        owner_id="owner",
        user_id="owner",
        parameters={"region": "cn"},
    ) == {"region": "cn"}
    assert parameters.parameters.saved == ("weekly-report", {"region": "cn"})


@pytest.mark.asyncio
async def test_skill_only_readme_reads_local_skill_from_persisted_bot() -> None:
    service, _parameters, _reader = _asset_service()

    assert "# Report" in await service.get_readme_by_skill(
        skill_id="42", actor_id="owner"
    )


class _RepoSkill:
    def get_by_id(self, skill_id: str):
        if skill_id == "43":
            return {
                "id": "43",
                "name": "market-skill",
                "git_path": "git://market-skill",
                "user_id": None,
                "bolt_id": "default",
            }
        return None


class _RepoReader:
    def get_repository_skill_content(self, skill_id: str):
        assert skill_id == "43"
        return "# Market skill"


class _UnexpectedBotRepository:
    def __getattr__(self, name):
        raise AssertionError(f"public Repo README unexpectedly used BotRepository.{name}")


class _RepoFactory:
    def create(self):
        return _RepoReader()


@pytest.mark.asyncio
async def test_skill_only_readme_reads_public_repo_without_bot_lookup() -> None:
    service = SkillQueryService(
        _RepoSkill(),
        _UnexpectedBotRepository(),
        object(),
        object(),
        _RepoFactory(),
        object(),
        lambda: object(),
    )

    assert await service.get_readme_by_skill(skill_id="43", actor_id="caller") == (
        "# Market skill"
    )


@pytest.mark.asyncio
async def test_parameter_persistence_failure_is_not_reported_as_success() -> None:
    service, _parameters, _reader = _asset_service(save_result=False)

    with pytest.raises(LocalSkillStorageError):
        await service.replace_parameters(
            skill_id="42",
            bot_id="bot",
            owner_id="owner",
            user_id="owner",
            parameters={"region": "cn"},
        )


def test_required_parameter_accepts_false_and_zero_values() -> None:
    """Required config means the complete object contains the key, not truthiness."""
    SkillQueryService._validate_parameters(
        """---
name: weekly-report
description: weekly
config:
  - name: enabled
    required: true
  - name: retries
    required: true
---
# Report
""",
        {"enabled": False, "retries": 0},
    )


def test_resolved_bot_skill_uses_installation_projection_for_active_state() -> None:
    """Detail flushes first, then answers ``active`` from Installation."""
    service, _parameters, reader = _asset_service()

    record = service.get_skill(
        skill_id="42",
        bot_id="bot",
        owner_id="owner",
        user_id="owner",
    )

    assert record["active"] is True
    assert reader.reads == [("bot", "owner")]


# ── The merged seam's own promise, against the real repository ──────


class _Database:
    def __init__(self, engine) -> None:
        self._session = sessionmaker(bind=engine)

    @contextmanager
    def transactional_orm_session(self):
        with self.orm_session() as session:
            yield session

    @contextmanager
    def orm_session(self):
        session = self._session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def test_a_bridged_skill_is_active_in_detail_before_any_listing_ran(tmp_path):
    """The flush belongs to *every* reader call, detail included.

    The Skill is a member of an active SkillSet but holds no Installation
    row — nothing has listed the Bot yet. The first read anyone performs is
    the detail, and it must already answer ``active: true``, because the
    reader flushed the Set's membership into Installation before answering.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'detail-first.db'}")
    for model in (
        BotModel,
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        BotMCPInstallation,
        DefaultSkillsetSkillExclusion,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    bots, skills = BotRepository(db), SkillRepository(db)

    bots.insert(
        {
            "bot_id": "bot",
            "entity_id": "owner",
            "entity_type": "staff",
            "creator_id": "owner",
            "owner_id": "owner",
            "active_engine": "openclaw",
        }
    )
    member = skills.create(
        {
            "name": "bridged",
            "git_path": "local://bridged",
            "user_id": "owner",
            "bolt_id": "bot",
        }
    )
    with db.orm_session() as session:
        skill_set = SkillSet(
            name="mine",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env=get_current_env(),
        )
        session.add(skill_set)
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=skill_set.id,
                skill_id=int(member["id"]),
                env=get_current_env(),
            )
        )

    reader = BotCapabilityStateReader(
        CapabilityDesiredStateRepository(db),
        bots,
        skills,
        PassthroughSkillVersionResolver(),
    )
    service = SkillQueryService(
        skills, bots, object(), reader, object(), object(), lambda: object()
    )

    record = service.get_skill(
        skill_id=str(member["id"]),
        bot_id="bot",
        owner_id="owner",
        user_id="owner",
    )

    assert record["active"] is True


class _ReadmeSkillRepository:
    def __init__(self, skills: dict[str, dict]):
        self.skills = skills

    def get_by_id(self, skill_id: str):
        return self.skills.get(skill_id)


class _ReadmeBotRepository:
    def __init__(self, bot: dict | None):
        self.bot = bot

    def get_unique_by_id(self, _bot_id: str):
        return self.bot


class _ReadmeStorage:
    def __init__(self, *contents):
        self.contents = iter(contents)

    async def read_file(self, _filename: str):
        return next(self.contents)


class _ReadmeFactory:
    def __init__(self, repository_content=None, *storage_contents):
        self.repository_content = repository_content
        self.storage_contents = storage_contents

    def create(self):
        return SimpleNamespace(
            get_repository_skill_content=lambda _skill_id: self.repository_content
        )

    def local_skill_package_storage_for_locator(self, **_kwargs):
        return _ReadmeStorage(*self.storage_contents)


class _ReadmeResolver:
    def resolve_for_bot(self, *_args):
        return SimpleNamespace(provider="local")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_id, skills",
    [
        ("not-a-number", {}),
        ("1", {}),
        ("1", {"1": {"git_path": "center://unsupported"}}),
        ("1", {"1": {"git_path": "local://x", "bolt_id": ""}}),
        ("1", {"1": {"git_path": "local://x", "bolt_id": "missing"}}),
    ],
)
async def test_skill_only_readme_masks_unresolvable_skill_rows(skill_id, skills):
    service = SkillQueryService(
        _ReadmeSkillRepository(skills),
        _ReadmeBotRepository(None),
        _Collaborators(),
        object(),
        _ReadmeFactory(),
        object(),
        _ReadmeResolver,
    )

    with pytest.raises(LocalSkillNotFoundError):
        await service.get_readme_by_skill(skill_id=skill_id, actor_id="actor")


@pytest.mark.asyncio
async def test_skill_only_readme_masks_missing_bot_owner_and_denied_collaborator():
    skill = {"git_path": "local://x", "bolt_id": "bot"}
    service = SkillQueryService(
        _ReadmeSkillRepository({"1": skill}),
        _ReadmeBotRepository({"entity_id": "e", "owner_id": "", "active_engine": "x"}),
        _Collaborators(),
        object(),
        _ReadmeFactory(),
        object(),
        _ReadmeResolver,
    )
    with pytest.raises(LocalSkillNotFoundError):
        await service.get_readme_by_skill(skill_id="1", actor_id="actor")

    service = SkillQueryService(
        _ReadmeSkillRepository({"1": skill}),
        _ReadmeBotRepository({"entity_id": "e", "owner_id": "owner", "active_engine": "x"}),
        _Collaborators(allowed=False),
        object(),
        _ReadmeFactory(),
        object(),
        _ReadmeResolver,
    )
    with pytest.raises(LocalSkillNotFoundError):
        await service.get_readme_by_skill(skill_id="1", actor_id="actor")


@pytest.mark.asyncio
async def test_skill_only_readme_masks_missing_public_repo_content():
    service = SkillQueryService(
        _ReadmeSkillRepository({"1": {"git_path": "git://market"}}),
        _ReadmeBotRepository(None),
        _Collaborators(),
        object(),
        _ReadmeFactory(None),
        object(),
        _ReadmeResolver,
    )
    with pytest.raises(LocalSkillNotFoundError):
        await service.get_readme_by_skill(skill_id="1", actor_id="actor")


@pytest.mark.asyncio
async def test_skill_only_readme_returns_string_content():
    service = SkillQueryService(
        _ReadmeSkillRepository({"1": {"git_path": "local://x", "bolt_id": "bot"}}),
        _ReadmeBotRepository(
            {"entity_id": "e", "owner_id": "owner", "active_engine": "x"}
        ),
        _Collaborators(),
        object(),
        _ReadmeFactory(None, "# direct"),
        object(),
        _ReadmeResolver,
    )
    assert await service.get_readme_by_skill(skill_id="1", actor_id="owner") == "# direct"


@pytest.mark.asyncio
async def test_skill_only_readme_tries_readme_fallback_and_decodes_bytes():
    service = SkillQueryService(
        _ReadmeSkillRepository({"1": {"git_path": "local://x", "bolt_id": "bot"}}),
        _ReadmeBotRepository(
            {"entity_id": "e", "owner_id": "owner", "active_engine": "x"}
        ),
        _Collaborators(),
        object(),
        _ReadmeFactory(None, None, b"# fallback"),
        object(),
        _ReadmeResolver,
    )
    assert await service.get_readme_by_skill(skill_id="1", actor_id="owner") == "# fallback"


@pytest.mark.asyncio
async def test_skill_only_readme_masks_empty_local_files():
    service = SkillQueryService(
        _ReadmeSkillRepository({"1": {"git_path": "local://x", "bolt_id": "bot"}}),
        _ReadmeBotRepository(
            {"entity_id": "e", "owner_id": "owner", "active_engine": "x"}
        ),
        _Collaborators(),
        object(),
        _ReadmeFactory(None, "", None),
        object(),
        _ReadmeResolver,
    )
    with pytest.raises(LocalSkillNotFoundError):
        await service.get_readme_by_skill(skill_id="1", actor_id="owner")
