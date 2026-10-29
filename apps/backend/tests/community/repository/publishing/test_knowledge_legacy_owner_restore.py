"""Legacy publication Owner metadata is not native source identity or KB trust."""

from unittest.mock import AsyncMock, Mock

import pytest
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.kernel.knowledge import KnowledgeError, KnowledgePublicationPolicy
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    publication,  # noqa: F401 - shared SQLite fixture
)
from tests.community.repository.publishing.test_knowledge_selection_restore import (
    _complete,
    _edit_to_a,
    _selection,
    restore_case as restore_case_fixture,  # noqa: F401 - shared real flow fixture
)


@pytest.fixture
def restore_case(request):
    return request.getfixturevalue("restore_case_fixture")


def _use_native_bot_lookup(case):
    service = BotService.__new__(BotService)
    service._repository = BotRepository(case.db)
    service._template_service = Mock()
    service._template_service.get_template.return_value = None
    service._device_service_provider = lambda: Mock(get_device=lambda **kwargs: None)
    case.flow._bot_service = service
    case.flow._publish_service._bot_service = service
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_native_pk_legacy_owner_restore_succeeds_without_knowledge_writes(
    restore_case, enabled,
):
    case = restore_case
    with case.db.orm_session() as session:
        bot = session.get(BotModel, 1)
        bot.entity_id = "staff_owner"
        bot.owner_id = "new-owner"
        bot.binding_id = 11
    service = _use_native_bot_lookup(case)
    resolved = BotAddressResolver(service._repository).resolve_publish_source(
        source_bot_pk=1, source_bot_id="service", legacy_owner_id="owner",
    )
    assert (resolved.bot_pk, resolved.entity_id) == (1, "staff_owner")
    actual = service.get_bot_by_address("service", "owner", source_bot_pk=1)
    assert (actual["entity_id"], actual["owner_id"]) == ("staff_owner", "new-owner")
    case.repo._knowledge_policy = KnowledgePublicationPolicy(enabled=enabled)
    case.behavior.restore_draft = AsyncMock(return_value=case.final_result)
    _edit_to_a(case)
    before = _selection(case)

    result = await case.flow.execute_restore_draft(
        draft_publish_id=case.draft.id, operation_id=case.operation.id,
        operator="new-owner",
    )

    assert result == case.final_result
    case.behavior.restore_draft.assert_awaited_once()
    operation = case.ledger.get_by_id(case.operation.id)
    assert (operation.state, operation.result) == ("completed", case.final_result)
    assert _selection(case) == before
    draft = case.repo.get_by_id(case.draft.id)
    if enabled:
        marker = draft.ext["knowledge_selection_restore"]
        assert marker["knowledge"] == {
            "status": "unknown", "reason": "KNOWLEDGE_PUBLISH_SOURCE_MISMATCH",
        }
    else:
        assert not draft.ext
    # Duplicate delivery cannot replay a completed restoration over newer edits.
    _edit_to_a(case)
    edited = _selection(case)
    assert await case.flow.execute_restore_draft(
        draft_publish_id=case.draft.id, operation_id=case.operation.id,
        operator="new-owner",
    ) == result
    assert _selection(case) == edited
    case.behavior.restore_draft.assert_awaited_once()


@pytest.mark.parametrize("legacy_row", ["draft", "previous", "both"])
def test_legacy_entity_metadata_never_trusts_even_a_current_scope_snapshot(
    restore_case, legacy_row,
):
    case = restore_case
    # Keep a valid, current-scope snapshot: publication metadata alone must be
    # enough to refuse KB application, rather than relying on snapshot corruption.
    with case.db.orm_session() as session:
        publish_ids = {"draft": [case.draft.id], "previous": [case.source.id],
                       "both": [case.draft.id, case.source.id]}[legacy_row]
        for publish_id in publish_ids:
            session.get(BotPublishModel, publish_id).owner_id = "legacy-owner"
    _edit_to_a(case)
    before = _selection(case)
    assert _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "completed"
    marker = case.repo.get_by_id(case.draft.id).ext["knowledge_selection_restore"]
    assert marker["knowledge"]["status"] == "unknown"
    # Direct repeated completion must also preserve later edits.
    _edit_to_a(case)
    edited = _selection(case)
    assert _complete(case)
    assert _selection(case) == edited


@pytest.mark.parametrize("field,value", [
    ("source_bot_pk", 999), ("source_bot_id", "foreign"), ("env", "prod"),
])
def test_native_restore_still_rejects_non_owner_source_mismatch(
    restore_case, field, value,
):
    case = restore_case
    with case.db.orm_session() as session:
        setattr(session.get(BotPublishModel, case.draft.id), field, value)
    before = _selection(case)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_PUBLISH_SOURCE_MISMATCH"):
        _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"
    assert not case.repo.get_by_id(case.draft.id).ext


@pytest.mark.parametrize("field,value", [
    ("bot_type", "personal"), ("is_delete", 1), ("avernet_tenant", "foreign"),
])
def test_legacy_owner_does_not_bypass_live_source_scope(restore_case, field, value):
    case = restore_case
    with case.db.orm_session() as session:
        bot = session.get(BotModel, 1)
        bot.entity_id = "staff_owner"
        setattr(bot, field, value)
    before = _selection(case)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_PUBLISH_SOURCE_MISMATCH"):
        _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"


def test_foreign_tenant_cannot_complete_legacy_restore(restore_case):
    case = restore_case
    before = _selection(case)
    with avernet_tenant_scope("foreign"):
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_PUBLISH_SOURCE_MISMATCH"):
            _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"


def test_legacy_owner_does_not_relax_build_snapshot_trust(request):
    db, repo, draft = request.getfixturevalue("publication")
    with db.orm_session() as session:
        session.get(BotModel, 1).entity_id = "staff_owner"
    result = repo.update_status(draft.id, "building", "draft")
    assert result.ext["knowledge_selection_snapshot"]["status"] == "unknown"
    assert result.ext["knowledge_selection_snapshot"]["reason"] == (
        "KNOWLEDGE_PUBLISH_SOURCE_MISMATCH"
    )


def test_legacy_owner_does_not_relax_online_completion_trust(request):
    from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
        _online, _start_online,
    )

    db, repo, draft = request.getfixturevalue("publication")
    online = _start_online(repo, draft)
    with db.orm_session() as session:
        session.get(BotModel, 1).entity_id = "staff_owner"
    before = _online(db)
    result = repo.compare_and_set_status_with_ext(
        publish_id=draft.id, source_status="online_pub", target_status="success",
        expected_ext=online.ext, ext=online.ext, observed_workflow_id=77,
    )
    assert result.ext["knowledge_selection_result"]["status"] == "unknown"
    assert _online(db) == before


@pytest.mark.parametrize("guard", ["deadline", "attempt", "source_version"])
def test_legacy_unknown_does_not_bypass_restore_guards(restore_case, guard):
    import json
    from tests.community.repository.publishing.test_knowledge_selection_restore import (
        _set_operation,
    )

    case = restore_case
    with case.db.orm_session() as session:
        session.get(BotModel, 1).entity_id = "staff_owner"
    overrides = {}
    if guard == "deadline":
        _set_operation(case, params=json.dumps({
            **case.operation.params, "deadline_at": "2000-01-01T00:00:00",
        }))
    elif guard == "attempt":
        overrides["expected_attempt"] = 2
    else:
        _set_operation(case, params=json.dumps({
            **case.operation.params, "source_version": 999,
        }))
    before = _selection(case)
    with pytest.raises(KnowledgeError):
        _complete(case, **overrides)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"
    assert not case.repo.get_by_id(case.draft.id).ext
