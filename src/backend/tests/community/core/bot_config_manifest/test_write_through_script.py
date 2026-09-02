"""``write_through_script`` and ``script_body`` (W8 Task 19).

The legacy startup-script write, through the manifest: the document's
``script`` section changes and nothing else does; the row gets the body the
materialiser would write; the next apply plans the script ``unchanged``; a
refusal leaves both untouched; no manifest means ``None`` and no write.
"""
from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.bot_config_manifest.apply.materialisers.script import (
    ScriptMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.schema import ManifestValidationError
from agentclaw.community.core.bot_config_manifest.services.config_manifest_service import (
    BotConfigManifestService,
)

from tests.community.core.bot_config_manifest.apply._fakes import (
    FakeStartupScriptService,
    make_context,
)
from tests.community.core.bot_config_manifest.test_config_manifest_service import (
    _FakeRepository,
    _TeclawTest,
)

_ENTITY = "u_owner"
_BOT = "b_1"
_DOCUMENT = (
    "schema_version: 1\n"
    "manifest:\n"
    "  identity: []\n"
    "script:\n"
    "  body: |\n"
    "    echo old\n"
)
_NO_SCRIPT = "schema_version: 1\nmanifest:\n  identity: []\n"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.bot_config_manifest.services.config_manifest_service.get_current_env",
        lambda: "dev",
    )
    monkeypatch.setattr(
        "agentclaw.community.core.bot_config_manifest.services.config_manifest_service.get_current_avernet_tenant",
        lambda: "teamclaw",
    )


def _service(document: str | None):
    repository = _FakeRepository()
    scripts = FakeStartupScriptService()
    service = BotConfigManifestService(repository, lambda: _TeclawTest(), lambda: scripts)
    if document is not None:
        service.put(
            entity_id=_ENTITY, bot_id=_BOT, document=document, modifier="seed",
            active_engine="claude_code", bot_type="personal",
        )
        repository.writes.clear()
    return service, repository, scripts


def _write(service, body):
    return service.write_through_script(
        entity_id=_ENTITY, bot_id=_BOT, body=body, modifier="u_actor",
        active_engine="claude_code", bot_type="personal",
    )


def test_no_manifest_means_none_and_no_write() -> None:
    service, repository, scripts = _service(None)
    assert _write(service, "echo x\n") is None
    assert repository.writes == [] and scripts.writes == 0
    assert service.script_body(entity_id=_ENTITY, bot_id=_BOT) is None


def test_replace_rewrites_the_section_and_the_row() -> None:
    service, repository, scripts = _service(_DOCUMENT)
    result = _write(service, "echo ${BOT_ENV} new\n")
    assert result is not None and result.declares_script
    assert result.record.document == (
        "schema_version: 1\nmanifest:\n  identity: []\nscript:\n  body: |\n    echo ${BOT_ENV} new\n"
    )
    assert result.record.modifier == "u_actor"
    # The row carries the substituted body, as the materialiser would write it.
    assert scripts.puts == [
        {"entity_id": _ENTITY, "bot_id": _BOT, "script": "echo dev new\n", "modifier": "u_actor"}
    ]
    assert service.script_body(entity_id=_ENTITY, bot_id=_BOT) == "echo ${BOT_ENV} new\n"


def test_append_when_the_document_declares_no_script() -> None:
    service, _repository, scripts = _service(_NO_SCRIPT)
    assert service.script_body(entity_id=_ENTITY, bot_id=_BOT) is None
    result = _write(service, "echo added\n")
    assert result is not None
    assert result.record.document == _NO_SCRIPT + "script:\n  body: |\n    echo added\n"
    assert scripts.body == "echo added\n"


def test_remove_drops_the_section_and_clears_the_row() -> None:
    service, _repository, scripts = _service(_DOCUMENT)
    result = _write(service, None)
    assert result is not None and not result.declares_script
    assert result.record.document == _NO_SCRIPT
    assert scripts.deletes == [{"entity_id": _ENTITY, "bot_id": _BOT}] and scripts.body == ""
    assert service.script_body(entity_id=_ENTITY, bot_id=_BOT) is None


def test_the_next_apply_plans_the_script_unchanged() -> None:
    service, _repository, scripts = _service(_DOCUMENT)
    _write(service, "echo ${BOT_ENV}\n")
    materialiser = ScriptMaterialiser(scripts)
    ctx = make_context(engine_type="claude_code", entity_id=_ENTITY)

    async def plan():
        resolved = await materialiser.resolve(ctx, [{"body": "echo ${BOT_ENV}\n"}])
        assert resolved.ok, resolved.failures
        return await materialiser.plan(ctx, resolved.intents)

    planned = asyncio.run(plan())
    assert [e.outcome for e in planned.entries] == ["unchanged"]


def test_a_refusal_leaves_the_document_and_the_row_untouched() -> None:
    service, repository, scripts = _service(_DOCUMENT)
    with pytest.raises(ManifestValidationError):
        service.write_through_script(
            entity_id=_ENTITY, bot_id=_BOT, body="echo x\n", modifier="u_actor",
            active_engine="teclaw", bot_type="personal",  # script is unsupported here
        )
    assert repository.writes == [] and scripts.writes == 0
    assert service.get(entity_id=_ENTITY, bot_id=_BOT).document == _DOCUMENT


def test_a_body_that_outgrows_the_row_after_substitution_is_refused_before_storing(monkeypatch) -> None:
    from agentclaw.community.core.bot_config_manifest.schema import MAX_SCRIPT_BYTES

    monkeypatch.setattr(
        "agentclaw.community.core.bot_config_manifest.services.config_manifest_service.get_current_avernet_tenant",
        lambda: "a-tenant-name-longer-than-its-placeholder",
    )
    service, repository, scripts = _service(_DOCUMENT)
    # Under the cap as written, over it once every ${BOT_TENANT} is substituted.
    body = "${BOT_TENANT}" * (MAX_SCRIPT_BYTES // len("${BOT_TENANT}"))
    assert len(body.encode()) <= MAX_SCRIPT_BYTES
    with pytest.raises(ManifestValidationError) as caught:
        _write(service, body)
    assert caught.value.violations[0].code == "script_too_large"
    assert repository.writes == [] and scripts.writes == 0
    assert service.get(entity_id=_ENTITY, bot_id=_BOT).document == _DOCUMENT
