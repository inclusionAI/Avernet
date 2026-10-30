"""Contract, biscuit, profile, probe, journal-mapping, fail-closed and seam
tests for the dual Session owner permissions case run (story #6)."""
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcs_e2e.session_owner.dual_case_run as dual
from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.session_owner.dual_case_run import (
    CASE_ID, CLEANUP_CHECKS, COMMAND, DEFAULT_LEAF_ROOT, HEADER_ENV,
    LEAF_ROOT_NAME, PROFILE_CONTRACT, PROBE_ROWS, REGISTRATION_SEAM,
    REPORT_CONTRACT, REQUIREMENTS,
)
from bcs_e2e.session_owner.local_session_owner_run import (
    JOURNAL_NAME, RESOURCE_DISPOSITION_PASS, SessionOwnerRunError,
)
from bcs_e2e.session_owner.remote_session_owner_adapter import RemoteSessionOwnerAdapter
from bcs_e2e.session_owner.session_owner_contract import (
    DOCUMENTED_GAPS, STORY_CHECKS, session_owner_source_binding,
)
from bcs_e2e.session_owner.dual_case_run import (
    dual_session_owner_case_run, dual_session_owner_case_command,
    load_session_owner_profile, register_session_owner_dual_case_commands,
    run_preprod_session_owner_route_probe, validate_session_owner_profile,
)

RUN_ID = '20261031-migration-wp2-session-owner-test'
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
COOKIE_CANARY = 'synthetic-cookie-value-never-persisted'
DRIVER_CANARY = 'synthetic-driver-token-never-persisted'


