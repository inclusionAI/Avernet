"""Semantic checks over non-credential Bot-authority journey observations.

The black-box child (``story.py``) emits schema-1 evidence: one raw observation
record per frozen ``AUTHORITY-JOURNEY.*`` obligation, the owned registration
plane summary, the CLI readiness gate receipt, the bounded envelope
precondition, the transfer renewal window and the retained-resource
disposition. These functions independently reject any observation that
contradicts its frozen obligation; they never inspect or retain an
authorization credential. Every evidence byte stream is additionally scanned
for credential canaries and principal-JWT shapes. Verified count: 42
obligations.
"""
from __future__ import annotations

import json
import re

from ..run_state import RunStateError
from .contract import (DISPOSITION, HTTP_CALL_COUNT, MINE_USER_ID, REDACTION_TOKENS,
                       REQUIREMENTS, TARGET_USER_ID, BUSINESS_HTTP_CALLS,
                       BOOTSTRAP_HTTP_CALLS)

OBLIGATION_PREFIX = 'AUTHORITY-JOURNEY.'
_JWT_SHAPE = re.compile(r'eyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{10,}')
_HEX8 = re.compile(r'^[0-9a-f]{8}$')


class AuthorityJourneyViolation(RunStateError):
    """A live observation contradicted a frozen Bot-authority obligation."""


def _fail(obligation: str, detail: str) -> None:
    raise AuthorityJourneyViolation(obligation + ': ' + detail)


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


def _record(evidence: dict, obligation: str) -> dict:
    obligations = _mapping(evidence.get('obligations'), 'obligations', obligation)
    if obligation not in obligations:
        _fail(obligation, 'observation record is missing')
    return _mapping(obligations.get(obligation), 'observation', obligation)


def _transport(record: dict, obligation: str) -> None:
    if record.get('transport') != 'product-http':
        _fail(obligation, 'observation did not come from the product HTTP surface')


def _status(record: dict, expected: int, obligation: str) -> None:
    if _integer(record.get('status'), 'status', obligation) != expected:
        _fail(obligation, 'observed HTTP status differs from the frozen expectation ' + str(expected))


# --------------------------------------------- O1-O3 ownership registration view
def check_owner_ownership_read_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'owner-ownership-read-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('method') != 'GET':
        _fail(obligation, 'ownership read did not use GET')
    _status(record, 200, obligation)


