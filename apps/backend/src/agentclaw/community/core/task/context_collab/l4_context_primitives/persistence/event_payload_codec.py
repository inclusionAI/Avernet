"""Codec for the reserved Work-subject envelope inside Event ``payload``.

The deployed ``ac_context_collab_event`` table has no ``task_ref`` or
``task_version_after`` columns.  The L4 domain still needs those facts to check
Task evidence, so the SQL boundary serializes them next to the user payload in
one reserved JSON key.  Readers strip that key, keeping the domain Event payload
unchanged and preventing the internal envelope from leaking through service APIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


TASK_SUBJECT_KEY = "__task_subject"


@dataclass(frozen=True)
class EventTaskSubject:
    """The persisted Work subject/revision pair, or all-null when absent."""

    task_ref: str | None
    task_version_after: int | None

    @classmethod
    def empty(cls) -> "EventTaskSubject":
        return cls(task_ref=None, task_version_after=None)


def encode_event_payload(payload: dict | None, subject: EventTaskSubject) -> str | None:
    """Serialize the user payload plus the reserved internal task subject."""
    if (
        payload is None
        and subject.task_ref is None
        and subject.task_version_after is None
    ):
        return None
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ContextCollabValidationError("Event payload must be an object or null")
    if TASK_SUBJECT_KEY in payload:
        raise ContextCollabValidationError(
            "Event task subject is a reserved payload key"
        )
    if subject.task_ref is None and subject.task_version_after is None:
        encoded = payload
    else:
        _validate_subject(subject)
        encoded = {
            **payload,
            TASK_SUBJECT_KEY: {
                "task_ref": subject.task_ref,
                "task_version_after": subject.task_version_after,
            },
        }
    return json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))


def decode_event_payload(
    raw_payload: str | None,
) -> tuple[dict | None, EventTaskSubject]:
    """Decode a physical payload into its public and internal components."""
    if raw_payload is None:
        return None, EventTaskSubject.empty()
    try:
        payload = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContextCollabValidationError(
            f"invalid Event payload JSON: {error}"
        ) from error
    # Historical writers serialized a null payload as JSON ``null`` rather
    # than SQL NULL; both forms represent the same nullable Event payload.
    if payload is None:
        return None, EventTaskSubject.empty()
    if not isinstance(payload, dict):
        raise ContextCollabValidationError("stored Event payload must be an object")
    raw_subject = payload.pop(TASK_SUBJECT_KEY, None)
    if raw_subject is None:
        return payload, EventTaskSubject.empty()
    if not isinstance(raw_subject, dict):
        raise ContextCollabValidationError("Event task subject must be an object")
    if set(raw_subject) != {"task_ref", "task_version_after"}:
        raise ContextCollabValidationError("Event task subject fields are invalid")
    subject = EventTaskSubject(
        task_ref=raw_subject["task_ref"],
        task_version_after=raw_subject["task_version_after"],
    )
    _validate_subject(subject)
    return payload, subject


def _validate_subject(subject: EventTaskSubject) -> None:
    if (subject.task_ref is None) != (subject.task_version_after is None):
        raise ContextCollabValidationError(
            "Event task subject fields must be set together"
        )
    if subject.task_ref is not None and (
        not isinstance(subject.task_ref, str) or not subject.task_ref.strip()
    ):
        raise ContextCollabValidationError("Event task_ref must be a non-empty string")
    if subject.task_version_after is not None and (
        type(subject.task_version_after) is not int or subject.task_version_after < 1
    ):
        raise ContextCollabValidationError(
            "Event task_version_after must be a positive integer"
        )
