"""Semantic checks over non-credential service policy observations.

The black-box children emit one raw observation record per frozen
``SERVICE-POLICY.*`` obligation:

- the owned-local leg (register identity mode) records the upstream journey
  incl. the mock-admission bootstrap and the disposable Bot registration;
- the preprod leg (bearer identity mode) records a live driver identity
  confirmation, an honest ``REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER``
  disposition for the registration obligation, and live replays of the six
  business obligations plus the disposable-only teardown.

These functions independently reject any observation that contradicts its
frozen obligation; they never inspect or retain an authorization credential.
"""
from __future__ import annotations

import re

from ..run_state import RunStateError
from .contract import (APPLICABLE_REQUIREMENTS, DRIVER_BOT_DELETE_DISPOSITION,
                       DRIVER_BOT_ID, NOT_APPLICABLE_REQUIREMENT, REGISTRATION_DISPOSITION,
                       REQUIREMENTS, REDACTION_CANARIES)

OBLIGATION_PREFIX = 'SERVICE-POLICY.'
REDACTION_STREAMS = ('story.stdout', 'story.stderr')
REDACTION_ARTIFACTS = ('child.receipt', 'child.evidence', 'flow.report', 'canonical.report')
_BOT_SESSION_KIND = 'service_invocation'


class ServicePolicyViolation(RunStateError):
    """A live observation contradicted a frozen service policy obligation."""


def _fail(obligation: str, detail: str) -> None:
    raise ServicePolicyViolation(obligation + ': ' + detail)


def _mapping(value: object, field: str, obligation: str) -> dict:
    if not isinstance(value, dict):
        _fail(obligation, field + ' observation is missing')
    return value


def _flag(value: object, field: str, obligation: str) -> bool:
    if value is not True and value is not False:
        _fail(obligation, field + ' must be a boolean')
    return value


def _integer(value: object, field: str, obligation: str) -> int:
    if type(value) is not int:
        _fail(obligation, field + ' must be an integer')
    return value


