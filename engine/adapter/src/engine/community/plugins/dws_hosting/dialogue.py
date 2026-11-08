"""Freeze context and its incremental sync ledger in the dispatch transaction."""

import json
from engine.community.kernel.dws_dialogue import DialogueMessage
from engine.community.kernel.dws_execution import AgentInvocation


def with_dialogue(db, turn, request: AgentInvocation) -> AgentInvocation:
    snapshot = request.dialogue_snapshot
    if snapshot is None:
        return request
    previous = db.execute(
        """SELECT e.invocation_json, e.state, e.turn_id, o.state AS delivery_state,
        o.delivery_json, o.updated_ms FROM executions e
        JOIN batches b ON b.batch_id=e.turn_id
        LEFT JOIN outbox o ON o.turn_id=e.turn_id AND o.binding_id=e.binding_id
        WHERE e.binding_id=? AND b.conversation_id=? AND e.turn_id!=?
        AND b.first_ms<=? ORDER BY b.first_ms, b.rowid""",
        (
            turn.binding.binding_id,
            turn.conversation_id,
            turn.turn_id,
            snapshot.captured_at_ms,
        ),
    ).fetchall()
    seen, own_turns, statuses = set(), set(), []
    # A later invocation may already have synced the status of an earlier run.
    # Gather the whole session ledger before deciding which statuses are new.
    for row in previous:
        invocation = AgentInvocation.model_validate_json(row["invocation_json"])
        if invocation.session_key == request.session_key:
            seen.update(invocation.dialogue_sync_keys)
    facts = {m.key: m for m in snapshot.messages}
    for row in previous:
        invocation = AgentInvocation.model_validate_json(row["invocation_json"])
        same_session = invocation.session_key == request.session_key
        if same_session:
            own_turns.add(row["turn_id"])
            # Reservation is evidence of an attempted sync, NOT proof of delivery.
            # Unknown runs are never redispatched. Their facts must not be re-injected blindly.
            seen.update(invocation.dialogue_sync_keys)
            data = json.loads(invocation.message)
            seen.update(
                "message:" + m["message_id"] for m in data.get("current_batch", [])
            )
        delivery = json.loads(row["delivery_json"]) if row["delivery_json"] else None
        sent = bool(
            delivery
            and delivery["purpose"] == "reply"
            and row["delivery_state"] == "sent"
        )
        if (
            sent
            and snapshot.captured_at_ms - 86_400_000
            <= row["updated_ms"]
            <= snapshot.captured_at_ms
        ):
            facts["reply:" + row["turn_id"]] = DialogueMessage(
                key="reply:" + row["turn_id"],
                role="assistant_sent",
                text=delivery["text"][:2000],
                created_at_ms=row["updated_ms"],
                truncated=len(delivery["text"]) > 2000,
            )
        if same_session:
            if sent:
                status = "sent"
            elif (
                delivery
                and delivery["purpose"] == "reply"
                and row["delivery_state"] not in ("cancelled", "manual")
            ):
                status = "unconfirmed"
            else:
                status = (
                    "not_sent"
                    if row["state"] in ("manual", "cancelled", "resolved")
                    else "unconfirmed"
                )
            key = "status:" + row["turn_id"] + ":" + status
            if key not in seen:
                statuses.append(
                    {
                        "key": key,
                        "run_id": invocation.run_id,
                        "status": status,
                        "source_message_ids": [
                            m["message_id"]
                            for m in json.loads(invocation.message).get(
                                "current_batch", []
                            )
                        ],
                    }
                )
    ordered = sorted(facts.values(), key=lambda m: (m.created_at_ms, m.key))[-40:]
    snapshot = snapshot.model_copy(update={"messages": tuple(ordered)})
    current_keys = {"message:" + m.message_id for m in turn.messages}
    delta = [
        m
        for m in ordered
        if m.key not in seen
        and m.key not in current_keys
        and not (
            m.role == "assistant_sent" and m.key.removeprefix("reply:") in own_turns
        )
    ]
    data = json.loads(request.message)
    data["conversation_updates"] = [m.model_dump() for m in delta]
    # A small current view survives session compaction without replaying the
    # whole history as new facts. Keep the incremental ledger unchanged.
    data["latest_exchange"] = [
        m.model_dump() | {"current_batch": m.key in current_keys}
        for m in ordered[-6:]
    ]
    data["owner_statements"] = [m.model_dump() for m in ordered if m.role == "owner"][-6:]
    data["delivery_updates"] = statuses[-40:]
    data["context_window"] = {"limited": True, "hours": 24, "max_messages": 40}
    return AgentInvocation.model_validate(
        request.model_dump()
        | {
            "message": json.dumps(data, ensure_ascii=False),
            "dialogue_snapshot": snapshot,
            "dialogue_sync_keys": tuple(
                sorted(
                    current_keys
                    | {m.key for m in delta}
                    | {s["key"] for s in statuses[-40:]}
                )
            ),
        }
    )
