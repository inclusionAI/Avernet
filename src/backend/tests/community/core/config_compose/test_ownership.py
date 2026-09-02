"""The composer's ``ownership`` map and the collector's platform-managed
branches (W8 Task 6).

ARCA compose is unchanged and carries no map. teclaw compose without a
manifest emits ``engine`` for every file category and today's empty lists;
with the platform asserting a category it emits ``platform`` and the index
refs, each of which resolves against the configured ``bot-data`` store.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
)
from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.config_compose.services.mcporter_composer import McporterComposer
from agentclaw.community.kernel.bot_config import BotConfigArtifact

from tests.community.core.config_compose.test_collector import _collector
from tests.community.core.config_compose.test_config_composer import _FakeCollector, _STORES


class _FakeManagedFiles:
    """A ``ManagedFilesReader`` + ``PlatformManagedCategoriesReader`` over dicts."""

    def __init__(self, *, asserted: set[str], identity=(), resources=(), skills=(), skill_files=None) -> None:
        self.asserted = frozenset(asserted)
        self._identity = list(identity)
        self._resources = list(resources)
        self._skills = list(skills)
        self._skill_files = dict(skill_files or {})
        self.calls: list[str] = []

    def platform_managed(self, req):
        self.calls.append("platform_managed")
        return self.asserted

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


def _req(engine_type: str = "teclaw") -> ComposeRequest:
    return ComposeRequest(entity_id="staff_u1", bot_id="bot1", user_id="u1", engine_type=engine_type)


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


def test_a_teclaw_artifact_from_a_collector_that_asserts_nothing_reads_engine() -> None:
    # The bare collector cannot answer "what does the platform assert": every
    # file category is the engine's, mcp the platform's — pre-W8 behaviour named.
    artifact = _composer(_FakeCollector()).compose(_req())
    assert artifact.ownership == {
        "mcp": "platform", "identity_files": "engine", "resources": "engine", "skills": "engine"
    }
    assert artifact.identity_files == [] and artifact.resources == [] and artifact.skills == []
    # And the map round-trips through the wire shape.
    assert BotConfigArtifact.from_dict(artifact.to_dict()).ownership == artifact.ownership


def test_the_map_follows_what_the_collector_asserts() -> None:
    managed = _FakeManagedFiles(asserted={"identity_files", "resources"}, identity=[_RULES], resources=[_FAQ])
    collector = _collector(skill_set_service=_skills_svc([]), managed_files_reader=managed)
    artifact = _composer(collector).compose(_req())

    assert artifact.ownership == {
        "mcp": "platform", "identity_files": "platform", "resources": "platform", "skills": "engine"
    }
    assert [(f.name, f.store, f.path) for f in artifact.identity_files] == [
        ("RULES.md", "bot-data", f"{_BASE}/identity/RULES.md")
    ]
    assert [(r.name, r.path) for r in artifact.resources] == [("faq.md", f"{_BASE}/workspace/kb/faq.md")]
    # Each ref resolves against the configured bot-data store.
    assert artifact.stores["bot-data"] == _STORES["bot-data"]
    # Asked once per compose, not once per category.
    assert managed.calls.count("platform_managed") == 1


# ── the collector's teclaw branches ────────────────────────────────────────


def test_teclaw_without_a_reader_answers_as_before_w8() -> None:
    collector = _collector(skill_set_service=_skills_svc([{"git_path": "local://skills-local/x", "name": "x"}]))
    req = _req()
    assert collector.platform_managed(req) == frozenset()
    assert collector.identity_files(req) == [] and collector.resources(req) == []
    assert collector.skills(req) == []


def test_arca_never_consults_the_reader() -> None:
    managed = _FakeManagedFiles(asserted={"identity_files"}, identity=[_RULES])
    identity_service = MagicMock()
    identity_service.get_bot_file_path.return_value = MagicMock(exists=lambda: False)
    collector = _collector(
        skill_set_service=_skills_svc([]), identity_service=identity_service, managed_files_reader=managed
    )
    assert collector.platform_managed(_req("openclaw")) == frozenset()
    assert collector.identity_files(_req("openclaw")) == []
    assert managed.calls == []


def test_an_asserted_category_reads_the_index_and_an_unasserted_one_does_not() -> None:
    managed = _FakeManagedFiles(asserted={"identity_files"}, identity=[_RULES], resources=[_FAQ])
    collector = _collector(skill_set_service=_skills_svc([]), managed_files_reader=managed)
    req = _req()
    assert collector.identity_files(req) == [_RULES]
    # resources is the engine's: the index is not consulted for it.
    assert collector.resources(req) == []


def test_platform_skills_emit_a_ref_and_the_files_for_active_packages_only() -> None:
    managed = _FakeManagedFiles(
        asserted={"skills"}, skills=[_LOOKUP, _STALE],
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
    # Only ``skills`` is the platform's: the SkillRef alone carries the
    # package (R-O3), and a resources list the map says the engine owns
    # carries nothing the engine would then ignore.
    assert collector.resources(req) == []
    # The active set was read once for the whole compose.
    assert collector._skill_set_service_factory.create.return_value.get_active_skills.call_count == 1

    artifact = _composer(collector).compose(req)
    assert artifact.ownership["skills"] == "platform"
    assert artifact.ownership["resources"] == "engine" and artifact.resources == []
    assert [s.name for s in artifact.skills] == ["weather", "order-lookup"]


def test_with_resources_platform_managed_too_the_package_files_ride_as_resources() -> None:
    managed = _FakeManagedFiles(
        asserted={"skills", "resources"}, skills=[_LOOKUP, _STALE], resources=[_FAQ],
        skill_files={"order-lookup": _LOOKUP_FILES, "stale": [CollectedFile("SKILL.md", store="bot-data", path="x")]},
    )
    rows = [{"git_path": "local://skills-local/order-lookup", "name": "order-lookup"}]
    collector = _collector(skill_set_service=_skills_svc(rows), managed_files_reader=managed)
    artifact = _composer(collector).compose(_req())
    # The map and the list agree: resources is the platform's, and it carries
    # the resource plus the active package's files — never the stale one's.
    assert artifact.ownership["resources"] == "platform"
    assert [r.path for r in artifact.resources] == [_FAQ.path] + [f.path for f in _LOOKUP_FILES]


def test_a_platform_skill_the_bot_no_longer_has_active_is_not_delivered() -> None:
    managed = _FakeManagedFiles(asserted={"skills"}, skills=[_LOOKUP], skill_files={"order-lookup": _LOOKUP_FILES})
    collector = _collector(skill_set_service=_skills_svc([]), managed_files_reader=managed)
    assert collector.skills(_req()) == [] and collector.resources(_req()) == []
