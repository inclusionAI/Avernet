"""``ManifestCompiler``: the stored document → a ``DesiredState``, through the real validator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import MANIFEST_STATE
from agentclaw.community.core.apply_engine.compiler import CompileRefused
from agentclaw.community.core.apply_engine.compiler import StateRef
from agentclaw.community.core.bot_config_manifest.binding import (
    ManifestBindingMismatchError, bound_manifest_state, manifest_digest,
)
from agentclaw.community.core.apply_engine.constructs import Category, Section
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


@pytest.mark.asyncio
async def test_bound_check_cannot_authorize_a_different_document_in_the_worker():
    compiler, content = _compiler(DOCUMENT)
    ref = bound_manifest_state(manifest_digest(DOCUMENT))
    compiler.check(make_context(), ref)
    compiler._manifests._document = DOCUMENT.replace("server_code: gh", "server_code: other")
    with pytest.raises(CompileRefused, match="bound Manifest has changed"):
        await compiler.compile(make_context(), ref, only=())
    assert content.blobs == {}, "even an empty phase verifies before filing or fetching"


@pytest.mark.asyncio
async def test_bound_compile_reads_once_and_executes_that_same_parsed_document():
    original = 'schema_version: 1\nmanifest:\n  identity:\n    - type: CLAUDE.md\n      content: original\n'
    compiler, content = _compiler(original)
    reads = []

    def read_once(**kwargs):
        reads.append(kwargs)
        return SimpleNamespace(document=original if len(reads) == 1 else original.replace("original", "changed"))

    compiler._manifests.get = read_once
    result = await compiler.compile(make_context(), bound_manifest_state(manifest_digest(original)))
    assert len(reads) == 1
    assert result.state.identity.entries[0].content.digest == digest_of(b"original")


@pytest.mark.parametrize("reference", ["", "unknown:" + "a" * 64, "sha256-json-v1:bad"])
@pytest.mark.asyncio
async def test_invalid_bound_reference_cannot_fall_back_to_current_document(reference):
    compiler, _ = _compiler(DOCUMENT)
    with pytest.raises(CompileRefused, match="invalid bound Manifest reference"):
        await compiler.compile(make_context(), StateRef("manifest", reference))


@pytest.mark.asyncio
async def test_missing_bound_document_refuses_but_ordinary_apply_can_use_updated_document():
    compiler, _ = _compiler(None)
    with pytest.raises(CompileRefused, match="document is missing"):
        await compiler.compile(make_context(), bound_manifest_state(manifest_digest(DOCUMENT)))
    compiler._manifests._document = 'schema_version: 1\nmanifest:\n  mcp:\n    - server_code: updated\n'
    result = await compiler.compile(make_context(), MANIFEST_STATE)
    assert result.state.mcp.entries[0].server_code == "updated"


def test_binding_preserves_canonical_semantics_and_rejects_execution_scope_mismatch():
    from dataclasses import replace

    assert manifest_digest('schema_version: 1\nmanifest: {}') == manifest_digest('{"manifest": {}, "schema_version": 1}')
    assert manifest_digest('schema_version: 1\nmanifest: {}') != manifest_digest('schema_version: 1\nmanifest: {resources: []}')
    compiler, _ = _compiler(DOCUMENT)
    ref = bound_manifest_state(manifest_digest(DOCUMENT))
    for ctx in [replace(make_context(), env="prod"), replace(make_context(), tenant="other")]:
        with pytest.raises(ManifestBindingMismatchError, match="scope"):
            compiler.check(ctx, ref)


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
    compiled = await compiler.compile(make_context(), MANIFEST_STATE)
    state = compiled.state
    assert compiled.sources == ()
    assert state.script.entries[0].body == "echo dev"
    assert [e.type for e in state.identity.entries] == ["CLAUDE.md"]
    assert [e.path for e in state.resources.entries] == ["data/faq.csv"]
    assert [e.name for e in state.skills.entries] == ["quality-check"]
    assert [e.server_code for e in state.mcp.entries] == ["gh"]
    assert state.engine_config is None and state.cli_tools is None
    assert state.declared == (
        Section.SCRIPT,
        Category.IDENTITY,
        Category.RESOURCES,
        Category.SKILLS,
        Category.MCP,
    )
    for digest in (state.identity.entries[0].content.digest, state.resources.entries[0].content.digest):
        assert content.is_reachable(None, digest, produced_by="manifest")


@pytest.mark.asyncio
async def test_no_document_compiles_to_the_empty_state_and_null_script_declares_none() -> None:
    compiler, _ = _compiler(None)
    assert (await compiler.compile(make_context(), MANIFEST_STATE)).state == DesiredState.EMPTY
    compiler, _ = _compiler("schema_version: 1\nscript: null\n")
    state = (await compiler.compile(make_context(), MANIFEST_STATE)).state
    assert state.script is not None and state.script.entries == ()
    assert state.declared == (Section.SCRIPT,)


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
    state = (await compiler.compile(make_context(), MANIFEST_STATE, only={Section.SCRIPT})).state
    assert state.declared == (Section.SCRIPT,)
    assert state.skills is None and state.identity is None
    assert objects.calls == []


@pytest.mark.asyncio
async def test_a_document_that_stopped_validating_is_refused_in_compile_and_raised_in_check() -> None:
    """``check`` answers the caller before an apply id exists, with the validator's own error; the
    same document failing in the worker is the engine's ``CompileRefused``, with the reason."""
    from agentclaw.community.core.bot_config_manifest.schema import ManifestValidationError

    compiler, _ = _compiler("schema_version: 1\nmanifest:\n  mcp: []\n")

    class _Refusing(_Manifests):
        def validate(self, *, document, active_engine, bot_type):
            raise ManifestValidationError([])

    compiler._manifests = _Refusing("schema_version: 1\nmanifest:\n  mcp: []\n")
    with pytest.raises(ManifestValidationError):
        compiler.check(make_context(), MANIFEST_STATE)
    with pytest.raises(CompileRefused, match="no longer validates") as refused:
        await compiler.compile(make_context(), MANIFEST_STATE)
    assert isinstance(refused.value.domain_error, ManifestValidationError)
    assert refused.value.__cause__ is refused.value.domain_error