def check_ownership_keeps_bot_id(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'ownership-keeps-bot-id'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _text(record.get('bot_id'), 'bot_id', obligation)


def check_ownership_identifies_registered_owner(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'ownership-identifies-registered-owner'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    owner = evidence.get('owner')
    if not isinstance(owner, dict) or record.get('owner_user_id') != owner.get('human_id'):
        _fail(obligation, 'ownership view does not name the registering owner human')


# --------------------------------------------- O4-O5 empty manager page
def check_manager_page_empty_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-page-empty-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_manager_page_starts_empty(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-page-starts-empty'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _integer(record.get('total'), 'total', obligation) != 0:
        _fail(obligation, 'fresh manager page is not empty')


# --------------------------------------------- O6-O7 owner-as-manager polarity
def check_owner_manager_add_rejected_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'owner-manager-add-rejected-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 409, obligation)


def check_owner_manager_add_error_code(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'owner-manager-add-error-code'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('error_code') != 'owner_not_manager':
        _fail(obligation, 'owner-as-manager add did not keep the stable polarity code')


# --------------------------------------------- O8-O9 add the target manager
def check_manager_add_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-add-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_manager_add_added_one(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-add-added-one'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _integer(record.get('added'), 'added', obligation) != 1:
        _fail(obligation, 'manager add did not report exactly one added')


# --------------------------------------------- O10-O12 populated manager page
def check_manager_page_populated_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-page-populated-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_manager_page_keeps_manager(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-page-keeps-manager'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _integer(record.get('total'), 'total', obligation) != 1:
        _fail(obligation, 'populated manager page lost the added manager')


def check_manager_page_exposes_added_manager(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-page-exposes-added-manager'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('target_user_id_present') is not True:
        _fail(obligation, 'manager page items do not expose the added target human')


# --------------------------------------------- O13-O14 remove the manager
def check_manager_remove_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-remove-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_manager_remove_removed_one(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-remove-removed-one'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _integer(record.get('removed'), 'removed', obligation) != 1:
        _fail(obligation, 'manager removal did not report exactly one removed')


# --------------------------------------------- O15-O16 idempotent absent remove
def check_manager_absent_remove_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-absent-remove-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_manager_absent_remove_removed_zero(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-absent-remove-removed-zero'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _integer(record.get('removed'), 'removed', obligation) != 0:
        _fail(obligation, 'absent manager removal reported a change')


# --------------------------------------------- O17 restore before transfer
def check_manager_restore_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'manager-restore-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


# --------------------------------------------- O18 public mine read
def check_mine_ensured_human_mine_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'mine-ensured-human-mine-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('api_face') != 'public-v1':
        _fail(obligation, 'mine read did not use the public-v1 face')
    _status(record, 200, obligation)


# --------------------------------------------- O19-O20 mine-ensured add
def check_mine_manager_add_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'mine-manager-add-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_mine_manager_add_added_one(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'mine-manager-add-added-one'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _integer(record.get('added'), 'added', obligation) != 1:
        _fail(obligation, 'mine-ensured manager add did not report exactly one added')


# --------------------------------------------- O21-O22 never-seen polarity
def check_never_seen_manager_add_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'never-seen-manager-add-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 400, obligation)


def check_never_seen_manager_error_code(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'never-seen-manager-error-code'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('error_code') != 'bot_authority_human_unknown':
        _fail(obligation, 'never-seen add did not keep the stable unknown-human code')


# --------------------------------------------- O23-O24 remove mine-ensured
def check_mine_manager_remove_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'mine-manager-remove-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_mine_manager_remove_removed_one(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'mine-manager-remove-removed-one'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _integer(record.get('removed'), 'removed', obligation) != 1:
        _fail(obligation, 'mine-ensured manager removal did not report exactly one removed')


# --------------------------------------------- O25-O27 transfer initiation
def check_transfer_initiate_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-initiate-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 201, obligation)


def check_transfer_initiate_pending(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-initiate-pending'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('transfer_status') != 'pending':
        _fail(obligation, 'initiated transfer is not pending')


def check_transfer_initiate_target_owner(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-initiate-target-owner'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('target_owner_user_id') != TARGET_USER_ID:
        _fail(obligation, 'initiated transfer does not name the frozen target human')


# --------------------------------------------- O28-O29 renewal-window refresh
def check_transfer_refresh_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-refresh-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_transfer_refresh_window_renewed(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-refresh-window-renewed'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    initiated = _integer(record.get('initiated_expires_at'), 'initiated expiry', obligation)
    refreshed = _integer(record.get('refreshed_expires_at'), 'refreshed expiry', obligation)
    if refreshed < initiated:
        _fail(obligation, 'refreshed expiry shrank the renewal window')
    if not 0 <= refreshed - initiated < 300000:
        _fail(obligation, 'renewal moved outside the frozen re-stamp gap')
    if _flag(record.get('renewed'), 'renewed', obligation) is not True:
        _fail(obligation, 'the refresh did not renew the transfer window')
    delta = _integer(record.get('delta_ms'), 'delta_ms', obligation)
    if delta != refreshed - initiated:
        _fail(obligation, 'renewal window bookkeeping drifted from the observed expiry')


# --------------------------------------------- O30-O31 target reads pending
def check_transfer_target_read_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-target-read-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_transfer_target_read_pending(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-target-read-pending'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('transfer_status') != 'pending':
        _fail(obligation, 'target read did not observe a pending round')


# --------------------------------------------- O32-O33 target accepts
def check_transfer_accept_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-accept-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_transfer_accept_accepted(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transfer-accept-accepted'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('transfer_status') != 'accepted':
        _fail(obligation, 'accept did not record the accepted status')


# --------------------------------------------- O34-O35 ownership moved
def check_transferred_ownership_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transferred-ownership-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_transferred_ownership_owner_moved(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transferred-ownership-owner-moved'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('owner_user_id') != TARGET_USER_ID:
        _fail(obligation, 'transferred ownership does not name the target human as owner')


# --------------------------------------------- O36-O38 transferred ledger
def check_transferred_ledger_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transferred-ledger-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_transferred_ledger_owner_names_target(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transferred-ledger-owner-names-target'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('ledger_owner_user_id') != TARGET_USER_ID:
        _fail(obligation, 'transferred ledger does not name the target human as owner')


def check_transferred_ledger_original_owner_plain_manager(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'transferred-ledger-original-owner-plain-manager'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if record.get('original_owner_present') is not True:
        _fail(obligation, 'transferred ledger dropped the original owner from plain managers')
    items = record.get('items')
    owner = evidence.get('owner')
    if (not isinstance(items, list) or not isinstance(owner, dict)
            or owner.get('human_id') not in items):
        _fail(obligation, 'transferred ledger lost the original owner human id')


# --------------------------------------------- O39-O40 received inbox
def check_received_inbox_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'received-inbox-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_received_inbox_contains_bot(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'received-inbox-contains-bot'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _flag(record.get('contains_bot'), 'contains_bot', obligation) is not True:
        _fail(obligation, 'received inbox raw body lost the accepted Bot id')
    if record.get('bot_id_seen') != evidence.get('owner', {}).get('bot_id'):
        _fail(obligation, 'received inbox named a Bot other than the accepted one')


# --------------------------------------------- O41-O42 sent outbox
def check_sent_inbox_status(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'sent-inbox-status'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    _status(record, 200, obligation)


def check_sent_inbox_contains_bot(evidence: dict) -> None:
    obligation = OBLIGATION_PREFIX + 'sent-inbox-contains-bot'
    record = _record(evidence, obligation)
    _transport(record, obligation)
    if _flag(record.get('contains_bot'), 'contains_bot', obligation) is not True:
        _fail(obligation, 'sent outbox raw body lost the accepted Bot id')
    if record.get('bot_id_seen') != evidence.get('owner', {}).get('bot_id'):
        _fail(obligation, 'sent outbox named a Bot other than the accepted one')


_CHECK_REGISTRY = {
    OBLIGATION_PREFIX + 'owner-ownership-read-status': check_owner_ownership_read_status,
    OBLIGATION_PREFIX + 'ownership-keeps-bot-id': check_ownership_keeps_bot_id,
    OBLIGATION_PREFIX + 'ownership-identifies-registered-owner': check_ownership_identifies_registered_owner,
    OBLIGATION_PREFIX + 'manager-page-empty-status': check_manager_page_empty_status,
    OBLIGATION_PREFIX + 'manager-page-starts-empty': check_manager_page_starts_empty,
    OBLIGATION_PREFIX + 'owner-manager-add-rejected-status': check_owner_manager_add_rejected_status,
    OBLIGATION_PREFIX + 'owner-manager-add-error-code': check_owner_manager_add_error_code,
    OBLIGATION_PREFIX + 'manager-add-status': check_manager_add_status,
    OBLIGATION_PREFIX + 'manager-add-added-one': check_manager_add_added_one,
    OBLIGATION_PREFIX + 'manager-page-populated-status': check_manager_page_populated_status,
    OBLIGATION_PREFIX + 'manager-page-keeps-manager': check_manager_page_keeps_manager,
    OBLIGATION_PREFIX + 'manager-page-exposes-added-manager': check_manager_page_exposes_added_manager,
    OBLIGATION_PREFIX + 'manager-remove-status': check_manager_remove_status,
    OBLIGATION_PREFIX + 'manager-remove-removed-one': check_manager_remove_removed_one,
    OBLIGATION_PREFIX + 'manager-absent-remove-status': check_manager_absent_remove_status,
    OBLIGATION_PREFIX + 'manager-absent-remove-removed-zero': check_manager_absent_remove_removed_zero,
    OBLIGATION_PREFIX + 'manager-restore-status': check_manager_restore_status,
    OBLIGATION_PREFIX + 'mine-ensured-human-mine-status': check_mine_ensured_human_mine_status,
    OBLIGATION_PREFIX + 'mine-manager-add-status': check_mine_manager_add_status,
    OBLIGATION_PREFIX + 'mine-manager-add-added-one': check_mine_manager_add_added_one,
    OBLIGATION_PREFIX + 'never-seen-manager-add-status': check_never_seen_manager_add_status,
    OBLIGATION_PREFIX + 'never-seen-manager-error-code': check_never_seen_manager_error_code,
    OBLIGATION_PREFIX + 'mine-manager-remove-status': check_mine_manager_remove_status,
    OBLIGATION_PREFIX + 'mine-manager-remove-removed-one': check_mine_manager_remove_removed_one,
    OBLIGATION_PREFIX + 'transfer-initiate-status': check_transfer_initiate_status,
    OBLIGATION_PREFIX + 'transfer-initiate-pending': check_transfer_initiate_pending,
    OBLIGATION_PREFIX + 'transfer-initiate-target-owner': check_transfer_initiate_target_owner,
    OBLIGATION_PREFIX + 'transfer-refresh-status': check_transfer_refresh_status,
    OBLIGATION_PREFIX + 'transfer-refresh-window-renewed': check_transfer_refresh_window_renewed,
    OBLIGATION_PREFIX + 'transfer-target-read-status': check_transfer_target_read_status,
    OBLIGATION_PREFIX + 'transfer-target-read-pending': check_transfer_target_read_pending,
    OBLIGATION_PREFIX + 'transfer-accept-status': check_transfer_accept_status,
    OBLIGATION_PREFIX + 'transfer-accept-accepted': check_transfer_accept_accepted,
    OBLIGATION_PREFIX + 'transferred-ownership-status': check_transferred_ownership_status,
    OBLIGATION_PREFIX + 'transferred-ownership-owner-moved': check_transferred_ownership_owner_moved,
    OBLIGATION_PREFIX + 'transferred-ledger-status': check_transferred_ledger_status,
    OBLIGATION_PREFIX + 'transferred-ledger-owner-names-target': check_transferred_ledger_owner_names_target,
    OBLIGATION_PREFIX + 'transferred-ledger-original-owner-plain-manager': check_transferred_ledger_original_owner_plain_manager,
    OBLIGATION_PREFIX + 'received-inbox-status': check_received_inbox_status,
    OBLIGATION_PREFIX + 'received-inbox-contains-bot': check_received_inbox_contains_bot,
    OBLIGATION_PREFIX + 'sent-inbox-status': check_sent_inbox_status,
    OBLIGATION_PREFIX + 'sent-inbox-contains-bot': check_sent_inbox_contains_bot,
}


def _envelope(evidence: dict) -> None:
    if not isinstance(evidence, dict):
        raise AuthorityJourneyViolation('Bot-authority evidence must be an object')
    if type(evidence.get('schema_version')) is not int or evidence.get('schema_version') != 1:
        raise AuthorityJourneyViolation('Bot-authority evidence envelope must be schema_version 1')
    if evidence.get('case_id') != 'LOCAL-AUTHORITY-JOURNEY-02':
        raise AuthorityJourneyViolation('Bot-authority evidence case id differs from the frozen map')
    if evidence.get('identity_mode') != 'register':
        raise AuthorityJourneyViolation('Bot-authority evidence identity mode is invalid')
    owner = _mapping(evidence.get('owner'), 'owner', OBLIGATION_PREFIX + 'owner-ownership-read-status')
    _text(owner.get('human_id'), 'owner human_id', OBLIGATION_PREFIX + 'owner-ownership-read-status')
    _text(owner.get('bot_id'), 'owner bot_id', OBLIGATION_PREFIX + 'owner-ownership-read-status')
    target = _mapping(evidence.get('target'), 'target', OBLIGATION_PREFIX + 'transfer-initiate-target-owner')
    if target.get('user_id') != TARGET_USER_ID:
        raise AuthorityJourneyViolation('Bot-authority evidence target human differs from the frozen map')
    _text(target.get('bot_id'), 'target bot_id', OBLIGATION_PREFIX + 'transfer-initiate-target-owner')
    mine = _mapping(evidence.get('mine'), 'mine', OBLIGATION_PREFIX + 'mine-ensured-human-mine-status')
    if mine.get('user_id') != MINE_USER_ID:
        raise AuthorityJourneyViolation('Bot-authority evidence mine-ensured human differs from the frozen map')
    never = evidence.get('never_user_id')
    if (not isinstance(never, str) or not never.startswith('never-seen-') or len(never) <= len('never-seen-')):
        raise AuthorityJourneyViolation('Bot-authority evidence lacks the fresh never-seen identity')
    gate = _mapping(evidence.get('cli_readiness_gate'), 'cli_readiness_gate',
                    OBLIGATION_PREFIX + 'mine-ensured-human-mine-status')
    if gate.get('exit_code') != 0 or gate.get('transport') != 'product-cli' \
            or gate.get('role') != 'readiness-gate-only' \
            or gate.get('authority_business_verbs') != 'none-frozen':
        raise AuthorityJourneyViolation('Bot-authority CLI readiness gate receipt is invalid')
    _text(gate.get('health_status'), 'health_status', OBLIGATION_PREFIX + 'mine-ensured-human-mine-status')
    plane = _mapping(evidence.get('registration_plane'), 'registration_plane',
                     OBLIGATION_PREFIX + 'owner-ownership-read-status')
    if plane.get('mock_admitted') is not True or plane.get('bots_registered') != 2 \
            or plane.get('distinct_bot_ids') is not True:
        raise AuthorityJourneyViolation('Bot-authority registration plane summary is invalid')
    envelope = _mapping(evidence.get('business_envelope'), 'business_envelope',
                        OBLIGATION_PREFIX + 'owner-ownership-read-status')
    if envelope.get('enforced') is not True or envelope.get('observed_keys') != [
            'code', 'message', 'request_id', 'data']:
        raise AuthorityJourneyViolation('Bot-authority business envelope precondition differs')
    if _integer(evidence.get('http_call_count'), 'http_call_count',
                OBLIGATION_PREFIX + 'manager-add-status') != HTTP_CALL_COUNT:
        raise AuthorityJourneyViolation('Bot-authority journey did not consume the frozen 24-call matrix')
    if _integer(evidence.get('bootstrap_http_call_count'), 'bootstrap http calls',
                OBLIGATION_PREFIX + 'owner-ownership-read-status') != BOOTSTRAP_HTTP_CALLS:
        raise AuthorityJourneyViolation('Bot-authority bootstrap did not consume the frozen 4-call plane')
    if _integer(evidence.get('business_http_call_count'), 'business http calls',
                OBLIGATION_PREFIX + 'manager-add-status') != BUSINESS_HTTP_CALLS:
        raise AuthorityJourneyViolation('Bot-authority business leg did not consume the frozen 20-call matrix')
    transfer = _mapping(evidence.get('transfer'), 'transfer',
                        OBLIGATION_PREFIX + 'transfer-refresh-window-renewed')
    initiated = _integer(transfer.get('initiated_expires_at'), 'initiated expiry',
                         OBLIGATION_PREFIX + 'transfer-refresh-window-renewed')
    refreshed = _integer(transfer.get('refreshed_expires_at'), 'refreshed expiry',
                         OBLIGATION_PREFIX + 'transfer-refresh-window-renewed')
    if refreshed < initiated or not 0 <= refreshed - initiated < 300000:
        raise AuthorityJourneyViolation('Bot-authority transfer renewal window drifted')
    if transfer.get('renewed') is not True or transfer.get('delta_ms') != refreshed - initiated:
        raise AuthorityJourneyViolation('Bot-authority transfer window bookkeeping differs')
    if evidence.get('resource_disposition') != DISPOSITION:
        raise AuthorityJourneyViolation('Bot-authority resource disposition differs from the frozen map')
    obligations = evidence.get('obligations')
    if not isinstance(obligations, dict) or set(obligations) != set(REQUIREMENTS):
        raise AuthorityJourneyViolation('Bot-authority obligations do not match the frozen map')
    payload = json.dumps(evidence, sort_keys=True, allow_nan=False)
    for token in REDACTION_TOKENS:
        if token in payload:
            raise AuthorityJourneyViolation('Bot-authority evidence retained a credential canary: ' + token)
    if _JWT_SHAPE.search(payload) is not None:
        raise AuthorityJourneyViolation('Bot-authority evidence retained principal-JWT-shaped material')


OBLIGATION_CHECKS = tuple(
    (obligation, _CHECK_REGISTRY[obligation]) for obligation in REQUIREMENTS)


def verify_all_obligations(evidence: dict) -> dict:
    """Run every independent obligation check and return a non-secret receipt."""
    _envelope(evidence)
    for obligation, check in OBLIGATION_CHECKS:
        if _CHECK_REGISTRY.get(obligation) is None:
            raise AuthorityJourneyViolation(obligation + ': no verification function registered')
        try:
            check(evidence)
        except AuthorityJourneyViolation:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AuthorityJourneyViolation(obligation + ': malformed observation') from None
    return {'status': 'PASS', 'checked': list(REQUIREMENTS)}


def scan_redaction(name: str, payload: bytes, *,
                   tokens: tuple[str, ...] = REDACTION_TOKENS) -> dict:
    """Return a non-secret observation of whether a byte stream leaks a canary."""
    if not isinstance(name, str) or not name:
        _fail(OBLIGATION_PREFIX + 'owner-ownership-read-status',
              'redaction scan name is invalid')
    if not isinstance(payload, bytes):
        _fail(OBLIGATION_PREFIX + 'owner-ownership-read-status',
              'redaction scan payload must be bytes')
    return {'name': name, 'clean': all(token.encode('utf-8') not in payload
                                       for token in tokens),
            'bytes': len(payload)}
