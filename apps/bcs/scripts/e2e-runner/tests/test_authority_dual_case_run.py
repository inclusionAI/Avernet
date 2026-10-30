"""Dual-tier contract, profile, fail-closed and leaf tests for the authority case."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcs_e2e.authority.dual_case_run as dual
from bcs_e2e.authority import contract
from bcs_e2e.authority.dual_case_run import (CASE_ID, CASE_KEYS, DEFAULT_LEAF_ROOT,
                                             PROFILE_CONTRACT, REPORT_CONTRACT, SCHEMA_VERSION,
                                             SOURCE_CASE_ID, SOURCE_SUITE, SUITE,
                                             _HEADER_ENV, _reason_priority, load_authority_profile,
                                             validate_authority_profile, authority_dual_adaptability,
                                             dual_authority_case_run, main)
from bcs_e2e.inventory import executor_digest
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import RunStateError, read

RUN_ID = '20261031-migration-wp2-authority-test'
OWNER_HUMAN = 'wp2authoritylocalhuman000'
DRIVER_CANARY = 'synthetic-driver-token-never-persisted'
COOKIE_CANARY = 'synthetic-human-cookie-never-persisted'
LOCAL_HELATH_CANARY = 'synthetic-local-health-observation'
WRONG_SHA = 'f' * 64


def local_profile() -> dict:
    return {
        'schema_version': 1, 'contract': PROFILE_CONTRACT,
        'environment': {'tier': 'owned-local', 'id': 'authority-owned-local-01',
                        'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'owned-local'},
        'human': {'user_id': OWNER_HUMAN},
        'auth': {'kind': 'owned-local',
                 'principal_signing_key_env': contract.PRINCIPAL_SIGNING_KEY_ENV},
        'authority': {'target_user_id': '002', 'mine_user_id': '137'},
        'cli': {'transport': 'owned-local-story', 'command_family': 'collaboration-authority',
                'api_face': 'public-v1'},
        'policy': {'timeout_seconds': 60, 'max_response_bytes': 1048576,
                   'failure_retention_hours': 24},
    }


def preprod_profile() -> dict:
    value = copy.deepcopy(local_profile())
    value['environment'] = {'tier': 'preprod', 'id': '20261031-migration-wp2-authority-pre',
                            'expected_bcs_env': 'pre'}
    value['entrypoint'] = {'kind': 'bcs-direct', 'origin': 'https://bcs-pre.alipay.com',
                           'api_face': 'internal-and-public-v1',
                           'manifest_face': 'internal-and-public-v1'}
    value['auth'] = {'kind': 'preprod-driver-bot-bearer',
                     'header_env': {'Authorization': dual._DRIVER_ENV, 'Cookie': dual._COOKIE_ENV}}
    value['authority'] = {'target_user_id_env': 'BCS_E2E_AUTHORITY_STAFF_B',
                          'mine_user_id_env': 'BCS_E2E_AUTHORITY_STAFF_MINE'}
    value['cli'] = {'transport': 'product-cli-http', 'command_family': 'collaboration-authority',
                    'api_face': 'public-v1'}
    value['policy'] = {'timeout_seconds': 90, 'max_response_bytes': 65536,
                       'failure_retention_hours': 168}
    return value


def executable_binary(root: Path, name: str = 'authority-bcs') -> tuple[Path, str]:
    binary = root / name
    binary.write_bytes(b'owned-authority-binary\n')
    binary.chmod(0o700)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def local_result(*, observed: int = contract.STORY_CHECKS) -> dict:
    return {
        'schema_version': 1, 'status': contract.STATUS, 'run_id': RUN_ID,
        'case_id': contract.CASE_ID, 'suite': contract.SUITE,
        'child_receipt': {'observed_checks': observed},
        'checks': {'status': 'PASS', 'checked': list(contract.REQUIREMENTS)},
        'canonical_summary': {'status': 'LOCAL_SUITE_PASS'},
    }


def blocked_execution_snapshot() -> dict:
    return {'case_id': CASE_ID, 'parent_id': contract.PARENT, 'status': 'BLOCKED',
            'completed_count': 0, 'total_count': len(contract.REQUIREMENTS),
            'error_type': dual._PREPROD_CREDENTIALS_UNAVAILABLE, 'ready_to_execute': False}


def route_observations(*, health_status=200, health_body='ok',
                       health_request_headers=None, health_body_class='json',
                       health_json_token=False, mint_status=200,
                       mint_body_class='html', mint_json_token=False) -> list[dict]:
    return [
        {'method': 'GET', 'url': 'https://bcs-pre.alipay.com/health',
         'request_headers': health_request_headers or [], 'observed_status': health_status,
         'transport_error': None, 'body_status': health_body,
         'body_class': health_body_class, 'json_token_key': health_json_token,
         'route': 'health',
         'expected': 'HTTP 200 with a healthy JSON body status (human cookie admitted)',
         'passed': health_status == 200 and health_body in ('ok', 'healthy')},
        {'method': 'GET', 'url': 'https://bcs-pre.alipay.com/register/token',
         'request_headers': [], 'observed_status': mint_status, 'transport_error': None,
         'body_status': None, 'body_class': mint_body_class,
         'json_token_key': mint_json_token, 'route': 'register-token-unauthenticated',
         'expected': ('no anonymous JSON token mint: the station ACE login gate (200 HTML) '
                      'or a 401/403 rejection'),
         'passed': not mint_json_token},
    ]


class AuthorityDualCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary, self.sha = executable_binary(self.root)
        self.cli_binary, self.cli_sha = executable_binary(self.root, name='authority-cli')

    def _write_profile(self, value: dict, name: str) -> Path:
        path = self.root / f'profile-{name}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def _base(self, profile_path: Path, state: Path, run_id: str = RUN_ID) -> dict:
        return dict(profile_path=profile_path, state_dir=state, run_id=run_id,
                    allow_write=True, confirm_run_id=run_id, binary=self.binary,
                    binary_sha256=self.sha, cli_binary=self.cli_binary,
                    cli_binary_sha256=self.cli_sha, leaf_root=self.root / 'leaves')

    # ------------------------------------------------------------- profiles
    def test_both_tier_profiles_validate(self):
        self.assertEqual(validate_authority_profile(local_profile())['environment']['tier'],
                         'owned-local')
        validated = validate_authority_profile(preprod_profile())
        self.assertEqual(validated['environment']['tier'], 'preprod')
        self.assertEqual(validated['auth']['header_env'],
                         {'Authorization': dual._DRIVER_ENV, 'Cookie': dual._COOKIE_ENV})
        self.assertEqual(load_authority_profile(
            self._write_profile(preprod_profile(), 'valid'))['environment']['id'],
            '20261031-migration-wp2-authority-pre')

    def test_profile_rejects_shape_placeholders_and_tier_drift(self):
        cases = (
            ('unknown-top-level', 'local', lambda v: v.__setitem__('credential_files', {})),
            ('schema-version', 'local', lambda v: v.__setitem__('schema_version', 2)),
            ('contract', 'local', lambda v: v.__setitem__('contract', 'wrong')),
            ('legacy-tier-wording', 'local',
             lambda v: v['environment'].__setitem__('tier', 'local')),
            ('bcs-env-binding', 'local',
             lambda v: v['environment'].__setitem__('expected_bcs_env', 'preprod')),
            ('environment-id-shape', 'local',
             lambda v: v['environment'].__setitem__('id', 'space id')),
            ('local-entrypoint-kind', 'local',
             lambda v: v['entrypoint'].__setitem__('kind', 'bcs-direct')),
            ('local-entrypoint-origin', 'local',
             lambda v: v['entrypoint'].__setitem__('origin', 'https://bcs-pre.alipay.com')),
            ('local-auth-kind', 'local', lambda v: v['auth'].__setitem__('kind', 'preprod')),
            ('local-signing-key-env', 'local',
             lambda v: v['auth'].__setitem__('principal_signing_key_env', 'OTHER_ENV_NAME')),
            ('authority-target-literal', 'local',
             lambda v: v['authority'].__setitem__('target_user_id', '003')),
            ('authority-mine-literal', 'local',
             lambda v: v['authority'].__setitem__('mine_user_id', '138')),
            ('local-env-reference-form', 'local',
             lambda v: v['authority'].__setitem__('target_user_id_env',
                                                  'BCS_E2E_AUTHORITY_STAFF_B')),
            ('human-user-id-shape', 'local', lambda v: v['human'].__setitem__('user_id', 'a b')),
            ('cli-transport', 'local',
             lambda v: v['cli'].__setitem__('transport', 'product-cli-http')),
            ('cli-family', 'local',
             lambda v: v['cli'].__setitem__('command_family', 'collaboration-draft')),
            ('cli-face', 'local', lambda v: v['cli'].__setitem__('api_face', 'legacy')),
            ('local-policy-timeout-low', 'local',
             lambda v: v['policy'].__setitem__('timeout_seconds', 0)),
            ('local-policy-timeout-high', 'local',
             lambda v: v['policy'].__setitem__('timeout_seconds', 121)),
            ('local-policy-bytes-low', 'local',
             lambda v: v['policy'].__setitem__('max_response_bytes', 0)),
            ('local-policy-retention-high', 'local',
             lambda v: v['policy'].__setitem__('failure_retention_hours', 169)),
            ('preprod-entrypoint-kind', 'preprod',
             lambda v: v['entrypoint'].__setitem__('kind', 'owned-local')),
            ('preprod-origin-plain-http', 'preprod',
             lambda v: v['entrypoint'].__setitem__('origin', 'http://bcs-pre.alipay.com')),
            ('preprod-origin-credentials', 'preprod',
             lambda v: v['entrypoint'].__setitem__(
                 'origin', 'https://user:pass@bcs-pre.alipay.com')),
            ('preprod-origin-path', 'preprod',
             lambda v: v['entrypoint'].__setitem__('origin', 'https://bcs-pre.alipay.com/x')),
            ('preprod-api-face', 'preprod',
             lambda v: v['entrypoint'].__setitem__('api_face', 'legacy')),
            ('preprod-manifest-face', 'preprod',
             lambda v: v['entrypoint'].__setitem__('manifest_face', 'public-v1')),
            ('preprod-auth-kind', 'preprod',
             lambda v: v['auth'].__setitem__('kind', 'owned-local')),
            ('header-env-missing-cookie', 'preprod',
             lambda v: v['auth']['header_env'].pop('Cookie')),
            ('header-env-lowercase-ref', 'preprod',
             lambda v: v['auth']['header_env'].__setitem__('Authorization', 'bcs_e2e_driver_token')),
            ('header-env-invalid-ref', 'preprod',
             lambda v: v['auth']['header_env'].__setitem__('Authorization', 'not-a-valid-env-ref')),
            ('header-env-extra-header', 'preprod',
             lambda v: v['auth']['header_env'].__setitem__('X-Extra', 'BCS_E2E_DRIVER_TOKEN')),
            ('authority-target-env-ref', 'preprod',
             lambda v: v['authority'].__setitem__('target_user_id_env', 'WRONG_STAFF_REF')),
            ('authority-mine-env-ref', 'preprod',
             lambda v: v['authority'].__setitem__('mine_user_id_env', 'WRONG_MINE_REF')),
            ('preprod-cli-transport', 'preprod',
             lambda v: v['cli'].__setitem__('transport', 'owned-local-story')),
            ('preprod-policy-bytes-high', 'preprod',
             lambda v: v['policy'].__setitem__('max_response_bytes', 65537)),
            ('preprod-policy-timeout', 'preprod',
             lambda v: v['policy'].__setitem__('timeout_seconds', 0)),
        )
        for name, tier, mutate in cases:
            with self.subTest(mutation=name):
                value = copy.deepcopy(preprod_profile() if tier == 'preprod' else local_profile())
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_authority_profile(value)

    def test_require_resolved_rejects_placeholders_before_any_state_directory(self):
        value = copy.deepcopy(preprod_profile())
        value['human']['user_id'] = 'REPLACE_WITH_PREPROD_HUMAN_ID'
        profile_path = self._write_profile(value, 'placeholder')
        state = self.root / 'placeholder-state'
        with self.assertRaisesRegex(ConfigError, 'REPLACE_WITH_'):
            dual_authority_case_run(**self._base(profile_path, state))
        self.assertFalse(state.exists())

    # ----------------------------------------------------------------- entry
    def test_entry_enforces_write_gates_confirmation_and_binary_digests(self):
        profile_path = self._write_profile(local_profile(), 'gates')
        base = self._base(profile_path, self.root / 'gates-state')
        with self.assertRaisesRegex(ConfigError, 'exact Run confirmation'):
            dual_authority_case_run(**{**base, 'confirm_run_id': 'other'})
        with self.assertRaisesRegex(ConfigError, 'write permission'):
            dual_authority_case_run(**{**base, 'allow_write': False})
        existing = self.root / 'existing-state'
        existing.mkdir()
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_authority_case_run(**{**base, 'state_dir': existing})
        with patch('bcs_e2e.authority.run.local_authority_dual_run',
                   return_value=local_result()):
            with self.assertRaisesRegex(RunStateError, 'digest differs'):
                dual_authority_case_run(**{**base, 'binary_sha256': WRONG_SHA})
            with self.assertRaisesRegex(RunStateError, 'digest differs'):
                dual_authority_case_run(**{**base, 'cli_binary_sha256': WRONG_SHA})
            wrong = self.root / 'wrong'
            wrong.write_bytes(b'other')
            wrong.chmod(0o600)
            with self.assertRaisesRegex(RunStateError, 'must be an executable regular file'):
                dual_authority_case_run(**{**base, 'cli_binary': wrong})
        self.assertFalse((self.root / 'gates-state').exists())

    def test_entry_fresh_state_dir_rejects_relative_symlinked_and_existing_paths(self):
        profile_path = self._write_profile(local_profile(), 'fresh')
        base = self._base(profile_path, self.root / 'fresh-state')
        with patch('bcs_e2e.authority.run.local_authority_dual_run',
                   return_value=local_result()):
            with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
                dual_authority_case_run(**{**base, 'state_dir': Path('relative-state')})
            link = self.root / 'linked-state'
            link.symlink_to(self.root / 'elsewhere')
            with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
                dual_authority_case_run(**{**base, 'state_dir': link})
            created = self.root / 'created-parent'
            created.mkdir()
            with patch('bcs_e2e.authority.run.local_authority_dual_run',
                       return_value=local_result()):
                report = dual_authority_case_run(**self._base(profile_path, created / 'nested'))
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(read(created / 'nested' / 'authority-dual-run.json')['status'], 'PASS')

    def test_run_id_identifier_gate_fail_closes_before_state_creation(self):
        profile_path = self._write_profile(local_profile(), 'identifier')
        state = self.root / 'identifier-state'
        with self.assertRaises(ConfigError):
            dual_authority_case_run(**self._base(profile_path, state, run_id='wp2 run id'))
        self.assertFalse(state.exists())

    # ---------------------------------------------------------- local leg
    def test_local_tier_reuses_the_owned_local_receipt(self):
        profile_path = self._write_profile(local_profile(), 'local')
        state = self.root / 'entry-local'
        with patch('bcs_e2e.authority.run.local_authority_dual_run',
                   return_value=local_result()) as execute:
            report = dual_authority_case_run(**self._base(profile_path, state))
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['local_owned_run']['status'], contract.STATUS)
        self.assertEqual(report['local_owned_run']['checks'], 'PASS')
        self.assertEqual(report['local_owned_run']['child_receipt']['observed_checks'], 42)
        self.assertEqual(report['local_owned_run']['canonical_summary_status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(report['local_owned_run']['state_dir'],
                         str(Path(state / 'owned-authority').resolve()))
        self.assertEqual(report['execution']['status'], 'PASS')
        self.assertEqual(report['execution']['completed_count'], len(contract.REQUIREMENTS))
        self.assertEqual(report['obligation_summary']['story_checks'], 42)
        self.assertEqual(report['resource_disposition'], contract.DISPOSITION)
        self.assertEqual(report['credential_kind'], 'owned_local_ephemeral_principal')
        self.assertEqual(report['remote_validation'], 'NOT_RUN')
        self.assertFalse(report['grants_release_credit'])
        self.assertNotIn('preprod_blocker', report)
        self.assertNotIn('preprod_story', report)
        self.assertTrue(execute.called)
        # the owned-local evidence tree itself is pinned by test_authority_run.py
        self.assertNotIn('Bearer', json.dumps(report))
        self.assertEqual(report['state_dir'], str(Path(state).resolve()))

    def test_local_tier_blocks_on_an_incomplete_child_receipt(self):
        profile_path = self._write_profile(local_profile(), 'local-short')
        state = self.root / 'entry-local-short'
        with patch('bcs_e2e.authority.run.local_authority_dual_run',
                   return_value=local_result(observed=41)):
            report = dual_authority_case_run(**self._base(profile_path, state))
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['case']['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)

    def test_local_tier_fail_close_maps_runtime_explosions_to_unavailable(self):
        profile_path = self._write_profile(local_profile(), 'raising')
        with patch('bcs_e2e.authority.run.local_authority_dual_run',
                   side_effect=RunStateError('owned runtime exploded')):
            with self.assertRaisesRegex(ConfigError, 'unavailable'):
                raised_state = self.root / 'entry-raise'
                dual_authority_case_run(**self._base(profile_path, raised_state))
        # the entry fences froze the profile and binding before the leg exploded;
        # no run report is ever published for an unavailable executor
        self.assertTrue(raised_state.is_dir())
        self.assertTrue((raised_state / 'authority-dual-binding.json').is_file())
        self.assertFalse((raised_state / 'authority-dual-run.json').exists())

    # --------------------------------------------------------- preprod leg
    def test_admission_resolves_only_the_named_references(self):
        with patch.dict(os.environ, {dual._DRIVER_ENV: DRIVER_CANARY,
                                     dual._COOKIE_ENV: COOKIE_CANARY}):
            resolved, values, closed = dual._admission(dict(_HEADER_ENV))
        self.assertTrue(all(row['resolved'] is True for row in resolved.values()))
        self.assertEqual(values['Authorization'], DRIVER_CANARY)
        self.assertEqual(values['Cookie'], COOKIE_CANARY)
        self.assertIsNone(closed)

    def test_admission_fail_closes_on_missing_or_unsafe_credentials(self):
        cases = ({}, {dual._DRIVER_ENV: ''},
                 {dual._DRIVER_ENV: DRIVER_CANARY, dual._COOKIE_ENV: 'bad\ncookie'},
                 {dual._DRIVER_ENV: DRIVER_CANARY, dual._COOKIE_ENV: 'x' * 9000})
        for environment in cases:
            with self.subTest(environment=sorted(environment)):
                with patch.dict(os.environ, environment, clear=True):
                    resolved, values, closed = dual._admission(dict(_HEADER_ENV))
                self.assertIsNone(values)
                self.assertIsNotNone(closed)
                self.assertEqual(closed.reason_class, 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE')
                self.assertIn('never retried', closed.detail)
        resolved, values, closed = dual._admission(
            {'Authorization': 'LOWERCASE_ENV_NAME', 'Cookie': dual._COOKIE_ENV})
        self.assertIsNone(values)
        self.assertEqual(closed.reason_class, 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE')

    def test_reason_priority_orders_credentials_over_route_over_admission(self):
        self.assertEqual(_reason_priority(False, False)[0],
                         'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(_reason_priority(True, False)[0],
                         'PREPROD_AUTHORITY_ROUTE_PROBE_MISMATCH')
        self.assertEqual(_reason_priority(True, True)[0], 'PREPROD_AUTHORITY_ADMISSION_BLOCKED')

    def test_preprod_leg_fail_closes_credentials_unavailable_without_probe_leak(self):
        profile = validate_authority_profile(preprod_profile())
        admission, values, closed = dual._admission(dict(_HEADER_ENV))
        self.assertIsNone(values)
        with patch.object(dual, '_route_probes', return_value=route_observations()), \
                patch.object(dual, '_authenticated_probes', side_effect=AssertionError('never')):
            execution, receipt, blocker = dual._authority_preprod_leg(profile=profile)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(receipt['identity_mode'], 'read-only-admission-probes')
        self.assertEqual(blocker['reason_class'], 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(blocker['credential_refs'],
                         ['BCS_E2E_DRIVER_TOKEN', 'BCS_E2E_HUMAN_COOKIE'])
        self.assertEqual(blocker['authenticated_probe_routes'], [])
        self.assertIn('BCS_E2E_DRIVER_TOKEN', blocker['reason'])

    def test_preprod_leg_classifies_route_mismatch_when_admitted(self):
        profile = validate_authority_profile(preprod_profile())
        probes = route_observations()
        probes[0]['observed_status'] = 502
        probes[0]['passed'] = False
        with patch.object(dual, '_admission', return_value=(
                {'Authorization': {'env_ref': dual._DRIVER_ENV, 'resolved': True},
                 'Cookie': {'env_ref': dual._COOKIE_ENV, 'resolved': True}},
                {'Authorization': DRIVER_CANARY, 'Cookie': COOKIE_CANARY}, None)), \
                patch.object(dual, '_route_probes', return_value=probes), \
                patch.object(dual, '_authenticated_probes', return_value=[]):
            execution, receipt, blocker = dual._authority_preprod_leg(profile=profile)
        self.assertEqual(blocker['reason_class'], 'PREPROD_AUTHORITY_ROUTE_PROBE_MISMATCH')
        self.assertEqual(blocker['observed_status'], 502)
        self.assertEqual(receipt['authenticated_probe_observations'], [])
        self.assertTrue(execution.error_type == 'PREPROD_AUTHORITY_ROUTE_PROBE_MISMATCH')

    def test_preprod_leg_blocks_on_the_frozen_admission_column_when_healthy(self):
        profile = validate_authority_profile(preprod_profile())
        authenticated = [{'method': 'GET', 'url': 'x', 'request_headers': [],
                          'observed_status': 200, 'transport_error': None, 'body_status': 'ok',
                          'route': 'ownership-read-driver-bearer', 'expected': 'e',
                          'passed': True},
                         {'method': 'GET', 'url': 'y', 'request_headers': [],
                          'observed_status': 200, 'transport_error': None, 'body_status': 'ok',
                          'route': 'public-mine-read-human-cookie', 'expected': 'e',
                          'passed': True}]
        with patch.object(dual, '_admission', return_value=(
                {'Authorization': {'env_ref': dual._DRIVER_ENV, 'resolved': True}},
                {'Authorization': DRIVER_CANARY}, None)), \
                patch.object(dual, '_route_probes', return_value=route_observations()), \
                patch.object(dual, '_authenticated_probes', return_value=authenticated):
            execution, receipt, blocker = dual._authority_preprod_leg(profile=profile)
        self.assertEqual(blocker['reason_class'], 'PREPROD_AUTHORITY_ADMISSION_BLOCKED')
        self.assertIn('REMOTE_ADMISSION_GATED_READ_ONLY', blocker['reason'])
        self.assertEqual(receipt['authenticated_probe_observations'], authenticated)
        self.assertEqual(blocker['authenticated_probe_routes'],
                         ['ownership-read-driver-bearer', 'public-mine-read-human-cookie'])

    def test_preprod_tier_writes_the_blocked_evidence_leaf_and_diagnosis(self):
        profile_path = self._write_profile(preprod_profile(), 'preprod')
        state = self.root / 'entry-preprod-blocked'
        leaves = self.root / 'leaves-preprod'
        with patch.object(dual, '_admission', return_value=({}, None, dual.AuthorityClosedError(
                dual._PREPROD_CREDENTIALS_UNAVAILABLE, 'references not loaded'))), \
                patch.object(dual, '_route_probes', return_value=route_observations()), \
                patch.object(dual, '_authenticated_probes', side_effect=AssertionError('never')):
            report = dual_authority_case_run(**{**self._base(profile_path, state),
                                                'leaf_root': leaves})
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(report['preprod_blocker']['credential_refs'],
                         ['BCS_E2E_DRIVER_TOKEN', 'BCS_E2E_HUMAN_COOKIE'])
        self.assertEqual(report['resource_disposition'],
                         'AUTHORITY_PREPROD_ADMISSION_BLOCKED')
        self.assertEqual(report['credential_kind'],
                         'preprod_driver_bot_bearer_and_human_cookie_refs')
        self.assertEqual(report['environment']['api_face'], 'internal-and-public-v1')
        leaf = Path(report['preprod_evidence_leaf'])
        self.assertTrue(leaf.is_dir())
        slug = RUN_ID.replace('/', '_')
        verdict = read(leaf / (slug + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['error_type'], 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(verdict['story_checks'], 42)
        self.assertEqual(verdict['obligations_total'], 42)
        self.assertEqual(verdict['remote_validation'], 'NOT_RUN')
        self.assertFalse(verdict['grants_release_credit'])
        diagnosis = read(leaf / 'preprod-diagnosis.json')
        self.assertEqual(diagnosis['contract'], 'authority-preprod-diagnosis-v1')
        self.assertIn('gek online', diagnosis['context']['mainline_reaccount'])
        self.assertIn(diagnosis['context']['environment_column'],
                      contract.ENVIRONMENT['preprod'])
        self.assertIn('never retried', diagnosis['no_retry_statement'])
        self.assertIn('grants_release_credit stays false', diagnosis['no_credit_statement'])
        self.assertEqual(diagnosis['credential_refs'],
                         ['BCS_E2E_DRIVER_TOKEN', 'BCS_E2E_HUMAN_COOKIE'])
        combined = ''.join(path.read_text(encoding='utf-8') for path in leaf.iterdir())
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)

    def test_preprod_evidence_leaf_is_machine_readable_credential_free_and_one_time(self):
        report = {'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
                  'case_id': CASE_ID, 'source_case_id': SOURCE_CASE_ID,
                  'parent_id': contract.PARENT, 'run_id': RUN_ID, 'status': 'BLOCKED',
                  'profile_digest': 'digest', 'executor_digest': 'executor',
                  'bcs_binary_sha256': 'bcs-digest', 'cli_binary_sha256': 'cli-digest',
                  'source_binding_digest': 'binding-digest', 'state_dir': '/tmp/x',
                  'environment': {'tier': 'preprod', 'id': RUN_ID, 'expected_bcs_env': 'pre',
                                  'entrypoint_kind': 'bcs-direct',
                                  'api_face': 'internal-and-public-v1'},
                  'obligation_summary': {'total': len(contract.REQUIREMENTS),
                                         'requirement_ids': list(contract.REQUIREMENTS),
                                         'story_checks': 42},
                  'case': blocked_execution_snapshot(),
                  'execution': {'status': 'BLOCKED', 'completed_count': 0,
                                'total_count': len(contract.REQUIREMENTS),
                                'error_type': 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE'},
                  'resource_disposition': dual.DISPOSITION_PREPROD_BLOCKED,
                  'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
                  'preprod_blocker': {'reason_class': 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE',
                                      'observed_status': None, 'failure_label': '',
                                      'reason': 'references not loaded', 'story_exit_code': None,
                                      'story_timed_out': False, 'observed_checks': 0,
                                      'credential_refs': ['Authorization', 'Cookie'],
                                      'route_probes': route_observations(),
                                      'authenticated_probe_routes': []}}
        receipt = dual._empty_receipt(admission={}, route_probes=route_observations(),
                                      authenticated=[])
        leaf_root = self.root / 'leaf-direct'
        returned = dual._write_preprod_leaf(run_id=RUN_ID, report=report,
                                            story_receipt=receipt, leaf_root=leaf_root)
        self.assertEqual(returned, leaf_root)
        slug = RUN_ID.replace('/', '_')
        for name in (slug + '-stdout.json', slug + '-verdict.json', slug + '-stderr.txt',
                     'preprod-diagnosis.json'):
            self.assertTrue((leaf_root / name).is_file(), name)
        verdict = read(leaf_root / (slug + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['error_type'], 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(verdict['observed_status'], None)
        self.assertEqual(verdict['story_exit_code'], None)
        self.assertEqual(verdict['route_probes'], route_observations())
        diagnosis = read(leaf_root / 'preprod-diagnosis.json')
        self.assertEqual(diagnosis['local_leg']['obligations'], len(contract.REQUIREMENTS))
        self.assertEqual(diagnosis['local_leg']['story_checks'], 42)
        self.assertEqual(diagnosis['local_leg']['environment'], dict(contract.ENVIRONMENT))
        stdout_leaf = read(leaf_root / (slug + '-stdout.json'))
        self.assertEqual(stdout_leaf['status'], 'BLOCKED')
        stderr_text = (leaf_root / (slug + '-stderr.txt')).read_text(encoding='utf-8')
        self.assertIn('read-only preprod admission probes only', stderr_text)
        self.assertIn('no mutating story child', stderr_text)
        combined = ''.join(path.read_text(encoding='utf-8') for path in leaf_root.iterdir())
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)
        # one-time stdout leaf: a later report must not overwrite the first disclosure
        report['status'] = 'PASS'
        dual._write_preprod_leaf(run_id=RUN_ID, report=report, story_receipt=receipt,
                                 leaf_root=leaf_root)
        self.assertEqual(read(leaf_root / (slug + '-stdout.json'))['status'], 'BLOCKED')
        self.assertEqual(DEFAULT_LEAF_ROOT.name, '20261031-migration-wp2-authority-preprod')

    def test_preprod_leaf_stderr_refuses_to_persist_leaked_child_streams(self):
        report = {'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
                  'case_id': CASE_ID, 'source_case_id': SOURCE_CASE_ID,
                  'parent_id': contract.PARENT, 'run_id': RUN_ID, 'status': 'BLOCKED',
                  'profile_digest': 'digest', 'executor_digest': 'executor',
                  'bcs_binary_sha256': 'bcs-digest', 'cli_binary_sha256': 'cli-digest',
                  'source_binding_digest': 'binding-digest', 'state_dir': '/tmp/x',
                  'environment': {'tier': 'preprod', 'id': RUN_ID, 'expected_bcs_env': 'pre',
                                  'entrypoint_kind': 'bcs-direct'},
                  'obligation_summary': {'total': len(contract.REQUIREMENTS),
                                         'requirement_ids': list(contract.REQUIREMENTS),
                                         'story_checks': 42},
                  'case': blocked_execution_snapshot(),
                  'execution': {'status': 'BLOCKED', 'completed_count': 0,
                                'total_count': len(contract.REQUIREMENTS),
                                'error_type': 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE'},
                  'resource_disposition': dual.DISPOSITION_PREPROD_BLOCKED,
                  'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
                  'preprod_blocker': {'reason_class': 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE',
                                      'observed_status': None, 'failure_label': '',
                                      'reason': 'references not loaded', 'story_exit_code': None,
                                      'story_timed_out': False, 'observed_checks': 0,
                                      'credential_refs': ['Authorization', 'Cookie'],
                                      'route_probes': [], 'authenticated_probe_routes': []}}
        receipt = dual._empty_receipt(admission={}, route_probes=[], authenticated=[])
        receipt['route_probe_observations'] = [{
            'method': 'GET', 'url': 'https://bcs-pre.alipay.com/health',
            'request_headers': [], 'observed_status': 502,
            'transport_error': 'leaked Bearer ' + DRIVER_CANARY, 'body_status': None,
            'route': 'health', 'expected': 'e', 'passed': False}]
        leaf_root = self.root / 'leaf-dirty'
        dual._write_preprod_leaf(run_id=RUN_ID + '-dirty', report=report,
                                 story_receipt=receipt, leaf_root=leaf_root)
        slug = (RUN_ID + '-dirty').replace('/', '_')
        stderr_text = (leaf_root / (slug + '-stderr.txt')).read_text(encoding='utf-8')
        self.assertIn('redaction gate refused', stderr_text)
        verdict = read(leaf_root / (slug + '-verdict.json'))
        self.assertEqual(verdict['route_probes'], [])
        self.assertEqual(verdict['authenticated_probe_routes'], [])
        diagnosis = read(leaf_root / 'preprod-diagnosis.json')
        self.assertEqual(diagnosis['redaction_gate']['status'], 'BLOCKED')
        self.assertEqual(diagnosis['route_probe_observations'], [])
        self.assertEqual(diagnosis['authenticated_probe_observations'], [])

    def test_preprod_binding_profile_and_report_land_in_the_state_directory(self):
        profile_path = self._write_profile(preprod_profile(), 'files')
        state = self.root / 'entry-preprod-files'
        leaves = self.root / 'leaves-files'
        with patch.object(dual, '_admission', return_value=({}, None, dual.AuthorityClosedError(
                dual._PREPROD_CREDENTIALS_UNAVAILABLE, 'references not loaded'))), \
                patch.object(dual, '_route_probes', return_value=route_observations()), \
                patch.object(dual, '_authenticated_probes', side_effect=AssertionError('never')):
            dual_authority_case_run(**{**self._base(profile_path, state), 'leaf_root': leaves})
        binding = read(state / 'authority-dual-binding.json')
        self.assertEqual(binding['schema_version'], SCHEMA_VERSION)
        self.assertEqual(binding['contract'], REPORT_CONTRACT)
        self.assertEqual(binding['suite'], SUITE)
        self.assertEqual(binding['source_case_id'], SOURCE_CASE_ID)
        self.assertEqual(binding['source_suite'], SOURCE_SUITE)
        self.assertEqual(binding['parent_id'], contract.PARENT)
        self.assertEqual(binding['case_keys'], list(CASE_KEYS))
        self.assertEqual(binding['requirement_ids'], list(contract.REQUIREMENTS))
        self.assertEqual(binding['story_checks'], 42)
        self.assertEqual(binding['bcs_binary_sha256'], self.sha)
        self.assertEqual(binding['cli_binary_sha256'], self.cli_sha)
        self.assertEqual(binding['profile_digest'], fingerprint(validate_authority_profile(
            preprod_profile())))
        self.assertEqual(binding['executor_digest'], executor_digest())
        report = read(state / 'authority-dual-run.json')
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['preprod_evidence_leaf'], str(leaves))
        stored_profile = read(state / 'authority-profile.json')
        self.assertEqual(stored_profile['environment']['tier'], 'preprod')

    # ------------------------------------------------------------- honesty
    def test_http_probe_turns_transport_failures_into_observations(self):
        observation = dual._http_probe('http://127.0.0.1:1/health')
        self.assertIsNone(observation['observed_status'])
        self.assertIsInstance(observation['transport_error'], str)
        self.assertIsNone(observation['body_status'])

    def _fence_server(self, handler) -> tuple[ThreadingHTTPServer, str]:
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        return server, 'http://127.0.0.1:' + str(server.server_address[1])

    def test_route_probes_record_honest_observations_against_a_local_surface(self):
        class Fence(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/health':
                    body = json.dumps({'status': 'ok'}).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    body = json.dumps({'code': 'unauthorized', 'message': 'admission required',
                                       'request_id': 'req-local-fence', 'data': None}).encode()
                    self.send_response(401)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server, origin = self._fence_server(Fence)
        probes = dual._route_probes(origin)
        self.assertEqual([row['passed'] for row in probes], [True, True])
        self.assertEqual(probes[0]['observed_status'], 200)
        self.assertEqual(probes[0]['body_status'], 'ok')
        self.assertEqual(probes[1]['observed_status'], 401)
        # the mint route never admits mock headers on this honest wire
        raw = probes[1]
        self.assertEqual(raw['request_headers'], [])

    def test_authenticated_probes_record_statuses_without_credential_values(self):
        class Fence(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'code': 'ok', 'message': 'observed',
                                   'request_id': 'req-auth-fence', 'data': {}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server, origin = self._fence_server(Fence)
        values = {'Authorization': DRIVER_CANARY, 'Cookie': COOKIE_CANARY}
        probes = dual._authenticated_probes(origin, values)
        self.assertEqual([row['passed'] for row in probes], [True, True])
        self.assertEqual([row['route'] for row in probes],
                         ['ownership-read-driver-bearer', 'public-mine-read-human-cookie'])
        combined = json.dumps(probes)
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)
        for row in probes:
            self.assertEqual(row['method'], 'GET')
            self.assertEqual(row['request_headers'], ['Authorization']
                             if row['route'].startswith('ownership') else ['Cookie'])

    def test_route_and_authenticated_probes_are_frozen_to_read_only_verbs(self):
        self.assertEqual(dual._HEADER_ENV, {'Authorization': dual._DRIVER_ENV,
                                            'Cookie': dual._COOKIE_ENV})
        self.assertEqual(dual._DRIVER_ENV, 'BCS_E2E_DRIVER_TOKEN')
        self.assertEqual(dual._COOKIE_ENV, 'BCS_E2E_HUMAN_COOKIE')

    # -------------------------------------------------------------- module
    def test_dual_contract_constant_mapping_stays_frozen(self):
        self.assertEqual(CASE_ID, 'DUAL-AUTHORITY-JOURNEY-01')
        self.assertEqual(SUITE, 'dual-authority-v1')
        self.assertEqual(SOURCE_CASE_ID, contract.CASE_ID)
        self.assertEqual(SOURCE_SUITE, contract.SUITE)
        self.assertEqual(CASE_KEYS, ('authority',))
        self.assertEqual(SCHEMA_VERSION, 1)
        self.assertEqual(PROFILE_CONTRACT, 'dual-authority-profile-v1')
        self.assertEqual(REPORT_CONTRACT, 'dual-authority-case-run-v1')
        self.assertEqual(len(contract.REQUIREMENTS), 42)
        self.assertEqual(dual.STORY_CHECKS, 42)
        self.assertEqual(dual.REQUIREMENTS, contract.REQUIREMENTS)
        self.assertEqual(dual._LOCAL_PROFILE, 'owned-local')
        self.assertEqual(dual._PREPROD_PROFILE, 'preprod')
        self.assertEqual(dual.DISPOSITION_PREPROD_BLOCKED, 'AUTHORITY_PREPROD_ADMISSION_BLOCKED')
        self.assertEqual(dual.ENVIRONMENT, contract.ENVIRONMENT)

    def test_adaptability_describes_the_dual_surface_without_release_credit(self):
        value = authority_dual_adaptability()
        self.assertEqual(value['case_id'], CASE_ID)
        self.assertEqual(value['source_case_id'], SOURCE_CASE_ID)
        self.assertEqual(value['legacy_id'], contract.PARENT)
        self.assertEqual(value['requirement_ids'], list(contract.REQUIREMENTS))
        self.assertEqual(value['required_assertion_count'], 42)
        self.assertEqual(value['environments'],
                         ['owned-local-full-story', 'preprod-read-only-admission-probes'])
        self.assertEqual(value['local_executor_status'], 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(value['preprod_executor_status'],
                         'PREPROD_READ_ONLY_ADMISSION_GATED_PROBES')
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertFalse(value['grants_release_credit'])
        self.assertIn('read-only admission column', value['blocker'])
        self.assertIn('not release credit', value['blocker'])

    # --------------------------------------------------- registration seam
    def test_registration_seam_boolean_equivalence_only(self):
        from bcs_e2e.case_command_registration import CASE_COMMANDS
        registered = 'dual-authority-run' in CASE_COMMANDS
        planned = any(row.get('execution_command') == 'dual-authority-run'
                      for row in migration_plan()['local_backlog_case_slices'])
        self.assertEqual(registered, planned)

    # --------------------------------------------------------------- CLI
    def test_main_prints_the_pass_report_for_the_local_tier(self):
        profile_path = self._write_profile(local_profile(), 'main-ok')
        from contextlib import redirect_stdout
        import io
        state = self.root / 'main-state'
        with patch('bcs_e2e.authority.run.local_authority_dual_run',
                   return_value=local_result()):
            with redirect_stdout(io.StringIO()) as stdout:
                rc = main(['--profile', str(profile_path), '--state-dir', str(state),
                           '--run-id', RUN_ID, '--confirm-run-id', RUN_ID, '--allow-write',
                           '--binary', str(self.binary), '--binary-sha256', self.sha,
                           '--cli-binary', str(self.cli_binary), '--cli-binary-sha256',
                           self.cli_sha])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'PASS')

    def test_main_fail_closes_entry_errors_with_rc_two(self):
        profile_path = self._write_profile(local_profile(), 'main-blocked')
        from contextlib import redirect_stdout
        import io
        with redirect_stdout(io.StringIO()) as stdout:
            rc = main(['--profile', str(profile_path), '--state-dir', str(self.root / 'm'),
                       '--run-id', RUN_ID, '--confirm-run-id', 'other-id', '--allow-write',
                       '--binary', str(self.binary), '--binary-sha256', self.sha,
                       '--cli-binary', str(self.cli_binary), '--cli-binary-sha256', self.cli_sha])
        self.assertEqual(rc, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['status'], 'BLOCKED')
        self.assertEqual(payload['error_type'], 'ConfigError')
        self.assertFalse((self.root / 'm').exists())

    def test_subprocess_fail_closes_when_any_binary_pair_argument_is_missing(self):
        profile_path = self._write_profile(local_profile(), 'negctl')
        arguments = ['--profile', str(profile_path),
                     '--state-dir', str(self.root / 'negctl-state'),
                     '--run-id', RUN_ID, '--confirm-run-id', RUN_ID, '--allow-write',
                     '--binary', str(self.binary), '--binary-sha256', self.sha,
                     '--cli-binary', str(self.cli_binary), '--cli-binary-sha256', self.cli_sha]
        pairs = ('--binary', '--binary-sha256', '--cli-binary', '--cli-binary-sha256')
        for omitted in pairs:
            with self.subTest(omitted=omitted):
                argv = [sys.executable, '-m', 'bcs_e2e.authority.dual_case_run']
                skip = False
                for item in arguments:
                    if skip:
                        skip = False
                        continue
                    if item == omitted:
                        skip = True
                        continue
                    argv.append(item)
                result = subprocess.run(argv, cwd=str(ROOT), capture_output=True,
                                        timeout=120)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'negctl-state').exists())


if __name__ == '__main__':
    unittest.main()
class StationAdmissionGateTests(unittest.TestCase):
    """Station (in-China site) form: ACE SSO HTML gate on both frozen faces."""

    def _html_fence(self, *, health_body: str, mint_body: str) -> str:
        class Fence(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/health':
                    body = health_body.encode()
                else:
                    body = mint_body.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Fence)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        return 'http://127.0.0.1:' + str(server.server_address[1])

    def test_station_html_login_gate_fails_closed_on_health_and_blocks_mint(self):
        origin = self._html_fence(health_body='<html>pwdLogin.js injectInfo env:PROD</html>',
                                  mint_body='<html>station ACE login gate</html>')
        probes = dual._route_probes(origin)
        health, mint = probes
        self.assertEqual(health['observed_status'], 200)
        self.assertEqual(health['body_class'], 'html')
        self.assertIsNone(health['body_status'])
        self.assertFalse(health['passed'])
        self.assertEqual(mint['observed_status'], 200)
        self.assertEqual(mint['body_class'], 'html')
        self.assertFalse(mint['json_token_key'])
        self.assertTrue(mint['passed'])
        self.assertFalse(all(row['passed'] for row in probes))
        self.assertEqual(dual._reason_priority(True, False)[0],
                         dual._PREPROD_ROUTE_PROBE_MISMATCH)

    def test_health_probe_attaches_only_the_cookie_name_when_values_present(self):
        captured = {}

        def fake_probe(url, *, headers=None):
            key = 'health' if url.endswith('/health') else 'mint'
            captured[key] = sorted(headers or {})
            return {'method': 'GET', 'url': url, 'request_headers': sorted(headers or {}),
                    'observed_status': 200, 'transport_error': None, 'body_status': 'ok',
                    'body_class': 'json', 'json_token_key': False}

        with patch.object(dual, '_http_probe', side_effect=fake_probe):
            probes = dual._route_probes('https://bcs-pre.alipay.com',
                                        {'Cookie': 'canary-value', 'Authorization': 'Bearer x'})
        self.assertEqual(captured['health'], ['Cookie'])
        self.assertEqual(captured['mint'], [])
        self.assertEqual([row['passed'] for row in probes], [True, True])