def _text(value: object, field: str, obligation: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(obligation, field + ' must be a non-empty string')
    return value


def _in(value: object, allowed: set, field: str, obligation: str) -> object:
    if value not in allowed:
        _fail(obligation, field + ' took an unexpected value')
    return value


def _record(evidence: dict, obligation: str) -> dict:
    obligations = _mapping(evidence.get('obligations'), 'obligations', obligation)
    if obligation not in obligations:
        _fail(obligation, 'observation record is missing')
    return _mapping(obligations.get(obligation), 'observation', obligation)


def check_driver_identity_confirmed(evidence: dict) -> None:
    """Bearer bootstrap maps to a live driver Bot identity confirmation."""
    obligation = REQUIREMENTS[0]
    record = _record(evidence, obligation)
    _in(record.get('transport'), {'product-http'}, 'transport', obligation)
    _in(record.get('route'), {'GET /bots/{driver_bot_id}'}, 'route', obligation)
    if _integer(record.get('status'), 'status', obligation) != 200:
        _fail(obligation, 'driver identity read did not answer 200')
    if _flag(record.get('actor_match'), 'actor_match', obligation) is not True:
        _fail(obligation, 'driver identity read did not match the pre-admitted driver Bot')
    if DRIVER_BOT_ID.fullmatch(_text(record.get('driver_bot'), 'driver_bot', obligation)) is None:
        _fail(obligation, 'driver Bot id does not match the dedicated-pool station shape')


def check_registration_not_applicable(evidence: dict) -> None:
    """Shared preprod never registers Bots; the obligation maps honestly."""
    record = _record(evidence, NOT_APPLICABLE_REQUIREMENT)
    _in(record.get('transport'), {'none'}, 'transport', NOT_APPLICABLE_REQUIREMENT)
    if record.get('disposition') != REGISTRATION_DISPOSITION:
        _fail(NOT_APPLICABLE_REQUIREMENT, 'registration obligation lost its honest disposition')
    _flag(record.get('driver_bot_deletion_forbidden'),
          'driver_bot_deletion_forbidden', NOT_APPLICABLE_REQUIREMENT)


def check_legacy_encoding_ignored(evidence: dict) -> None:
    """Both legacy service_spec encodings persist nothing on either leg."""
    obligation = REQUIREMENTS[2]
    record = _record(evidence, obligation)
    groups = record.get('groups')
    if (not isinstance(groups, list) or len(groups) != 2
            or any(not isinstance(item, dict) for item in groups)):
        _fail(obligation, 'two disposable Group observations are required')
    for index, group in enumerate(groups):
        if _integer(group.get('created_status'), 'created_status', obligation) not in (200, 201):
            _fail(obligation, 'Group creation did not answer 200/201')
        if _integer(group.get('readback_status'), 'readback_status', obligation) != 200:
            _fail(obligation, 'Group read-back did not answer 200')
        for field in ('service_spec', 'retired_service_spec'):
            if group.get('created_' + field + '_absent') is not True:
                _fail(obligation, 'legacy encoding leaked into the created Group response')
            if group.get('readback_' + field + '_absent') is not True:
                _fail(obligation, 'legacy encoding leaked into the read-back Group response')
        if _flag(group.get('driver_owned'), 'driver_owned', obligation) is not True:
            _fail(obligation, 'Group is not owned by the story driver identity')
        if not isinstance(group.get('group_id'), str) or not group['group_id']:
            _fail(obligation, 'disposable Group id observation is missing')


def check_retired_settings_gone(evidence: dict) -> None:
    """PATCH settings with a legacy service_spec stays Gone 410."""
    obligation = REQUIREMENTS[3]
    record = _record(evidence, obligation)
    if _integer(record.get('status'), 'status', obligation) != 410:
        _fail(obligation, 'retired settings surface did not answer 410')
    if record.get('error') != 'service_policy_retired':
        _fail(obligation, 'retired settings surface lost the exact service_policy_retired error')


def check_invocation_lifecycle(evidence: dict) -> None:
    """Two distinct service_invocation Sessions persist their inputs."""
    obligation = REQUIREMENTS[4]
    record = _record(evidence, obligation)
    invocations = record.get('invocations')
    if (not isinstance(invocations, list) or len(invocations) != 2
            or any(not isinstance(item, dict) for item in invocations)):
        _fail(obligation, 'two service_invocation observations are required')
    session_ids = set()
    for index, invocation in enumerate(invocations):
        if _integer(invocation.get('status'), 'status', obligation) != 202:
            _fail(obligation, 'service invocation creation did not answer 202')
        if invocation.get('session_kind') != _BOT_SESSION_KIND:
            _fail(obligation, 'invocation Session is not service_invocation')
        if invocation.get('lifecycle_status') != 'running':
            _fail(obligation, 'invocation Session is not running')
        if invocation.get('stored_input') != {'run': index}:
            _fail(obligation, 'invocation Session did not persist its submitted input')
        session_id = _text(invocation.get('session_id'), 'session_id', obligation)
        session_ids.add(session_id)
    if len(session_ids) != 2:
        _fail(obligation, 'the two invocations unexpectedly shared a Session id')


def check_running_reactivation_denied(evidence: dict) -> None:
    """A running invocation refuses reactivation with 409 + running shape."""
    obligation = REQUIREMENTS[5]
    record = _record(evidence, obligation)
    if _integer(record.get('status'), 'status', obligation) != 409:
        _fail(obligation, 'running reactivation did not answer 409')
    if 'running' not in _text(record.get('error'), 'error', obligation):
        _fail(obligation, 'running reactivation lost its running-shaped error')
    if record.get('stored_input') != {'run': 0}:
        _fail(obligation, 'stored invocation input changed across the refused reactivation')


def check_chat_session_guard(evidence: dict) -> None:
    """A completed chat Session cannot convert into a service invocation."""
    obligation = REQUIREMENTS[6]
    record = _record(evidence, obligation)
    if _integer(record.get('chat_create_status'), 'chat_create_status', obligation) != 201:
        _fail(obligation, 'chat Session creation did not answer 201')
    if record.get('completed_status') != 'completed':
        _fail(obligation, 'chat Session did not complete')
    if _integer(record.get('conversion_status'), 'conversion_status', obligation) != 400:
        _fail(obligation, 'service conversion of a chat Session did not answer 400')
    if 'not service_invocation' not in _text(record.get('error'), 'error', obligation):
        _fail(obligation, 'service conversion lost its not-service_invocation error')
    if record.get('final_status') != 'completed':
        _fail(obligation, 'refused conversion disturbed the chat Session status')
    if record.get('final_output') != {'ok': True}:
        _fail(obligation, 'refused conversion disturbed the chat Session output')


def check_cleanup_disposition(evidence: dict) -> None:
    """Teardown deletes the three Sessions and two Groups, never the driver Bot."""
    obligation = REQUIREMENTS[7]
    record = _record(evidence, obligation)
    if _integer(record.get('sessions_deleted'), 'sessions_deleted', obligation) != 3:
        _fail(obligation, 'the story did not delete exactly three disposable Sessions')
    if _integer(record.get('groups_deleted'), 'groups_deleted', obligation) != 2:
        _fail(obligation, 'the story did not delete exactly two disposable Groups')
    delete_statuses = record.get('delete_statuses')
    if (not isinstance(delete_statuses, list) or len(delete_statuses) != 5
            or any(status not in (200, 204) for status in delete_statuses)):
        _fail(obligation, 'teardown demands five successful 200/204 dispositions')
    if record.get('driver_bot_delete_disposition') != DRIVER_BOT_DELETE_DISPOSITION:
        _fail(obligation, 'the shared dedicated driver Bot must never be deleted')
    failures = record.get('failures')
    if failures not in (None, []):
        _fail(obligation, 'teardown reported failures instead of failing the story')
    if record.get('groups_teardown_matches_creation') is not True:
        _fail(obligation, 'teardown did not cover exactly the Groups the story created')


OBLIGATION_CHECKS = (
    (REQUIREMENTS[0], check_driver_identity_confirmed),
    (NOT_APPLICABLE_REQUIREMENT, check_registration_not_applicable),
    (REQUIREMENTS[2], check_legacy_encoding_ignored),
    (REQUIREMENTS[3], check_retired_settings_gone),
    (REQUIREMENTS[4], check_invocation_lifecycle),
    (REQUIREMENTS[5], check_running_reactivation_denied),
    (REQUIREMENTS[6], check_chat_session_guard),
    (REQUIREMENTS[7], check_cleanup_disposition),
)


def verify_all_obligations(evidence: dict) -> dict:
    """Run every frozen obligation check; any violation fails the slice."""
    if not isinstance(evidence, dict):
        raise ServicePolicyViolation('service policy evidence envelope is not an object')
    checked = []
    for requirement_id, checker in OBLIGATION_CHECKS:
        checker(evidence)
        checked.append(requirement_id)
    if sorted(checked) != sorted(REQUIREMENTS):
        _fail(REQUIREMENTS[0], 'obligation coverage drifted from the frozen map')
    return {'status': 'PASS', 'checked': checked}


def scan_redaction(name: str, payload: bytes) -> dict:
    """Reject any captured stream or artifact that contains a credential canary."""
    text = payload.decode('utf-8', errors='replace') if isinstance(payload, bytes) else str(payload)
    hits = sorted({canary for canary in REDACTION_CANARIES if canary in text})
    return {'name': name, 'clean': not hits, 'canary_hits': hits}
