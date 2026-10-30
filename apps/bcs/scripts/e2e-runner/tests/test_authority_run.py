"""Lifecycle, drift, redaction and permission tests for the authority owned run."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.authority import contract
from bcs_e2e.authority.checks import verify_all_obligations
from bcs_e2e.authority.reporting import COMPLETION_NAME
from bcs_e2e.authority.run import (BINDING_NAME, CASE_NAME, EVIDENCE_NAME, FLOW_NAME,
                                   STORY_ERR_LOG_NAME, STORY_LOG_NAME, STORY_SOURCE, STATUS,
                                   local_authority_dual_run, validate_authority_binary,
                                   _parse_story_marker, _redaction_entries)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError, read

OWNER_HUMAN = 'wp2authorityownedhuman00000'
OWNER_BOT = 'bot_1a2b3c4d'
TARGET_BOT = 'bot_5e6f7a8b'
INITIATED = 1800000000000
REFRESHED = INITIATED + 1000
GOOD_MARKER = b'Bot authority journey: 42 live HTTP assertions passed'
RUN_SIGNING_VALUE = 'wp2-run-fence-signing-key-value-0123456789abcdef'
STRICT_AUTH = {'chain': ['local', 'session'], 'require_authentication': False,
               'allow_mock_headers': True}


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
                                                      delta_ms=REFRESHED - INITIATED,
                                                      renewed=True),
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
    return {contract.OBLIGATION_PREFIX + slug: row for slug, row in records.items()}


def valid_evidence() -> dict:
    return {
        'schema_version': 1, 'case_id': 'LOCAL-AUTHORITY-JOURNEY-02', 'identity_mode': 'register',
        'owner': {'human_id': OWNER_HUMAN, 'bot_id': OWNER_BOT},
        'target': {'user_id': '002', 'bot_id': TARGET_BOT},
        'mine': {'user_id': '137'}, 'never_user_id': 'never-seen-run000000001',
        'cli_readiness_gate': {'exit_code': 0, 'health_status': 'healthy',
                               'transport': 'product-cli', 'role': 'readiness-gate-only',
                               'authority_business_verbs': 'none-frozen'},
        'registration_plane': {'mock_admitted': True, 'bots_registered': 2,
                               'distinct_bot_ids': True},
        'business_envelope': {'enforced': True,
                              'observed_keys': ['code', 'message', 'request_id', 'data']},
        'http_call_count': 24, 'bootstrap_http_call_count': 4,
        'business_http_call_count': 20,
        'transfer': {'initiated_expires_at': INITIATED, 'refreshed_expires_at': REFRESHED,
                     'delta_ms': REFRESHED - INITIATED, 'renewed': True},
        'resource_disposition': 'AUTHORITY_TWO_BOTS_CREATED_AND_RETAINED',
        'obligations': obligation_records(),
    }


class FakeOwnedBcs:
    """Owned runtime twin: strict admission config wire, RUNNING then STOPPED."""

    instances: list['FakeOwnedBcs'] = []

    def __init__(self, *, binary, binary_sha256, root, run_id,
                 allow_ambient_local_identity, ready=True, strict_auth=True):
        self.root = Path(root)
        self.binary = Path(binary)
        self.binary_sha256 = binary_sha256
        self.run_id = run_id
        self.ready = ready
        self.strict_auth = strict_auth
        self.inputs = SimpleNamespace(
            human_id=OWNER_HUMAN, origin='http://127.0.0.1:12345',
            environment={contract.PRINCIPAL_SIGNING_KEY_ENV: RUN_SIGNING_VALUE})
        FakeOwnedBcs.instances.append(self)

    def __enter__(self):
        self.root.mkdir(parents=True)
        (self.root / 'console.log').write_text('owned runtime console clean\n', encoding='utf-8')
        auth = dict(STRICT_AUTH) if self.strict_auth else dict(STRICT_AUTH,
                                                               allow_mock_headers=False)
        (self.root / 'bcs-config.json').write_text(json.dumps(
            {'bind': '127.0.0.1', 'port': 1, 'store_messages': False, 'auth': auth}, indent=2),
            encoding='utf-8')
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.run_id, 'status': 'RUNNING',
             'returncode': None, 'ready': self.ready, 'forced_stop': False}), encoding='utf-8')
        return self

    def __exit__(self, *_args):
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.run_id, 'status': 'STOPPED',
             'returncode': 0, 'ready': False, 'forced_stop': False}), encoding='utf-8')
        return False


def fake_story(evidence=None, *, marker=GOOD_MARKER, exit_code=0, stderr=b'',
               mutate_binding=False, capture=None):
    def launch(argv, cwd, deadline, env=None):
        if capture is not None:
            capture['argv'] = [str(item) for item in argv]
            capture['cwd'] = str(cwd)
            capture['deadline'] = deadline
            capture['env'] = dict(env or {})
        evidence_path = Path(argv[argv.index('--evidence-file') + 1])
        payload = valid_evidence() if evidence is None else evidence
        evidence_path.write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')
        if mutate_binding:
            binding_path = Path(cwd) / BINDING_NAME
            value = json.loads(binding_path.read_text(encoding='utf-8'))
            value['run_id'] = 'changed-after-binding'
            binding_path.write_text(json.dumps(value), encoding='utf-8')
        return {'exit_code': exit_code, 'timed_out': False, 'duration_seconds': 0.01,
                'stdout': marker, 'stderr': stderr, 'process': None}
    return launch


class AuthorityRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.binary = self.base / 'bcs'
        self.binary.write_bytes(b'owned-bcs-binary\n')
        self.binary.chmod(0o700)
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.cli_binary = self.base / 'bcs-cli'
        self.cli_binary.write_bytes(b'owned-bcs-cli-binary\n')
        self.cli_binary.chmod(0o700)
        self.cli_sha = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()
        self.state = self.base / 'state'
        self.kwargs = dict(binary=self.binary, binary_sha256=self.sha,
                           cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha,
                           state_dir=self.state, run_id='wp2-authority-run-ok',
                           confirm_run_id='wp2-authority-run-ok', allow_local_processes=True,
                           allow_fixture_write=True, allow_write=True)
        FakeOwnedBcs.instances = []

    def tearDown(self):
        self.temp.cleanup()

    def run_story(self, launch, **overrides):
        kwargs = dict(self.kwargs)
        kwargs.update(overrides)
        with patch('bcs_e2e.authority.run.OwnedBcsProcess', FakeOwnedBcs), \
                patch('bcs_e2e.authority.run._launch_story', side_effect=launch):
            return local_authority_dual_run(**kwargs)

    # ------------------------------------------------------------- happy path
    def test_success_publishes_the_dual_local_receipt_without_release_credit(self):
        captured = {}
        result = self.run_story(fake_story(capture=captured),
                                run_id='wp2-authority-run-ok',
                                confirm_run_id='wp2-authority-run-ok')
        self.assertEqual(result['status'], STATUS)
        self.assertNotIn('error_type', result)
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['canonical_summary']['verification_boundary']['grants_release_credit'])
        self.assertFalse(result['canonical_summary']['ready_to_execute'])
        self.assertEqual(result['canonical_summary']['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(result['canonical_summary']['verification_boundary']['preprod'],
                         'DUAL_PREPROD_LEG_OUTSIDE_THIS_LOCAL_RECEIPT')
        # child argv contract: frozen ids, env-name signing reference, no secret value
        argv = captured['argv']
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(Path(argv[1]).name == 'story.py')
        self.assertEqual(argv[argv.index('--base-url') + 1], 'http://127.0.0.1:12345')
        self.assertEqual(argv[argv.index('--owner-human') + 1], OWNER_HUMAN)
        self.assertEqual(argv[argv.index('--target-user-id') + 1], '002')
        self.assertEqual(argv[argv.index('--mine-user-id') + 1], '137')
        self.assertEqual(argv[argv.index('--principal-signing-key-env') + 1],
                         contract.PRINCIPAL_SIGNING_KEY_ENV)
        self.assertIn('--run-tag', argv)
        self.assertEqual(argv[argv.index('--run-tag') + 1], 'wp2-authority-run-ok')
        self.assertIn('--cli-binary', argv)
        self.assertEqual(Path(argv[argv.index('--cli-binary') + 1]), self.cli_binary)
        self.assertIn('--evidence-file', argv)
        self.assertNotIn(RUN_SIGNING_VALUE, json.dumps(argv))
        # env threading carries the signing key by name into the child environment
        child_env = captured['env']
        self.assertEqual(child_env.get(contract.PRINCIPAL_SIGNING_KEY_ENV), RUN_SIGNING_VALUE)
        self.assertIn('PATH', child_env)
        # dual-case wiring constants inside the receipt
        self.assertEqual(result['case_id'], contract.CASE_ID)
        self.assertEqual(result['bcs_binary_sha256'], self.sha)
        self.assertEqual(result['cli_binary_sha256'], self.cli_sha)
        self.assertFalse(result['auto_build'])
        self.assertTrue(result['strict_auth'])

    def test_success_writes_the_frozen_owned_local_evidence_tree(self):
        result = self.run_story(fake_story())
        root = self.state
        for name in (BINDING_NAME, EVIDENCE_NAME, FLOW_NAME, CASE_NAME, COMPLETION_NAME,
                     STORY_LOG_NAME, STORY_ERR_LOG_NAME, 'owned-bcs.json', 'bcs-config.json',
                     'console.log'):
            self.assertTrue((root / name).is_file(), name)
        for report in ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md'):
            self.assertTrue((root / report).is_file(), report)
        self.assertEqual(result['http_call_count'], 24)
        evidence = read(root / EVIDENCE_NAME)
        self.assertEqual(evidence['case_id'], 'LOCAL-AUTHORITY-JOURNEY-02')
        self.assertEqual(verify_all_obligations(evidence)['status'], 'PASS')
        case = read(root / CASE_NAME)
        self.assertEqual(case['status'], STATUS)
        self.assertEqual(len(case['requirements']), 42)
        self.assertTrue(all(row['status'] == 'PASS' for row in case['requirements']))
        self.assertEqual(case['story_markers'], {'observed': True, 'live_http_assertions': 42})
        self.assertEqual(case['resource_disposition'],
                         'AUTHORITY_TWO_BOTS_CREATED_AND_RETAINED')
        self.assertEqual(case['checks']['status'], 'PASS')
        self.assertEqual(case['redaction']['status'], 'PASS')
        binding = read(root / BINDING_NAME)
        self.assertEqual(binding['binary_kind'], 'bcs')
        self.assertEqual(binding['bcs_binary_sha256'], self.sha)
        self.assertEqual(binding['cli_binary_sha256'], self.cli_sha)
        self.assertEqual(binding['strict_auth'], True)
        self.assertEqual(binding['auto_build'], False)
        self.assertEqual(binding['selected_requirement_ids'], list(contract.REQUIREMENTS))
        self.assertEqual(binding['environment'], contract.ENVIRONMENT)
        self.assertEqual(binding['frozen_binaries'],
                         {'bcs_sha256': contract.BCS_BINARY_SHA256,
                          'bcs_cli_sha256': contract.CLI_BINARY_SHA256})
        self.assertEqual(binding['human_id'], OWNER_HUMAN)
        self.assertEqual(len(binding['selected_requirement_ids']), 42)
        completion = read(root / COMPLETION_NAME)
        self.assertEqual(completion['contract'], 'owned-local-authority-dual-completion-v1')
        flow = read(root / FLOW_NAME)
        self.assertEqual(flow['status'], STATUS)
        self.assertEqual(flow['case']['status'], STATUS)

    def test_owned_evidence_tree_stays_credential_free(self):
        self.run_story(fake_story())
        root = self.state
        for path in sorted(root.rglob('*')):
            if path.is_file():
                raw = path.read_bytes()
                scanned = raw.decode('utf-8', errors='replace')
                # credential-bearing forms are banned on every owned surface
                self.assertNotRegex(scanned, r'bot_token\s*[:=]\s*["\'][A-Za-z0-9]', str(path))
                self.assertNotRegex(scanned, r'Bearer\s+[A-Za-z0-9._-]{20,}', str(path))
                self.assertNotRegex(scanned, r'X-Mock-User-Id:\s*[^$\s"\'\\\\]', str(path))
                self.assertNotIn(RUN_SIGNING_VALUE, scanned, str(path))
                # committed Bot ids are legitimate child-evidence rows only;
                # every other owned-local surface stays free of real bot ids
                if path.name != EVIDENCE_NAME:
                    self.assertNotRegex(scanned, r'bot_[0-9a-f]{8}', str(path))
                # the frozen binding legally declares the redaction-token and
                # principal-header NAMES plus the upstream anchor literals;
                # runtime surfaces must not carry even those names
                if path.name != BINDING_NAME:
                    for canary in ('bot_token', 'X-Avernet-Principal', 'register?token',
                                   'Bearer '):
                        self.assertNotIn(canary.encode(), raw, str(path))
        stdout_log = (root / STORY_LOG_NAME).read_bytes()
        self.assertEqual(stdout_log, GOOD_MARKER)

    # ------------------------------------------------------------ blocked legs
    def test_bad_marker_or_nonzero_exit_is_blocked(self):
        cases = (fake_story(marker=b'Bot authority journey: 37 live HTTP assertions passed'),
                 fake_story(exit_code=1))
        for launch in cases:
            with self.subTest(launch=launch):
                state = self.base / ('state-' + str(len(list(self.base.iterdir()))))
                kwargs = dict(self.kwargs)
                kwargs['state_dir'] = state
                with patch('bcs_e2e.authority.run.OwnedBcsProcess', FakeOwnedBcs), \
                        patch('bcs_e2e.authority.run._launch_story', side_effect=launch):
                    result = local_authority_dual_run(**kwargs)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['canonical_summary']['status'], 'BLOCKED')

    def test_obligation_violation_in_child_evidence_blocks_the_case(self):
        evidence = valid_evidence()
        evidence['obligations'][contract.OBLIGATION_PREFIX
                                + 'manager-add-added-one']['added'] = 2
        result = self.run_story(fake_story(evidence=evidence))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['checks']['status'], 'BLOCKED')
        self.assertEqual(result['case'], {})

    def test_binding_or_runtime_drift_is_blocked(self):
        result = self.run_story(fake_story(mutate_binding=True))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'DriftFenceError')
        self.assertEqual(result['canonical_summary']['status'], 'BLOCKED')
        self.assertFalse((self.state / 'authority-dual-completion.json').exists())

    def test_unready_owned_runtime_blocks(self):
        with patch('bcs_e2e.authority.run.OwnedBcsProcess',
                   lambda **kwargs: FakeOwnedBcs(ready=False, **kwargs)), \
                patch('bcs_e2e.authority.run._launch_story', side_effect=fake_story()):
            result = local_authority_dual_run(**self.kwargs)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')

    def test_strict_admission_configuration_is_fenced(self):
        with patch('bcs_e2e.authority.run.OwnedBcsProcess',
                   lambda **kwargs: FakeOwnedBcs(strict_auth=False, **kwargs)), \
                patch('bcs_e2e.authority.run._launch_story', side_effect=fake_story()):
            result = local_authority_dual_run(**self.kwargs)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')

    def test_story_deadline_is_enforced_and_reported(self):
        def hanging(argv, cwd, deadline, env=None):
            raise RunStateError('authority story exceeded its 900-second deadline')
        with patch('bcs_e2e.authority.run.OwnedBcsProcess', FakeOwnedBcs), \
                patch('bcs_e2e.authority.run._launch_story', side_effect=hanging):
            result = local_authority_dual_run(**self.kwargs)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')

    def test_credential_leak_in_child_stream_blocks_and_never_writes_child_logs(self):
        result = self.run_story(fake_story(stderr=b'leaked Bearer secret-value'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RedactionGateError')
        self.assertFalse((self.state / STORY_ERR_LOG_NAME).exists())
        self.assertNotIn('secret-value', json.dumps(result))

    # ----------------------------------------------------------- entry fences
    def test_binary_validation_and_marker_seams(self):
        self.assertEqual(_parse_story_marker(GOOD_MARKER.decode()), (True, 42))
        self.assertEqual(_parse_story_marker(b'Bot authority journey: 5 live HTTP assertions passed'),
                         (True, 5))
        self.assertEqual(_parse_story_marker(b'nothing here'), (False, 0))
        self.assertEqual(validate_authority_binary(self.binary, self.sha), self.binary)
        with self.assertRaises(RunStateError):
            validate_authority_binary(self.binary, '0' * 64)
        self.binary.chmod(0o600)
        with self.assertRaises(RunStateError):
            validate_authority_binary(self.binary, self.sha)
        self.binary.chmod(0o700)
        link = self.base / 'link-bcs'
        link.symlink_to(self.binary)
        with self.assertRaises(RunStateError):
            validate_authority_binary(link, self.sha)
        with self.assertRaises(RunStateError):
            validate_authority_binary(self.base / 'absent-bcs', self.sha)
        self.assertTrue(Path(STORY_SOURCE).name == 'story.py')
        self.assertTrue(Path(STORY_SOURCE).is_file())

    def test_redaction_entries_gate_streams_and_artifacts(self):
        streams, artifacts, ok = _redaction_entries(
            [('story.stdout', b'clean'), ('story.stderr', b'leaked Bearer value')],
            [('child.receipt', b'{}')])
        self.assertTrue(streams[0]['clean'])
        self.assertFalse(streams[1]['clean'])
        self.assertTrue(artifacts[0]['clean'])
        self.assertFalse(ok)

    def test_wrong_binary_digests_fail_closed_before_any_state_directory(self):
        for field, value in (('binary_sha256', 'f' * 64), ('cli_binary_sha256', 'e' * 64)):
            with self.subTest(field=field):
                kwargs = dict(self.kwargs)
                kwargs[field] = value
                with self.assertRaises(RunStateError):
                    local_authority_dual_run(**kwargs)
                self.assertFalse(self.state.exists())

    def test_permissions_fresh_state_and_exact_confirmation_are_required(self):
        for field, value in (('allow_local_processes', False), ('allow_fixture_write', False),
                             ('allow_write', False), ('confirm_run_id', 'wp2-other-run')):
            with self.subTest(field=field):
                kwargs = dict(self.kwargs)
                kwargs[field] = value
                with self.assertRaises(RunStateError):
                    local_authority_dual_run(**kwargs)
                self.assertFalse(self.state.exists())
        self.state.mkdir()
        with self.assertRaisesRegex(RunStateError, 'fresh state directory'):
            with patch('bcs_e2e.authority.run.OwnedBcsProcess', FakeOwnedBcs), \
                    patch('bcs_e2e.authority.run._launch_story', side_effect=fake_story()):
                local_authority_dual_run(**self.kwargs)
        self.state.rmdir()
        link_state = self.base / 'linked-state'
        link_state.symlink_to(self.base / 'elsewhere')
        with self.assertRaises(RunStateError):
            with patch('bcs_e2e.authority.run.OwnedBcsProcess', FakeOwnedBcs), \
                    patch('bcs_e2e.authority.run._launch_story', side_effect=fake_story()):
                local_authority_dual_run(**dict(self.kwargs, state_dir=link_state / 'inner'))

    def test_run_id_identifier_gate(self):
        with self.assertRaises(ConfigError):
            with patch('bcs_e2e.authority.run.OwnedBcsProcess', FakeOwnedBcs), \
                    patch('bcs_e2e.authority.run._launch_story', side_effect=fake_story()):
                local_authority_dual_run(**dict(self.kwargs, run_id='wp2 run id',
                                                 confirm_run_id='wp2 run id'))

    def test_blocked_child_never_publishes_the_case_file(self):
        result = self.run_story(fake_story(marker=b'Bot authority journey: 41 live HTTP assertions passed'))
        self.assertEqual(result['status'], 'BLOCKED')
        root = self.state
        self.assertFalse((root / CASE_NAME).exists())
        # A blocked child still discloses itself through an honest BLOCKED
        # completion and canonical report (mirroring the draft_delivery
        # publication structure); only the drift branch returns early.
        completion = read(root / COMPLETION_NAME)
        self.assertEqual(completion['contract'], 'owned-local-authority-dual-completion-v1')
        summary = read(root / 'reports' / 'summary.json')
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertFalse(summary['verification_boundary']['grants_release_credit'])
        self.assertTrue((root / FLOW_NAME).is_file())
        self.assertTrue((root / BINDING_NAME).is_file())
        self.assertTrue((root / EVIDENCE_NAME).is_file())


if __name__ == '__main__':
    unittest.main()
