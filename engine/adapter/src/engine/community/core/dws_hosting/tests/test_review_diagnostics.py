"""Internal review audit and out-of-scope suppression, with no real model or sends."""

import json
import sqlite3

import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.core.dws_hosting.reply_review import review_reply
from engine.community.kernel.dws_execution import ExecutionError
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.executions import SqliteHostingExecutionStore
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from .test_context import Directory, context_provider, setup_turn
from .test_execution import build_execution, decision_json, result_json
from .test_reply_review import Reviewer, candidate, hi_request


def verdict(
    code="off_topic",
    reason="Candidate introduces an unsupported plan.",
    action="reject",
):
    return {"verdict": action, "reason_code": code, "reason": reason}


async def test_rejection_audit_is_durable_but_never_in_notice_or_projection(tmp_path):
    state, ledger, turn, path = setup_turn(tmp_path)
    diagnosis = verdict(
        reason="Private diagnostic: candidate invents a departure plan."
    )
    model = Reviewer(result=result_json(json.dumps(diagnosis)))
    worker = HostingExecutor(state, ledger, model, context_provider(Directory()))
    record = await worker.execute("binding", turn.turn_id)
    assert record.reason == "reply_review_rejected" and record.state == "manual"
    assert record.review.model_dump() == diagnosis
    assert "review" not in record.model_dump()
    assert diagnosis["reason"] not in repr(record)
    restored = SqliteHostingExecutionStore(path).get("binding", turn.turn_id)
    assert restored.review == record.review
    queued = SqliteHostingOutbox(path).enqueue("binding", turn.turn_id, now_ms=10000)
    assert queued.delivery.purpose == "owner_notice"
    assert diagnosis["reason"] not in queued.delivery.text
    assert diagnosis["reason_code"] not in queued.delivery.text
    assert "未通过内容校验" in queued.delivery.text
    await worker.resume("binding", turn.turn_id)
    assert len(model.reviews) == 1
    # A late update cannot overwrite the original diagnosis.
    assert ledger.require_manual("binding", turn.turn_id, "late", ended=True) == record


