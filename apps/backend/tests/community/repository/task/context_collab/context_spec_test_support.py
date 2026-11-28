"""Direct helpers for tests that mutate the real Collet context_spec envelope."""

import json


def stored_context_value(row, key: str):
    data = json.loads(row.context_spec)
    return data[key]


def store_context_value(row, key: str, value) -> None:
    data = json.loads(row.context_spec)
    data[key] = value
    row.context_spec = json.dumps(data)


def stored_event_subject(row):
    """Return the persisted Event Work-subject tuple from payload storage."""
    from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.event_payload_codec import (
        TASK_SUBJECT_KEY,
    )

    payload = json.loads(row.payload) if row.payload is not None else {}
    subject = payload.pop(TASK_SUBJECT_KEY, None) or {}
    return subject.get("task_ref"), subject.get("task_version_after")


def store_event_subject(row, task_ref=None, task_version_after=None) -> None:
    """Write the reserved Event Work-subject envelope in payload storage."""
    from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.event_payload_codec import (
        TASK_SUBJECT_KEY,
    )

    payload = json.loads(row.payload) if row.payload is not None else {}
    if task_ref is None and task_version_after is None:
        payload.pop(TASK_SUBJECT_KEY, None)
    else:
        payload[TASK_SUBJECT_KEY] = {
            "task_ref": task_ref,
            "task_version_after": task_version_after,
        }
    row.payload = json.dumps(payload)
