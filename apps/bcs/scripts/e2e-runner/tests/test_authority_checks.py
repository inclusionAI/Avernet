"""Independent semantic checks over the 42-obligation authority evidence map.

Every frozen obligation carries one machine observation; the mutation matrix
below flips exactly one field per obligation plus every envelope freeze and
asserts the fail-closed ``AuthorityJourneyViolation`` verdict. Builders never
carry credentials: the redaction canaries and principal-JWT shapes are probed
with synthetic strings only.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.authority import checks, contract
from bcs_e2e.authority.checks import (
    OBLIGATION_CHECKS, AuthorityJourneyViolation, scan_redaction, verify_all_obligations)
from bcs_e2e.authority.contract import REQUIREMENTS
from bcs_e2e.run_state import RunStateError

OWNER_HUMAN = 'wp2authorityownerhuman00000000'
OWNER_BOT = 'bot_1a2b3c4d'
TARGET_BOT = 'bot_5e6f7a8b'
INITIATED = 1800000000000
REFRESHED = INITIATED + 1000


def _status_row(method: str, status: int) -> dict:
    return {'transport': 'product-http', 'method': method, 'status': status}


def _value_row(**fields) -> dict:
    row = {'transport': 'product-http'}
    row.update(fields)
    return row


def obligation_records() -> dict:
    records = {
        'owner-ownership-read-status': _status_row('GET', 200),
        'ownership-keeps-bot-id': _value_row(bot_id=OWNER_BOT),
        'ownership-identifies-registered-owner': _value_row(owner_user_id=OWNER_HUMAN),
        'manager-page-empty-status': _status_row('GET', 200),
        'manager-page-starts-empty': _value_row(total=0),
        'owner-manager-add-rejected-status': _status_row('PUT', 409),
        'owner-manager-add-error-code': _value_row(error_code='owner_not_manager'),
        'manager-add-status': _status_row('PUT', 200),
        'manager-add-added-one': _value_row(added=1),
        'manager-page-populated-status': _status_row('GET', 200),
        'manager-page-keeps-manager': _value_row(total=1),
        'manager-page-exposes-added-manager': _value_row(items=['002'],
                                                         target_user_id_present=True),
        'manager-remove-status': _status_row('DELETE', 200),
        'manager-remove-removed-one': _value_row(removed=1),
        'manager-absent-remove-status': _status_row('DELETE', 200),
        'manager-absent-remove-removed-zero': _value_row(removed=0),
        'manager-restore-status': _status_row('PUT', 200),
        'mine-ensured-human-mine-status': _value_row(api_face='public-v1', method='GET',
                                                     status=200),
        'mine-manager-add-status': _status_row('PUT', 200),
        'mine-manager-add-added-one': _value_row(added=1),
        'never-seen-manager-add-status': _status_row('PUT', 400),
        'never-seen-manager-error-code': _value_row(error_code='bot_authority_human_unknown'),
        'mine-manager-remove-status': _status_row('DELETE', 200),
        'mine-manager-remove-removed-one': _value_row(removed=1),
        'transfer-initiate-status': _status_row('PUT', 201),
        'transfer-initiate-pending': _value_row(transfer_status='pending'),
        'transfer-initiate-target-owner': _value_row(target_owner_user_id='002'),
        'transfer-refresh-status': _status_row('PUT', 200),
        'transfer-refresh-window-renewed': _value_row(initiated_expires_at=INITIATED,
                                                      refreshed_expires_at=REFRESHED,
                                                      delta_ms=1000, renewed=True),
        'transfer-target-read-status': _status_row('GET', 200),
        'transfer-target-read-pending': _value_row(transfer_status='pending'),
        'transfer-accept-status': _status_row('POST', 200),
        'transfer-accept-accepted': _value_row(transfer_status='accepted'),
        'transferred-ownership-status': _status_row('GET', 200),
        'transferred-ownership-owner-moved': _value_row(owner_user_id='002'),
        'transferred-ledger-status': _status_row('GET', 200),
        'transferred-ledger-owner-names-target': _value_row(ledger_owner_user_id='002'),
        'transferred-ledger-original-owner-plain-manager': _value_row(
            items=[OWNER_HUMAN], original_owner_present=True),
        'received-inbox-status': _status_row('GET', 200),
        'received-inbox-contains-bot': _value_row(contains_bot=True, bot_id_seen=OWNER_BOT),
        'sent-inbox-status': _status_row('GET', 200),
        'sent-inbox-contains-bot': _value_row(contains_bot=True, bot_id_seen=OWNER_BOT),
    }
    assert len(records) == 42
    assert set(records) == {name[len('AUTHORITY-JOURNEY.'):] for name in REQUIREMENTS}
    return {contract.OBLIGATION_PREFIX + slug: row for slug, row in records.items()}


def valid_evidence() -> dict:
    return {
        'schema_version': 1,
        'case_id': 'LOCAL-AUTHORITY-JOURNEY-02',
        'identity_mode': 'register',
        'owner': {'human_id': OWNER_HUMAN, 'bot_id': OWNER_BOT},
        'target': {'user_id': '002', 'bot_id': TARGET_BOT},
        'mine': {'user_id': '137'},
        'never_user_id': 'never-seen-fence0001',
        'cli_readiness_gate': {'exit_code': 0, 'health_status': 'healthy',
                               'transport': 'product-cli', 'role': 'readiness-gate-only',
                               'authority_business_verbs': 'none-frozen'},
        'registration_plane': {'mock_admitted': True, 'bots_registered': 2,
                               'distinct_bot_ids': True},
        'business_envelope': {'enforced': True,
                              'observed_keys': ['code', 'message', 'request_id', 'data']},
        'http_call_count': 24,
        'bootstrap_http_call_count': 4,
        'business_http_call_count': 20,
        'transfer': {'initiated_expires_at': INITIATED, 'refreshed_expires_at': REFRESHED,
                     'delta_ms': REFRESHED - INITIATED, 'renewed': True},
        'resource_disposition': 'AUTHORITY_TWO_BOTS_CREATED_AND_RETAINED',
        'obligations': obligation_records(),
    }


def mutate_obligation(evidence: dict, slug: str, field: str, bad: object) -> dict:
    value = copy.deepcopy(evidence)
    value['obligations'][contract.OBLIGATION_PREFIX + slug][field] = bad
    return value


class AuthorityChecksTests(unittest.TestCase):
    def test_valid_evidence_passes_all_42_obligations(self):
        receipt = verify_all_obligations(valid_evidence())
        self.assertEqual(receipt, {'status': 'PASS', 'checked': list(REQUIREMENTS)})

    def test_every_obligation_rejects_its_key_field_mutation(self):
        table = (
            ('owner-ownership-read-status', 'status', 404),
            ('owner-ownership-read-status', 'method', 'PUT'),
            ('ownership-keeps-bot-id', 'bot_id', ''),
            ('ownership-identifies-registered-owner', 'owner_user_id', '002'),
            ('manager-page-empty-status', 'status', 204),
            ('manager-page-starts-empty', 'total', 1),
            ('owner-manager-add-rejected-status', 'status', 200),
            ('owner-manager-add-error-code', 'error_code', 'bot_authority_human_unknown'),
            ('manager-add-status', 'status', 201),
            ('manager-add-added-one', 'added', 2),
            ('manager-page-populated-status', 'status', 500),
            ('manager-page-keeps-manager', 'total', 0),
            ('manager-page-exposes-added-manager', 'target_user_id_present', False),
            ('manager-remove-status', 'status', 409),
            ('manager-remove-removed-one', 'removed', 0),
            ('manager-absent-remove-status', 'status', 204),
            ('manager-absent-remove-removed-zero', 'removed', 1),
            ('manager-restore-status', 'status', 201),
            ('mine-ensured-human-mine-status', 'api_face', 'internal-v1'),
            ('mine-manager-add-status', 'status', 201),
            ('mine-manager-add-added-one', 'added', 0),
            ('never-seen-manager-add-status', 'status', 200),
            ('never-seen-manager-error-code', 'error_code', 'owner_not_manager'),
            ('mine-manager-remove-status', 'status', 409),
            ('mine-manager-remove-removed-one', 'removed', 0),
            ('transfer-initiate-status', 'status', 200),
            ('transfer-initiate-pending', 'transfer_status', 'accepted'),
            ('transfer-initiate-target-owner', 'target_owner_user_id', '137'),
            ('transfer-refresh-status', 'status', 201),
            ('transfer-refresh-window-renewed', 'renewed', False),
            ('transfer-refresh-window-renewed', 'initiated_expires_at', 'epoch-ms'),
            ('transfer-refresh-window-renewed', 'refreshed_expires_at', INITIATED - 1),
            ('transfer-refresh-window-renewed', 'delta_ms', 999),
            ('transfer-target-read-status', 'status', 404),
            ('transfer-target-read-pending', 'transfer_status', 'accepted'),
            ('transfer-accept-status', 'status', 201),
            ('transfer-accept-accepted', 'transfer_status', 'pending'),
            ('transferred-ownership-status', 'status', 409),
            ('transferred-ownership-owner-moved', 'owner_user_id', OWNER_HUMAN),
            ('transferred-ledger-status', 'status', 204),
            ('transferred-ledger-owner-names-target', 'ledger_owner_user_id', '137'),
            ('transferred-ledger-original-owner-plain-manager', 'original_owner_present', False),
            ('transferred-ledger-original-owner-plain-manager', 'items', ['137']),
            ('received-inbox-status', 'status', 500),
            ('received-inbox-contains-bot', 'contains_bot', False),
            ('received-inbox-contains-bot', 'bot_id_seen', TARGET_BOT),
            ('sent-inbox-status', 'status', 403),
            ('sent-inbox-contains-bot', 'contains_bot', False),
            ('sent-inbox-contains-bot', 'bot_id_seen', 'bot_ffffffff'),
        )
        for slug, field, bad in table:
            with self.subTest(obligation=slug, field=field):
                with self.assertRaises(AuthorityJourneyViolation):
                    verify_all_obligations(mutate_obligation(valid_evidence(), slug, field, bad))

    def test_envelope_freezes_schema_case_identity_and_disposition(self):
        def overridden(mutator) -> dict:
            value = valid_evidence()
            mutator(value)
            return value

        mutations = (
            lambda value: value.__setitem__('schema_version', 2),
            lambda value: value.__setitem__('case_id', 'LOCAL-AUTHORITY-JOURNEY-01'),
            lambda value: value.__setitem__('identity_mode', 'bearer'),
            lambda value: value['owner'].__setitem__('human_id', ''),
            lambda value: value['owner'].__setitem__('bot_id', 'bot_not frozen shape'),
            lambda value: value['target'].__setitem__('user_id', '003'),
            lambda value: value['target'].__setitem__('bot_id', ''),
            lambda value: value['mine'].__setitem__('user_id', '002'),
            lambda value: value.__setitem__('never_user_id', 'seen-earlier-fixed'),
            lambda value: value.__setitem__('resource_disposition', 'TWO_BOTS_CREATED'),
            lambda value: value.__setitem__('http_call_count', 23),
            lambda value: value.__setitem__('bootstrap_http_call_count', 5),
            lambda value: value.__setitem__('business_http_call_count', 19),
            lambda value: value['cli_readiness_gate'].__setitem__('exit_code', 1),
            lambda value: value['cli_readiness_gate'].__setitem__('transport', 'product-http'),
            lambda value: value['cli_readiness_gate'].__setitem__('role', 'business-verbs'),
            lambda value: value['cli_readiness_gate'].__setitem__('authority_business_verbs',
                                                                   'transfers'),
            lambda value: value['cli_readiness_gate'].__setitem__('health_status', ''),
            lambda value: value['registration_plane'].__setitem__('mock_admitted', False),
            lambda value: value['registration_plane'].__setitem__('bots_registered', 1),
            lambda value: value['registration_plane'].__setitem__('distinct_bot_ids', False),
            lambda value: value['business_envelope'].__setitem__('enforced', False),
            lambda value: value['business_envelope'].__setitem__('observed_keys',
                                                                 ['code', 'data']),
        )
        for mutator in mutations:
            with self.subTest(mutator=mutator.__name__ if hasattr(mutator, '__name__') else ''):
                with self.assertRaises(AuthorityJourneyViolation):
                    verify_all_obligations(overridden(mutator))

    def test_transfer_window_bookkeeping_is_frozen(self):
        expired = valid_evidence()
        expired['transfer']['refreshed_expires_at'] = INITIATED - 1
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(expired)
        drifted = valid_evidence()
        drifted['transfer']['delta_ms'] = 65536
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(drifted)
        matching = valid_evidence()
        matching['transfer']['initiated_expires_at'] = REFRESHED
        matching['transfer']['refreshed_expires_at'] = REFRESHED
        matching['transfer']['delta_ms'] = 0
        self.assertEqual(verify_all_obligations(matching)['status'], 'PASS')

    def test_redaction_canaries_and_jwt_shapes_are_rejected(self):
        leaked = valid_evidence()
        leaked['business_envelope']['note'] = 'captured Bearer driver value'
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(leaked)
        jwt_shaped = valid_evidence()
        jwt_shaped['mine']['note'] = ('eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJnYXRld2F5In.'
                                      'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c')
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(jwt_shaped)
        token_named = valid_evidence()
        token_named['owner']['note'] = 'bot_token retained beside the observation'
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(token_named)

    def test_obligations_set_and_order_are_exactly_the_frozen_map(self):
        evidence = valid_evidence()
        self.assertEqual(set(evidence['obligations']), set(REQUIREMENTS))
        self.assertEqual(len(evidence['obligations']), 42)
        self.assertEqual([name for name, _ in OBLIGATION_CHECKS], list(REQUIREMENTS))
        self.assertEqual(len(OBLIGATION_CHECKS), 42)
        self.assertTrue(all(callable(fn) for _, fn in OBLIGATION_CHECKS))
        missing = valid_evidence()
        del missing['obligations'][REQUIREMENTS[7]]
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(missing)
        doubled = valid_evidence()
        doubled['obligations']['AUTHORITY-JOURNEY.owner-ownership-read-status-duplicate'] = \
            _status_row('GET', 200)
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(doubled)

    def test_malformed_observation_records_fail_closed(self):
        wrapped = valid_evidence()
        wrapped['obligations'][REQUIREMENTS[0]] = 'product-http'
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations(wrapped)
        emptied = valid_evidence()
        emptied['obligations'][REQUIREMENTS[1]] = None
        with self.assertRaises(RunStateError):
            verify_all_obligations(emptied)
        with self.assertRaises(AuthorityJourneyViolation):
            verify_all_obligations({'obligations': None})

    def test_scan_redaction_observes_canary_presence_without_retaining_it(self):
        clean = scan_redaction('authority-evidence.json', b'{"status": "PASS"}')
        self.assertTrue(clean['clean'])
        self.assertEqual(clean['bytes'], len(b'{"status": "PASS"}'))
        dirty = scan_redaction('authority-story.log', b'leaked Bearer secret-value')
        self.assertFalse(dirty['clean'])
        with self.assertRaises(AuthorityJourneyViolation):
            scan_redaction('', b'clean')
        with self.assertRaises(AuthorityJourneyViolation):
            scan_redaction('story.err.log', 'not-bytes')
        custom = scan_redaction('story.stdout', b'register?token=abc',
                                tokens=('register?token',))
        self.assertFalse(custom['clean'])

    def test_error_class_seam_is_a_run_state_error(self):
        self.assertTrue(issubclass(AuthorityJourneyViolation, RunStateError))


if __name__ == '__main__':
    unittest.main()
