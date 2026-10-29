"""Draft restore completion fences against the real publication/operation ledger."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.repository.implementations.publishing.publish_operation import (
    OrmPublishOperationRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    DraftRestoreRetryableError,
    PublishFlowServiceError,
)

from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    _real_flow,
    _start_online,
    publication,  # noqa: F401 - shared real SQLite publication fixture
)


@pytest.fixture
def restore_case(request):
    db, repo, source = request.getfixturevalue("publication")
    online = _start_online(repo, source)
    source = repo.compare_and_set_status_with_ext(
        publish_id=source.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext,
        ext=online.ext,
        observed_workflow_id=((online.ext or {}).get("publish") or {}).get("online", 77),
    )
    draft = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=source.id,
        )
    )
    ledger = OrmPublishOperationRepository(db)
    operation = ledger.insert(
        dict(
            publish_id=draft.id,
            operation_kind=str(PublishOperationKind.DRAFT_RESTORE),
            stage="draft",
            attempt=1,
            request_id="restore_review_attempt_1",
            bot_uuid="draft-device",
            operator="owner",
            params=dict(
                source_publish_id=source.id,
                source_version=source.version,
                deadline_at=(datetime.now() + timedelta(minutes=30)).isoformat(),
            ),
        )
    )
    flow, _queue = _real_flow(repo)
    flow._publish_operation_repo = ledger
    flow._bot_service.get_bot.return_value = {
        "binding_id": 11,
        "bot_type": "service",
        "entity_id": "owner",
    }
    # dev baseline resolves the draft-restore source Bot through the internal
    # address lookup; keep the stub JSON-serializable for the operation result.
    flow._bot_service.get_bot_by_address.return_value = {
        "binding_id": 11,
        "bot_type": "service",
        "entity_id": "owner",
    }
    flow._publish_service.get_device_binding_by_id = MagicMock(
        return_value=SimpleNamespace(device_id="draft-device", status="ACTIVE"),
    )
    behavior = MagicMock()
    behavior.validate_draft_restore_artifact.return_value = None
    behavior.draft_restore_uses_workflow = False
    flow.provider_behavior = lambda _bot: behavior
    return SimpleNamespace(
        db=db,
        repo=repo,
        source=source,
        draft=draft,
        ledger=ledger,
        operation=operation,
        flow=flow,
        behavior=behavior,
        final_result={
            "draft_binding_id": 11,
            "status": "success",
            "restore_type": "local",
        },
    )


def _edit_to_a(case):
    with case.db.orm_session() as session:
        for row in session.query(Binding).all():
            row.enabled = row.kb_id == "A"
            row.revision += 1
            row.updated_by = "owner"


def _selection(case):
    with case.db.orm_session() as session:
        return {
            row.kb_id: (row.enabled, row.online_enabled, row.revision, row.updated_by)
            for row in session.query(Binding).all()
        }


@pytest.mark.asyncio
async def test_failed_during_device_restore_does_not_overwrite_later_draft_edit(
    restore_case,
):
    case = restore_case
    edited = None

    async def delayed_device_result(**_kwargs):
        nonlocal edited
        case.ledger.fail(case.operation.id, "restore timed out while awaiting device")
        _edit_to_a(case)
        edited = _selection(case)
        return {"status": "success", "restore_type": "local"}

    case.behavior.restore_draft = delayed_device_result
    with pytest.raises(PublishFlowServiceError) as excinfo:
        await case.flow.execute_restore_draft(
            draft_publish_id=case.draft.id,
            operation_id=case.operation.id,
            operator="owner",
        )
    # Terminal ledger states fail the attempt instead of spinning as retryable.
    assert not isinstance(excinfo.value, DraftRestoreRetryableError)
    assert _selection(case) == edited
    operation = case.ledger.get_by_id(case.operation.id)
    assert operation.state == "failed"
    assert operation.result is None
    assert "knowledge_selection_restore" not in (
        case.repo.get_by_id(case.draft.id).ext or {}
    )


def _complete(case, **overrides):
    arguments = dict(
        draft_publish_id=case.draft.id,
        source_publish_id=case.source.id,
        operation_id=case.operation.id,
        expected_attempt=case.operation.attempt,
        final_result=case.final_result,
    )
    arguments.update(overrides)
    return case.repo.restore_draft_knowledge_selection(**arguments)


def _set_operation(case, **values):
    from agentclaw.community.core.service_bot.repository.models import (
        PublishOperationModel,
    )

    with case.db.orm_session() as session:
        session.query(PublishOperationModel).filter_by(id=case.operation.id).update(
            values
        )


@pytest.mark.parametrize("with_workflow", [False, True])
def test_completion_commits_selection_marker_result_and_native_state(
    restore_case, with_workflow
):
    case = restore_case
    _edit_to_a(case)
    if with_workflow:
        case.ledger.record_workflow(case.operation.id, baas_publish_id=77)
    assert _complete(case)
    selected = _selection(case)
    assert selected["A"][:2] == (False, False)
    assert selected["B"][:2] == (True, True)
    operation = case.ledger.get_by_id(case.operation.id)
    assert operation.state == "completed"
    assert operation.result == case.final_result
    assert case.repo.get_by_id(case.draft.id).ext["knowledge_selection_restore"] == {
        "operation_id": case.operation.id,
        "source_publish_id": case.source.id,
    }


@pytest.mark.parametrize("state", ["failed", "abandoned"])
def test_terminal_attempt_cannot_mutate_any_restore_state(restore_case, state):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    case = restore_case
    _edit_to_a(case)
    _set_operation(case, state=state)
    before = _selection(case)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_RESTORE_OPERATION_TERMINAL"):
        _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).result is None
    assert not case.repo.get_by_id(case.draft.id).ext


@pytest.mark.parametrize(
    "deadline", [None, "", "not-a-date", 10, [], "2000-01-01T00:00:00"]
)
def test_invalid_or_expired_persisted_deadline_never_completes(restore_case, deadline):
    import json

    from agentclaw.community.kernel.knowledge import KnowledgeError

    case = restore_case
    params = {**case.operation.params, "deadline_at": deadline}
    _set_operation(case, params=json.dumps(params))
    _edit_to_a(case)
    before = _selection(case)
    with pytest.raises(
        KnowledgeError, match="KNOWLEDGE_RESTORE_(DEADLINE_INVALID|OPERATION_EXPIRED)"
    ):
        _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"
    assert case.ledger.get_by_id(case.operation.id).result is None
    assert not case.repo.get_by_id(case.draft.id).ext


@pytest.mark.parametrize("attempt", [0, True, 2])
def test_expected_attempt_is_required_and_must_match_locked_ledger(
    restore_case, attempt
):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    case = restore_case
    _edit_to_a(case)
    before = _selection(case)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_RESTORE_OPERATION_MISMATCH"):
        _complete(case, expected_attempt=attempt)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"


@pytest.mark.parametrize(
    "field,value",
    [
        ("publish_id", 999),
        ("operation_kind", "restart"),
        ("stage", "online"),
        ("env", "prod"),
    ],
)
def test_foreign_operation_identity_is_rejected(restore_case, field, value):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    case = restore_case
    _set_operation(case, **{field: value})
    _edit_to_a(case)
    before = _selection(case)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_RESTORE_OPERATION_MISMATCH"):
        _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).result is None


def test_older_attempt_cannot_complete_when_a_new_attempt_exists(restore_case):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    case = restore_case
    case.ledger.insert(
        dict(
            publish_id=case.draft.id,
            operation_kind=str(PublishOperationKind.DRAFT_RESTORE),
            stage="draft",
            attempt=2,
            request_id="restore_review_attempt_2",
            params=case.operation.params,
        )
    )
    _edit_to_a(case)
    before = _selection(case)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_RESTORE_OPERATION_SUPERSEDED"):
        _complete(case)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"


def test_completed_replay_preserves_later_edits_result_and_next_attempt(restore_case):
    case = restore_case
    assert _complete(case)
    original_ext = case.repo.get_by_id(case.draft.id).ext
    _edit_to_a(case)
    before = _selection(case)
    newer = case.ledger.insert(
        dict(
            publish_id=case.draft.id,
            operation_kind=str(PublishOperationKind.DRAFT_RESTORE),
            stage="draft",
            attempt=2,
            request_id="restore_review_attempt_2",
            params=case.operation.params,
        )
    )
    assert _complete(case, final_result={"status": "success", "do_not_overwrite": True})
    assert _selection(case) == before
    assert case.repo.get_by_id(case.draft.id).ext == original_ext
    assert case.ledger.get_by_id(case.operation.id).result == case.final_result
    assert case.ledger.get_by_id(newer.id).state == "pending"


@pytest.mark.asyncio
async def test_native_delivery_success_and_completed_redelivery_keep_durable_result(
    restore_case,
):
    from unittest.mock import AsyncMock

    case = restore_case
    _edit_to_a(case)
    case.behavior.restore_draft = AsyncMock(
        return_value={"status": "success", "restore_type": "local"}
    )
    result = await case.flow.execute_restore_draft(
        draft_publish_id=case.draft.id,
        operation_id=case.operation.id,
        operator="owner",
    )
    assert result == case.final_result
    assert case.ledger.get_by_id(case.operation.id).state == "completed"
    assert _selection(case)["B"][0] is True
    _edit_to_a(case)
    before = _selection(case)
    case.behavior.restore_draft.reset_mock()
    replay = await case.flow.execute_restore_draft(
        draft_publish_id=case.draft.id,
        operation_id=case.operation.id,
        operator="owner",
    )
    assert replay == result
    assert _selection(case) == before
    case.behavior.restore_draft.assert_not_awaited()


@pytest.mark.parametrize(
    "table", ["ac_bot_knowledge_binding", "ac_bot_publish", "ac_publish_operation"]
)
def test_write_failure_rolls_back_every_restore_component(restore_case, table):
    from sqlalchemy import event

    case = restore_case
    _edit_to_a(case)
    before = _selection(case)
    engine = case.db.factory.kw["bind"]

    def fail_write(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith(f"UPDATE {table} "):
            raise RuntimeError("injected restore persistence failure")

    event.listen(engine, "before_cursor_execute", fail_write)
    try:
        with pytest.raises(RuntimeError, match="injected restore persistence failure"):
            _complete(case)
    finally:
        event.remove(engine, "before_cursor_execute", fail_write)
    assert _selection(case) == before
    assert not case.repo.get_by_id(case.draft.id).ext
    operation = case.ledger.get_by_id(case.operation.id)
    assert operation.state == "pending"
    assert operation.result is None


@pytest.mark.parametrize("offset_seconds", [0, -1, 1])
def test_deadline_boundary_uses_persisted_timezone(
    restore_case, monkeypatch, offset_seconds
):
    import json
    from datetime import timezone

    from agentclaw.community.core.repository.implementations.publishing import (
        knowledge_selection,
    )
    from agentclaw.community.kernel.knowledge import KnowledgeError

    case = restore_case
    now = datetime(2026, 10, 26, 19, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    _set_operation(
        case,
        params=json.dumps(
            {
                **case.operation.params,
                "deadline_at": (now + timedelta(seconds=offset_seconds)).isoformat(),
            }
        ),
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(knowledge_selection, "datetime", Clock)
    _edit_to_a(case)
    before = _selection(case)
    if offset_seconds <= 0:
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_RESTORE_OPERATION_EXPIRED"):
            _complete(case)
        assert _selection(case) == before
        assert case.ledger.get_by_id(case.operation.id).state == "pending"
    else:
        assert _complete(case)
        assert case.ledger.get_by_id(case.operation.id).state == "completed"


@pytest.mark.asyncio
async def test_expired_while_device_waits_has_no_completion_side_effects(restore_case):
    import json

    case = restore_case
    _edit_to_a(case)
    before = _selection(case)

    async def delayed_device_result(**_kwargs):
        _set_operation(
            case,
            params=json.dumps(
                {
                    **case.operation.params,
                    "deadline_at": "2000-01-01T00:00:00+08:00",
                }
            ),
        )
        return {"status": "success", "restore_type": "local"}

    case.behavior.restore_draft = delayed_device_result
    with pytest.raises(
        PublishFlowServiceError, match="KNOWLEDGE_RESTORE_OPERATION_EXPIRED"
    ) as excinfo:
        await case.flow.execute_restore_draft(
            draft_publish_id=case.draft.id,
            operation_id=case.operation.id,
            operator="owner",
        )
    # A deterministic deadline failure terminates the attempt immediately.
    assert not isinstance(excinfo.value, DraftRestoreRetryableError)
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "failed"
    assert case.ledger.get_by_id(case.operation.id).result is None
    assert not case.repo.get_by_id(case.draft.id).ext


def test_missing_ledger_never_uses_legacy_restore_bypass(restore_case):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    case = restore_case
    _edit_to_a(case)
    before = _selection(case)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_RESTORE_OPERATION_MISMATCH"):
        _complete(case, operation_id=999)
    assert _selection(case) == before
    assert not case.repo.get_by_id(case.draft.id).ext


def test_deadline_is_rechecked_after_binding_lock_wait(restore_case, monkeypatch):
    from agentclaw.community.core.repository.implementations.publishing import (
        knowledge_selection,
    )
    from agentclaw.community.kernel.knowledge import KnowledgeError
    from sqlalchemy import event

    case = restore_case
    _edit_to_a(case)
    before = _selection(case)
    deadline = datetime.fromisoformat(case.operation.params["deadline_at"])
    clock = {"now": deadline - timedelta(seconds=1), "waited": False}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(knowledge_selection, "datetime", Clock)
    engine = case.db.factory.kw["bind"]

    def expire_while_loading_bindings(
        _connection, _cursor, statement, _parameters, _context, _many
    ):
        if (
            statement.startswith("SELECT ")
            and "FROM ac_bot_knowledge_binding" in statement
        ):
            clock["now"] = deadline
            clock["waited"] = True

    event.listen(engine, "before_cursor_execute", expire_while_loading_bindings)
    try:
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_RESTORE_OPERATION_EXPIRED"):
            _complete(case)
    finally:
        event.remove(engine, "before_cursor_execute", expire_while_loading_bindings)
    assert clock["waited"]
    assert _selection(case) == before
    assert case.ledger.get_by_id(case.operation.id).state == "pending"
    assert case.ledger.get_by_id(case.operation.id).result is None
    assert not case.repo.get_by_id(case.draft.id).ext


@pytest.mark.parametrize("missing", ["snapshot", "binding"])
def test_unknown_history_does_not_reject_native_restore_completion(restore_case, missing):
    import json
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel

    case = restore_case
    _edit_to_a(case)
    with case.db.orm_session() as session:
        if missing == "binding":
            session.query(Binding).filter_by(kb_id="B").delete()
        else:
            ext = dict(case.source.ext)
            ext.pop("knowledge_selection_snapshot")
            session.query(BotPublishModel).filter_by(id=case.source.id).update({"ext": json.dumps(ext)})
    before = _selection(case)
    assert _complete(case)
    assert case.ledger.get_by_id(case.operation.id).state == "completed"
    assert _selection(case) == before
    marker = case.repo.get_by_id(case.draft.id).ext["knowledge_selection_restore"]
    assert marker["knowledge"]["status"] == "unknown"
