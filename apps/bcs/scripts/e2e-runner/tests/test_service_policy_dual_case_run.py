"""Contract, profile, fail-closed and CLI registration tests for the dual service policy case."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcs_e2e.service_policy.dual_case_run as dual
from bcs_e2e.cli import main
from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.inventory import executor_digest, load_executor_migration
from bcs_e2e.local_service_policy_contract import REQUIREMENTS as FLAT_REQUIREMENTS
from bcs_e2e.local_service_policy_contract import STORY_CHECKS
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, fingerprint, identifier
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.service_policy import contract
from bcs_e2e.service_policy.checks import (ServicePolicyViolation, scan_redaction,
                                           verify_all_obligations)
from bcs_e2e.service_policy.dual_case_run import (
    CASE_ID, CASE_KEYS, DEFAULT_LEAF_ROOT, PreprodClosedError, PROFILE_CONTRACT,
    REPORT_CONTRACT, SCHEMA_VERSION, STORY_DEADLINE_SECONDS, _bounded_environment,
    _failure_label, _observed_status, _parse_story_marker, _preprod_argv, _preprod_execution,
    _local_execution, _write_preprod_leaf, dual_service_policy_case_run,
    load_service_policy_profile, validate_service_policy_profile)
from bcs_e2e.service_policy.contract import (BEARER_STORY_CHECKS, COOKIE_ENV, DRIVER_ENV,
                                             DRIVER_BOT_ID, EFFECTS, ENV_REF, HEADER_ENV,
                                             MAX_CREDENTIAL_BYTES, REGISTRATION_DISPOSITION,
                                             REQUIREMENTS, REDACTION_CANARIES, STATION_ROUTES,
                                             frozen_dual_contract, service_policy_dual_adaptability,
                                             service_policy_dual_source_binding)

RUN_ID = '20261031-migration-wps-service-policy-test'
DRIVER_BOT = '20261026_xq102n2b:39464'
HUMAN_USER = '39464'
COOKIE_CANARY = 'synthetic-cookie-value-never-persisted'
DRIVER_CANARY = 'synthetic-driver-token-never-persisted'
WRONG_SHA = 'f' * 64
GOOD_MARKER = ('Retired service policy story: ' + str(BEARER_STORY_CHECKS)
               + ' HTTP/response checks passed').encode()
GOOD_LABEL = b'Retired service policy story failed: interaction guard lost: unexpected HTTP status 409'


def local_profile() -> dict:
    return {
        'schema_version': 1,
        'contract': PROFILE_CONTRACT,
        'environment': {'tier': 'local', 'id': 'service-policy-local',
                        'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'owned-local'},
        'human': {'user_id': HUMAN_USER},
        'auth': {'kind': 'owned-local'},
        'cli': {'transport': 'owned-local-story', 'command_family': 'retired-service-policy',
                'api_face': 'legacy'},
        'policy': {'exclusive_use_confirmed': True, 'timeout_seconds': 60,
                   'max_response_bytes': 1048576, 'failure_retention_hours': 24},
        'station_routes': [dict(row) for row in STATION_ROUTES],
        'effects': dict(EFFECTS),
    }


def preprod_profile() -> dict:
    value = copy.deepcopy(local_profile())
    value['environment'] = {'tier': 'preprod', 'id': 'service-policy-pre',
                            'expected_bcs_env': 'pre'}
    value['entrypoint'] = {'kind': 'bcs-direct', 'origin': 'https://bcs-pre.alipay.com',
                           'api_face': 'legacy', 'manifest_face': 'legacy'}
    value['auth'] = {'kind': 'preprod-driver-bot-bearer',
                     'header_env': {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}}
    value['actors'] = {'driver': {'bot_id': DRIVER_BOT}}
    value['cli'] = {'transport': 'product-http', 'command_family': 'retired-service-policy',
                    'api_face': 'legacy'}
    return value


def obligation_records() -> dict:
    object_record = {
        'created_status': 200, 'created_service_spec_absent': True,
        'created_retired_service_spec_absent': True, 'readback_status': 200,
        'readback_service_spec_absent': True, 'readback_retired_service_spec_absent': True,
        'driver_owned': True}
    invocations = [
        {'session_id': 'service-session-0', 'status': 202,
         'session_kind': 'service_invocation', 'lifecycle_status': 'running',
         'stored_input': {'run': 0}},
        {'session_id': 'service-session-1', 'status': 202,
         'session_kind': 'service_invocation', 'lifecycle_status': 'running',
         'stored_input': {'run': 1}}]
    return {
        REQUIREMENTS[0]: {
            'transport': 'product-http', 'route': 'GET /bots/{driver_bot_id}',
            'status': 200, 'actor_match': True, 'driver_bot': DRIVER_BOT,
            'cookie_mode': 'duo_attached_after_presence_validation'},
        REQUIREMENTS[1]: {
            'transport': 'none', 'disposition': REGISTRATION_DISPOSITION,
            'driver_bot_deletion_forbidden': True},
        REQUIREMENTS[2]: {
            'transport': 'product-http',
            'groups': [dict(object_record, group_id='group-legacy-object'),
                       dict(object_record, group_id='group-legacy-null', created_status=201)]},
        REQUIREMENTS[3]: {
            'transport': 'product-http', 'status': 410,
            'error': 'service_policy_retired'},
        REQUIREMENTS[4]: {
            'transport': 'product-http', 'group_id': 'group-legacy-object',
            'invocations': invocations},
        REQUIREMENTS[5]: {
            'transport': 'product-http', 'status': 409,
            'error': 'invocation is running', 'stored_input': {'run': 0}},
        REQUIREMENTS[6]: {
            'transport': 'product-http', 'chat_create_status': 201,
            'completed_status': 'completed', 'conversion_status': 400,
            'error': 'not service_invocation: chat Session cannot convert',
            'final_status': 'completed', 'final_output': {'ok': True}},
        REQUIREMENTS[7]: {
            'transport': 'product-http', 'sessions_deleted': 3, 'groups_deleted': 2,
            'delete_statuses': [200, 204, 200, 204, 200],
            'driver_bot_delete_disposition': 'SKIPPED_SHARED_DRIVER_BOT',
            'failures': [], 'groups_teardown_matches_creation': True},
    }


def bearer_evidence() -> dict:
    return {
        'schema_version': 1, 'identity_mode': 'bearer', 'driver_bot': DRIVER_BOT,
        'groups': ['group-legacy-object', 'group-legacy-null'],
        'sessions': ['service-session-0', 'service-session-1', 'chat-session-0'],
        'obligations': obligation_records(),
        'failures': [], 'groups_teardown_matches_creation': True,
    }


def local_result(*, observed: int = STORY_CHECKS, marker: bool = True,
                 status: str = 'LOCAL_SERVICE_POLICY_PASS') -> dict:
    receipt = {
        'schema_version': 1, 'run_id': RUN_ID, 'case_id': 'LOCAL-SERVICE-POLICY-01',
        'argv': [sys.executable, 'service_policy_story.py', '--base-url',
                 'http://127.0.0.1:0'],
        'exit_code': 0, 'timed_out': False, 'duration_seconds': 1.0,
        'markers': {'retired-service-policy-compliance': marker},
        'observed_checks': observed, 'redaction_gate': True,
        'stdout_sha256': '0' * 64, 'stdout_bytes': 0,
        'stderr_sha256': '0' * 64, 'stderr_bytes': 0}
    return {
        'schema_version': 1, 'status': status, 'run_id': RUN_ID,
        'case_id': 'LOCAL-SERVICE-POLICY-01', 'suite': 'owned-local-service-policy-v1',
        'executor_digest': 'digest', 'binary_kind': 'bcs', 'bcs_binary_sha256': '0' * 64,
        'auto_build': False, 'child_receipt': receipt,
        'case': {'status': status} if status == 'LOCAL_SERVICE_POLICY_PASS' else {},
        'credentials': {'credential_memory': 'NOT_CAPTURED'},
        'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
        'ready_to_execute': status == 'LOCAL_SERVICE_POLICY_PASS',
        'canonical_summary': {'status': 'LOCAL_SUITE_PASS'},
        'resource_disposition': 'STORY_SCOPED_TEMPORARY_DIRECTORY'}


def executable_binary(root: Path, name: str = 'bcs-fixture') -> tuple[Path, str]:
    binary = root / name
    binary.write_bytes(b'owned-bcs-fixture-bytes')
    binary.chmod(0o700)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


class ServicePolicyDualCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary, self.sha = executable_binary(self.root)

    def _write_profile(self, value: dict, name: str) -> Path:
        path = self.root / f'profile-{name}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    # ------------------------------------------------------------- frozen contract
    def test_frozen_dual_contract_module_constants(self):
        self.assertEqual(dual.CASE_ID, 'DUAL-SERVICE-POLICY-01')
        self.assertEqual(dual.SUITE, 'dual-service-policy-v1')
        self.assertEqual(dual.CASE_KEYS, ('service-policy',))
        self.assertEqual(dual.SCHEMA_VERSION, 1)
        self.assertEqual(dual.BEARER_STORY_CHECKS, 20)
        self.assertEqual(dual.STORY_CHECKS, 23)
        self.assertEqual(dual.PARENT, 'story_retired_service_policy')
        self.assertEqual(dual.SOURCE_CASE_ID, 'LOCAL-SERVICE-POLICY-01')
        self.assertEqual(dual.PROFILE_CONTRACT, 'dual-service-policy-profile-v1')
        self.assertEqual(dual.REPORT_CONTRACT, 'dual-service-policy-case-run-v1')
        self.assertEqual(dual.STORY_DEADLINE_SECONDS, 900)
        self.assertEqual(dual._APPLICABLE_COUNT, 7)
        self.assertEqual(list(dual.REQUIREMENTS), list(FLAT_REQUIREMENTS))
        self.assertEqual(len(dual.REQUIREMENTS), 8)
        self.assertTrue(dual.BEARER_STORY_SOURCE.is_file())
        self.assertEqual(dual.BEARER_STORY_SOURCE.name, 'story.py')
        self.assertTrue(dual._MARKER_RE.search(GOOD_MARKER.decode()))
        self.assertIsNone(dual._MARKER_RE.search(b'Retired service policy story: 19 checks'.decode()))

    def test_frozen_station_and_policy_truths(self):
        self.assertEqual(len(STATION_ROUTES), 13)
        self.assertEqual(dual.STATION_ROUTES, contract.STATION_ROUTES)
        self.assertEqual(contract.HEADER_ENV, {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
        self.assertEqual(DRIVER_ENV, 'BCS_E2E_DRIVER_TOKEN')
        self.assertEqual(COOKIE_ENV, 'BCS_E2E_HUMAN_COOKIE')
        self.assertEqual(contract.DUAL_ENVIRONMENT, {'local': 'LOCAL_EXECUTOR_ONLY',
                                                     'preprod': 'REMOTE_BEARER_MUTATING_ADMITTED'})
        self.assertEqual(contract.OWNED_RUNTIME_BINARY['package'], 'bcs')
        self.assertEqual(contract.OWNED_RUNTIME_BINARY['auto_build'], False)
        self.assertEqual(contract.OWNED_RUNTIME_BINARY['required_arguments'],
                         ['--binary', '--binary-sha256'])
        self.assertEqual(contract.DRIVER_BOT_DELETE_DISPOSITION, 'SKIPPED_SHARED_DRIVER_BOT')
        self.assertEqual(EFFECTS['disposable_groups'], 2)
        self.assertEqual(EFFECTS['disposable_sessions'], 3)
        self.assertEqual(EFFECTS['bot_registration'], 'FORBIDDEN')
        self.assertEqual(EFFECTS['shared_driver_bot_deletion'], 'FORBIDDEN')
        self.assertTrue(DRIVER_BOT_ID.fullmatch(DRIVER_BOT))
        self.assertIsNone(DRIVER_BOT_ID.fullmatch('20261026_xq102n2b'))
        self.assertTrue(ENV_REF.fullmatch('BCS_E2E_DRIVER_TOKEN'))
        self.assertIsNone(ENV_REF.fullmatch('lowercase_ref'))
        self.assertEqual(MAX_CREDENTIAL_BYTES, 8192)
        self.assertIn('bot_token', REDACTION_CANARIES)
        self.assertIn('Bearer ', REDACTION_CANARIES)
        self.assertEqual(DEFAULT_LEAF_ROOT.name,
                         '20261031-migration-wps-service-policy-preprod')
        self.assertEqual(DEFAULT_LEAF_ROOT, Path.home() / '.codex' / 'bcs-e2e'
                         / '20261031-migration-wps-service-policy-preprod')

    def test_frozen_dual_contract_json_matches_the_module(self):
        frozen = json.loads((ROOT / 'service-policy-dual-migration-contract.json')
                            .read_text(encoding='utf-8'))
        self.assertEqual(frozen, frozen_dual_contract())
        self.assertEqual(service_policy_dual_source_binding(), frozen)
        self.assertEqual(frozen['dual_case_id'], 'DUAL-SERVICE-POLICY-01')
        self.assertEqual(frozen['story_checks'], {'local': 23, 'bearer': 20})
        self.assertEqual(frozen['identity_modes'], {'local': 'register', 'preprod': 'bearer'})
        self.assertEqual(frozen['remote_validation'], 'NOT_RUN')
        self.assertFalse(frozen['grants_release_credit'])

    def test_dual_source_binding_drift_fails_closed(self):
        base = json.loads((ROOT / 'service-policy-dual-migration-contract.json')
                          .read_text(encoding='utf-8'))
        drifted = dict(base)
        drifted['story_checks'] = {'local': 23, 'bearer': 19}
        with patch('bcs_e2e.service_policy.contract.read', return_value=drifted):
            with self.assertRaises(RunStateError):
                service_policy_dual_source_binding()
            with self.assertRaises(RunStateError):
                dual_service_policy_case_run(
                    profile_path=self._write_profile(local_profile(), 'drift'),
                    state_dir=self.root / 'drift-state', run_id=RUN_ID, allow_write=True,
                    confirm_run_id=RUN_ID, binary=self.binary, binary_sha256=self.sha)
        self.assertFalse((self.root / 'drift-state').exists())

    def test_flat_source_binding_drift_fails_the_dual_binding(self):
        base = json.loads((ROOT / 'service-policy-migration-contract.json')
                          .read_text(encoding='utf-8'))
        drifted = dict(base)
        drifted['story_checks'] = 24
        with patch('bcs_e2e.local_service_policy_contract.read', return_value=drifted):
            with self.assertRaises(RunStateError):
                service_policy_dual_source_binding()

    def test_service_policy_dual_adaptability_surface(self):
        value = service_policy_dual_adaptability()
        self.assertEqual(value['case_id'], 'DUAL-SERVICE-POLICY-01')
        self.assertEqual(value['source_case_id'], 'LOCAL-SERVICE-POLICY-01')
        self.assertEqual(value['legacy_id'], 'story_retired_service_policy')
        self.assertEqual(value['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(value['applicable_requirement_ids'],
                         [row for row in REQUIREMENTS if row != REQUIREMENTS[1]])
        self.assertEqual(value['not_applicable_requirement'], REQUIREMENTS[1])
        self.assertEqual(value['not_applicable_disposition'], REGISTRATION_DISPOSITION)
        self.assertEqual(value['environments'],
                         ['owned-local-full-story', 'preprod-full-story-bcs-direct-bearer'])
        self.assertEqual(value['local_executor_status'], 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(value['preprod_executor_status'], 'PREPROD_BEARER_STORY_CHILD')
        self.assertEqual(value['required_assertion_count'], 8)
        self.assertEqual(value['applicable_assertion_count'], 7)
        self.assertEqual(value['local_story_checks'], 23)
        self.assertEqual(value['bearer_story_checks'], 20)
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertFalse(value['grants_release_credit'])
        self.assertIn('preprod', value['blocker'])

    def test_obligation_checks_and_redaction_scan_semantics(self):
        self.assertEqual(verify_all_obligations(bearer_evidence()),
                         {'status': 'PASS', 'checked': list(REQUIREMENTS)})
        self.assertTrue(issubclass(ServicePolicyViolation, RunStateError))
        with self.assertRaises(ServicePolicyViolation):
            verify_all_obligations({'obligations': {}})
        clean = scan_redaction('story.stdout', GOOD_MARKER)
        self.assertTrue(clean['clean'])
        self.assertEqual(clean['canary_hits'], [])
        dirty = scan_redaction('story.stderr', b'leaked Bearer driver secret')
        self.assertFalse(dirty['clean'])
        self.assertEqual(dirty['canary_hits'], ['Bearer '])

    # ------------------------------------------------------------------- profiles
    def test_both_tier_profiles_validate(self):
        self.assertEqual(validate_service_policy_profile(local_profile())['environment']['tier'],
                         'local')
        validated = validate_service_policy_profile(preprod_profile())
        self.assertEqual(validated['environment']['tier'], 'preprod')
        self.assertEqual(validated['actors']['driver']['bot_id'], DRIVER_BOT)
        self.assertEqual(validated['auth']['header_env'],
                         {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
        self.assertEqual(load_service_policy_profile(
            self._write_profile(preprod_profile(), 'valid'))['environment']['id'],
            'service-policy-pre')

    def test_profile_rejects_drift_shapes(self):
        cases = (
            ('unknown-top-level', lambda value: value.__setitem__('credential_files', {}), True),
            ('schema-version', lambda value: value.__setitem__('schema_version', 2), True),
            ('contract', lambda value: value.__setitem__('contract', 'wrong'), True),
            ('tier', lambda value: value['environment'].__setitem__('tier', 'staging'), True),
            ('bcs-env-binding', lambda value: value['environment'].__setitem__(
                'expected_bcs_env', 'prod'), True),
            ('local-entrypoint-kind', lambda value: value['entrypoint'].__setitem__(
                'kind', 'bcs-direct'), False),
            ('local-with-actors', lambda value: value.__setitem__(
                'actors', {'driver': {'bot_id': DRIVER_BOT}}), False),
            ('preprod-entrypoint-kind', lambda value: value.update(
                {'environment': {'tier': 'preprod', 'id': 'x', 'expected_bcs_env': 'pre'},
                 'entrypoint': {'kind': 'owned-local'}, 'auth': {'kind': 'owned-local'},
                 'actors': {'driver': {'bot_id': DRIVER_BOT}},
                 'cli': {'transport': 'product-http',
                         'command_family': 'retired-service-policy',
                         'api_face': 'legacy'}}), True),
            ('preprod-entrypoint-missing-origin', lambda value: value['entrypoint'].pop(
                'origin'), True),
            ('preprod-origin-http', lambda value: value['entrypoint'].__setitem__(
                'origin', 'http://bcs-pre.alipay.com'), True),
            ('preprod-origin-path', lambda value: value['entrypoint'].__setitem__(
                'origin', 'https://bcs-pre.alipay.com/v1'), True),
            ('preprod-origin-face', lambda value: value['entrypoint'].__setitem__(
                'api_face', 'public-v1'), True),
            ('preprod-manifest-face', lambda value: value['entrypoint'].__setitem__(
                'manifest_face', 'public-v1'), True),
            ('human', lambda value: value['human'].__setitem__('user_id', 'user space'), True),
            ('local-auth-swap', lambda value: value['auth'].__setitem__(
                'kind', 'preprod-driver-bot-bearer'), False),
            ('preprod-auth-swap', lambda value: value['auth'].__setitem__(
                'kind', 'owned-local'), True),
            ('header-env-missing-cookie', lambda value: value['auth']['header_env'].pop(
                'Cookie'), True),
            ('header-env-extra-header', lambda value: value['auth']['header_env'].__setitem__(
                'X-Extra', 'BCS_E2E_DRIVER_TOKEN'), True),
            ('header-env-lowercase-ref', lambda value: value['auth']['header_env'].__setitem__(
                'Authorization', 'bcs_e2e_driver_token'), True),
            ('header-env-invalid-ref', lambda value: value['auth']['header_env'].__setitem__(
                'Authorization', 'not-a-valid-env-ref'), True),
            ('header-env-swapped-refs', lambda value: value['auth']['header_env'].__setitem__(
                'Authorization', COOKIE_ENV), True),
            ('actors-missing', lambda value: value.pop('actors'), True),
            ('actors-extra-role', lambda value: value['actors'].__setitem__(
                'member', {'bot_id': '20261026_zy82brbu:39464'}), True),
            ('actors-driver-extra-key', lambda value: value['actors']['driver'].__setitem__(
                'owner_id', HUMAN_USER), True),
            ('driver-bot-shape', lambda value: value['actors']['driver'].__setitem__(
                'bot_id', '20261026_xq102n2b'), True),
            ('driver-bot-uppercase', lambda value: value['actors']['driver'].__setitem__(
                'bot_id', '20261026_XQ102N2B:39464'), True),
            ('cli-transport-swap', lambda value: value['cli'].__setitem__(
                'transport', 'owned-local-story'), True),
            ('local-cli-transport', lambda value: value['cli'].__setitem__(
                'transport', 'product-http'), False),
            ('cli-family', lambda value: value['cli'].__setitem__(
                'command_family', 'collaboration-draft'), True),
            ('cli-face', lambda value: value['cli'].__setitem__('api_face', 'public-v1'), True),
            ('policy-exclusive', lambda value: value['policy'].__setitem__(
                'exclusive_use_confirmed', False), True),
            ('policy-timeout-low', lambda value: value['policy'].__setitem__(
                'timeout_seconds', 0), True),
            ('policy-timeout-high', lambda value: value['policy'].__setitem__(
                'timeout_seconds', 121), True),
            ('policy-timeout-type', lambda value: value['policy'].__setitem__(
                'timeout_seconds', '60'), True),
            ('policy-bytes', lambda value: value['policy'].__setitem__(
                'max_response_bytes', 0), True),
            ('policy-retention', lambda value: value['policy'].__setitem__(
                'failure_retention_hours', 169), True),
            ('policy-extra-key', lambda value: value['policy'].__setitem__(
                'credential_cache', True), True),
            ('station-routes-drift', lambda value: value['station_routes'][0].__setitem__(
                'station_status', 201), True),
            ('station-routes-extra-row', lambda value: value['station_routes'].append(
                dict(STATION_ROUTES[0])), True),
            ('station-routes-type', lambda value: value.__setitem__(
                'station_routes', tuple(dict(row) for row in STATION_ROUTES)), True),
            ('effects-drift', lambda value: value['effects'].__setitem__(
                'bot_registration', 'ALLOWED'), True),
            ('effects-missing-key', lambda value: value['effects'].pop(
                'implicit_effects'), True),
        )
        for name, mutate, is_preprod in cases:
            with self.subTest(mutation=name):
                value = copy.deepcopy(preprod_profile() if is_preprod else local_profile())
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_service_policy_profile(value)

    def test_load_service_policy_profile_fail_closes(self):
        target = self.root / 'profile-link.json'
        real = self._write_profile(preprod_profile(), 'link-target')
        target.symlink_to(real)
        with self.assertRaisesRegex(ConfigError, 'regular file'):
            load_service_policy_profile(target)
        broken = self.root / 'profile-broken.json'
        broken.write_text('not json', encoding='utf-8')
        with self.assertRaisesRegex(ConfigError, 'unreadable JSON'):
            load_service_policy_profile(broken)
        missing = self.root / 'profile-missing.json'
        with self.assertRaisesRegex(ConfigError, 'regular file'):
            load_service_policy_profile(missing)

    def test_entry_rejects_unresolved_placeholders(self):
        value = preprod_profile()
        value['human']['user_id'] = 'REPLACE_WITH_39464'
        path = self._write_profile(value, 'placeholder')
        with self.assertRaisesRegex(ConfigError, 'REPLACE_WITH'):
            dual_service_policy_case_run(
                profile_path=path, state_dir=self.root / 'placeholder-state', run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.sha)

    # ----------------------------------------------------------------------- seams
    def test_preprod_argv_keeps_credentials_out_of_the_command_line(self):
        evidence = self.root / 'evidence.json'
        argv = _preprod_argv(base_url='https://bcs-pre.alipay.com', driver_bot=DRIVER_BOT,
                             evidence_path=evidence)
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(Path(argv[1]).name, 'story.py')
        self.assertTrue(dual.BEARER_STORY_SOURCE.is_file())
        self.assertIn('--base-url', argv)
        self.assertEqual(argv[argv.index('--base-url') + 1], 'https://bcs-pre.alipay.com')
        self.assertIn('--driver-bot', argv)
        self.assertEqual(argv[argv.index('--driver-bot') + 1], DRIVER_BOT)
        self.assertEqual(argv[argv.index('--bearer-token-env') + 1], DRIVER_ENV)
        self.assertEqual(argv[argv.index('--cookie-env') + 1], COOKIE_ENV)
        self.assertIn(str(evidence), argv)
        joined = ' '.join(str(item) for item in argv)
        self.assertNotIn(DRIVER_CANARY, joined)
        self.assertNotIn(COOKIE_CANARY, joined)
        self.assertNotIn('register', joined)

    def test_bounded_environment_resolves_only_the_named_references(self):
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: COOKIE_CANARY}):
            child = _bounded_environment({'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
        self.assertEqual(child[DRIVER_ENV], DRIVER_CANARY)
        self.assertEqual(child[COOKIE_ENV], COOKIE_CANARY)

    def test_bounded_environment_fail_closes_on_missing_or_unsafe_credentials(self):
        cases = ({}, {DRIVER_ENV: ''}, {COOKIE_ENV: ''},
                 {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: 'bad\ncookie'},
                 {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: 'x' * 9000},
                 {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: 'café-cookie'})
        for environment in cases:
            with self.subTest(environment=sorted(environment)):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(PreprodClosedError) as ctx:
                        _bounded_environment({'Authorization': DRIVER_ENV,
                                              'Cookie': COOKIE_ENV})
                self.assertEqual(ctx.exception.reason_class,
                                 'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
                self.assertIsNone(ctx.exception.observed_status)

    def test_story_diagnosis_parses_status_and_label(self):
        self.assertEqual(_observed_status('nothing'), None)
        self.assertEqual(_observed_status('unexpected HTTP status 409'), 409)
        self.assertEqual(_failure_label(
            'Retired service policy story failed: interaction guard lost: '
            'unexpected HTTP status 409\n'), 'interaction guard lost: unexpected HTTP status 409')
        self.assertEqual(_failure_label('no label here'), '')
        self.assertEqual(_parse_story_marker(GOOD_MARKER.decode()), (True, BEARER_STORY_CHECKS))
        self.assertEqual(_parse_story_marker(GOOD_MARKER), (True, BEARER_STORY_CHECKS))
        self.assertEqual(_parse_story_marker(b'Retired service policy story: 19 '
                                             b'HTTP/response checks passed'), (True, 19))
        self.assertEqual(_parse_story_marker(b'no marker'), (False, 0))

    def test_story_marker_prefix_and_literals_match_the_child(self):
        self.assertEqual(dual._LOCAL_BLOCKED, 'LOCAL_SERVICE_POLICY_BLOCKED')
        self.assertEqual(dual._CREDENTIALS_UNAVAILABLE,
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(dual._STORY_BLOCKED, 'PREPROD_SERVICE_POLICY_STORY_BLOCKED')
        self.assertEqual(dual._OBLIGATION_VIOLATION,
                         'PREPROD_SERVICE_POLICY_OBLIGATION_VIOLATION')
        self.assertEqual(dual._REDACTION_GATE, 'PREPROD_SERVICE_POLICY_REDACTION_GATE')
        self.assertEqual(dual._STORY_DEADLINE, 'PREPROD_SERVICE_POLICY_STORY_DEADLINE')

    # --------------------------------------------------------------- local leg
    def test_local_execution_admits_only_the_complete_child_receipt(self):
        execution = _local_execution(local_result())
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, len(REQUIREMENTS))
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        self.assertTrue(all(step.status == 'PASS' for step in execution.steps))
        execution.validate()
        for step in execution.steps:
            self.assertEqual(step.data, {'source_case_id': 'LOCAL-SERVICE-POLICY-01'})
            self.assertEqual(step.evidence['story_checks'], 23)
            self.assertEqual(step.evidence['assertion_scope'], 'FROZEN_CASE_LEVEL_CHILD')
            self.assertFalse(step.evidence['grants_release_credit'])
        for observed in (STORY_CHECKS - 1, 0):
            for field in ('observed', 'marker', 'status'):
                kwargs = {}
                if field == 'observed':
                    kwargs['observed'] = observed
                elif field == 'marker':
                    kwargs['marker'] = False
                else:
                    kwargs['status'] = 'BLOCKED'
                blocked = _local_execution(local_result(**kwargs))
                self.assertEqual(blocked.status, 'BLOCKED')
                self.assertEqual(blocked.completed_count, 0)
                self.assertEqual(blocked.error_type, 'LOCAL_SERVICE_POLICY_BLOCKED')
                self.assertTrue(all(step.status == 'NOT_RUN' for step in blocked.steps))
                with self.subTest(failure=field, observed=observed):
                    blocked.validate()

    def test_preprod_execution_maps_option_a_and_the_live_replays(self):
        execution = _preprod_execution(bearer_evidence(), blocked_error='BLOCKED')
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 8)
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        execution.validate()
        modes = {step.requirement_id: step.evidence['preprod_mode'] for step in execution.steps}
        self.assertEqual(modes[REQUIREMENTS[0]], 'BEARER_IDENTITY_CONFIRM')
        self.assertEqual(modes[REQUIREMENTS[1]], REGISTRATION_DISPOSITION)
        for requirement_id in REQUIREMENTS[2:]:
            self.assertEqual(modes[requirement_id], 'LIVE_BEARER_REPLAY')
        for step in execution.steps:
            self.assertEqual(step.status, 'PASS')
            self.assertEqual(step.method, 'execute_service_policy_obligation')
            self.assertEqual(step.evidence['identity_mode'], 'bearer')
            self.assertFalse(step.evidence['grants_release_credit'])
            self.assertEqual(step.evidence['remote_validation'], 'NOT_RUN')
        registration = execution.steps[1]
        self.assertEqual(registration.data['disposition'], REGISTRATION_DISPOSITION)
        self.assertTrue(registration.data['driver_bot_deletion_forbidden'])
        incomplete = bearer_evidence()
        incomplete['obligations'].pop(REQUIREMENTS[7])
        blocked = _preprod_execution(incomplete, blocked_error='PREPROD_BLOCKED')
        self.assertEqual(blocked.status, 'BLOCKED')
        self.assertEqual(blocked.completed_count, 0)
        self.assertEqual(blocked.steps[0].error_type, 'PREPROD_BLOCKED')
        self.assertTrue(all(step.status == 'NOT_RUN' for step in blocked.steps))

    # --------------------------------------------------------------- preprod leg
    def _state(self, name: str) -> Path:
        state = self.root / name
        state.mkdir()
        return state

    def _launch_stub(self, *, exit_code=0, stdout=GOOD_MARKER, stderr=b'', timed_out=False):
        def stub(*_args, **_kwargs):
            return {'exit_code': exit_code, 'timed_out': timed_out,
                    'duration_seconds': 0.01, 'stdout': stdout, 'stderr': stderr}
        return stub

    def test_preprod_leg_passes_with_the_frozen_bearer_receipt(self):
        state = self._state('leg-pass')
        evidence_path = state / 'service-policy-preprod-evidence.json'
        evidence_path.write_text(json.dumps(bearer_evidence()), encoding='utf-8')
        profile = validate_service_policy_profile(preprod_profile())
        with patch.object(dual, '_launch_preprod_story', side_effect=self._launch_stub()):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 8)
        execution.validate()
        self.assertEqual(receipt['identity_mode'], 'bearer')
        self.assertEqual(receipt['observed_checks'], BEARER_STORY_CHECKS)
        self.assertEqual(receipt['markers'], {'retired-service-policy-compliance': True})
        self.assertEqual(receipt['redaction_status'], 'PASS')
        self.assertEqual(receipt['checks_status'], 'PASS')
        self.assertEqual(receipt['credential_refs'], sorted(HEADER_ENV.values()))
        self.assertIn('--driver-bot', receipt['argv'])
        self.assertIn(DRIVER_BOT, receipt['argv'])
        self.assertIsNone(blocker)
        self.assertTrue((state / 'preprod-story-work').is_dir())
        self.assertNotIn(DRIVER_CANARY, json.dumps(receipt))
        self.assertNotIn(COOKIE_CANARY, json.dumps(receipt))

    def test_preprod_leg_classifies_every_fail_closed_reason(self):
        state = self._state('leg-classify')
        evidence_path = state / 'service-policy-preprod-evidence.json'
        evidence_path.write_text(json.dumps(bearer_evidence()), encoding='utf-8')
        profile = validate_service_policy_profile(preprod_profile())
        scenarios = (
            ('deadline', self._launch_stub(timed_out=True),
             'PREPROD_SERVICE_POLICY_STORY_DEADLINE'),
            ('blocked-exit', self._launch_stub(
                exit_code=1, stdout=b'', stderr=GOOD_LABEL),
             'PREPROD_SERVICE_POLICY_STORY_BLOCKED'),
            ('marker-short', self._launch_stub(
                stdout=b'Retired service policy story: 19 HTTP/response checks passed'),
             'PREPROD_SERVICE_POLICY_STORY_BLOCKED'),
            ('no-evidence', self._launch_stub(),
             'PREPROD_SERVICE_POLICY_STORY_BLOCKED'),
            ('redaction', self._launch_stub(
                exit_code=1, stdout=b'', stderr=b'story failed: leaked Bearer driver secret'),
             'PREPROD_SERVICE_POLICY_REDACTION_GATE'),
        )
        for name, stub, reason in scenarios:
            with self.subTest(reason=name):
                if name != 'no-evidence':
                    evidence_path.write_text(json.dumps(bearer_evidence()),
                                             encoding='utf-8')
                elif evidence_path.exists():
                    evidence_path.unlink()
                with patch.object(dual, '_launch_preprod_story', side_effect=stub):
                    execution, receipt, blocker = dual._preprod_leg(
                        profile=profile, state_dir=state, run_id=RUN_ID)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(blocker['reason_class'], reason)
                self.assertEqual(receipt['checks_status'], 'BLOCKED')
                self.assertIn('argv', receipt)
                self.assertIn('credential_refs', blocker)
                self.assertEqual(blocker['credential_refs'], sorted(HEADER_ENV.values()))
                execution.validate()
                self.assertTrue(all(step.status == 'NOT_RUN' for step in execution.steps))

    def test_preprod_leg_reports_status_label_and_obligation_violations(self):
        state = self._state('leg-labels')
        evidence_path = state / 'service-policy-preprod-evidence.json'
        evidence_path.write_text(json.dumps(bearer_evidence()), encoding='utf-8')
        profile = validate_service_policy_profile(preprod_profile())
        with patch.object(dual, '_launch_preprod_story', side_effect=self._launch_stub(
                exit_code=1, stderr=GOOD_LABEL)):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(blocker['observed_status'], 409)
        self.assertEqual(blocker['failure_label'],
                         'interaction guard lost: unexpected HTTP status 409')
        self.assertEqual(blocker['observed_checks'], BEARER_STORY_CHECKS)
        self.assertEqual(receipt['observed_checks'], BEARER_STORY_CHECKS)
        violating = bearer_evidence()
        del violating['obligations'][REQUIREMENTS[7]]
        evidence_path.write_text(json.dumps(violating), encoding='utf-8')
        with patch.object(dual, '_launch_preprod_story', side_effect=self._launch_stub()):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID + '-violation')
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(blocker['reason_class'],
                         'PREPROD_SERVICE_POLICY_OBLIGATION_VIOLATION')
        self.assertEqual(receipt['checks_status'], 'BLOCKED')
        malformed = bearer_evidence()
        malformed['obligations'][REQUIREMENTS[0]] = 'not-a-dict'
        evidence_path.write_text(json.dumps(malformed), encoding='utf-8')
        with patch.object(dual, '_launch_preprod_story', side_effect=self._launch_stub()):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID + '-malformed')
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(blocker['reason_class'], 'PREPROD_SERVICE_POLICY_OBLIGATION_VIOLATION')

    def test_preprod_leg_fail_closes_when_the_story_cannot_start(self):
        state = self._state('leg-closed')
        profile = validate_service_policy_profile(preprod_profile())
        closed = PreprodClosedError('PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE',
                                    'environment reference ' + DRIVER_ENV + ' is not loaded',
                                    observed_status=None)
        with patch.object(dual, '_launch_preprod_story', side_effect=closed):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.error_type,
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(receipt['exit_code'], None)
        self.assertEqual(receipt['observed_checks'], 0)
        self.assertEqual(receipt['identity_mode'], 'bearer')
        self.assertIn('argv', receipt)
        self.assertEqual(blocker['reason_class'],
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(blocker['observed_status'], None)
        self.assertEqual(blocker['story_exit_code'], None)
        self.assertTrue((state / 'preprod-story-work').is_dir())

    def test_preprod_leg_rejects_station_shape_before_any_work(self):
        state = self._state('leg-shape')
        profile = validate_service_policy_profile(preprod_profile())
        profile['actors']['driver']['bot_id'] = 'not-a-station-shape'
        with self.assertRaisesRegex(ConfigError, 'station shape'):
            dual._preprod_leg(profile=profile, state_dir=state, run_id=RUN_ID)
        self.assertFalse((state / 'preprod-story-work').exists())

    # ---------------------------------------------------------------- evidence leaf
    def test_preprod_evidence_leaf_is_machine_readable_and_credential_free(self):
        state = self._state('leaf')
        leaf_root = state / 'leaves'
        report = {'status': 'BLOCKED', 'schema_version': dual.SCHEMA_VERSION,
                  'error_type': 'PREPROD_SERVICE_POLICY_STORY_BLOCKED',
                  'execution': {'completed_count': 0, 'total_count': len(REQUIREMENTS),
                                'status': 'BLOCKED'},
                  'preprod_blocker': {
                      'reason_class': 'PREPROD_SERVICE_POLICY_STORY_BLOCKED',
                      'observed_status': 409,
                      'failure_label': 'interaction guard lost: unexpected HTTP status 409',
                      'reason': 'child did not complete the frozen journey',
                      'story_exit_code': 1, 'story_timed_out': False,
                      'observed_checks': 0},
                  'profile_digest': 'digest', 'executor_digest': 'executor',
                  'bcs_binary_sha256': '0' * 64,
                  'environment': {'tier': 'preprod', 'id': RUN_ID, 'expected_bcs_env': 'pre',
                                  'entrypoint_kind': 'bcs-direct'}}
        receipt = {'exit_code': 1, 'observed_checks': 0, 'timed_out': False,
                   'duration_seconds': 1.0, 'stderr': b'clean child stderr'}
        root = _write_preprod_leaf(run_id=RUN_ID, report=report, story_receipt=receipt,
                                   leaf_root=leaf_root)
        self.assertEqual(root, leaf_root)
        slug = RUN_ID.replace('/', '_')
        for name in (slug + '-stdout.json', slug + '-verdict.json', slug + '-stderr.txt'):
            self.assertTrue((leaf_root / name).is_file(), name)
        verdict = read(leaf_root / (slug + '-verdict.json'))
        self.assertEqual(verdict['schema_version'], SCHEMA_VERSION)
        self.assertEqual(verdict['contract'], REPORT_CONTRACT)
        self.assertEqual(verdict['case_id'], CASE_ID)
        self.assertEqual(verdict['parent_id'], dual.PARENT)
        self.assertEqual(verdict['run_id'], RUN_ID)
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['error_type'], 'PREPROD_SERVICE_POLICY_STORY_BLOCKED')
        self.assertEqual(verdict['reason'], 'child did not complete the frozen journey')
        self.assertEqual(verdict['observed_status'], 409)
        self.assertEqual(verdict['profile_digest'], 'digest')
        self.assertEqual(verdict['executor_digest'], 'executor')
        self.assertEqual(verdict['bcs_binary_sha256'], '0' * 64)
        self.assertEqual(verdict['story_exit_code'], 1)
        self.assertEqual(verdict['story_observed_checks'], 0)
        self.assertEqual(verdict['story_checks'], BEARER_STORY_CHECKS)
        self.assertFalse(verdict['story_timed_out'])
        self.assertEqual(verdict['obligations_total'], len(REQUIREMENTS))
        self.assertEqual(verdict['obligations_completed'], 0)
        self.assertEqual(verdict['remote_validation'], 'NOT_RUN')
        self.assertFalse(verdict['grants_release_credit'])
        stdout_leaf = read(leaf_root / (slug + '-stdout.json'))
        self.assertEqual(stdout_leaf['status'], 'BLOCKED')
        combined = ''.join(path.read_text(encoding='utf-8') for path in leaf_root.iterdir())
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)
        dirty = self._state('leaf-dirty') / 'leaves'
        receipt_dirty = dict(receipt, stderr=b'leaked Bearer driver secret')
        _write_preprod_leaf(run_id=RUN_ID + '-dirty', report=report,
                            story_receipt=receipt_dirty, leaf_root=dirty)
        stderr_text = (dirty / ((RUN_ID + '-dirty').replace('/', '_') + '-stderr.txt')
                       ).read_text(encoding='utf-8')
        self.assertIn('redaction gate refused', stderr_text)

    # --------------------------------------------------------------------- entry
    def test_local_tier_reuses_the_owned_local_receipt(self):
        profile_path = self._write_profile(local_profile(), 'local')
        state_dir = self.root / 'entry-local'
        with patch.object(dual, 'local_service_policy_run',
                          return_value=local_result()) as execute:
            report = dual_service_policy_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.sha)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['contract'], REPORT_CONTRACT)
        self.assertEqual(report['case_id'], CASE_ID)
        self.assertEqual(report['source_case_id'], 'LOCAL-SERVICE-POLICY-01')
        self.assertEqual(report['parent_id'], dual.PARENT)
        self.assertEqual(report['run_id'], RUN_ID)
        self.assertEqual(report['bcs_binary_sha256'], self.sha)
        self.assertTrue(report['executor_digest'])
        self.assertEqual(report['execution']['completed_count'], 8)
        self.assertEqual(report['execution']['total_count'], 8)
        self.assertEqual(report['obligation_summary']['total'], 8)
        self.assertEqual(report['obligation_summary']['applicable'], 7)
        self.assertEqual(report['obligation_summary']['not_applicable'], 1)
        self.assertEqual(report['obligation_summary']['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(report['obligation_summary']['local_story_checks'], 23)
        self.assertEqual(report['obligation_summary']['bearer_story_checks'], 20)
        self.assertEqual(report['obligation_summary']['not_applicable_requirement'],
                         REQUIREMENTS[1])
        self.assertEqual(report['obligation_summary']['not_applicable_disposition'],
                         REGISTRATION_DISPOSITION)
        self.assertEqual(report['resource_disposition'],
                         'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED')
        self.assertEqual(report['credential_kind'], 'owned_local_ephemeral_principal')
        self.assertEqual(report['environment']['tier'], 'local')
        self.assertEqual(report['environment']['entrypoint_kind'], 'owned-local')
        self.assertEqual(report['local_owned_run']['status'], 'LOCAL_SERVICE_POLICY_PASS')
        self.assertEqual(report['local_owned_run']['canonical_summary_status'],
                         'LOCAL_SUITE_PASS')
        self.assertEqual(report['local_owned_run']['child_receipt']['observed_checks'],
                         STORY_CHECKS)
        self.assertNotIn('preprod_blocker', report)
        self.assertNotIn('preprod_evidence_leaf', report)
        execute.assert_called_once()
        kwargs = execute.call_args.kwargs
        self.assertEqual(kwargs['binary'], self.binary)
        self.assertEqual(kwargs['binary_sha256'], self.sha)
        self.assertEqual(Path(kwargs['state_dir']),
                         (state_dir / 'owned-service-policy').resolve())
        self.assertEqual(Path(kwargs['state_dir']), Path(report['local_owned_run']['state_dir']))
        self.assertEqual(kwargs['run_id'], RUN_ID)
        self.assertTrue(kwargs['allow_local_processes'])
        self.assertTrue(kwargs['allow_fixture_write'])
        self.assertTrue(kwargs['allow_write'])
        report_back = read(state_dir / 'service-policy-dual-run.json')
        self.assertEqual(report_back['status'], 'PASS')
        binding = read(state_dir / 'service-policy-binding.json')
        self.assertEqual(binding['contract'], REPORT_CONTRACT)
        self.assertEqual(binding['suite'], 'dual-service-policy-v1')
        self.assertEqual(binding['case_id'], CASE_ID)
        self.assertEqual(binding['source_case_id'], 'LOCAL-SERVICE-POLICY-01')
        self.assertEqual(binding['parent_id'], dual.PARENT)
        self.assertEqual(binding['story_checks'], {'local': 23, 'bearer': 20})
        self.assertEqual(binding['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['bcs_binary_sha256'], self.sha)
        self.assertEqual(binding['profile_digest'], fingerprint(report_back['case'] and
                   load_service_policy_profile(profile_path)))
        self.assertEqual(binding['executor_digest'], executor_digest())
        case_snapshot = report['case']
        self.assertEqual(case_snapshot['case_id'], CASE_ID)
        self.assertEqual(case_snapshot['parent_id'], dual.PARENT)
        self.assertEqual(case_snapshot['status'], 'PASS')
        self.assertEqual(len(case_snapshot['steps']), 8)
        combined = ''.join(path.read_text(encoding='utf-8')
                           for path in state_dir.glob('*.json'))
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)

    def test_local_tier_blocks_on_an_incomplete_child_receipt(self):
        self._write_profile(local_profile(), 'local-short')
        with patch.object(dual, 'local_service_policy_run',
                          return_value=local_result(observed=STORY_CHECKS - 1)):
            report = dual_service_policy_case_run(
                profile_path=self.root / 'profile-local-short.json',
                state_dir=self.root / 'entry-local-short', run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.sha)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)
        self.assertTrue(report['case']['error_type'])
        self.assertEqual(report['case']['error_type'], 'LOCAL_SERVICE_POLICY_BLOCKED')

    def test_preprod_tier_passes_without_writing_a_blocked_leaf(self):
        profile_path = self._write_profile(preprod_profile(), 'preprod')
        state_dir = self.root / 'entry-preprod'
        execution = _preprod_execution(bearer_evidence(), blocked_error='BLOCKED')
        receipt = {'exit_code': 0, 'timed_out': False, 'duration_seconds': 1.0,
                   'observed_checks': BEARER_STORY_CHECKS,
                   'markers': {'retired-service-policy-compliance': True},
                   'argv': [sys.executable, str(dual.BEARER_STORY_SOURCE)],
                   'identity_mode': 'bearer', 'redaction_status': 'PASS',
                   'checks_status': 'PASS'}
        leaves = self.root / 'leaves-preprod-pass'
        with patch.object(dual, '_preprod_leg', return_value=(execution, receipt, None)):
            report = dual_service_policy_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.sha, leaf_root=leaves)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['credential_kind'], 'preprod_driver_bot_bearer')
        self.assertEqual(report['environment']['tier'], 'preprod')
        self.assertEqual(report['environment']['id'], 'service-policy-pre')
        self.assertEqual(report['environment']['expected_bcs_env'], 'pre')
        self.assertEqual(report['environment']['entrypoint_kind'], 'bcs-direct')
        self.assertNotIn('api_face', report['environment'])
        self.assertEqual(report['execution']['completed_count'], 8)
        self.assertEqual(report['resource_disposition'],
                         'DISPOSABLE_PREPROD_GROUPS_AND_SESSIONS_DELETED_DRIVER_BOT_RETAINED')
        self.assertFalse(report['grants_release_credit'])
        self.assertEqual(report['remote_validation'], 'NOT_RUN')
        self.assertNotIn('preprod_blocker', report)
        self.assertNotIn('preprod_evidence_leaf', report)
        self.assertFalse(leaves.exists())
        report_back = read(state_dir / 'service-policy-dual-run.json')
        self.assertEqual(report_back['status'], 'PASS')
        self.assertEqual(report_back['preprod_story']['observed_checks'],
                         BEARER_STORY_CHECKS)

    def test_preprod_tier_writes_the_blocked_evidence_leaf(self):
        profile_path = self._write_profile(preprod_profile(), 'preprod-blocked')
        state_dir = self.root / 'entry-preprod-blocked'
        run_id = RUN_ID + '-blocked'
        execution = dual._blocked_case_execution('PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        receipt = {'exit_code': None, 'timed_out': False, 'duration_seconds': 0.0,
                   'observed_checks': 0,
                   'markers': {'retired-service-policy-compliance': False},
                   'argv': [sys.executable], 'identity_mode': 'bearer',
                   'credential_refs': sorted(HEADER_ENV.values()),
                   'redaction_status': 'NOT_RUN', 'checks_status': 'BLOCKED'}
        blocker = {'reason_class': 'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE',
                   'observed_status': None, 'failure_label': '',
                   'reason': 'environment reference ' + DRIVER_ENV + ' is not loaded',
                   'story_exit_code': None, 'story_timed_out': False,
                   'observed_checks': 0, 'credential_refs': sorted(HEADER_ENV.values())}
        leaves = self.root / 'leaves-preprod-blocked'
        with patch.object(dual, '_preprod_leg', return_value=(execution, receipt, blocker)):
            report = dual_service_policy_case_run(
                profile_path=profile_path, state_dir=state_dir, run_id=run_id,
                allow_write=True, confirm_run_id=run_id, binary=self.binary,
                binary_sha256=self.sha, leaf_root=leaves)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['case']['status'], 'BLOCKED')
        self.assertEqual(report['execution']['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(report['preprod_blocker']['credential_refs'],
                         sorted(HEADER_ENV.values()))
        self.assertEqual(report['environment']['api_face'], 'legacy')
        leaf = Path(report['preprod_evidence_leaf'])
        self.assertTrue(leaf.is_dir())
        verdict = read(leaf / (run_id.replace('/', '_') + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['error_type'],
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(verdict['story_checks'], BEARER_STORY_CHECKS)
        self.assertEqual(verdict['obligations_total'], 8)
        self.assertFalse(verdict['grants_release_credit'])
        combined = ''.join(path.read_text(encoding='utf-8') for path in leaf.iterdir())
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)

    def test_entry_fail_closes_on_lift_time_preprod_errors(self):
        profile_path = self._write_profile(preprod_profile(), 'error')
        closed = PreprodClosedError('PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE',
                                    'environment reference ' + DRIVER_ENV
                                    + ' for header Authorization is not loaded')
        with patch.object(dual, '_preprod_leg', side_effect=closed):
            report = dual_service_policy_case_run(
                profile_path=profile_path, state_dir=self.root / 'entry-closed',
                run_id=RUN_ID + '-closed', allow_write=True,
                confirm_run_id=RUN_ID + '-closed', binary=self.binary,
                binary_sha256=self.sha, leaf_root=self.root / 'leaves-closed')
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['status'], 'BLOCKED')
        self.assertEqual(report['execution']['error_type'],
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(report['execution']['completed_count'], 0)
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(report['preprod_blocker']['credential_refs'],
                         sorted(HEADER_ENV.values()))
        self.assertEqual(report['preprod_story']['checks_status'], 'BLOCKED')
        self.assertEqual(report['preprod_story']['identity_mode'], 'bearer')
        leaf = Path(report['preprod_evidence_leaf'])
        stdout_leaf = read(leaf / ((RUN_ID + '-closed').replace('/', '_') + '-stdout.json'))
        self.assertEqual(stdout_leaf['status'], 'BLOCKED')
        self.assertEqual(stdout_leaf['preprod_blocker']['reason_class'],
                         'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE')

    def test_entry_wraps_lift_time_local_and_config_errors_as_unavailable(self):
        profile_path = self._write_profile(local_profile(), 'raising')
        with patch.object(dual, 'local_service_policy_run',
                          side_effect=RunStateError('owned runtime exploded')):
            with self.assertRaisesRegex(ConfigError, 'unavailable'):
                dual_service_policy_case_run(
                    profile_path=profile_path, state_dir=self.root / 'entry-raise-local',
                    run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                    binary=self.binary, binary_sha256=self.sha)
        preprod_path = self._write_profile(preprod_profile(), 'raising')
        with patch.object(dual, '_preprod_leg',
                          side_effect=ConfigError('profile junk')):
            with self.assertRaisesRegex(ConfigError, 'unavailable'):
                dual_service_policy_case_run(
                    profile_path=preprod_path, state_dir=self.root / 'entry-raise-pre',
                    run_id=RUN_ID + '-pre', allow_write=True,
                    confirm_run_id=RUN_ID + '-pre', binary=self.binary,
                    binary_sha256=self.sha)

    def test_entry_enforces_write_gates_and_fresh_state(self):
        profile_path = self._write_profile(local_profile(), 'gates')
        base = dict(profile_path=profile_path, state_dir=self.root / 'gates-state',
                    run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                    binary=self.binary, binary_sha256=self.sha)
        with self.assertRaisesRegex(ConfigError, 'write permission'):
            dual_service_policy_case_run(**{**base, 'allow_write': False})
        with self.assertRaisesRegex(ConfigError, 'Run confirmation'):
            dual_service_policy_case_run(**{**base, 'confirm_run_id': 'other-run'})
        existing = self.root / 'existing-state'
        existing.mkdir()
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_service_policy_case_run(**{**base, 'state_dir': existing})
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_service_policy_case_run(**{**base, 'state_dir': Path('relative-state')})
        missing_parent = self.root / 'missing-parent' / 'state'
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_service_policy_case_run(**{**base, 'state_dir': missing_parent})
        linked = self.root / 'linked-state'
        linked.symlink_to(self.root / 'linked-target')
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_service_policy_case_run(**{**base, 'state_dir': linked})

    def test_entry_verifies_the_bcs_binary_digest_on_both_tiers(self):
        wrong = self.root / 'wrong-bcs'
        wrong.write_bytes(b'other-bcs-bytes')
        wrong.chmod(0o700)
        for tier, factory in (('local', local_profile), ('preprod', preprod_profile)):
            with self.subTest(tier=tier):
                with self.assertRaisesRegex(RunStateError, 'digest differs'):
                    dual_service_policy_case_run(
                        profile_path=self._write_profile(factory(), 'digest-' + tier),
                        state_dir=self.root / ('digest-state-' + tier), run_id=RUN_ID,
                        allow_write=True, confirm_run_id=RUN_ID, binary=wrong,
                        binary_sha256=WRONG_SHA)
        self.assertFalse((self.root / 'digest-state-local').exists())
        self.assertFalse((self.root / 'digest-state-preprod').exists())
        for tier, factory in (('local', local_profile), ('preprod', preprod_profile)):
            with self.subTest(tier=tier, gate='executable'):
                frozen = self.root / ('frozen-' + tier)
                frozen.write_bytes(b'owned-bcs-fixture-bytes')
                frozen.chmod(0o600)
                with self.assertRaisesRegex(RunStateError, 'executable'):
                    dual_service_policy_case_run(
                        profile_path=self._write_profile(factory(), 'exec-' + tier),
                        state_dir=self.root / ('exec-state-' + tier), run_id=RUN_ID,
                        allow_write=True, confirm_run_id=RUN_ID, binary=frozen,
                        binary_sha256=hashlib.sha256(
                            frozen.read_bytes()).hexdigest())

    # ------------------------------------------------------------ registration
    def test_migration_plan_executor_and_cli_expose_the_dual_case(self):
        self.assertIn('dual-service-policy-run', CASE_COMMANDS)
        row = load_executor_migration()[dual.PARENT]
        self.assertEqual(row['mode'], 'DUAL')
        self.assertEqual(row['preprod_verdict'], 'NOT_RUN')
        plan = migration_plan()
        slice_row = next(item for item in plan['local_backlog_case_slices']
                         if item['case_id'] == CASE_ID)
        self.assertEqual(slice_row['suite'], 'dual-service-policy-v1')
        self.assertEqual(slice_row['legacy_id'], dual.PARENT)
        self.assertEqual(slice_row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(slice_row['execution_command'], 'dual-service-policy-run')
        self.assertEqual(slice_row['report_command'], '')
        self.assertEqual(slice_row['required_assertion_count'], 8)
        self.assertEqual(slice_row['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(slice_row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'state_dir', 'run_id',
            'confirm_run_id', 'allow_write'])
        self.assertEqual(slice_row['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                                    'preprod': 'REMOTE_BEARER_MUTATING_ADMITTED'})
        self.assertFalse(slice_row['release_parent_denominator'])
        self.assertTrue(slice_row['independent_slice'])
        self.assertEqual(slice_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(slice_row['grants_release_credit'])
        self.assertEqual(slice_row['source_map'], 'service-policy-dual-migration-contract.json')
        self.assertTrue(any(isinstance(item, dict)
                            and item.get('dual_case_id') == CASE_ID
                            for item in plan['additional_source_maps']))

    def test_cli_dispatches_the_dual_command_with_the_frozen_signature(self):
        profile_path = self._write_profile(local_profile(), 'cli')
        state_dir = self.root / 'cli-state'
        arguments = [
            'dual-service-policy-run', '--profile', str(profile_path),
            '--state-dir', str(state_dir), '--run-id', RUN_ID,
            '--confirm-run-id', RUN_ID, '--allow-write', '--binary',
            str(self.binary), '--binary-sha256', self.sha,
        ]
        with patch('bcs_e2e.cli.dual_service_policy_case_run',
                   return_value={'status': 'PASS'}) as execute, \
                redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path, state_dir=state_dir, run_id=RUN_ID,
            allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
            binary_sha256=self.sha)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'PASS')

    def test_cli_returns_nonzero_for_a_blocked_report(self):
        profile_path = self._write_profile(preprod_profile(), 'cli-blocked')
        state_dir = self.root / 'cli-blocked-state'
        arguments = [
            'dual-service-policy-run', '--profile', str(profile_path),
            '--state-dir', str(state_dir), '--run-id', RUN_ID + '-cli',
            '--confirm-run-id', RUN_ID + '-cli', '--allow-write', '--binary',
            str(self.binary), '--binary-sha256', self.sha,
        ]
        with patch('bcs_e2e.cli.dual_service_policy_case_run',
                   return_value={'status': 'BLOCKED'}):
            with redirect_stdout(io.StringIO()):
                rc = main(arguments)
        self.assertEqual(rc, 2)

    def test_cli_parser_requires_the_binary_pair(self):
        profile_path = self._write_profile(local_profile(), 'parser')
        for omitted in (('--binary',), ('--binary-sha256',)):
            with self.subTest(omitted=omitted[0]):
                arguments = [
                    'dual-service-policy-run', '--profile', str(profile_path),
                    '--state-dir', str(self.root / ('parser-state' + omitted[0][2:])),
                    '--run-id', RUN_ID, '--confirm-run-id', RUN_ID, '--allow-write',
                    '--binary', str(self.binary), '--binary-sha256', self.sha]
                index = arguments.index(omitted[0])
                arguments[index:index + 2] = []
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                    main(arguments)

    def test_entry_validated_profile_digest_binds_the_report(self):
        profile_path = self._write_profile(local_profile(), 'digest')
        with patch.object(dual, 'local_service_policy_run',
                          return_value=local_result()):
            report = dual_service_policy_case_run(
                profile_path=profile_path, state_dir=self.root / 'entry-digest',
                run_id=RUN_ID + '-digest', allow_write=True,
                confirm_run_id=RUN_ID + '-digest', binary=self.binary,
                binary_sha256=self.sha)
        validated = load_service_policy_profile(profile_path)
        self.assertEqual(report['profile_digest'], fingerprint(validated))
        self.assertEqual(report['executor_digest'], executor_digest())
        self.assertTrue(report['source_binding_digest'])
        self.assertTrue(report['flat_source_binding_digest'])


class DuoCookieAdmissionTests(unittest.TestCase):
    """Station-form duo credential: validated Human cookie rides with the bearer."""

    def test_bearer_child_attaches_human_cookie_beside_driver_bearer(self):
        from bcs_e2e.service_policy.story import Story
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY,
                                     COOKIE_ENV: COOKIE_CANARY}):
            story = Story('https://bcs-pre.alipay.com', DRIVER_BOT,
                          bearer_token_env=DRIVER_ENV, cookie_env=COOKIE_ENV)
        headers = story._headers()
        self.assertEqual(headers['Authorization'], 'Bearer ' + DRIVER_CANARY)
        self.assertEqual(headers['Cookie'], COOKIE_CANARY)
        self.assertIn(COOKIE_CANARY, story.secrets)

    def test_bearer_child_fail_closes_when_cookie_reference_missing(self):
        from bcs_e2e.service_policy.story import Story, StoryFailure
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY}, clear=False):
            os.environ.pop(COOKIE_ENV, None)
            with self.assertRaises(StoryFailure):
                Story('https://bcs-pre.alipay.com', DRIVER_BOT,
                      bearer_token_env=DRIVER_ENV, cookie_env=COOKIE_ENV)


if __name__ == '__main__':
    unittest.main()