def executable_binary(root: Path, name: str = 'bcs-fixture') -> tuple[Path, str]:
    binary = root / name
    binary.write_bytes(b'owned-bcs-fixture-bytes')
    binary.chmod(0o700)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def local_profile() -> dict:
    return {
        'schema_version': 1,
        'contract': PROFILE_CONTRACT,
        'environment': {'tier': 'local', 'id': 'session-owner-local',
                        'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'owned-local'},
        'human': {'user_id': 'e2e-session-owner-fixture'},
        'auth': {'kind': 'owned-local'},
        'cli': {'transport': 'owned-local-story', 'command_family': 'session-owner',
                'api_face': 'public-v1'},
        'policy': {'fixture_pool': 'session-owner-story', 'exclusive_use_confirmed': True,
                   'timeout_seconds': 10, 'max_response_bytes': 65536,
                   'failure_retention_hours': 24},
    }


def preprod_profile() -> dict:
    value = local_profile()
    value['environment'] = {'tier': 'preprod', 'id': '20261031-migration-wp2-session-pre',
                            'expected_bcs_env': 'pre'}
    value['entrypoint'] = {'kind': 'gateway', 'origin': 'https://bcs-pre.alipay.com',
                           'api_face': 'public-v1', 'manifest_face': 'legacy'}
    value['human'] = {'user_id': '39464'}
    value['auth'] = {'kind': 'preprod-human-cookie',
                     'header_env': {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}}
    value['cli'] = {'transport': 'product-cli-http', 'command_family': 'session-owner',
                    'api_face': 'public-v1'}
    return value


def journal(*, requirements: dict | None = None, status: str = 'LOCAL_SESSION_OWNER_PASS',
            error_type: str | None = None) -> dict:
    value = {'schema_version': 1, 'status': status, 'story_http_checks': STORY_CHECKS,
             'cleanup_http_checks': CLEANUP_CHECKS,
             'observed_assertion_count': STORY_CHECKS + CLEANUP_CHECKS,
             'story_markers': {'observed': True, 'http_checks': STORY_CHECKS},
             'resource_disposition': RESOURCE_DISPOSITION_PASS,
             'requirements': [{'id': name,
                               'status': (requirements or {}).get(name, 'PASS')}
                              for name in REQUIREMENTS],
             'operations': [], 'checks': [], 'resources': []}
    if error_type is not None:
        value['error_type'] = error_type
    return value


def local_result(*, requirements: dict | None = None) -> dict:
    return {'status': 'LOCAL_SESSION_OWNER_PASS',
            'journal': journal(requirements=requirements),
            'canonical_summary': {'status': 'LOCAL_SUITE_PASS'}}


class FakeProbeClient:
    """Scripted statuses without opening a socket."""

    def __init__(self, statuses: dict | None = None, *, baseline: int = 404):
        self.statuses = statuses or {}
        self.baseline = baseline
        self.calls = []

    def request(self, *, method: str, path: str) -> dict:
        self.calls.append({'method': method, 'path': path})
        status = self.baseline if path == dual._BASELINE_PATH else self.statuses.get(path, 404)
        return {'method': method, 'path': path, 'http_status': status,
                'response_bytes': 0, 'cookie_sent': False,
                'authorization_sent': False, 'transport_error': None}


def scripted_statuses() -> dict:
    statuses = {}
    for row in PROBE_ROWS:
        statuses[dual._probe_path(row['path'])] = (
            405 if row['expected_present'] else 404)
    return statuses


class SessionOwnerDualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary, self.server_sha = executable_binary(self.root)
        self.cli_binary, self.cli_sha = executable_binary(self.root, name='bcs-cli-fixture')

    def _write_profile(self, value: dict, name: str) -> Path:
        path = self.root / f'profile-{name}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    # ------------------------------------------------------------- biscuits
    def test_case_run_signature_admits_both_frozen_biscuit_pairs(self):
        parameters = inspect.signature(dual_session_owner_case_run).parameters
        for name in ('binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256'):
            parameter = parameters[name]
            self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(parameter.default, inspect.Parameter.empty, name)
        self.assertEqual(parameters['leaf_root'].default, None)

    def test_frozen_pair_validation_rejects_symlinks_missing_files_and_drift(self):
        good, good_sha = executable_binary(self.root, name='pair-bcs')
        self.assertEqual(dual._validate_frozen_pair(good, good_sha, label='CLI'), good)
        with self.assertRaisesRegex(RunStateError, 'digest differs'):
            dual._validate_frozen_pair(good, 'f' * 64, label='BCS')
        linked = self.root / 'linked-bcs'
        linked.symlink_to(good)
        with self.assertRaisesRegex(RunStateError, 'regular frozen'):
            dual._validate_frozen_pair(linked, good_sha, label='CLI')
        locked = self.root / 'nonexec-bcs'
        locked.write_bytes(b'x')
        locked.chmod(0o640)
        with self.assertRaisesRegex(RunStateError, 'executable'):
            dual._validate_frozen_pair(locked, hashlib.sha256(b'x').hexdigest(), label='CLI')

    def test_server_pair_reuses_the_frozen_family_validator(self):
        from bcs_e2e.session_owner.local_session_owner_run import validate_session_owner_binary
        self.assertEqual(validate_session_owner_binary(self.binary, self.server_sha),
                         self.binary)
        with self.assertRaises(RunStateError):
            validate_session_owner_binary(self.binary, 'e' * 64)

    # ------------------------------------------------------------- profiles
    def test_both_tier_profiles_validate(self):
        self.assertEqual(validate_session_owner_profile(local_profile())['environment']['tier'],
                         'local')
        validated = validate_session_owner_profile(preprod_profile())
        self.assertEqual(validated['entrypoint']['api_face'], 'public-v1')
        self.assertEqual(validated['auth']['header_env'],
                         {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
        self.assertEqual(load_session_owner_profile(
            self._write_profile(preprod_profile(), 'valid'))['environment']['id'],
            '20261031-migration-wp2-session-pre')

    def test_profile_rejects_shape_placeholders_and_face_drift(self):
        cases = (
            ('unknown-top-level', lambda v: v.__setitem__('credential_files', {})),
            ('contract', lambda v: v.__setitem__('contract', 'wrong')),
            ('tier', lambda v: v['environment'].__setitem__('tier', 'main')),
            ('local-entrypoint-kind', lambda v: v['entrypoint'].__setitem__('kind', 'bcs-direct')),
            ('local-auth-kind', lambda v: v['auth'].__setitem__('kind', 'preprod-human-cookie')),
            ('local-cli-transport', lambda v: v['cli'].__setitem__('transport', 'product-cli-http')),
            ('local-cli-face', lambda v: v['cli'].__setitem__('api_face', 'legacy')),
            ('preprod-entrypoint-kind', lambda v: v['entrypoint'].__setitem__('kind', 'owned-local')),
            ('preprod-origin-http', lambda v: v['entrypoint'].__setitem__(
                'origin', 'http://bcs-pre.alipay.com')),
            ('preprod-face', lambda v: v['entrypoint'].__setitem__('api_face', 'legacy')),
            ('preprod-manifest', lambda v: v['entrypoint'].__setitem__('manifest_face', 'public-v1')),
            ('human', lambda v: v['human'].__setitem__('user_id', 'user name')),
            ('auth-kind-swap', lambda v: v['auth'].__setitem__('kind', 'owned-local')),
            ('header-env-missing-cookie', lambda v: v['auth']['header_env'].pop('Cookie')),
            ('header-env-extra', lambda v: v['auth']['header_env'].__setitem__(
                'X-Extra', 'BCS_E2E_DRIVER_TOKEN')),
            ('cli-family', lambda v: v['cli'].__setitem__('command_family', 'session')),
            ('cli-transport-swap', lambda v: v['cli'].__setitem__(
                'transport', 'owned-local-story')),
            ('policy-exclusive', lambda v: v['policy'].__setitem__('exclusive_use_confirmed', False)),
            ('policy-timeout-high', lambda v: v['policy'].__setitem__('timeout_seconds', 31)),
            ('policy-bytes-zero', lambda v: v['policy'].__setitem__('max_response_bytes', 0)),
            ('policy-retention-high', lambda v: v['policy'].__setitem__(
                'failure_retention_hours', 169)),
        )
        for name, mutate in cases:
            with self.subTest(mutation=name):
                value = copy.deepcopy(local_profile() if name.startswith('local-')
                                      else preprod_profile())
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_session_owner_profile(value)

    # --------------------------------------------------------------- probes
    def test_probe_classifies_present_and_absent_routes_against_the_baseline(self):
        client = FakeProbeClient(scripted_statuses())
        result = run_preprod_session_owner_route_probe(preprod_profile(), credentials={},
                                                       client=client)
        self.assertEqual(result['status'], 'PASS')
        self.assertIsNone(result['error_type'])
        self.assertEqual(len(client.calls), len(PROBE_ROWS) + 1)
        self.assertEqual(client.calls[0]['path'], dual._BASELINE_PATH)
        present = sum(1 for row in PROBE_ROWS if row['expected_present'])
        self.assertEqual(result['present_count'], present)
        self.assertEqual(result['absent_count'], len(PROBE_ROWS) - present)
        self.assertEqual(result['match_count'], len(PROBE_ROWS))
        self.assertFalse(result['credentialed'])
        self.assertFalse(result['grants_release_credit'])

    def test_probe_rows_split_public_v1_present_from_documented_gap_absent(self):
        present_rows = [row for row in PROBE_ROWS if row['expected_present']]
        absent_rows = [row for row in PROBE_ROWS if not row['expected_present']]
        self.assertEqual(len(present_rows), 13)
        self.assertEqual(len(absent_rows), 11)
        gateway_absent = [row for row in absent_rows if row['face'] == 'gateway-public-v1']
        self.assertEqual(len(gateway_absent), 5)
        self.assertEqual({row['operation'] for row in gateway_absent}, {
            'gateway.session_participants_add', 'gateway.session_participants_patch',
            'gateway.session_participants_remove', 'gateway.session_collect_add',
            'gateway.session_collect_remove'})
        self.assertTrue(all('participants' in row['operation'] or 'collect' in row['operation']
                            for row in gateway_absent))
        for row in absent_rows:
            if row['face'] == 'station-legacy-bare':
                self.assertIn(row['operation'], ('station.ensure_human',
                                                 'station.register_token',
                                                 'station.register_bot',
                                                 'station.bot_onboard',
                                                 'station.bot_delete',
                                                 'station.bot_visibility'))

    def test_probe_blocks_on_unexpected_baseline_or_route_mismatch(self):
        blocked = run_preprod_session_owner_route_probe(
            preprod_profile(), credentials={}, client=FakeProbeClient({}, baseline=401))
        self.assertEqual(blocked['status'], 'BLOCKED')
        self.assertEqual(blocked['error_type'], 'PREPROD_PROBE_BASELINE_UNEXPECTED')
        mismatch = FakeProbeClient()
        result = run_preprod_session_owner_route_probe(
            preprod_profile(), credentials={}, client=mismatch)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'PREPROD_SESSION_OWNER_ROUTE_UNEXPECTED')

    def test_probe_never_uses_a_bound_write_method_on_reviewed_routes(self):
        for row in PROBE_ROWS:
            self.assertNotEqual(row['canonical_method'], row['probe_method'])

    # -------------------------------------------------------- admissions
    def test_credential_admission_resolves_only_named_references(self):
        with patch.dict(os.environ, {COOKIE_ENV: COOKIE_CANARY, DRIVER_ENV: DRIVER_CANARY}):
            admission = dual._credential_admission(HEADER_ENV)
        self.assertEqual(admission['status'], 'ADMITTED')
        self.assertEqual(admission['resolved_references'], sorted(HEADER_ENV.values()))
        self.assertNotIn(COOKIE_CANARY, json.dumps(admission))

    def test_credential_admission_fail_closes_without_storing_values(self):
        with patch.dict(os.environ, {}, clear=True):
            admission = dual._credential_admission(HEADER_ENV)
        self.assertEqual(admission['status'], 'BLOCKED')
        self.assertEqual(admission['reason_class'],
                         'PREPROD_SESSION_OWNER_CREDENTIALS_UNAVAILABLE')
        self.assertIn(HEADER_ENV['Cookie'], admission['reason'])
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(dual.PreprodClosedError) as ctx:
                dual._resolve_credential_values(HEADER_ENV)
        self.assertEqual(ctx.exception.reason_class,
                         'PREPROD_SESSION_OWNER_CREDENTIALS_UNAVAILABLE')

    def test_camel_case_reader_is_logging_only(self):
        value, spelling = dual._camel_case_field({'requestId': 'abc'}, 'requestId', 'request_id')
        self.assertEqual((value, spelling), ('abc', 'requestId'))
        self.assertEqual(dual._camel_case_field({'request_id': 'abc'}, 'requestId', 'request_id'),
                         ('abc', 'request_id'))
        self.assertEqual(dual._camel_case_field({}, 'requestId', 'request_id'), (None, 'absent'))
        self.assertEqual(dual._serialization_sample()['decision_use'], 'none_logging_only')

    def test_remote_adapter_preflight_is_offline_and_reports_the_four_gaps(self):
        profile = validate_session_owner_profile(preprod_profile())
        receipt = RemoteSessionOwnerAdapter(profile).preflight()
        self.assertEqual(receipt['status'], 'NOT_RUN')
        self.assertEqual(receipt['gap_ids'], list(DOCUMENTED_GAPS.keys()))
        self.assertEqual(receipt['documented_gaps'], dict(DOCUMENTED_GAPS))
        self.assertFalse(receipt['grants_release_credit'])
        for key in DOCUMENTED_GAPS:
            self.assertIn(key, receipt['documented_gaps'])

    # ------------------------------------------------------ journal mapping
    def test_local_execution_maps_the_complete_journal(self):
        execution = dual._session_local_execution(local_result())
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, len(REQUIREMENTS))
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        self.assertTrue(all(step.status == 'PASS' for step in execution.steps))
        execution.validate()
        self.assertEqual(execution.steps[0].evidence['story_http_checks'], STORY_CHECKS)
        self.assertEqual(execution.steps[0].evidence['cleanup_http_checks'], CLEANUP_CHECKS)
        self.assertEqual(execution.steps[0].evidence['author_cli_pair'],
                         'author_cli_pair_admitted_frozen_not_consumed_by_family_journey')

    def test_local_execution_maps_partial_journal_progress_honestly(self):
        failed = dict.fromkeys(REQUIREMENTS, 'PASS')
        failed[REQUIREMENTS[3]] = 'NOT_RUN'
        execution = dual._session_local_execution(
            {'status': 'BLOCKED', 'journal': journal(requirements=failed,
                                                     status='BLOCKED',
                                                     error_type='SessionOwnerRunError'),
             'canonical_summary': {}, 'error_type': 'SessionOwnerRunError'})
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 3)
        steps = list(execution.steps)
        self.assertTrue(all(step.status == 'PASS' for step in steps[:3]))
        self.assertEqual(steps[3].status, 'NOT_RUN')
        self.assertEqual(steps[3].error_type, 'SessionOwnerRunError')
        self.assertTrue(all(step.status == 'NOT_RUN' and step.error_type is None
                            for step in steps[4:]))

    def test_local_execution_gates_on_the_frozen_journal_accounting(self):
        for mutation in (
                {'story_http_checks': 59},
                {'cleanup_http_checks': 3},
                {'observed_assertion_count': 63},
                {'story_markers': {'observed': True, 'http_checks': 59}},
                {'resource_disposition': 'UNVERIFIED'},
        ):
            with self.subTest(mutation=mutation):
                broken = journal()
                broken.update(mutation)
                execution = dual._session_local_execution(
                    {'status': 'LOCAL_SESSION_OWNER_PASS', 'journal': broken,
                     'canonical_summary': {'status': 'LOCAL_SUITE_PASS'}})
                self.assertEqual(execution.status, 'BLOCKED')
        missing_canonical = local_result()
        missing_canonical['canonical_summary'] = {'status': 'BLOCKED'}
        self.assertEqual(dual._session_local_execution(missing_canonical).status, 'BLOCKED')

    # --------------------------------------------------------------- entry
    def test_local_tier_delegates_and_writes_binding_profile_and_report(self):
        state_dir = self.root / 'local-state'
        with patch('bcs_e2e.session_owner.dual_case_run.local_session_owner_run',
                   return_value=local_result()) as execute:
            report = dual_session_owner_case_run(
                profile_path=self._write_profile(local_profile(), 'local'),
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha, leaf_root=self.root / 'leaves')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['case_id'], CASE_ID)
        self.assertEqual(report['execution']['completed_count'], len(REQUIREMENTS))
        self.assertEqual(report['obligation_summary']['story_checks'], STORY_CHECKS)
        self.assertEqual(report['obligation_summary']['cleanup_checks'], CLEANUP_CHECKS)
        self.assertEqual(report['local_owned_run']['status'], 'LOCAL_SESSION_OWNER_PASS')
        self.assertEqual(report['local_owned_run']['journal']['story_http_checks'], STORY_CHECKS)
        self.assertEqual(report['local_owned_run']['journal']['cleanup_http_checks'],
                         CLEANUP_CHECKS)
        self.assertEqual(report['resource_disposition'], RESOURCE_DISPOSITION_PASS)
        self.assertEqual(report['environment']['tier'], 'local')
        self.assertEqual(report['credential_kind'], 'owned_local_ephemeral_gateway_principals')
        self.assertFalse(report['grants_release_credit'])
        self.assertEqual(report['author_cli_pair']['status'],
                         'author_cli_pair_admitted_frozen_not_consumed_by_family_journey')
        execute.assert_called_once()
        kwargs = execute.call_args.kwargs
        self.assertEqual(kwargs['state_dir'], state_dir / 'owned-session-owner')
        self.assertTrue(kwargs['allow_write'])
        read(state_dir / 'session-owner-dual-run.json')
        binding = read(state_dir / 'session-owner-dual-binding.json')
        self.assertEqual(binding['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['cleanup_checks'], CLEANUP_CHECKS)

    def test_local_tier_maps_an_incomplete_journal_to_blocked(self):
        state_dir = self.root / 'local-blocked'
        partial = local_result()
        partial['status'] = 'BLOCKED'
        partial['canonical_summary'] = {'status': 'BLOCKED'}
        partial['journal'] = journal(requirements=dict.fromkeys(REQUIREMENTS, 'NOT_RUN'),
                                     status='BLOCKED', error_type='SessionOwnerRunError')
        partial['error_type'] = 'SessionOwnerRunError'
        with patch('bcs_e2e.session_owner.dual_case_run.local_session_owner_run',
                   return_value=partial):
            report = dual_session_owner_case_run(
                profile_path=self._write_profile(local_profile(), 'local'),
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)
        self.assertEqual(report['execution']['error_type'], 'SessionOwnerRunError')
        self.assertEqual(report['case']['steps'][0]['error_type'],
                         'SessionOwnerRunError')

    def test_local_tier_surfaces_the_retained_journal_when_the_executor_raises(self):
        state_dir = self.root / 'local-raise'
        retained = journal(requirements={REQUIREMENTS[0]: 'PASS',
                                         REQUIREMENTS[1]: 'PASS'},
                           status='BLOCKED', error_type='SessionOwnerRunError')
        for row in retained['requirements'][2:]:
            row['status'] = 'NOT_RUN'

        def fake_executor(**kwargs):
            owned = Path(kwargs['state_dir'])
            owned.mkdir(parents=True, exist_ok=True)
            (owned / JOURNAL_NAME).write_text(json.dumps(retained), encoding='utf-8')
            raise SessionOwnerRunError('retained journal story failure')

        with patch('bcs_e2e.session_owner.dual_case_run.local_session_owner_run',
                   side_effect=fake_executor):
            report = dual_session_owner_case_run(
                profile_path=self._write_profile(local_profile(), 'local'),
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['session_local_failure']['error_type'],
                         'SessionOwnerRunError')
        self.assertEqual(report['local_owned_run']['journal']['status'], 'BLOCKED')
        steps = report['case']['steps']
        self.assertEqual([step['status'] for step in steps[:2]], ['PASS', 'PASS'])
        self.assertEqual(steps[2]['error_type'], 'SessionOwnerRunError')
        self.assertTrue(all(step['status'] == 'NOT_RUN' for step in steps[3:]))
        self.assertEqual(report['execution']['completed_count'], 2)

    def test_command_gates_reject_insecure_write_and_fresh_state(self):
        profile_path = self._write_profile(local_profile(), 'gates')
        with self.assertRaisesRegex(ConfigError, 'explicit write permission'):
            dual_session_owner_case_run(
                profile_path=profile_path, state_dir=self.root / 'g1', run_id=RUN_ID,
                allow_write=False, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)
        with self.assertRaisesRegex(ConfigError, 'exact Run confirmation'):
            dual_session_owner_case_run(
                profile_path=profile_path, state_dir=self.root / 'g2', run_id=RUN_ID,
                allow_write=True, confirm_run_id='other', binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)
        existing = self.root / 'g3'
        existing.mkdir()
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_session_owner_case_run(
                profile_path=profile_path, state_dir=existing, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)

    def test_missing_biscuit_pairs_fail_closed_on_the_server_leg(self):
        profile_path = self._write_profile(local_profile(), 'biscuits')
        with self.assertRaises(RunStateError):
            dual_session_owner_case_run(
                profile_path=profile_path, state_dir=self.root / 'biscuit-state',
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                binary=self.binary, binary_sha256='mismatch',
                cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha)

    def test_preprod_tier_fail_closes_with_admission_probe_preflight_and_leaf(self):
        state_dir = self.root / 'preprod-state'
        leaf_root = self.root / 'preprod-leaves'
        scripted_probe = {'status': 'PASS', 'error_type': None, 'baseline': {}, 'checks': [],
                          'present_count': 13, 'absent_count': 11, 'match_count': 24,
                          'required_count': len(PROBE_ROWS), 'credentialed': True,
                          'remote_validation': 'NOT_RUN', 'grants_release_credit': False}
        with patch.dict(os.environ, {COOKIE_ENV: COOKIE_CANARY, DRIVER_ENV: DRIVER_CANARY}), \
                patch('bcs_e2e.session_owner.dual_case_run.'
                      'run_preprod_session_owner_route_probe',
                      return_value=scripted_probe) as probe:
            report = dual_session_owner_case_run(
                profile_path=self._write_profile(preprod_profile(), 'preprod'),
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha, leaf_root=leaf_root)
        probe.assert_called_once()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['error_type'],
                         'PREPROD_SESSION_OWNER_FULL_STORY_NOT_EXECUTED')
        self.assertEqual(report['credential_admission']['status'], 'ADMITTED')
        self.assertEqual(report['remote_adapter_preflight']['status'], 'NOT_RUN')
        self.assertEqual(report['remote_adapter_preflight']['gap_ids'],
                         list(DOCUMENTED_GAPS.keys()))
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_SESSION_OWNER_FULL_STORY_NOT_EXECUTED')
        self.assertEqual(len(report['preprod_blocker']['documented_gaps']), 4)
        self.assertEqual(report['resource_disposition'], None)
        leaf_dir = Path(report['preprod_evidence_leaf'])
        self.assertEqual(leaf_dir, leaf_root / 'session-owner')
        verdict = read(leaf_dir / (RUN_ID + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['documented_gap_ids'], list(DOCUMENTED_GAPS.keys()))
        self.assertFalse(verdict['grants_release_credit'])
        persisted = ''.join(path.read_text(encoding='utf-8', errors='replace')
                            for path in list(state_dir.rglob('*')) + list(leaf_dir.rglob('*'))
                            if path.is_file())
        self.assertNotIn(COOKIE_CANARY, persisted)
        self.assertNotIn(DRIVER_CANARY, persisted)

    def test_preprod_tier_blocked_at_entry_when_credentials_are_unavailable(self):
        state_dir = self.root / 'preprod-entry-state'
        leaf_root = self.root / 'preprod-entry-leaves'
        with patch.dict(os.environ, {}, clear=True), \
                patch('bcs_e2e.session_owner.dual_case_run.'
                      'run_preprod_session_owner_route_probe',
                      return_value={'status': 'BLOCKED', 'checks': []}):
            report = dual_session_owner_case_run(
                profile_path=self._write_profile(preprod_profile(), 'preprod-entry'),
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha, leaf_root=leaf_root)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['credential_admission']['status'], 'BLOCKED')
        self.assertEqual(report['credential_admission']['reason_class'],
                         'PREPROD_SESSION_OWNER_CREDENTIALS_UNAVAILABLE')
        verdict = read(leaf_root / 'session-owner' / (RUN_ID + '-verdict.json'))
        self.assertEqual(verdict['credential_admission'], 'BLOCKED')

    # --------------------------------------------------------------- seams
    def test_registration_helper_parser_carries_both_required_biscuit_pairs(self):
        parser = argparse.ArgumentParser()
        register_session_owner_dual_case_commands(
            parser.add_subparsers(dest='command', required=True))
        argv = [COMMAND, '--profile', '/p.json', '--binary', '/bcs',
                '--binary-sha256', 'a' * 64, '--cli-binary', '/bcs-cli',
                '--cli-binary-sha256', 'b' * 64, '--state-dir', '/state',
                '--run-id', RUN_ID, '--confirm-run-id', RUN_ID, '--allow-write']
        args = parser.parse_args(argv)
        self.assertEqual(args.command, COMMAND)
        self.assertEqual(args.binary, Path('/bcs'))
        self.assertEqual(args.cli_binary, Path('/bcs-cli'))
        self.assertTrue(args.allow_write)
        for missing_index in (3, 5, 7, 9):
            with self.subTest(missing=argv[missing_index]):
                partial = [item for index, item in enumerate(argv)
                           if index not in (missing_index, missing_index + 1)]
                with self.assertRaises(SystemExit):
                    parser.parse_args(partial)

    def test_registration_seam_is_wired_by_mainline_without_release_credit(self):
        self.assertIn(COMMAND, CASE_COMMANDS)
        seam = REGISTRATION_SEAM
        self.assertEqual(seam['status'], 'DESCRIBE_ONLY_MAINLINE_WRITES')
        for file_name in ('cli.py', 'case_command_registration.py', 'migration_inventory.py',
                          'executor-migration.json'):
            self.assertIn(file_name, seam['cli_py'] + seam['case_command_registration_py']
                          + seam['migration_inventory_py'] + seam['executor_migration_json'])
        executor_rows = json.loads((ROOT / 'executor-migration.json').read_text(encoding='utf-8'))['rows']
        migrated = next(item for item in executor_rows if item['id'] == 'story_session_owner_permissions')
        self.assertEqual(migrated['executor_migration']['mode'], 'DUAL')
        self.assertEqual(migrated['executor_migration']['preprod_verdict'], 'NOT_RUN')
        command = dual_session_owner_case_command()
        self.assertEqual(command['command'], COMMAND)
        self.assertEqual(command['required_binary_pairs']['server'],
                         ['--binary', '--binary-sha256'])
        self.assertFalse(command['grants_release_credit'])

    def test_default_leaf_root_and_adaptability_disclosure(self):
        self.assertEqual(DEFAULT_LEAF_ROOT.name, LEAF_ROOT_NAME)
        adaptability = dual.session_owner_dual_adaptability_ext()
        self.assertEqual(adaptability['case_id'], CASE_ID)
        self.assertEqual(adaptability['source_case_id'], 'LOCAL-SESSION-OWNER-01')
        self.assertEqual(adaptability['required_assertion_count'],
                         STORY_CHECKS + CLEANUP_CHECKS)
        self.assertFalse(adaptability['grants_release_credit'])

    def test_source_binding_stays_frozen(self):
        binding = session_owner_source_binding()
        self.assertEqual(binding['parent'], 'story_session_owner_permissions')
        self.assertEqual(binding['story_checks'], STORY_CHECKS)

    def test_migration_contract_json_is_frozen_and_complete(self):
        contract_path = ROOT / 'session-owner-permissions-migration-contract.json'
        self.assertTrue(contract_path.is_file(), contract_path)
        value = json.loads(contract_path.read_text(encoding='utf-8'))
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['parent'], 'story_session_owner_permissions')
        self.assertEqual(value['obligations_count'], len(REQUIREMENTS))
        self.assertEqual([row['id'] for row in value['obligations']], list(REQUIREMENTS))
        for row in value['obligations']:
            self.assertTrue(row['expectation'])
            self.assertTrue(row['shell_note'])
        self.assertEqual(value['shell_step_map']['parsed_upstream_steps'], 1)
        surface = value['dual_surface']
        self.assertEqual(surface['module'], 'bcs_e2e.session_owner.dual_case_run')
        self.assertEqual(surface['command'], COMMAND)
        self.assertEqual(value['local_backlog']['execution_command'],
                         'local-session-owner-run')
        self.assertEqual(value['local_backlog']['execution_module'],
                         'bcs_e2e.session_owner.local_session_owner_run.local_session_owner_run')
        self.assertEqual(set(value['documented_gaps']), set(DOCUMENTED_GAPS.keys()))
        self.assertEqual(value['upstream_assertions']['run_http'], STORY_CHECKS)
        self.assertEqual(value['upstream_assertions']['cleanup_http'], CLEANUP_CHECKS)
        self.assertFalse(value['grants_release_credit'])
        self.assertIn('registration_seam', value)
        self.assertIn('flat_module_budget_note', value)


if __name__ == '__main__':
    unittest.main()
