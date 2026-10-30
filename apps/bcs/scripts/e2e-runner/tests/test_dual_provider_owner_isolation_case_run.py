"""Contract, biscuit, profile, probe, fail-closed and seam tests for the
dual Provider owner-isolation case run (story #5 LOCAL_ONLY -> DUAL)."""
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

import bcs_e2e.provider_owner_dual_case_run as dual
from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.local_provider_owner_run import validate_provider_owner_binary
from bcs_e2e.profile import ConfigError
from bcs_e2e.provider_owner_contract import (REQUIREMENTS, STORY_CHECKS)
from bcs_e2e.provider_owner_dual_case_run import (
    CASE_ID, COMMAND, DEFAULT_LEAF_ROOT, HEADER_ENV, LEAF_ROOT_NAME,
    PROFILE_CONTRACT, PROBE_ROWS, REGISTRATION_SEAM, REPORT_CONTRACT,
    PreprodClosedError, dual_provider_owner_case_run,
    dual_provider_owner_case_command, load_provider_owner_profile,
    provider_owner_dual_adaptability, register_provider_owner_dual_case_commands,
    run_preprod_provider_owner_route_probe, validate_provider_owner_profile,
)
from bcs_e2e.run_state import RunStateError, read

RUN_ID = '20261031-migration-wp2-provider-owner-test'
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
        'environment': {'tier': 'local', 'id': 'provider-owner-local',
                        'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'owned-local'},
        'human': {'user_id': 'provider-owner-story'},
        'auth': {'kind': 'owned-local'},
        'cli': {'transport': 'owned-local-story', 'command_family': 'provider-owner',
                'api_face': 'legacy'},
        'policy': {'fixture_pool': 'provider-owner-story', 'exclusive_use_confirmed': True,
                   'timeout_seconds': 10, 'max_response_bytes': 65536,
                   'failure_retention_hours': 24},
    }


def preprod_profile() -> dict:
    value = local_profile()
    value['environment'] = {'tier': 'preprod', 'id': '20261031-migration-wp2-provider-pre',
                            'expected_bcs_env': 'pre'}
    value['entrypoint'] = {'kind': 'bcs-direct', 'origin': 'https://bcs-pre.alipay.com',
                           'api_face': 'legacy', 'manifest_face': 'legacy'}
    value['human'] = {'user_id': '39464'}
    value['auth'] = {'kind': 'preprod-human-cookie',
                     'header_env': {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}}
    value['cli'] = {'transport': 'product-cli-http', 'command_family': 'provider-owner',
                    'api_face': 'legacy'}
    return value


def local_result(*, observed: int = STORY_CHECKS) -> dict:
    return {
        'status': 'LOCAL_PROVIDER_OWNER_PASS',
        'child_receipt': {'markers': {'owned-local-provider-owner-compliance': True},
                          'observed_checks': observed},
        'canonical_summary': {'status': 'LOCAL_SUITE_PASS'},
    }


class FakeProbeClient:
    """Scripted statuses without opening a socket."""

    def __init__(self, statuses: dict | None = None, *, baseline: int = 404):
        self.statuses = statuses or {}
        self.baseline = baseline
        self.calls = []

    def request(self, *, method: str, path: str) -> dict:
        self.calls.append({'method': method, 'path': path})
        status = self.baseline if path == dual._BASELINE_PATH else (
            self.statuses.get(path, 404))
        return {'method': method, 'path': path, 'http_status': status,
                'response_bytes': 0, 'cookie_sent': False,
                'authorization_sent': False, 'transport_error': None}


