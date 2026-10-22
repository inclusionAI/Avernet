import json
import sqlite3

import pytest

from engine.community.core.dws_hosting.reply_review import review_reply
from engine.community.core.dws_hosting.execution import invocation_for
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from .test_delivery import resolve
from .test_execution import build_execution, Engine
from .test_reply_review import candidate
from .test_state_contract import message


@pytest.mark.parametrize(
    "excluded",
    [
        None,
        "awaiting",
        "unknown",
        "accepted",
        "owner_notice",
        "epoch",
        "conversation",
        "future",
    ],
)
def test_only_confirmed_prior_same_scope_replies_are_context(tmp_path, excluded):
    state, ledger, turn, directory = build_execution(tmp_path, text="签到又改了")
    resolve(ledger, turn)
    outbox = SqliteHostingOutbox(directory)
    delivery = outbox.enqueue("binding", turn.turn_id, now_ms=6000)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute(
            "UPDATE batches SET state='completed' WHERE batch_id=?", (turn.turn_id,)
        )
        db.execute("UPDATE outbox SET state='sent' WHERE turn_id=?", (turn.turn_id,))
        if excluded in ("awaiting", "unknown", "accepted"):
            db.execute("UPDATE outbox SET state=?", (excluded,))
        elif excluded == "owner_notice":
            db.execute(
                "UPDATE outbox SET delivery_json=json_set(delivery_json,'$.purpose','owner_notice')"
            )
        elif excluded == "epoch":
            db.execute("UPDATE batches SET binding_epoch=binding_epoch-1")
        elif excluded == "conversation":
            db.execute("UPDATE batches SET conversation_id='other'")
        elif excluded == "future":
            db.execute("UPDATE batches SET first_ms=999999")
    state.ingest(
        "binding", message("followup", text="对呢对呢", created=7000), now_ms=7000
    )
    current = state.claim_ready("binding", now_ms=12000)
    history = ledger.review_context("binding", current.turn_id)
    if excluded:
        assert history == ()
    else:
        assert history == (
            {"role": "peer", "text": "签到又改了"},
            {"role": "assistant_sent", "text": delivery.delivery.text},
        )


async def test_followup_review_receives_context_without_replaying_original(tmp_path):
    turn = build_execution(tmp_path, text="对呢对呢")[2]
    engine = Engine()
    history = (
        {"role": "peer", "text": "现在的签到真是牛逼"},
        {"role": "assistant_sent", "text": "又整活了？咋了这回"},
    )
    await review_reply(
        engine, invocation_for(turn), candidate("说说，是又加了什么新花样"), history
    )
    data = json.loads(engine.reviews[0].message.split("\nINPUT_JSON:\n")[1])
    assert data["dialogue"][:2] == list(history)
    assert data["dialogue"][-1]["current_batch"] is True
    assert not engine.calls
