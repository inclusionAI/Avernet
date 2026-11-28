"""The physical Event schema stores Work facts in a reserved payload envelope."""

import json

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.event_payload_codec import (
    EventTaskSubject,
    decode_event_payload,
    encode_event_payload,
)


def test_round_trip_strips_internal_subject_from_public_payload():
    raw = encode_event_payload(
        {"message": "hello"},
        EventTaskSubject(task_ref="task_1", task_version_after=1),
    )
    payload, subject = decode_event_payload(raw)
    assert payload == {"message": "hello"}
    assert subject == EventTaskSubject("task_1", 1)
    assert "__task_subject" in json.loads(raw)


def test_taskless_payload_remains_null():
    assert encode_event_payload(None, EventTaskSubject.empty()) is None
    assert decode_event_payload(None) == (None, EventTaskSubject.empty())
    # The old row codec serialized None as JSON ``null`` instead of SQL NULL.
    assert decode_event_payload("null") == (None, EventTaskSubject.empty())


@pytest.mark.parametrize(
    "payload, subject",
    [
        ({"__task_subject": {}}, EventTaskSubject.empty()),
        (
            {"message": "hello"},
            EventTaskSubject(task_ref="task_1", task_version_after=None),
        ),
        ({"message": "hello"}, EventTaskSubject(task_ref=None, task_version_after=1)),
        (
            {"message": "hello"},
            EventTaskSubject(task_ref="task_1", task_version_after=0),
        ),
    ],
)
def test_encoder_rejects_reserved_or_half_filled_subject(payload, subject):
    with pytest.raises(ContextCollabValidationError):
        encode_event_payload(payload, subject)


def test_decoder_rejects_corrupt_subject():
    raw = json.dumps({"__task_subject": {"task_ref": "task_1"}})
    with pytest.raises(ContextCollabValidationError):
        decode_event_payload(raw)