class ProviderOwnerDualTests(unittest.TestCase):
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
        parameters = inspect.signature(dual_provider_owner_case_run).parameters
        for name in ('binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256'):
            parameter = parameters[name]
            self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(parameter.default, inspect.Parameter.empty, name)
        self.assertIs(parameters['leaf_root'].default, None)

    def test_frozen_pair_validation_rejects_symlinks_missing_files_and_drift(self):
        good, good_sha = executable_binary(self.root, name='pair-bcs')
        self.assertEqual(dual._validate_frozen_pair(good, good_sha, label='CLI'), good)
        for label, binary, digest in (
                ('missing', self.root / 'absent-bcs', '0' * 64),
                ('drift', good, 'f' * 64),
        ):
            with self.subTest(control=label):
                with self.assertRaisesRegex(RunStateError, 'digest differs|regular frozen'):
                    dual._validate_frozen_pair(binary, digest, label='BCS')
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
        self.assertEqual(validate_provider_owner_binary(self.binary, self.server_sha),
                         self.binary)
        with self.assertRaises(RunStateError):
            validate_provider_owner_binary(self.binary, 'e' * 64)

    # ------------------------------------------------------------- profiles
    def test_both_tier_profiles_validate(self):
        self.assertEqual(validate_provider_owner_profile(local_profile())['environment']['tier'],
                         'local')
        validated = validate_provider_owner_profile(preprod_profile())
        self.assertEqual(validated['auth']['header_env'],
                         {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
        self.assertEqual(load_provider_owner_profile(
            self._write_profile(preprod_profile(), 'valid'))['environment']['id'],
            '20261031-migration-wp2-provider-pre')

    def test_profile_rejects_shape_placeholders_and_face_drift(self):
        cases = (
            ('unknown-top-level', lambda v: v.__setitem__('credential_files', {})),
            ('schema-version', lambda v: v.__setitem__('schema_version', 2)),
            ('contract', lambda v: v.__setitem__('contract', 'wrong')),
            ('tier', lambda v: v['environment'].__setitem__('tier', 'main')),
            ('bcs-env-binding', lambda v: v['environment'].__setitem__('expected_bcs_env', 'prod')),
            ('local-entrypoint-kind', lambda v: v['entrypoint'].__setitem__('kind', 'bcs-direct')),
            ('preprod-entrypoint-kind', lambda v: v.update(
                {'environment': {'tier': 'preprod', 'id': 'x', 'expected_bcs_env': 'pre'},
                 'entrypoint': {'kind': 'owned-local'}, 'auth': {'kind': 'owned-local'},
                 'cli': {'transport': 'product-cli-http', 'command_family': 'provider-owner',
                         'api_face': 'legacy'}})),
            ('preprod-origin-face', lambda v: v['entrypoint'].__setitem__('api_face', 'public-v1')),
            ('preprod-origin-http', lambda v: v['entrypoint'].__setitem__(
                'origin', 'http://bcs-pre.alipay.com')),
            ('human', lambda v: v['human'].__setitem__('user_id', 'user name')),
            ('auth-kind-swap', lambda v: v['auth'].__setitem__('kind', 'owned-local')),
            ('auth-extra-header', lambda v: v['auth'].update(
                {'header_env': {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV,
                                'X-Extra': 'BCS_E2E_DRIVER_TOKEN'}})),
            ('header-env-missing-cookie', lambda v: v['auth']['header_env'].pop('Cookie')),
            ('header-env-lowercase-ref', lambda v: v['auth']['header_env'].__setitem__(
                'Authorization', 'bcs_e2e_driver_token')),
            ('cli-transport-swap', lambda v: v['cli'].__setitem__('transport', 'owned-local-story')),
            ('cli-family', lambda v: v['cli'].__setitem__('command_family', 'provider-subbots')),
            ('cli-face', lambda v: v['cli'].__setitem__('api_face', 'public-v1')),
            ('policy-exclusive', lambda v: v['policy'].__setitem__('exclusive_use_confirmed', False)),
            ('policy-timeout-high', lambda v: v['policy'].__setitem__('timeout_seconds', 31)),
            ('policy-bytes-high', lambda v: v['policy'].__setitem__('max_response_bytes', 65537)),
            ('policy-retention-high', lambda v: v['policy'].__setitem__(
                'failure_retention_hours', 169)),
        )
        for name, mutate in cases:
            with self.subTest(mutation=name):
                value = copy.deepcopy(preprod_profile() if name != 'local-entrypoint-kind'
                                      else local_profile())
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_provider_owner_profile(value)

    def test_profile_loading_rejects_symlinks_and_unreadable_json(self):
        good = self._write_profile(local_profile(), 'loadable')
        linked = self.root / 'profile-linked.json'
        linked.symlink_to(good)
        with self.assertRaises(ConfigError):
            load_provider_owner_profile(linked)
        broken = self.root / 'profile-broken.json'
        broken.write_text('{not json', encoding='utf-8')
        with self.assertRaises(ConfigError):
            load_provider_owner_profile(broken)

    # --------------------------------------------------------------- probes
    def test_probe_classifies_present_and_absent_routes_against_the_baseline(self):
        statuses = {}
        for row in PROBE_ROWS:
            statuses[row['path'].replace('{provider_id}', dual._FIFO_SENTINEL)
                     .replace('{provider_bot_ref}', dual._FIFO_SENTINEL)
                     .replace('{bot_uuid}', dual._FIFO_SENTINEL)
                     .replace('{group}', dual._FIFO_SENTINEL)
                     .replace('{invite}', dual._FIFO_SENTINEL)
                     .replace('{session}', dual._FIFO_SENTINEL)] = (
                405 if row['expected_present'] else 404)
        client = FakeProbeClient(statuses)
        result = run_preprod_provider_owner_route_probe(preprod_profile(), credentials={},
                                                        client=client)
        self.assertEqual(result['status'], 'PASS')
        self.assertIsNone(result['error_type'])
        self.assertEqual(len(client.calls), len(PROBE_ROWS) + 1)
        self.assertEqual(client.calls[0]['path'], dual._BASELINE_PATH)
        self.assertEqual(result['present_count'], 2)
        self.assertEqual(result['absent_count'], len(PROBE_ROWS) - 2)
        self.assertEqual(result['match_count'], len(PROBE_ROWS))
        self.assertFalse(result['credentialed'])
        self.assertFalse(result['grants_release_credit'])
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        for record in result['checks']:
            self.assertIn(record['http_status'], (404, 405))

    def test_probe_blocks_on_unexpected_baseline_or_route_mismatch(self):
        blocked = run_preprod_provider_owner_route_probe(
            preprod_profile(), credentials={},
            client=FakeProbeClient({}, baseline=401))
        self.assertEqual(blocked['status'], 'BLOCKED')
        self.assertEqual(blocked['error_type'], 'PREPROD_PROBE_BASELINE_UNEXPECTED')
        self.assertEqual(blocked['checks'], [])
        mismatched = FakeProbeClient()
        mismatched.statuses = {
            dual._probe_path(row['path']): (404 if row['expected_present'] else 405)
            for row in PROBE_ROWS}
        result = run_preprod_provider_owner_route_probe(
            preprod_profile(), credentials={}, client=mismatched)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'PREPROD_PROVIDER_OWNER_ROUTE_UNEXPECTED')
        self.assertEqual(result['match_count'], 0)
        self.assertEqual(result['present_count'], 16)
        self.assertEqual(result['absent_count'], 2)

    def test_probe_rows_are_wrong_method_and_read_only_calibrated(self):
        for row in PROBE_ROWS:
            self.assertNotEqual(row['canonical_method'], row['probe_method'])
            self.assertIn(row['probe_method'], ('GET', 'PUT', 'POST'))
        station = [row for row in PROBE_ROWS if row['face'] == 'station-legacy']
        self.assertEqual(len(station), 16)
        self.assertTrue(all(row['expected_present'] is False for row in station))

    # ------------------------------------------------------- admissions
    def test_credential_admission_resolves_only_named_references(self):
        with patch.dict(os.environ, {COOKIE_ENV: COOKIE_CANARY, DRIVER_ENV: DRIVER_CANARY}):
            admission = dual._credential_admission(HEADER_ENV)
        self.assertEqual(admission['status'], 'ADMITTED')
        self.assertEqual(admission['resolved_references'], sorted(HEADER_ENV.values()))
        self.assertNotIn(COOKIE_CANARY, json.dumps(admission))
        self.assertNotIn(DRIVER_CANARY, json.dumps(admission))

    def test_credential_admission_fail_closes_without_storing_values(self):
        for environment in ({}, {COOKIE_ENV: ''},
                            {COOKIE_ENV: 'bad\ncookie', DRIVER_ENV: 'x'}):
            with self.subTest(environment=sorted(environment)):
                with patch.dict(os.environ, environment, clear=True):
                    admission = dual._credential_admission(HEADER_ENV)
                self.assertEqual(admission['status'], 'BLOCKED')
                self.assertEqual(admission['reason_class'],
                                 'PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE')
                self.assertIn(HEADER_ENV['Cookie'], admission['reason'])
                self.assertNotIn(COOKIE_CANARY, json.dumps(admission))

    def test_resolve_credential_values_raises_the_closed_reason(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PreprodClosedError) as ctx:
                dual._resolve_credential_values(HEADER_ENV)
        self.assertEqual(ctx.exception.reason_class,
                         'PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE')
        self.assertNotIn(COOKIE_CANARY, str(ctx.exception))

    def test_camel_case_reader_is_logging_only(self):
        record = {'requestId': 'abc', 'other': 1}
        value, spelling = dual._camel_case_field(record, 'requestId', 'request_id')
        self.assertEqual((value, spelling), ('abc', 'requestId'))
        value, spelling = dual._camel_case_field({'request_id': 'abc'}, 'requestId', 'request_id')
        self.assertEqual((value, spelling), ('abc', 'request_id'))
        self.assertEqual(dual._camel_case_field({}, 'requestId', 'request_id'), (None, 'absent'))
        sample = dual._serialization_sample()
        self.assertEqual(sample['decision_use'], 'none_logging_only')

    # ----------------------------------------------------------- executions
    def test_local_execution_admits_only_the_complete_child_receipt(self):
        execution = dual._local_execution(local_result())
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, len(REQUIREMENTS))
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        self.assertTrue(all(step.status == 'PASS' for step in execution.steps))
        execution.validate()
        self.assertEqual(execution.steps[0].evidence['story_checks'], STORY_CHECKS)
        self.assertEqual(execution.steps[0].evidence['author_cli_pair'],
                         'author_cli_pair_admitted_frozen_not_consumed_by_family_journey')
        for mutation in ({'observed_checks': 44}, {'markers': {'owned-local-provider-owner-compliance': False}}):
            with self.subTest(mutation=mutation):
                blocked_result = local_result()
                blocked_result['child_receipt'].update(mutation)
                blocked = dual._local_execution(blocked_result)
                self.assertEqual(blocked.status, 'BLOCKED')
                self.assertEqual(blocked.completed_count, 0)
                self.assertEqual(blocked.error_type, 'LOCAL_PROVIDER_OWNER_BLOCKED')
                self.assertTrue(all(step.status == 'NOT_RUN' for step in blocked.steps))
                self.assertEqual(blocked.steps[0].error_type, 'LOCAL_PROVIDER_OWNER_BLOCKED')
        blocked_result = {'status': 'LOCAL_PROVIDER_OWNER_PASS',
                          'child_receipt': {'markers': {'owned-local-provider-owner-compliance': True},
                                            'observed_checks': STORY_CHECKS},
                          'canonical_summary': {'status': 'OTHER'}}
        blocked = dual._local_execution(blocked_result)
        self.assertEqual(blocked.status, 'BLOCKED')

    def test_preprod_execution_fail_closes_the_whole_case(self):
        execution = dual._preprod_execution({'status': 'PASS'}, {'status': 'BLOCKED'})
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.total_count, len(REQUIREMENTS))
        self.assertEqual(execution.error_type, 'PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED')
        execution.validate()
        self.assertEqual(execution.steps[0].error_type,
                         'PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED')
        for step in execution.steps[1:]:
            self.assertEqual(step.status, 'NOT_RUN')
            self.assertIsNone(step.error_type)

    # --------------------------------------------------------------- entry
    def test_local_tier_delegates_and_writes_binding_profile_and_report(self):
        profile_path = self._write_profile(local_profile(), 'local')
        state_dir = self.root / 'local-state'
        with patch('bcs_e2e.provider_owner_dual_case_run.local_provider_owner_run',
                   return_value=local_result()) as execute:
            report = dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha, leaf_root=self.root / 'leaves')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['case_id'], CASE_ID)
        self.assertEqual(report['execution']['completed_count'], len(REQUIREMENTS))
        self.assertEqual(report['obligation_summary']['story_checks'], STORY_CHECKS)
        self.assertEqual(report['local_owned_run']['status'], 'LOCAL_PROVIDER_OWNER_PASS')
        self.assertEqual(report['local_owned_run']['canonical_summary_status'],
                         'LOCAL_SUITE_PASS')
        self.assertEqual(report['environment']['tier'], 'local')
        self.assertEqual(report['bcs_binary_sha256'], self.server_sha)
        self.assertEqual(report['cli_binary_sha256'], self.cli_sha)
        self.assertEqual(report['resource_disposition'], 'STORY_SCOPED_TEMPORARY_DIRECTORY')
        self.assertEqual(report['remote_validation'], 'NOT_RUN')
        self.assertFalse(report['grants_release_credit'])
        self.assertEqual(report['author_cli_pair']['status'],
                         'author_cli_pair_admitted_frozen_not_consumed_by_family_journey')
        self.assertIsNone(report['case']['error_type'])
        execute.assert_called_once()
        execute_kwargs = execute.call_args.kwargs
        self.assertEqual(execute_kwargs['state_dir'], state_dir / 'owned-provider-owner')
        self.assertEqual(execute_kwargs['confirm_run_id'], RUN_ID)
        self.assertTrue(execute_kwargs['allow_write'])
        read(state_dir / 'provider-owner-dual-run.json')
        binding = read(state_dir / 'provider-owner-dual-binding.json')
        self.assertEqual(binding['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['author_cli_pair_status'],
                         'author_cli_pair_admitted_frozen_not_consumed_by_family_journey')
        self.assertEqual(binding['local_backlog_commands'],
                         ['local-provider-owner-run', 'local-provider-owner-report'])

    def test_local_tier_maps_an_incomplete_child_receipt_to_blocked(self):
        state_dir = self.root / 'local-blocked'
        blocked_result = {
            'status': 'BLOCKED', 'error_type': 'RunStateError',
            'child_receipt': {'markers': {'owned-local-provider-owner-compliance': False},
                              'observed_checks': 0},
            'canonical_summary': {'status': 'BLOCKED'},
        }
        with patch('bcs_e2e.provider_owner_dual_case_run.local_provider_owner_run',
                   return_value=blocked_result):
            report = dual_provider_owner_case_run(
                profile_path=self._write_profile(local_profile(), 'local'),
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.binary, binary_sha256=self.server_sha,
                cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)
        self.assertEqual(report['execution']['error_type'], 'LOCAL_PROVIDER_OWNER_BLOCKED')
        self.assertEqual(report['case']['status'], 'BLOCKED')
        self.assertEqual(report['case']['steps'][0]['error_type'],
                         'LOCAL_PROVIDER_OWNER_BLOCKED')
        self.assertTrue(all(step['status'] == 'NOT_RUN'
                            for step in report['case']['steps'][1:]))

    def test_preprod_tier_fail_closes_with_admission_probe_and_leaf(self):
        profile_path = self._write_profile(preprod_profile(), 'preprod')
        state_dir = self.root / 'preprod-state'
        leaf_root = self.root / 'preprod-leaves'
        scripted_probe = {'status': 'PASS', 'error_type': None, 'baseline': {},
                          'checks': [], 'present_count': 0, 'absent_count': 0,
                          'match_count': 0, 'required_count': len(PROBE_ROWS),
                          'credentialed': True, 'remote_validation': 'NOT_RUN',
                          'grants_release_credit': False}
        with patch.dict(os.environ, {COOKIE_ENV: COOKIE_CANARY, DRIVER_ENV: DRIVER_CANARY}), \
                patch('bcs_e2e.provider_owner_dual_case_run.'
                      'run_preprod_provider_owner_route_probe',
                      return_value=scripted_probe) as probe:
            report = dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha, leaf_root=leaf_root)
        probe.assert_called_once()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['error_type'],
                         'PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED')
        self.assertEqual(report['credential_admission']['status'], 'ADMITTED')
        self.assertEqual(report['credential_admission']['resolved_references'],
                         sorted(HEADER_ENV.values()))
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED')
        self.assertEqual(report['preprod_probe'], scripted_probe)
        self.assertEqual(report['environment']['tier'], 'preprod')
        self.assertEqual(report['credential_kind'], 'preprod_human_cookie_route_probe')
        self.assertFalse(report['grants_release_credit'])
        leaf_dir = Path(report['preprod_evidence_leaf'])
        self.assertEqual(leaf_dir, leaf_root / 'provider-owner')
        slug = (RUN_ID + '-stdout.json')
        self.assertTrue((leaf_dir / slug).is_file())
        verdict = read(leaf_dir / (RUN_ID + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['error_type'],
                         'PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED')
        self.assertEqual(verdict['bcs_binary_sha256'], self.server_sha)
        self.assertEqual(verdict['cli_binary_sha256'], self.cli_sha)
        self.assertFalse(verdict['grants_release_credit'])
        persisted = ''.join(path.read_text(encoding='utf-8', errors='replace')
                            for path in list(state_dir.rglob('*')) + list(leaf_dir.rglob('*'))
                            if path.is_file())
        self.assertNotIn(COOKIE_CANARY, persisted)
        self.assertNotIn(DRIVER_CANARY, persisted)

    def test_preprod_tier_blocked_at_entry_when_credentials_are_unavailable(self):
        profile_path = self._write_profile(preprod_profile(), 'preprod-entry')
        state_dir = self.root / 'preprod-entry-state'
        leaf_root = self.root / 'preprod-entry-leaves'
        with patch.dict(os.environ, {}, clear=True), \
                patch('bcs_e2e.provider_owner_dual_case_run.'
                      'run_preprod_provider_owner_route_probe',
                      return_value={'status': 'BLOCKED', 'checks': []}) as probe:
            report = dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha, leaf_root=leaf_root)
        probe.assert_called_once()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['credential_admission']['status'], 'BLOCKED')
        self.assertEqual(report['credential_admission']['reason_class'],
                         'PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(report['preprod_blocker']['credential_admission']['status'],
                         'BLOCKED')
        self.assertEqual(report['preprod_blocker']['credential_refs'],
                         sorted(HEADER_ENV.values()))
        verdict = read(leaf_root / 'provider-owner' / (RUN_ID + '-verdict.json'))
        self.assertEqual(verdict['credential_admission'], 'BLOCKED')
        self.assertEqual(verdict['credential_admission_reason_class'],
                         'PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE')

    def test_command_gates_reject_insecure_write_and_fresh_state(self):
        profile_path = self._write_profile(local_profile(), 'gates')
        with self.assertRaisesRegex(ConfigError, 'explicit write permission'):
            dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=self.root / 'g1', run_id=RUN_ID,
                allow_write=False, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)
        with self.assertRaisesRegex(ConfigError, 'exact Run confirmation'):
            dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=self.root / 'g2', run_id=RUN_ID,
                allow_write=True, confirm_run_id='other', binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)
        existing = self.root / 'g3'
        existing.mkdir()
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=existing, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)
        linked = self.root / 'g4'
        linked.symlink_to(self.root / 'elsewhere')
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=linked, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.server_sha, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)

    def test_missing_biscuit_pairs_fail_closed_on_the_server_leg(self):
        profile_path = self._write_profile(local_profile(), 'biscuits')
        state_dir = self.root / 'biscuit-state'
        with self.assertRaises(RunStateError):
            dual_provider_owner_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256='mismatch', cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha)

    # ------------------------------------------------------------- seams
    def test_registration_helper_parser_carries_both_required_biscuit_pairs(self):
        parser = argparse.ArgumentParser()
        register_provider_owner_dual_case_commands(parser.add_subparsers(dest='command',
                                                                        required=True))
        argv = [COMMAND, '--profile', '/p.json', '--binary', '/bcs',
                '--binary-sha256', 'a' * 64, '--cli-binary', '/bcs-cli',
                '--cli-binary-sha256', 'b' * 64, '--state-dir', '/state',
                '--run-id', RUN_ID, '--confirm-run-id', RUN_ID, '--allow-write']
        args = parser.parse_args(argv)
        self.assertEqual(args.command, COMMAND)
        self.assertEqual(args.binary, Path('/bcs'))
        self.assertEqual(args.cli_binary, Path('/bcs-cli'))
        self.assertTrue(args.allow_write)
        self.assertIsNone(args.leaf_root)
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
        migrated = next(item for item in executor_rows if item['id'] == 'story_provider_owner_isolation')
        self.assertEqual(migrated['executor_migration']['mode'], 'DUAL')
        self.assertEqual(migrated['executor_migration']['preprod_verdict'], 'NOT_RUN')
        command = dual_provider_owner_case_command()
        self.assertEqual(command['command'], COMMAND)
        self.assertEqual(command['required_binary_pairs']['server'],
                         ['--binary', '--binary-sha256'])
        self.assertEqual(command['required_binary_pairs']['author_cli'],
                         ['--cli-binary', '--cli-binary-sha256'])
        self.assertFalse(command['grants_release_credit'])

    def test_default_leaf_root_and_adaptability_disclosure(self):
        self.assertEqual(DEFAULT_LEAF_ROOT.name, LEAF_ROOT_NAME)
        adaptability = provider_owner_dual_adaptability()
        self.assertEqual(adaptability['case_id'], CASE_ID)
        self.assertEqual(adaptability['source_case_id'], 'LOCAL-PROVIDER-OWNER-01')
        self.assertEqual(adaptability['requirement_ids'], list(REQUIREMENTS))
        self.assertIn('author_cli_pair_status', adaptability)
        self.assertFalse(adaptability['grants_release_credit'])
        serialized = json.dumps(adaptability, sort_keys=True)
        self.assertNotIn(COOKIE_CANARY, serialized)

    def test_migration_contract_json_is_frozen_and_complete(self):
        contract_path = ROOT / 'provider-owner-isolation-migration-contract.json'
        self.assertTrue(contract_path.is_file(), contract_path)
        value = json.loads(contract_path.read_text(encoding='utf-8'))
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['parent'], 'story_provider_owner_isolation')
        self.assertEqual(value['obligations_count'], len(REQUIREMENTS))
        self.assertEqual([row['id'] for row in value['obligations']], list(REQUIREMENTS))
        for row in value['obligations']:
            self.assertTrue(row['expectation'])
            self.assertEqual(len(row['story_lines']), 2)
            self.assertTrue(row['shell_note'])
        self.assertEqual(value['shell_step_map']['parsed_upstream_steps'], 1)
        python_step = [row for row in value['shell_step_map']['steps']
                       if row['step'] == 2][0]
        self.assertEqual(python_step['maps_to_local_obligations'], list(REQUIREMENTS))
        self.assertEqual([row['maps_to_local_obligations']
                          for row in value['shell_step_map']['steps']
                          if row['step'] != 2], [[], []])
        surface = value['dual_surface']
        self.assertEqual(surface['module'], 'bcs_e2e.provider_owner_dual_case_run')
        self.assertEqual(surface['command'], COMMAND)
        self.assertEqual(surface['required_binary_pairs']['author_cli'],
                         ['--cli-binary', '--cli-binary-sha256'])
        self.assertEqual(value['local_backlog']['execution_command'], 'local-provider-owner-run')
        self.assertEqual(value['local_backlog']['execution_module'],
                         'bcs_e2e.local_provider_owner_run.local_provider_owner_run')
        with open(ROOT / 'provider-owner-migration-contract.json', encoding='utf-8') as handle:
            family = json.load(handle)
        self.assertEqual(value['documented_gaps'], family['documented_gaps'])
        self.assertFalse(value['grants_release_credit'])
        self.assertIn('registration_seam', value)
        self.assertIn('flat_module_budget_note', value)


if __name__ == '__main__':
    unittest.main()
