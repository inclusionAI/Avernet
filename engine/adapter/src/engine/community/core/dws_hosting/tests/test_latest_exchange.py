"""Context/guard regressions; scripted verdicts do not measure model accuracy."""

import json
import sqlite3

import pytest

from engine.community.core.dws_hosting.dialogue import HostingDialogue
from engine.community.core.dws_hosting.execution import HostingExecutor
from .test_context import VerifiedContextFixture
from .test_dialogue import History
from .test_execution import Engine, build_execution, decision_json, result_json
from .test_state_contract import message


@pytest.mark.parametrize("action", ["reply", "ignore"])
async def test_short_confirmation_keeps_roles_and_nearest_context(tmp_path, action):
    state, ledger, turn, directory = build_execution(tmp_path, text="对呢~")
    older = tuple(
        message(f"old-{i}", sender="self", text="较早的话题", created=100 + i)
        for i in range(12)
    )
    question = message("question", sender="self", text="吃了饭过来吗？", created=900)
    engine = Engine(text=decision_json(action))
    record = await HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(
            History((*older, question, *turn.messages)), lambda: 6000
        ),
    ).execute("binding", turn.turn_id)
    body = json.loads(engine.calls[0].message)
    assert len(body["latest_exchange"]) == 6
    assert [
        (m["role"], m["text"], m["current_batch"]) for m in body["latest_exchange"][-2:]
    ] == [
        ("owner", "吃了饭过来吗？", False),
        ("peer", "对呢~", True),
    ]
    assert len(record.invocation.dialogue_snapshot.messages) == 14
    assert len(body["conversation_updates"]) == 13
    assert len(body["current_batch"]) == 1
    assert len(body["owner_statements"]) == 6
    assert all(m["role"] == "owner" for m in body["owner_statements"])
    assert body["owner_statements"][-1]["text"] == "吃了饭过来吗？"
    if action == "reply":
        data = json.loads(engine.reviews[0].message.split("\nINPUT_JSON:\n")[1])
        assert [m["text"] for m in data["latest_exchange"][-2:]] == [
            "吃了饭过来吗？",
            "对呢~",
        ]
        assert len(data["dialogue"]) == 14
        assert data["owner_statements"][-1]["text"] == "吃了饭过来吗？"
    else:
        assert not engine.reviews
        assert record.decision.action == "ignore"
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


async def test_unrelated_candidate_remains_rejected(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path, text="对呢~")

    class Reject(Engine):
        async def review_reply(self, request):
            self.reviews.append(request)
            return result_json('{"verdict":"reject"}')

    engine = Reject(
        text=json.dumps(
            {
                "schema_version": 1,
                "rule_version": 1,
                "action": "reply",
                "text": "你们在忙啥呢？",
            }
        )
    )
    record = await HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(
            History(
                (
                    message(
                        "question", sender="self", text="吃了饭过来吗？", created=900
                    ),
                    *turn.messages,
                )
            ),
            lambda: 6000,
        ),
    ).execute("binding", turn.turn_id)
    assert record.state == "manual" and record.reason == "reply_review_rejected"
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