@pytest.mark.parametrize("proposed_action", ["reply", "escalate"])
async def test_only_named_contact_rule_never_notifies_for_outside_scope(
    tmp_path, proposed_action
):
    rule = "只托管郭欣发给我的消息"
    state, ledger, turn, path = setup_turn(
        tmp_path, rule, sender="other-peer", name="另一位同事"
    )
    directory = Directory()
    directory.rows[("contact", "user", "search", "--query", "另一位同事")] = [
        {"openDingTalkId": "other-peer", "name": "另一位同事"}
    ]
    model = Reviewer(
        text=decision_json(proposed_action),
        result=result_json(
            json.dumps(verdict("outside_rule", "Sender is not the named contact."))
        ),
    )
    record = await HostingExecutor(
        state, ledger, model, context_provider(directory)
    ).execute("binding", turn.turn_id)
    assert record.state == "resolved" and record.decision.action == "ignore"
    assert record.review.reason_code == "outside_rule"
    assert record.invocation.hosting_context.sender.names == ("另一位同事",)
    assert len(model.calls) == len(model.reviews) == 1
    data = json.loads(model.reviews[0].message.split("\nINPUT_JSON:\n", 1)[1])
    assert data["confirmed_rules"]["rules"] == rule
    assert data["directory_facts"]["sender"]["names"] == ["另一位同事"]
    assert data["candidate_action"] == proposed_action
    assert ("candidate_summary" in data) == (proposed_action == "escalate")
    assert (
        SqliteHostingOutbox(path).enqueue("binding", turn.turn_id, now_ms=10000) is None
    )
    with sqlite3.connect(path / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
        assert db.execute("SELECT state FROM batches").fetchone()[0] == "completed"


@pytest.mark.parametrize("action", ["reply", "escalate"])
async def test_in_scope_approval_preserves_original_action_and_text(tmp_path, action):
    state, ledger, turn, path = setup_turn(tmp_path)
    diagnosis = verdict("approved", "In scope and supported.", "approve")
    model = Reviewer(
        text=decision_json(action), result=result_json(json.dumps(diagnosis))
    )
    record = await HostingExecutor(
        state, ledger, model, context_provider(Directory())
    ).execute("binding", turn.turn_id)
    assert record.decision.model_dump(exclude_unset=True) == json.loads(
        decision_json(action)
    )
    assert record.review.model_dump() == diagnosis
    queued = SqliteHostingOutbox(path).enqueue("binding", turn.turn_id, now_ms=10000)
    assert queued.delivery.purpose == ("reply" if action == "reply" else "owner_notice")
    assert diagnosis["reason"] not in queued.delivery.text


@pytest.mark.parametrize(
    "code",
    [
        "identity_uncertain",
        "context_insufficient",
        "unsupported_fact",
        "off_topic",
        "other",
    ],
)
async def test_uncertainty_is_manual_not_silently_treated_as_scope_exclusion(
    tmp_path, code
):
    state, ledger, turn, _ = build_execution(tmp_path)
    model = Reviewer(result=result_json(json.dumps(verdict(code))))
    record = await HostingExecutor(state, ledger, model).execute(
        "binding", turn.turn_id
    )
    assert record.state == "manual" and record.decision is None
    assert record.review.reason_code == code


@pytest.mark.parametrize(
    "payload",
    [
        verdict(action="approve"),
        verdict("outside_rule", action="skip_owner_replied"),
        verdict("approved"),
        verdict("unexpected"),
        verdict(reason=""),
        verdict(reason=" " * 10),
        verdict(reason=" " * 300),
        verdict(reason="x" * 241 + "\x00"),
        verdict(reason="x" * 241 + "\n"),
        verdict(reason="x" * 241 + "\u2028"),
        verdict(reason="hidden\x00data"),
        verdict(reason="line\nline"),
        verdict(reason="line\u2028line"),
        verdict(reason={"private": "data"}),
        verdict() | {"text": "replacement"},
        {"verdict": "reject", "reason_code": "outside_rule"},
        {"verdict": "reject", "reason": "Outside scope"},
    ],
)
async def test_malformed_diagnostic_cannot_approve_or_silently_skip(tmp_path, payload):
    with pytest.raises(ExecutionError, match="reply_review_unavailable"):
        await review_reply(
            Reviewer(result=result_json(json.dumps(payload))),
            hi_request(tmp_path),
            candidate(),
        )


async def test_legacy_rejection_is_unspecified_not_invented_scope_reason(tmp_path):
    state, ledger, turn, _ = build_execution(tmp_path)
    record = await HostingExecutor(
        state, ledger, Reviewer(result=result_json('{"verdict":"reject"}'))
    ).execute("binding", turn.turn_id)
    assert record.state == "manual"
    assert record.review.reason_code == "unspecified"


@pytest.mark.parametrize(
    "action,code,state,decision",
    [
        ("approve", "approved", "resolved", "reply"),
        ("reject", "off_topic", "manual", None),
        ("reject", "outside_rule", "resolved", "ignore"),
        ("reject", "identity_uncertain", "manual", None),
        ("skip_owner_replied", "owner_replied", "manual", None),
    ],
)
async def test_verbose_reason_preserves_verdict_and_safety(
    tmp_path, action, code, state, decision
):
    store, ledger, turn, path = build_execution(tmp_path)
    reason = "Diagnostic explanation. " * 20
    model = Reviewer(result=result_json(json.dumps(verdict(code, reason, action))))
    record = await HostingExecutor(store, ledger, model).execute(
        "binding", turn.turn_id
    )
    assert record.state == state
    assert (record.decision.action if record.decision else None) == decision
    assert record.review.verdict == action
    assert record.review.reason_code == code
    assert record.review.reason == reason[:237] + "..."
    assert len(record.review.reason) == 240
    assert (
        SqliteHostingExecutionStore(path).get("binding", turn.turn_id).review
        == record.review
    )
    assert len(model.reviews) == 1  # No second model call just to shorten prose.


@pytest.mark.parametrize("size", [1, 239, 240, 241, 364])
async def test_reason_length_boundary_does_not_change_reply(tmp_path, size):
    diagnosis = verdict("approved", "确" * size, "approve")
    proposed = candidate()
    result = await review_reply(
        Reviewer(result=result_json(json.dumps(diagnosis))),
        hi_request(tmp_path),
        proposed,
    )
    assert result.decision is proposed
    assert result.review.reason == ("确" * size if size <= 240 else "确" * 237 + "...")


@pytest.mark.parametrize(
    "payload",
    [
        verdict("outside_rule", "x" * 364, "approve"),
        verdict("unknown", "x" * 364, "reject"),
        verdict("approved", "x" * 364, "approve") | {"text": "replacement"},
    ],
)
async def test_truncating_prose_never_repairs_invalid_control_fields(tmp_path, payload):
    with pytest.raises(ExecutionError, match="reply_review_unavailable"):
        await review_reply(
            Reviewer(result=result_json(json.dumps(payload))),
            hi_request(tmp_path),
            candidate(),
        )


@pytest.mark.parametrize("stage", ["engine", "result_parse", "diagnostic_schema"])
async def test_unavailable_logs_only_safe_failure_stage(tmp_path, caplog, stage):
    private = "PRIVATE-model-response"
    model = Reviewer(
        failure=ExecutionError(private) if stage == "engine" else None,
        result=result_json(
            private if stage == "result_parse" else json.dumps({"verdict": private})
        ),
    )
    with pytest.raises(ExecutionError, match="reply_review_unavailable"):
        await review_reply(model, hi_request(tmp_path), candidate())
    assert "stage=" + stage in caplog.text
    assert private not in caplog.text


async def test_v6_upgrade_preserves_existing_records_and_does_not_enable_or_replay(
    tmp_path,
):
    state, ledger, turn, path = build_execution(tmp_path)
    worker = HostingExecutor(
        state, ledger, Reviewer(result=result_json('{"verdict":"reject"}'))
    )
    old = await worker.execute("binding", turn.turn_id)
    state.set_enabled("binding", False, now_ms=10000)
    with sqlite3.connect(path / "dws_hosting.sqlite3") as db:
        db.execute("ALTER TABLE executions DROP COLUMN review_json")
        db.execute("PRAGMA user_version = 6")
        controls = db.execute(
            "SELECT enabled,epoch,rule_version,rule_text FROM bindings"
        ).fetchall()
    restored = SqliteHostingExecutionStore(path).get("binding", turn.turn_id)
    assert restored.model_dump() == old.model_dump()
    assert restored.review is None  # No backfilled reason for historical rejections.
    with sqlite3.connect(path / "dws_hosting.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert (
            db.execute(
                "SELECT enabled,epoch,rule_version,rule_text FROM bindings"
            ).fetchall()
            == controls
        )
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    HostingDatabase(path)  # Idempotent reopen.


async def test_review_write_failure_cannot_release_or_enqueue(tmp_path):
    state, ledger, turn, path = build_execution(tmp_path)
    with sqlite3.connect(path / "dws_hosting.sqlite3") as db:
        db.execute(
            "CREATE TRIGGER deny_review BEFORE UPDATE OF review_json ON executions BEGIN SELECT RAISE(ABORT, 'full'); END"
        )
    worker = HostingExecutor(
        state, ledger, Reviewer(result=result_json(json.dumps(verdict())))
    )
    with pytest.raises(sqlite3.IntegrityError):
        await worker.execute("binding", turn.turn_id)
    record = ledger.get("binding", turn.turn_id)
    assert record.state == "received" and record.review is None
    assert (
        SqliteHostingOutbox(path).enqueue("binding", turn.turn_id, now_ms=10000) is None
    )


async def test_revoked_control_is_not_restored_by_outside_rule_skip(tmp_path):
    state, ledger, turn, path = build_execution(tmp_path)

    class RevokingReviewer(Reviewer):
        async def review_reply(self, request):
            state.set_enabled("binding", False, now_ms=10000)
            return await super().review_reply(request)

    model = RevokingReviewer(result=result_json(json.dumps(verdict("outside_rule"))))
    record = await HostingExecutor(state, ledger, model).execute(
        "binding", turn.turn_id
    )
    assert record.state == "cancelled" and record.decision is None
    assert (
        SqliteHostingOutbox(path).enqueue("binding", turn.turn_id, now_ms=10000) is None
    )


async def test_explicit_owner_replied_diagnostic_is_persisted_with_silent_skip(
    tmp_path, monkeypatch
):
    state, ledger, turn, path = build_execution(tmp_path)
    recent = (
        {
            "role": "owner",
            "text": "Already answered.",
            "created_at_ms": turn.messages[-1].created_at_ms + 1,
        },
    )
    monkeypatch.setattr(type(ledger), "review_context", lambda *args: recent)
    model = Reviewer(
        result=result_json(
            json.dumps(
                verdict(
                    "owner_replied",
                    "Owner has answered this batch.",
                    "skip_owner_replied",
                )
            )
        )
    )
    record = await HostingExecutor(state, ledger, model).execute(
        "binding", turn.turn_id
    )
    assert record.decision.action == "ignore"
    assert record.review.reason_code == "owner_replied"
    assert (
        SqliteHostingOutbox(path).enqueue("binding", turn.turn_id, now_ms=10000) is None
    )


def test_failed_review_migration_keeps_old_version(tmp_path):
    _, _, _, path = build_execution(tmp_path)
    with sqlite3.connect(path / "dws_hosting.sqlite3") as db:
        # A corrupt v6 fixture already has the destination column.
        db.execute("PRAGMA user_version = 6")
    with pytest.raises(sqlite3.OperationalError):
        HostingDatabase(path)
    with sqlite3.connect(path / "dws_hosting.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
