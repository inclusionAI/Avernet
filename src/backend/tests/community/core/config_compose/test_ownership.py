"""The composer's ``ownership`` map and the collector's platform-owned
branches (W8 Task 6).

Ownership follows the operation. ARCA compose is unchanged and carries no
map. A teclaw compose the engine owns emits ``engine`` for every category
but ``mcp`` and today's empty lists; one the platform owns emits ``platform``
for every category and the store's refs, each of which resolves against the
configured ``bot-data`` store.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeOccasion,
    ComposeRequest,
)
from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.config_compose.services.mcporter_composer import McporterComposer
from agentclaw.community.kernel.bot_config import BotConfigArtifact

from tests.community.core.config_compose.test_collector import _collector
from tests.community.core.config_compose.test_config_composer import _FakeCollector, _STORES

#: What a compose the platform does *not* own reads. Two categories are the
#: platform's on every occasion and are named here rather than derived: ``mcp``,
#: because the artifact has carried the whole MCP set since W12 and there is no
#: engine state for it to keep; and ``cli_tools`` since W9, because the platform
#: holds the tools' bytes and the table is their desired state — writing
#: ``engine`` for it on a runtime edit would tell the engine to keep tools the
#: platform had just removed.
_ALL_ENGINE = {
    "mcp": "platform", "identity_files": "engine", "resources": "engine", "skills": "engine",
    "cli_tools": "platform",
}
_ALL_PLATFORM = {category: "platform" for category in _ALL_ENGINE}


class _FakeManagedFiles:
    """A ``ManagedFilesReader`` + ``PlatformOwnershipReader`` over dicts."""

    def __init__(
        self, *, identity=(), resources=(), skills=(), skill_files=None,
        serves: str = "teclaw", owns: frozenset[ComposeOccasion] = frozenset({ComposeOccasion.MANIFEST_APPLY}),
    ) -> None:
        self._identity = list(identity)
        self._resources = list(resources)
        self._skills = list(skills)
        self._skill_files = dict(skill_files or {})
        self._serves = serves
        self._owns = owns
        self.calls: list[str] = []

    def platform_owns(self, req):
        # The engine decision is the reader's, as in the real one: it owns
        # nothing for an engine it does not serve.
        self.calls.append("platform_owns")
        return req.engine_type == self._serves and req.occasion in self._owns

    def identity_files(self, req):
        return list(self._identity)

    def resources(self, req):
        return list(self._resources)

    def skills(self, req):
        return list(self._skills)

    def skill_files(self, req, names):
        return [f for n in sorted(names) for f in self._skill_files.get(n, [])]


_BASE = "staff_u1/bot1_manifest/teclaw"
_RULES = CollectedFile("RULES.md", store="bot-data", path=f"{_BASE}/identity/RULES.md")
_FAQ = CollectedFile("faq.md", store="bot-data", path=f"{_BASE}/workspace/kb/faq.md")
_LOOKUP = CollectedSkill("order-lookup", "user", store="bot-data", path=f"{_BASE}/workspace/skills-local/order-lookup")
_LOOKUP_FILES = [
    CollectedFile("SKILL.md", store="bot-data", path=f"{_BASE}/workspace/skills-local/order-lookup/SKILL.md"),
]
_STALE = CollectedSkill("stale", "user", store="bot-data", path=f"{_BASE}/workspace/skills-local/stale")


def _req(
    engine_type: str = "teclaw", occasion: ComposeOccasion = ComposeOccasion.MANIFEST_APPLY
) -> ComposeRequest:
    return ComposeRequest(
        entity_id="staff_u1", bot_id="bot1", user_id="u1", engine_type=engine_type, occasion=occasion
    )


def _skills_svc(rows: list[dict[str, Any]]):
    svc = MagicMock()
    svc.get_active_skills.return_value = rows
    return svc


def _composer(collector) -> ConfigComposer:
    return ConfigComposer(mcporter_composer=McporterComposer(), collector=collector, stores=_STORES)


# ── the composer's map ─────────────────────────────────────────────────────


def test_an_arca_artifact_carries_no_map() -> None:
    artifact = _composer(_FakeCollector()).compose(_req("openclaw"))
    assert artifact.ownership is None
    assert "ownership" not in artifact.to_dict()


def test_a_teclaw_artifact_from_a_collector_that_owns_nothing_reads_engine() -> None:
    # The bare collector cannot answer "does the platform own this compose":
    # every category is the engine's except the two that are the platform's on
    # every occasion (``mcp``, and ``cli_tools`` since W9) — pre-W8 behaviour
    # named, whatever the occasion.
    for occasion in ComposeOccasion:
        artifact = _composer(_FakeCollector()).compose(_req(occasion=occasion))
        assert artifact.ownership == _ALL_ENGINE, occasion
        assert artifact.identity_files == [] and artifact.resources == [] and artifact.skills == []
    # And the map round-trips through the wire shape.
    assert BotConfigArtifact.from_dict(artifact.to_dict()).ownership == artifact.ownership


def test_the_map_follows_the_operation_not_the_categories() -> None:
    """A manifest apply's compose is the platform's for every category; a
    runtime edit's compose is the engine's for every category but ``mcp`` and
    ``cli_tools``, and reads no managed file — whatever the store holds."""
    managed = _FakeManagedFiles(identity=[_RULES], resources=[_FAQ])
    collector = _collector(skill_set_service=_skills_svc([]), managed_files_reader=managed)

    applied = _composer(collector).compose(_req(occasion=ComposeOccasion.MANIFEST_APPLY))
    assert applied.ownership == _ALL_PLATFORM
    assert [(f.name, f.store, f.path) for f in applied.identity_files] == [
        ("RULES.md", "bot-data", f"{_BASE}/identity/RULES.md")
    ]
    assert [(r.name, r.path) for r in applied.resources] == [("faq.md", f"{_BASE}/workspace/kb/faq.md")]
    # Each ref resolves against the configured bot-data store.
    assert applied.stores["bot-data"] == _STORES["bot-data"]
    # Asked once per compose, not once per category.
    assert managed.calls.count("platform_owns") == 1

    edited = _composer(collector).compose(_req(occasion=ComposeOccasion.RUNTIME))
    assert edited.ownership == _ALL_ENGINE
    assert edited.identity_files == [] and edited.resources == []


# ── the collector's teclaw branches ────────────────────────────────────────


def test_teclaw_without_a_reader_answers_as_before_w8() -> None:
    collector = _collector(skill_set_service=_skills_svc([{"git_path": "local://skills-local/x", "name": "x"}]))
    req = _req()
    assert collector.platform_owns(req) is False
    assert collector.identity_files(req) == [] and collector.resources(req) == []
    assert collector.skills(req) == []


def test_the_reader_decides_the_engine_so_arca_reads_no_managed_file() -> None:
    """The collector names no engine: it asks the reader, and the reader
    owns nothing for a family it does not serve, so an ARCA compose
    reads no managed file and answers exactly as before W8."""
    managed = _FakeManagedFiles(identity=[_RULES])
    identity_service = MagicMock()
    identity_service.get_bot_file_path.return_value = MagicMock(exists=lambda: False)
    collector = _collector(
        skill_set_service=_skills_svc([]), identity_service=identity_service, managed_files_reader=managed
    )
    assert collector.platform_owns(_req("openclaw")) is False
    assert collector.identity_files(_req("openclaw")) == []
    assert managed.calls == ["platform_owns"], "asked once, memoised, answered nothing"


def test_platform_skills_emit_a_ref_and_the_files_for_active_packages_only() -> None:
    managed = _FakeManagedFiles(
        skills=[_LOOKUP, _STALE],
        skill_files={"order-lookup": _LOOKUP_FILES, "stale": [CollectedFile("SKILL.md", store="bot-data", path="x")]},
    )
    rows = [
        {"git_path": "git://team/weather", "name": "weather"},
        {"git_path": "local://skills-local/order-lookup", "name": "order-lookup"},
    ]
    collector = _collector(skill_set_service=_skills_svc(rows), managed_files_reader=managed)
    req = _req()

    skills = collector.skills(req)
    assert [(s.name, s.scope, s.store, s.path) for s in skills] == [
        ("weather", "shared", "skill-repo", "team/weather"),
        ("order-lookup", "user", "bot-data", f"{_BASE}/workspace/skills-local/order-lookup"),
    ]
    # The package's files ride as resources refs beside the SkillRef (R-O3)
    # — the active package's, never the stale one's.
    assert [f.path for f in collector.resources(req)] == [f.path for f in _LOOKUP_FILES]
    # The active set was read once for the whole compose.
    assert collector._skill_set_service_factory.create.return_value.get_active_skills.call_count == 1

    artifact = _composer(collector).compose(req)
    assert artifact.ownership == _ALL_PLATFORM
    assert [s.name for s in artifact.skills] == ["weather", "order-lookup"]
    assert [r.path for r in artifact.resources] == [f.path for f in _LOOKUP_FILES]


def test_the_resources_list_carries_the_resources_then_the_package_files() -> None:
    managed = _FakeManagedFiles(
        skills=[_LOOKUP, _STALE], resources=[_FAQ],
        skill_files={"order-lookup": _LOOKUP_FILES, "stale": [CollectedFile("SKILL.md", store="bot-data", path="x")]},
    )
    rows = [{"git_path": "local://skills-local/order-lookup", "name": "order-lookup"}]
    collector = _collector(skill_set_service=_skills_svc(rows), managed_files_reader=managed)
    artifact = _composer(collector).compose(_req())
    assert artifact.ownership["resources"] == "platform"
    assert [r.path for r in artifact.resources] == [_FAQ.path] + [f.path for f in _LOOKUP_FILES]


def test_a_platform_skill_the_bot_no_longer_has_active_is_not_delivered() -> None:
    managed = _FakeManagedFiles(skills=[_LOOKUP], skill_files={"order-lookup": _LOOKUP_FILES})
    collector = _collector(skill_set_service=_skills_svc([]), managed_files_reader=managed)
    assert collector.skills(_req()) == [] and collector.resources(_req()) == []


def test_a_runtime_edit_delivers_no_managed_skill() -> None:
    """A skill upload's compose is the engine's: the platform's package
    copies stay in the store and the artifact carries none of them."""
    managed = _FakeManagedFiles(skills=[_LOOKUP], skill_files={"order-lookup": _LOOKUP_FILES})
    rows = [{"git_path": "local://skills-local/order-lookup", "name": "order-lookup"}]
    collector = _collector(skill_set_service=_skills_svc(rows), managed_files_reader=managed)
    req = _req(occasion=ComposeOccasion.RUNTIME)
    assert collector.skills(req) == [] and collector.resources(req) == []
