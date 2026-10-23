"""``ManifestCompiler``: the stored document → a ``DesiredState``, through the real validator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from agentclaw.community.core.apply_engine.compiler import ManifestStateRef
from agentclaw.community.core.apply_engine.constructs import ManifestCategory, ManifestSection
from agentclaw.community.core.apply_engine.desired_state import DesiredState
from agentclaw.community.core.bot_config_manifest.sources.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities
from agentclaw.community.core.bot_config_manifest.compiler import ManifestCompiler
from agentclaw.community.core.bot_config_manifest.compiler.session import digest_of
from agentclaw.community.core.bot_config_manifest.schema.validator import validate_document
from tests.community.core.apply_engine._fakes import (
    FakeGitClient,
    FakeManifestContent,
    FakeObjectCredentials,
    build_skill_zip,
    make_context,
    real_validator,
    seeded_object_store,
)

PACKAGE = build_skill_zip("quality-check")

DOCUMENT_TEMPLATE = """schema_version: 1
sources:
  fixtures:
    protocol: oss
    bucket: manifest-fixtures
    key: "fixtures/qc.zip"
    auth: oss-cred
manifest:
  identity:
    - type: CLAUDE.md
      content: "# me\\n"
  resources:
    - path: data/faq.csv
      content: "q,a\\n"
  skills:
    - name: quality-check
      from: fixtures
      digest: {digest}
  mcp:
    - server_code: gh
script:
  body: "echo ${{BOT_ENV}}"
"""
DOCUMENT = DOCUMENT_TEMPLATE.format(digest=digest_of(PACKAGE))


class _Manifests:
    def __init__(self, document: str | None) -> None:
        self._document = document

    def get(self, *, entity_id: str, bot_id: str):
        return None if self._document is None else SimpleNamespace(document=self._document)

    def validate(self, *, document: str, active_engine: str, bot_type: str):
        caps = resolve_capabilities(active_engine=active_engine, bot_type=bot_type, is_teclaw=lambda e: e == "teclaw")
        return validate_document(document, caps)


class _Applies:
    def recent(self, **kwargs):
        return []


def _compiler(document: str | None, objects: dict[str, bytes] | None = None):
    content = FakeManifestContent()
    resolver = DeclaredSourceResolver(content, FakeObjectCredentials(), seeded_object_store(objects or {}))
    compiler = ManifestCompiler(
        manifests=_Manifests(document),
        content=content,
        resolver=resolver,
        validator=real_validator(),
        git_client_provider=FakeGitClient,
        applies=_Applies(),
    )
    return compiler, content


@pytest.mark.asyncio
async def test_a_stored_document_compiles_to_a_complete_state() -> None:
    compiler, content = _compiler(DOCUMENT, {"fixtures/qc.zip": PACKAGE})
    compiled = await compiler.compile(make_context(), ManifestStateRef())
    state = compiled.state
    assert compiled.sources == ()
    assert state.script.entries[0].body == "echo dev"
    assert [e.type for e in state.identity.entries] == ["CLAUDE.md"]
    assert [e.path for e in state.resources.entries] == ["data/faq.csv"]
    assert [e.name for e in state.skills.entries] == ["quality-check"]
    assert [e.server_code for e in state.mcp.entries] == ["gh"]
    assert state.engine_config is None and state.cli_tools is None
    assert state.declared == (
        ManifestSection.SCRIPT,
        ManifestCategory.IDENTITY,
        ManifestCategory.RESOURCES,
        ManifestCategory.SKILLS,
        ManifestCategory.MCP,
    )
    for digest in (state.identity.entries[0].content.digest, state.resources.entries[0].content.digest):
        assert content.is_reachable(None, digest, produced_by="manifest")


@pytest.mark.asyncio
async def test_no_document_compiles_to_the_empty_state_and_null_script_declares_none() -> None:
    compiler, _ = _compiler(None)
    assert (await compiler.compile(make_context(), ManifestStateRef())).state == DesiredState.EMPTY
    compiler, _ = _compiler("schema_version: 1\nscript: null\n")
    state = (await compiler.compile(make_context(), ManifestStateRef())).state
    assert state.script is not None and state.script.entries == ()
    assert state.declared == (ManifestSection.SCRIPT,)


@pytest.mark.asyncio
async def test_compile_document_takes_a_parsed_document_as_is() -> None:
    compiler, _ = _compiler(None)
    parsed = yaml.safe_load(
        "schema_version: 1\nmanifest:\n  mcp:\n    - server_code: gh\n  engine_config:\n    config:\n      model: fast\n"
    )
    state = (await compiler.compile_document(make_context(), parsed)).state
    assert state.mcp.entries[0].server_code == "gh" and state.script is None
    assert state.engine_config.entries[0].config["model"] == "fast"


@pytest.mark.asyncio
async def test_only_narrows_the_compile_to_a_phases_constructs_and_fetches_nothing_else() -> None:
    """The pre-container half compiles the script alone: the skill is neither fetched nor declared."""
    objects = seeded_object_store({"fixtures/qc.zip": PACKAGE})
    content = FakeManifestContent()
    resolver = DeclaredSourceResolver(content, FakeObjectCredentials(), objects)
    compiler = ManifestCompiler(
        manifests=_Manifests(DOCUMENT),
        content=content,
        resolver=resolver,
        validator=real_validator(),
        git_client_provider=FakeGitClient,
        applies=_Applies(),
    )
    state = (await compiler.compile(make_context(), ManifestStateRef(), only={ManifestSection.SCRIPT})).state
    assert state.declared == (ManifestSection.SCRIPT,)
    assert state.skills is None and state.identity is None
    assert objects.calls == []
