"""Contract, profile, fail-closed, delegation and reporting tests for the slow-Judge dual case.

The preprod leg is proven zero-socket by patching ``socket.socket`` itself; the
owned-local leg is proven by injecting a fake flat receipt through the
``local_slow_judge_run`` delegation seam. No test touches the network.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcs_e2e.slow_judge.dual_case_run as dual
from bcs_e2e.inventory import current_manifest, executor_digest, load_executor_migration
from bcs_e2e.local_judge_contract import CASE_ID as SOURCE_CASE_ID
from bcs_e2e.local_judge_contract import PARENT, REQUIREMENTS, SUITE as SOURCE_SUITE
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read, write
from bcs_e2e.slow_judge import contract as family
from bcs_e2e.slow_judge.reporting import (BINDING_NAME, COMPLETION_NAME, FLOW_NAME,
                                          build_slow_judge_dual_report,
                                          build_slow_judge_dual_summary,
                                          publish_slow_judge_dual_report, scan_redaction)
from bcs_e2e.slow_judge.contract import (DEFAULT_LEAF_ROOT, HEADER_ENV, JUDGE_BLOCKS,
                                         LOCAL_PROFILE_KEYS, MOCK_GATE_INHERITANCE,
                                         OBLIGATION_COUNT, PREPROD_ACTOR_KEYS,
                                         PREPROD_MODES, PREPROD_JOURNEY, STATION_BOT_ID,
                                         COMMAND, DUAL_CASE_ID, DUAL_SUITE, PROFILE_CONTRACT,
                                         preprod_obligation_modes,
                                         slow_judge_dual_adaptability,
                                         slow_judge_dual_source_binding, frozen_dual_contract)
from bcs_e2e.slow_judge.dual_case_run import (CASE_ID, CASE_KEYS, PreprodClosedError,
                                              _bounded_environment, _empty_receipt,
                                              _fresh_state_dir, _local_execution,
                                              _receiver_receipt, _validate_binary_pair,
                                              _write_preprod_leaf, build_slow_judge_dual_argv_parser,
                                              dual_slow_judge_case_run,
                                              load_slow_judge_profile, register_slow_judge_dual_case_commands,
                                              validate_slow_judge_profile)
from bcs_e2e.run_state import RunStateError

RUN_ID = '20261031-w3a-s28-slow-judge-test'
DRIVER_ENV = HEADER_ENV['Authorization']
COOKIE_ENV = HEADER_ENV['Cookie']
DRIVER_CANARY = 's28-synthetic-driver-token-never-persisted'
COOKIE_CANARY = 's28-synthetic-human-cookie-never-persisted'
SHA256_A = 'a' * 64
BOT_ID_DRIVER = '20261031_s28driver:17'
BOT_ID_MEMBER = '20261031_s28member:18'


def provider_row(index: int, suffix: str) -> dict:
    return {'delivery_request_id': f'smnode-s28-{index}-{suffix}',
            'transport_request_id': f'tr-s28-{index}', 'provider_id': 's28provider',
            'provider_bot_ref': f's28provider-bot:{index}', 'bot_id': f'20261000_provider:{index}',
            'session_id': f'sess-s28-{index}', 'bcn_group_id': f'grp-s28-{index}',
            'method': 'chat.send', 'protocol_version': '2.0',
            'sender_actor_id': f'sender-s28-{index}', 'body_sha256': 'b' * 64}


def receiver_snapshot(**overrides) -> dict:
    """The owned loopback receiver snapshot as the real flat run produces it."""
    snapshot = {
        'state': 'CLOSED', 'failed': False,
        'provider_receipts': [provider_row(0, 'review-0'), provider_row(1, 'review-0'),
                              provider_row(2, 'publish-0')],
        'provider_receipt_limit': 3, 'judge_started': True, 'judge_released': True,
        'judge_finished': True,
        'judge_receipt': {'model': 'owned-local-slow-judge', 'message_count': 2,
                          'response_format_type': 'json_schema', 'body_sha256': 'c' * 64},
        'judge_admissions': 1, 'judge_request_count': 1, 'judge_outcomes': [],
        'provider_inject_count': 0, 'connection_total': 4,
    }
    snapshot.update(overrides)
    return snapshot


def local_profile() -> dict:
    return {
        'schema_version': 1, 'contract': PROFILE_CONTRACT,
        'environment': {'tier': 'local', 'id': 's28-owned-local', 'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'owned-local-story'},
        'human': {'user_id': 'e2ehumanabcdef0123456789'},
        'auth': {'kind': 'owned-local'},
        'cli': {'transport': 'owned-local-story', 'command_family': 'judged-provider-callback',
                'api_face': 'current'},
        'policy': {'exclusive_use_confirmed': True, 'timeout_seconds': 90,
                   'max_response_bytes': 1048576, 'failure_retention_hours': 24},
        'judge': dict(JUDGE_BLOCKS['local']),
        'effects': {
            'disposable_groups': 1, 'disposable_provider_bots': 1,
            'provider_registration': 'OWNED_EPHEMERAL_REGISTERED_BY_RUN',
            'teardown': 'BUSINESS_DELETE_GROUP_AND_PROVIDER_BOT_RETIRE',
            'implicit_run': 'PRESERVED_EXPLICIT_RECEIPT_BOUNDARY'},
    }


def preprod_profile() -> dict:
    value = local_profile()
    value['environment'] = {'tier': 'preprod', 'id': '20261031-w3a-s28-pre',
                            'expected_bcs_env': 'pre'}
    value['entrypoint'] = {'kind': 'bcs-direct', 'origin': 'https://bcs-pre.alipay.com',
                           'api_face': 'current', 'manifest_face': 'current'}
    value['auth'] = {'kind': 'preprod-driver-bot-bearer',
                     'header_env': {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}}
    value['actors'] = {'driver': {'bot_id': BOT_ID_DRIVER}, 'member': {'bot_id': BOT_ID_MEMBER}}
    value['cli'] = {'transport': 'product-http', 'command_family': 'judged-provider-callback',
                    'api_face': 'current'}
    value['judge'] = dict(JUDGE_BLOCKS['preprod'])
    value['effects'] = {
        'disposable_groups': 'SEAM_PENDING', 'disposable_provider_bots': 0,
        'provider_registration': 'FORBIDDEN_ON_SHARED_PREPROD',
        'teardown': 'PREPROD_JOURNEY_SEAM_PENDING',
        'implicit_run': 'PRESERVED_EXPLICIT_RECEIPT_BOUNDARY'}
    return value


def fake_flat_result(run_id: str, *, status: str = 'LOCAL_SLOW_JUDGE_PASS',
                     sandbox: dict | None = None) -> dict:
    from bcs_e2e.case_rewrite import slow_judge_case_rewrite

    plan = slow_judge_case_rewrite()
    passed = status == 'LOCAL_SLOW_JUDGE_PASS'
    steps = []
    for requirement in plan.requirements:
        steps.append({'requirement_id': requirement.requirement_id,
                      'method': plan.adapters[requirement.requirement_id],
                      'status': 'PASS' if passed else 'NOT_RUN',
                      'data': {'source_case_id': SOURCE_CASE_ID} if passed else None,
                      'evidence': {}, 'error_type': None if passed else 'ADAPTER_FAILED',
                      'error_gap': None})
    case = {'schema_version': 1, 'run_id': run_id, 'case_id': SOURCE_CASE_ID,
            'parent_id': PARENT, 'status': 'PASS' if passed else 'BLOCKED',
            'steps': steps, 'completed_count': OBLIGATION_COUNT if passed else 0,
            'total_count': OBLIGATION_COUNT, 'error_type': None if passed else 'ADAPTER_FAILED',
            'ready_to_execute': passed, 'executor_digest': executor_digest()}
    return {
        'schema_version': 1, 'status': status, 'run_id': run_id,
        'case_id': SOURCE_CASE_ID, 'executor_digest': executor_digest(), 'case': case,
        'shutdown': {'status': 'STOPPED', 'returncode': 0, 'origin': 'http://127.0.0.1:1',
                     'pid': 1, 'binary_sha256': SHA256_A, 'ready': False, 'forced_stop': False,
                     'ownership': 'live-child-handle-only'},
        'credentials': {'credential_memory': 'CLEARED'},
        'judge_sandbox': receiver_snapshot() if sandbox is None else sandbox,
        'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
        'ready_to_execute': False,
        'canonical_summary': {'status': 'LOCAL_SUITE_PASS' if passed else 'BLOCKED'},
    }


def executable_binary(root: Path, name: str = 'bcs-fixture') -> tuple[Path, str]:
    binary = root / name
    binary.write_bytes(b'owned-bcs-s28-fixture-bytes')
    binary.chmod(0o700)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def all_text(root: Path) -> str:
    chunks = []
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            chunks.append(path.read_text(encoding='utf-8', errors='replace'))
    return '\n'.join(chunks)


class SlowJudgeDualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary, self.sha = executable_binary(self.root)
        self.cli_binary, self.cli_sha = executable_binary(self.root, name='bcs-cli-fixture')

    def _write_profile(self, value: dict, name: str) -> Path:
        path = self.root / f'profile-{name}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    # ------------------------------------------------------------- profiles
    def test_both_tier_profiles_validate(self):
        self.assertEqual(validate_slow_judge_profile(local_profile())['environment']['tier'],
                         'local')
        self.assertEqual(set(validate_slow_judge_profile(local_profile())),
                         set(LOCAL_PROFILE_KEYS))
        validated = validate_slow_judge_profile(preprod_profile())
        self.assertEqual(validated['environment']['tier'], 'preprod')
        self.assertEqual(set(validated), set(LOCAL_PROFILE_KEYS) | {'actors'})
        self.assertEqual(set(validated['actors']), set(PREPROD_ACTOR_KEYS))
        self.assertEqual(validated['auth']['header_env'], dict(HEADER_ENV))
        self.assertEqual(load_slow_judge_profile(
            self._write_profile(preprod_profile(), 'valid'))['environment']['id'],
            '20261031-w3a-s28-pre')

    def test_profile_rejects_key_set_and_nested_drift(self):
        cases = (
            ('unknown-top-level', lambda v: v.__setitem__('credential_files', {})),
            ('actors-on-local', lambda v: v.__setitem__('actors', {
                'driver': {'bot_id': BOT_ID_DRIVER}})),
            ('schema-version', lambda v: v.__setitem__('schema_version', 2)),
            ('contract', lambda v: v.__setitem__('contract', 'wrong')),
            ('tier', lambda v: v['environment'].__setitem__('tier', 'prod')),
            ('bcs-env-binding-local', lambda v: v['environment'].__setitem__(
                'expected_bcs_env', 'pre')),
            ('local-entrypoint-kind', lambda v: (
                v['entrypoint'].__setitem__('kind', 'bcs-direct'))),
            ('entrypoint-extra-kind-local', lambda v: (
                v['entrypoint'].__setitem__('manifest_face', 'legacy'))),
            ('human', lambda v: v['human'].__setitem__('user_id', 'user name')),
            ('auth-kind-swap', lambda v: v['auth'].__setitem__('kind', 'preprod-driver-bot-bearer')),
            ('cli-transport-swap', lambda v: v['cli'].__setitem__('transport', 'product-http')),
            ('cli-family', lambda v: v['cli'].__setitem__('command_family', 'fixed-loop')),
            ('cli-face', lambda v: v['cli'].__setitem__('api_face', 'legacy')),
            ('policy-exclusive', lambda v: v['policy'].__setitem__('exclusive_use_confirmed', False)),
            ('policy-timeout-low', lambda v: v['policy'].__setitem__('timeout_seconds', 0)),
            ('policy-timeout-high', lambda v: v['policy'].__setitem__('timeout_seconds', 121)),
            ('policy-bytes', lambda v: v['policy'].__setitem__('max_response_bytes', 0)),
            ('policy-retention', lambda v: v['policy'].__setitem__(
                'failure_retention_hours', 169)),
            ('judge-drift-local', lambda v: v['judge'].__setitem__('kind', 'preprod-admitted-real-judge')),
            ('effects-drift-local', lambda v: v['effects'].__setitem__('disposable_groups', 2)),
        )
        for name, mutate in cases:
            with self.subTest(mutation=name):
                value = copy.deepcopy(local_profile())
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_slow_judge_profile(value)

    def test_preprod_profile_rejects_preprod_only_drift(self):
        cases = (
            ('expected-bcs-env', lambda v: v['environment'].__setitem__('expected_bcs_env', 'local')),
            ('entrypoint-local-kind', lambda v: v['entrypoint'].__setitem__('kind', 'owned-local-story')),
            ('entrypoint-http-origin', lambda v: v['entrypoint'].__setitem__(
                'origin', 'http://bcs-pre.alipay.com')),
            ('entrypoint-origin-path', lambda v: v['entrypoint'].__setitem__(
                'origin', 'https://bcs-pre.alipay.com/journey')),
            ('entrypoint-face', lambda v: v['entrypoint'].__setitem__('api_face', 'legacy')),
            ('auth-owned-local', lambda v: v['auth'].__setitem__('kind', 'owned-local')),
            ('header-env-missing-cookie', lambda v: v['auth']['header_env'].pop('Cookie')),
            ('header-env-lowercase-ref', lambda v: v['auth']['header_env'].__setitem__(
                'Authorization', 'bcs_e2e_driver_token')),
            ('header-env-invalid-ref', lambda v: v['auth']['header_env'].__setitem__(
                'Authorization', 'not-a-valid-env-ref')),
            ('actors-missing-member', lambda v: v['actors'].pop('member')),
            ('actors-extra-role', lambda v: v['actors'].__setitem__('guest', {'bot_id': BOT_ID_DRIVER})),
            ('actor-station-shape', lambda v: v['actors']['driver'].__setitem__(
                'bot_id', 'driver-bot')),
            ('actor-extra-key', lambda v: v['actors']['member'].__setitem__('cookie', 'x')),
            ('cli-transport-owned', lambda v: v['cli'].__setitem__('transport', 'owned-local-story')),
            ('judge-drift-preprod', lambda v: v['judge'].__setitem__('kind', 'owned-loopback-receiver')),
            ('effects-local-frozen', lambda v: v['effects'].__setitem__(
                'disposable_provider_bots', 1)),
        )
        for name, mutate in cases:
            with self.subTest(mutation=name):
                value = copy.deepcopy(preprod_profile())
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_slow_judge_profile(value)

    def test_station_shape_matches_the_frozen_regex(self):
        self.assertTrue(STATION_BOT_ID.fullmatch(BOT_ID_DRIVER))
        self.assertTrue(STATION_BOT_ID.fullmatch(BOT_ID_MEMBER))
        self.assertIsNone(STATION_BOT_ID.fullmatch('20261031driver:17'))
        self.assertIsNone(STATION_BOT_ID.fullmatch('20261031_DRV:17'))
        self.assertIsNone(STATION_BOT_ID.fullmatch(''))

    def test_load_profile_rejects_symlink_missing_and_bad_json(self):
        target = self._write_profile(local_profile(), 'target')
        link = self.root / 'profile-link.json'
        link.symlink_to(target)
        with self.assertRaises(ConfigError):
            load_slow_judge_profile(link)
        with self.assertRaises(ConfigError):
            load_slow_judge_profile(self.root / 'profile-absent.json')
        broken = self.root / 'profile-broken.json'
        broken.write_text('{not json', encoding='utf-8')
        with self.assertRaises(ConfigError):
            load_slow_judge_profile(broken)
        duplicate = self.root / 'profile-dup.json'
        duplicate.write_text('{"schema_version": 1, "schema_version": 1}', encoding='utf-8')
        with self.assertRaises(ConfigError):
            load_slow_judge_profile(duplicate)

    def test_placeholders_are_rejected_before_the_state_directory(self):
        value = copy.deepcopy(preprod_profile())
        value['human']['user_id'] = 'REPLACE_WITH_HUMAN_USER_ID'
        path = self._write_profile(value, 'placeholder')
        state = self.root / 'state-placeholder'
        with self.assertRaisesRegex(ConfigError, 'REPLACE_WITH_'):
            dual_slow_judge_case_run(
                profile_path=path, state_dir=state, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.binary, binary_sha256=self.sha)
        self.assertFalse(state.exists())

    # ---------------------------------------------------- state dir and env
    def test_fresh_state_dir_contract(self):
        fresh = _fresh_state_dir(self.root / 'fresh-leaf')
        self.assertTrue(fresh.is_dir())
        self.assertNotEqual(fresh.stat().st_mode & 0o022, 0o022)
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            _fresh_state_dir(fresh)
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            _fresh_state_dir(Path('relative/state/leaf'))
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            _fresh_state_dir(self.root / 'fresh-leaf')

    def test_fresh_state_dir_rejects_symlinks(self):
        target = self.root / 'symlink-target'
        target.mkdir()
        link = self.root / 'symlink-leaf'
        link.symlink_to(target)
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            _fresh_state_dir(link)
        linked_parent = self.root / 'linked-parent'
        linked_parent.symlink_to(target)
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            _fresh_state_dir(linked_parent / 'leaf')

    def test_bounded_environment_resolves_only_named_references(self):
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: COOKIE_CANARY}):
            child = _bounded_environment(dict(HEADER_ENV))
        self.assertEqual(child[DRIVER_ENV], DRIVER_CANARY)
        self.assertEqual(child[COOKIE_ENV], COOKIE_CANARY)

    def test_bounded_environment_fail_closes_on_missing_or_unsafe_credentials(self):
        cases = ({}, {DRIVER_ENV: ''}, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: 'bad\ncookie'},
                 {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: 'x' * 9000},
                 {COOKIE_ENV: COOKIE_CANARY})
        for environment in cases:
            with self.subTest(environment=sorted(environment)):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(PreprodClosedError) as ctx:
                        _bounded_environment(dict(HEADER_ENV))
                self.assertEqual(ctx.exception.reason_class,
                                 'PREPROD_SLOW_JUDGE_CREDENTIALS_UNAVAILABLE')
                self.assertIsNone(ctx.exception.observed_status)

    # ------------------------------------------------------------- binaries
    def test_binary_pair_admission_fences_the_digest(self):
        self.assertEqual(_validate_binary_pair(self.binary, self.sha, 'BCS'), self.binary)
        with self.assertRaisesRegex(RunStateError, 'differs'):
            _validate_binary_pair(self.binary, SHA256_A, 'BCS')
        with self.assertRaisesRegex(RunStateError, 'differs'):
            _validate_binary_pair(self.cli_binary, SHA256_A, 'CLI')
        for label, path, digest in (
                ('non-executable', self.root / 'plain.txt', self.sha),
                ('symlink', None, self.sha)):
            with self.subTest(case=label):
                if label == 'non-executable':
                    path = self.root / 'plain.txt'
                    path.write_text('not-executable', encoding='utf-8')
                    with self.assertRaises(RunStateError):
                        _validate_binary_pair(path, digest, 'BCS')
                else:
                    link = self.root / 'bcs-link'
                    link.symlink_to(self.binary)
                    with self.assertRaises(RunStateError):
                        _validate_binary_pair(link, digest, 'BCS')
        with self.assertRaises(ConfigError):
            _validate_binary_pair(self.binary, 'ZZZ', 'BCS')
        with self.assertRaises(ConfigError):
            _validate_binary_pair(self.binary, 'a' * 63, 'BCS')

    # -------------------------------------------------------- receiver gate
    def test_receiver_receipt_admits_the_probe_truth(self):
        projection = _receiver_receipt(receiver_snapshot())
        self.assertEqual(projection['state'], 'CLOSED')
        self.assertEqual(projection['judge_model'], 'owned-local-slow-judge')
        self.assertEqual(projection['judge_response_format'], 'json_schema')
        self.assertEqual(projection['provider_chat_send_receipts'], 3)
        self.assertEqual(projection['distinct_delivery_requests'], 3)
        self.assertEqual(projection['judge_request_count'], 1)
        self.assertEqual(projection['judge_admissions'], 1)
        self.assertEqual(projection['connection_total'], 4)
        self.assertEqual(projection['provider_inject_count'], 0)
        self.assertEqual(projection['credential_material'], 'none')

    def test_receiver_receipt_rejects_every_drift(self):
        drifts = (
            {'state': 'OPEN'}, {'failed': True}, {'judge_started': False},
            {'judge_released': False},
            {'judge_receipt': {'model': 'other-model', 'message_count': 2,
                               'response_format_type': 'json_schema',
                               'body_sha256': 'c' * 64}},
            {'judge_receipt': {'model': 'owned-local-slow-judge', 'message_count': 2,
                               'response_format_type': 'text', 'body_sha256': 'c' * 64}},
            {'provider_receipts': [provider_row(0, 'review-0')]},
            {'provider_receipts': [provider_row(0, 'review-0'), provider_row(0, 'review-0')]},
            {'provider_receipts': [dict(provider_row(0, 'review-0'), method='chat.inject'),
                                   dict(provider_row(1, 'review-0'))]},
            {'provider_receipts': [dict(provider_row(0, 'review-0'), protocol_version='1.4'),
                                   dict(provider_row(1, 'review-0'))]},
            {'judge_request_count': 0}, {'judge_request_count': 2},
            {'judge_admissions': 0}, {'judge_admissions': 2},
            {'connection_total': 2}, {'connection_total': 'four'},
            {'provider_inject_count': -1}, {'provider_inject_count': 'zero'},
        )
        for overrides in drifts:
            with self.subTest(drift=sorted(overrides)):
                with self.assertRaisesRegex(RunStateError, 'receiver receipt drift'):
                    _receiver_receipt(receiver_snapshot(**overrides))
        with self.assertRaisesRegex(RunStateError, 'receiver snapshot is missing'):
            _receiver_receipt(None)

    # -------------------------------------------------------- local leg gate
    def test_local_execution_admits_only_the_complete_child_receipt(self):
        execution = _local_execution(fake_flat_result(RUN_ID), RUN_ID)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, OBLIGATION_COUNT)
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        from bcs_e2e.case_rewrite import slow_judge_case_rewrite

        plan = slow_judge_case_rewrite()
        self.assertEqual([step.method for step in execution.steps],
                         [plan.adapters[name] for name in plan.requirement_ids])
        self.assertTrue(all(step.status == 'PASS' for step in execution.steps))
        execution.validate()

    def test_local_execution_blocks_on_incomplete_or_mismatched_receipts(self):
        blocked = fake_flat_result(RUN_ID, status='BLOCKED')
        execution = _local_execution(blocked, RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.error_type, 'LOCAL_SLOW_JUDGE_BLOCKED')
        self.assertTrue(all(step.status == 'NOT_RUN' for step in execution.steps))
        other_run = fake_flat_result('20261031-w3a-s28-other-run')
        execution = _local_execution(other_run, RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        with self.assertRaisesRegex(RunStateError, 'receiver receipt drift'):
            _local_execution(fake_flat_result(RUN_ID,
                                              sandbox=receiver_snapshot(state='OPEN')), RUN_ID)
        with self.assertRaisesRegex(RunStateError, 'receiver receipt drift'):
            _local_execution(fake_flat_result(RUN_ID, status='LOCAL_SLOW_JUDGE_PASS',
                                              sandbox=receiver_snapshot(failed=True)), RUN_ID)

    # -------------------------------------------------------- preprod leg
    def test_preprod_leg_fail_closes_without_sockets(self):
        profile = validate_slow_judge_profile(preprod_profile())
        state = self.root / 'leg-state'
        state.mkdir()
        with patch.dict(os.environ, {}, clear=True), \
                patch('socket.socket', side_effect=AssertionError('network forbidden')), \
                patch.object(dual, 'local_slow_judge_run',
                             side_effect=AssertionError('local delegation forbidden')):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.error_type, 'PREPROD_SLOW_JUDGE_CREDENTIALS_UNAVAILABLE')
        self.assertEqual([step.status for step in execution.steps], ['NOT_RUN'] * OBLIGATION_COUNT)
        self.assertEqual(blocker['reason_class'], 'PREPROD_SLOW_JUDGE_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(blocker['network_requests'], 0)
        self.assertEqual(blocker['credential_refs'], sorted(HEADER_ENV.values()))
        self.assertEqual(receipt['network_requests'], 0)
        self.assertEqual(receipt['checks_status'], 'BLOCKED')
        self.assertIn('slow-judge-journey-admission', receipt['markers'])
        self.assertIs(receipt['markers']['slow-judge-journey-admission'], False)

    def test_preprod_leg_classifies_credential_window_versus_journey_seam(self):
        profile = validate_slow_judge_profile(preprod_profile())
        state = self.root / 'leg-classify'
        state.mkdir()
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: COOKIE_CANARY}), \
                patch('socket.socket', side_effect=AssertionError('network forbidden')):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.error_type, 'PREPROD_SLOW_JUDGE_JOURNEY_SEAM_PENDING')
        self.assertEqual(blocker['reason_class'], 'PREPROD_SLOW_JUDGE_JOURNEY_SEAM_PENDING')
        self.assertEqual(blocker['network_requests'], 0)
        self.assertEqual(blocker['journey_child_status'], 'SEAM_PENDING_MAINLINE')
        self.assertEqual(receipt['journey_child_status'], 'SEAM_PENDING_MAINLINE')
        combined = json.dumps(receipt) + json.dumps(blocker)
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)

    def test_preprod_leg_rejects_station_drift_without_raising(self):
        profile = validate_slow_judge_profile(preprod_profile())
        profile['actors']['driver']['bot_id'] = 'not-a-station-bot'
        state = self.root / 'leg-drift'
        state.mkdir()
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: COOKIE_CANARY}):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(blocker['reason_class'], 'PREPROD_SLOW_JUDGE_STATION_DRIFT')
        self.assertEqual(blocker['network_requests'], 0)

    def test_empty_receipt_is_credential_free(self):
        receipt = _empty_receipt()
        self.assertEqual(receipt['identity_mode'], 'bearer')
        self.assertEqual(receipt['network_requests'], 0)
        self.assertEqual(receipt['credential_refs'], sorted(HEADER_ENV.values()))
        self.assertEqual(receipt['observed_obligations'], 0)
        self.assertNotIn('Bearer ', json.dumps(receipt))

    def test_write_preprod_leaf_is_machine_readable_and_credential_free(self):
        report = {'status': 'BLOCKED', 'schema_version': 1,
                  'error_type': 'PREPROD_SLOW_JUDGE_JOURNEY_SEAM_PENDING',
                  'execution': {'completed_count': 0, 'total_count': OBLIGATION_COUNT,
                                'status': 'BLOCKED'},
                  'obligation_summary': {'total': OBLIGATION_COUNT},
                  'preprod_blocker': {'reason_class': 'PREPROD_SLOW_JUDGE_JOURNEY_SEAM_PENDING',
                                      'network_requests': 0, 'observed_status': None,
                                      'reason': 'seam pending'},
                  'profile_digest': 'digest', 'executor_digest': executor_digest(),
                  'bcs_binary_sha256': self.sha, 'cli_binary_sha256': None,
                  'environment': {'tier': 'preprod', 'id': RUN_ID,
                                  'expected_bcs_env': 'pre', 'entrypoint_kind': 'bcs-direct'}}
        receipt = _empty_receipt()
        leaf_root = self.root / 'leaves'
        returned = _write_preprod_leaf(run_id=RUN_ID, report=report, receipt=receipt,
                                       leaf_root=leaf_root)
        self.assertEqual(returned, leaf_root)
        slug = RUN_ID
        for name in (slug + '-stdout.json', slug + '-verdict.json', slug + '-stderr.txt'):
            self.assertTrue((leaf_root / name).is_file(), name)
        verdict = read(leaf_root / (slug + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['contract'], 'dual-slow-judge-case-run-v1')
        self.assertEqual(verdict['obligations_total'], OBLIGATION_COUNT)
        self.assertEqual(verdict['obligations_completed'], 0)
        self.assertEqual(verdict['network_requests'], 0)
        self.assertEqual(verdict['mock_gate_inherited'], 'MOCK')
        self.assertIsNone(verdict['observed_status'])
        self.assertFalse(verdict['grants_release_credit'])
        self.assertEqual(verdict['identity_mode'], 'bearer')
        combined = all_text(leaf_root)
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)
        self.assertNotIn('Bearer ', combined)
        self.assertEqual(DEFAULT_LEAF_ROOT.name, '20261031-migration-w3a-s28-slow-judge-preprod')

    # ------------------------------------------------------------- reporting
    def _synthetic_binding(self) -> dict:
        return {'schema_version': 1, 'contract': 'dual-slow-judge-case-run-v1',
                'run_id': RUN_ID, 'executor_digest': executor_digest(),
                'source_case_id': SOURCE_CASE_ID, 'local_suite_id': SOURCE_SUITE,
                'bcs_binary_sha256': self.sha, 'cli_binary_sha256': None,
                'cli_pair_status': 'NOT_SUPPLIED_BY_CALLER',
                'legacy_inventory_count': len(current_manifest()['legacy_inventory']),
                'preprod_obligation_modes': {
                    'MOCK_RECEIVER_LOCAL_ONLY': 5,
                    'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER': 4,
                    'LIVE_BEARER_REPLAY': 13}}

    def test_summary_carries_the_mock_gate_inheritance_and_mode_counts(self):
        summary = build_slow_judge_dual_summary(self._synthetic_binding(), {
            'status': 'PASS', 'environment': {'tier': 'local', 'id': 's28',
                                              'expected_bcs_env': 'local',
                                              'entrypoint_kind': 'owned-local-story',
                                              'identity_mode': 'register',
                                              'frozen_contract': 'LOCAL_EXECUTOR_ONLY'},
            'obligation_summary': {'total': OBLIGATION_COUNT},
            'resource_disposition': 'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED',
            'error_type': None})
        self.assertEqual(summary['status'], 'PASS')
        self.assertEqual(summary['scope']['suite'], DUAL_SUITE)
        self.assertEqual(summary['scope']['parent'], PARENT)
        self.assertEqual(summary['scope']['legacy_inventory_count'],
                         len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['mock_gate_inheritance']['ledger_gate'], 'MOCK')
        self.assertEqual(summary['mock_gate_inheritance']['ledger_row'], PARENT)
        self.assertEqual(summary['mock_gate_inheritance']['receiver_reuse'],
                         'reused-not-rewritten')
        self.assertEqual(summary['mock_gate_inheritance']['grants_release_credit'], False)
        self.assertEqual(summary['preprod_obligation_mode_counts'],
                         {'MOCK_RECEIVER_LOCAL_ONLY': 5,
                          'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER': 4,
                          'LIVE_BEARER_REPLAY': 13})
        self.assertEqual(len(summary['requirements']), OBLIGATION_COUNT)
        self.assertEqual(summary['reports']['expected_assertion_count'], OBLIGATION_COUNT)
        self.assertIs(summary['ready_to_execute'], False)
        self.assertIs(summary['verification_boundary']['grants_release_credit'], False)

    def test_publish_writes_the_triple_and_completion_and_verifies_offline(self):
        state = self.root / 'publish-state'
        state.mkdir()
        binding = self._synthetic_binding()
        report = {'schema_version': 1, 'status': 'BLOCKED', 'environment': {'tier': 'preprod', 'id': 's28-pre',
                                                        'expected_bcs_env': 'pre',
                                                        'entrypoint_kind': 'bcs-direct',
                                                        'identity_mode': 'bearer',
                                                        'frozen_contract': 'REMOTE_JOURNEY_SEAM_PENDING'},
                  'obligation_summary': {'total': OBLIGATION_COUNT},
                  'resource_disposition': 'PREPROD_JOURNEY_SEAM_PENDING_NO_REMOTE_RESOURCES',
                  'error_type': 'PREPROD_SLOW_JUDGE_JOURNEY_SEAM_PENDING'}
        write(state / BINDING_NAME, binding)
        write(state / FLOW_NAME, report)
        published = publish_slow_judge_dual_report(state, binding, report)
        self.assertEqual(published['status'], 'BLOCKED')
        for name in ('summary.json', 'junit.xml', 'summary.md'):
            self.assertTrue((state / 'reports' / name).is_file(), name)
        self.assertTrue((state / COMPLETION_NAME).is_file())
        summary = build_slow_judge_dual_report(state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertEqual(summary['scope']['suite'], DUAL_SUITE)
        markdown = (state / 'reports' / 'summary.md').read_text(encoding='utf-8')
        self.assertIn('NOT_QUALIFIED', markdown)
        summary_json = (state / 'reports' / 'summary.json').read_text(encoding='utf-8')
        self.assertIn('"MOCK"', summary_json)
        self.assertIn('DUAL-SLOW-JUDGE-01', summary_json)
        tampered = dict(report)
        tampered['status'] = 'PASS'
        write(state / FLOW_NAME, tampered)
        with self.assertRaises(RunStateError):
            publish_slow_judge_dual_report(state, binding, report)

    def test_scan_redaction_catches_every_frozen_canary(self):
        self.assertTrue(scan_redaction('unit', b'clean evidence')['clean'])
        for canary in ('bot_token', 'X-BCS-Bot-Token', 'starpoint-data',
                       'x-proxypass-token', 'Bearer '):
            with self.subTest(canary=canary):
                row = scan_redaction('unit', ('east ' + canary + ' west').encode('utf-8'))
                self.assertFalse(row['clean'])
                self.assertEqual(row['canaries'], [canary])

    # --------------------------------------------------------------- entry
    def _entry(self, *, tier: str = 'local', run_id: str = RUN_ID, **overrides):
        kwargs = dict(profile_path=self._write_profile(
            local_profile() if tier == 'local' else preprod_profile(), 'entry'),
            state_dir=self.root / ('state-' + run_id), run_id=run_id, allow_write=True,
            confirm_run_id=run_id, binary=self.binary, binary_sha256=self.sha)
        kwargs.update(overrides)
        return kwargs

    def test_local_entry_delegates_and_publishes_a_self_contained_receipt(self):
        kwargs = self._entry()
        fake = fake_flat_result(RUN_ID)
        with patch.object(dual, 'local_slow_judge_run', return_value=fake) as delegated:
            report = dual_slow_judge_case_run(**kwargs)
        delegated.assert_called_once()
        delegated_kwargs = delegated.call_args.kwargs
        self.assertEqual(delegated_kwargs['binary'], kwargs['binary'])
        self.assertEqual(delegated_kwargs['binary_sha256'], self.sha)
        self.assertEqual(delegated_kwargs['state_dir'], kwargs['state_dir'] / 'owned-slow-judge')
        self.assertEqual(delegated_kwargs['run_id'], RUN_ID)
        self.assertEqual(delegated_kwargs['confirm_run_id'], RUN_ID)
        self.assertIs(delegated_kwargs['allow_local_processes'], True)
        self.assertIs(delegated_kwargs['allow_fixture_write'], True)
        self.assertIs(delegated_kwargs['allow_write'], True)
        self.assertNotIn('cli_binary', delegated_kwargs)
        self.assertNotIn('cli_binary_sha256', delegated_kwargs)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['contract'], 'dual-slow-judge-case-run-v1')
        self.assertEqual(report['case']['completed_count'], OBLIGATION_COUNT)
        self.assertEqual(report['execution']['completed_count'], OBLIGATION_COUNT)
        self.assertEqual(report['environment']['frozen_contract'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(report['environment']['identity_mode'], 'register')
        receiver = report['local_owned_run']['judge_receiver_receipt']
        self.assertEqual(receiver['connection_total'], 4)
        self.assertEqual(receiver['judge_request_count'], 1)
        self.assertEqual(receiver['judge_model'], 'owned-local-slow-judge')
        self.assertEqual(report['local_owned_run']['canonical_summary_status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(report['local_owned_run']['shutdown_status'], 'STOPPED')
        self.assertEqual(report['local_owned_run']['credential_memory'], 'CLEARED')
        self.assertEqual(report['judge_gate']['receiver_reuse'], 'reused-not-rewritten')
        self.assertEqual(report['judge_gate']['consumed_this_wave'], True)
        self.assertEqual(report['obligation_summary']['inherited_ledger_gate'], 'MOCK')
        self.assertEqual(report['obligation_summary']['preprod_obligation_modes']['MOCK_RECEIVER_LOCAL_ONLY'],
                         [name for name in REQUIREMENTS if family.preprod_obligation_modes()[name]
                          == 'MOCK_RECEIVER_LOCAL_ONLY'])
        self.assertNotIn('preprod_blocker', report)
        self.assertEqual(report['resource_disposition'],
                         'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED')
        read(kwargs['state_dir'] / 'slow-judge-dual-run.json')
        read(kwargs['state_dir'] / 'slow-judge-dual-binding.json')
        read(kwargs['state_dir'] / 'slow-judge-dual-profile.json')
        for name in ('summary.json', 'junit.xml', 'summary.md'):
            self.assertTrue((kwargs['state_dir'] / 'reports' / name).is_file(), name)
        completion = read(kwargs['state_dir'] / COMPLETION_NAME)
        self.assertEqual(completion['contract'], 'dual-slow-judge-completion-v1')
        offline = build_slow_judge_dual_report(kwargs['state_dir'])
        self.assertEqual(offline['status'], 'PASS')
        self.assertEqual(offline['requirements'][0]['id'], REQUIREMENTS[0])
        text = all_text(kwargs['state_dir'])
        self.assertNotIn(DRIVER_CANARY, text)
        self.assertNotIn(COOKIE_CANARY, text)
        self.assertNotIn('Bearer ', text)

    def test_local_entry_blocks_honestly_on_an_incomplete_flat_receipt(self):
        kwargs = self._entry()
        with patch.object(dual, 'local_slow_judge_run',
                          return_value=fake_flat_result(RUN_ID, status='BLOCKED')):
            report = dual_slow_judge_case_run(**kwargs)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)
        self.assertEqual(report['case']['status'], 'BLOCKED')
        offline = build_slow_judge_dual_report(kwargs['state_dir'])
        self.assertEqual(offline['status'], 'BLOCKED')
        text = all_text(kwargs['state_dir'])
        self.assertNotIn(DRIVER_CANARY, text)

    def test_preprod_entry_fails_closed_writes_the_leaf_without_sockets(self):
        kwargs = self._entry(tier='preprod', run_id=RUN_ID + '-pre',
                             leaf_root=self.root / 'pre')
        with patch.dict(os.environ, {}, clear=True), \
                patch('socket.socket', side_effect=AssertionError('network forbidden')), \
                patch.object(dual, 'local_slow_judge_run',
                             side_effect=AssertionError('local delegation forbidden')):
            report = dual_slow_judge_case_run(**kwargs)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_SLOW_JUDGE_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(report['preprod_blocker']['network_requests'], 0)
        self.assertEqual(report['preprod_story']['network_requests'], 0)
        self.assertEqual(report['preprod_journey'], PREPROD_JOURNEY)
        self.assertEqual(report['judge_gate']['consumed_this_wave'], False)
        self.assertEqual(report['resource_disposition'],
                         'PREPROD_JOURNEY_SEAM_PENDING_NO_REMOTE_RESOURCES')
        leaf = Path(report['preprod_evidence_leaf'])
        self.assertEqual(leaf, self.root / 'pre')
        for name in (RUN_ID + '-pre-stdout.json', RUN_ID + '-pre-verdict.json',
                     RUN_ID + '-pre-stderr.txt'):
            self.assertTrue((leaf / name).is_file(), name)
        verdict = read(leaf / (RUN_ID + '-pre-verdict.json'))
        self.assertEqual(verdict['error_type'], 'PREPROD_SLOW_JUDGE_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(verdict['obligations_total'], OBLIGATION_COUNT)
        combined = all_text(kwargs['state_dir']) + all_text(leaf)
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)
        self.assertNotIn('Bearer ', combined)

    def test_preprod_entry_classifies_the_loaded_credential_window_as_seam_pending(self):
        kwargs = self._entry(tier='preprod', run_id=RUN_ID + '-seam',
                             leaf_root=self.root / 'seam')
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: COOKIE_CANARY}), \
                patch('socket.socket', side_effect=AssertionError('network forbidden')):
            report = dual_slow_judge_case_run(**kwargs)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_SLOW_JUDGE_JOURNEY_SEAM_PENDING')
        self.assertEqual(report['preprod_blocker']['network_requests'], 0)
        verdict = read(self.root / 'seam' / (RUN_ID + '-seam-verdict.json'))
        self.assertEqual(verdict['network_requests'], 0)
        self.assertEqual(verdict['mock_gate_inherited'], 'MOCK')
        self.assertFalse(verdict['grants_release_credit'])

    def test_entry_enforces_write_gates_confirmation_and_state_dir_contract(self):
        profile_path = self._write_profile(local_profile(), 'gates')
        base = dict(profile_path=profile_path, state_dir=self.root / 'gates-state',
                    run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                    binary=self.binary, binary_sha256=self.sha)
        with self.assertRaisesRegex(ConfigError, 'exact Run confirmation'):
            dual_slow_judge_case_run(**{**base, 'confirm_run_id': 'other'})
        with self.assertRaisesRegex(ConfigError, 'write permission'):
            dual_slow_judge_case_run(**{**base, 'allow_write': False})
        with self.assertRaisesRegex(ConfigError, 'pair together or not at all'):
            dual_slow_judge_case_run(**{**base, 'cli_binary': self.cli_binary})
        with self.assertRaisesRegex(RunStateError, 'differs'):
            dual_slow_judge_case_run(**{**base, 'binary_sha256': SHA256_A})
        with self.assertRaisesRegex(RunStateError, 'absolute regular executable'):
            dual_slow_judge_case_run(**{**base, 'binary': self.root / 'absent-bcs'})
        self.assertFalse((self.root / 'gates-state').exists())
        prebuilt = self.root / 'prebuilt-state'
        prebuilt.mkdir()
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_slow_judge_case_run(**{**base, 'state_dir': prebuilt})
        relative_entry = self.root / 'relative-entry.json'
        relative_entry.write_text(json.dumps(local_profile()), encoding='utf-8')
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_slow_judge_case_run(**{**base, 'state_dir': Path('relative-leaf'),
                                        'profile_path': relative_entry})
        link_target = self.root / 'link-target'
        link_target.mkdir()
        linked = self.root / 'linked-leaf'
        linked.symlink_to(link_target)
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_slow_judge_case_run(**{**base, 'state_dir': linked})

    def test_cli_pair_is_admitted_digest_fenced_and_never_consumed(self):
        kwargs = self._entry(cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha)
        fake = fake_flat_result(RUN_ID)
        with patch.object(dual, 'local_slow_judge_run', return_value=fake) as delegated:
            report = dual_slow_judge_case_run(**kwargs)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['cli_pair_status'], family.CLI_PAIR_STATUS)
        self.assertEqual(report['cli_binary_sha256'], self.cli_sha)
        binding = read(kwargs['state_dir'] / 'slow-judge-dual-binding.json')
        self.assertEqual(binding['cli_pair_status'], family.CLI_PAIR_STATUS)
        self.assertEqual(binding['cli_binary_sha256'], self.cli_sha)
        delegated.assert_called_once()
        self.assertNotIn('cli_binary', delegated.call_args.kwargs)
        self.assertNotIn('cli_binary_sha256', delegated.call_args.kwargs)
        with self.assertRaisesRegex(RunStateError, 'differs'):
            dual_slow_judge_case_run(**self._entry(
                cli_binary=self.cli_binary, cli_binary_sha256=SHA256_A))

    def test_local_leg_failure_is_reported_as_unavailable(self):
        kwargs = self._entry()
        with patch.object(dual, 'local_slow_judge_run',
                          side_effect=RunStateError('owned runtime exploded')):
            with self.assertRaisesRegex(ConfigError, 'unavailable'):
                dual_slow_judge_case_run(**kwargs)
        self.assertTrue(kwargs['state_dir'].exists())
        self.assertFalse((kwargs['state_dir'] / 'slow-judge-dual-run.json').exists())

    def test_receiver_drift_is_loud_instead_of_a_fake_pass(self):
        kwargs = self._entry()
        fake = fake_flat_result(RUN_ID)
        fake = copy.deepcopy(fake)
        fake['judge_sandbox']['connection_total'] = 1
        with patch.object(dual, 'local_slow_judge_run', return_value=fake):
            with self.assertRaisesRegex(ConfigError, 'unavailable'):
                dual_slow_judge_case_run(**kwargs)
        self.assertFalse((kwargs['state_dir'] / 'slow-judge-dual-run.json').exists())

    # ----------------------------------------------------- registration seam
    def test_parser_admits_the_frozen_optional_pairs(self):
        parser = build_slow_judge_dual_argv_parser()
        args = parser.parse_args(['--profile', 'p.json', '--state-dir', 'state',
                                  '--run-id', RUN_ID, '--confirm-run-id', RUN_ID,
                                  '--allow-write', '--binary', str(self.binary),
                                  '--binary-sha256', self.sha])
        self.assertIsNone(args.cli_binary)
        self.assertIsNone(args.cli_binary_sha256)
        self.assertIsNone(args.leaf_root)
        self.assertIs(args.allow_write, True)
        args = parser.parse_args(['--profile', 'p.json', '--state-dir', 'state',
                                  '--run-id', RUN_ID, '--confirm-run-id', RUN_ID,
                                  '--binary', str(self.binary), '--binary-sha256', self.sha,
                                  '--cli-binary', str(self.cli_binary),
                                  '--cli-binary-sha256', self.cli_sha,
                                  '--leaf-root', str(self.root)])
        self.assertEqual(args.cli_binary, self.cli_binary)
        self.assertEqual(args.leaf_root, self.root)

    def test_register_helper_exposes_the_frozen_command(self):
        import argparse

        commands = argparse.ArgumentParser().add_subparsers(dest='command')
        register_slow_judge_dual_case_commands(commands)
        choices = commands.choices
        self.assertIn(COMMAND, choices)
        slow_judge = choices[COMMAND]
        required = {action.dest for action in slow_judge._actions
                    if action.required and action.dest != 'help'}
        self.assertEqual(required, {'profile', 'state_dir', 'run_id', 'confirm_run_id',
                                    'binary', 'binary_sha256'})
        optional = {action.dest for action in slow_judge._actions if not action.required}
        self.assertIn('cli_binary', optional)
        self.assertIn('cli_binary_sha256', optional)
        self.assertIn('leaf_root', optional)
        self.assertIn('allow_write', optional)

    # ------------------------------------------------------- family constants
    def test_frozen_family_surface_matches_the_umbrella_contract(self):
        self.assertEqual(COMMAND, 'dual-slow-judge-run')
        self.assertEqual(CASE_KEYS, ('slow-judge',))
        self.assertEqual(CASE_ID, 'DUAL-SLOW-JUDGE-01')
        self.assertEqual(dual.SUITE, 'dual-slow-judge-v1')
        self.assertEqual(dual.SOURCE_CASE_ID if hasattr(dual, 'SOURCE_CASE_ID') else SOURCE_CASE_ID,
                         SOURCE_CASE_ID)
        self.assertEqual(family.OBLIGATION_COUNT, 22)
        self.assertEqual(set(LOCAL_PROFILE_KEYS), {
            'schema_version', 'contract', 'environment', 'entrypoint', 'human', 'auth',
            'cli', 'policy', 'judge', 'effects'})
        self.assertEqual(set(PREPROD_ACTOR_KEYS), {'driver', 'member'})
        self.assertEqual(family.ENVIRONMENT, {'local': 'LOCAL_EXECUTOR_ONLY',
                                              'preprod': 'REMOTE_JOURNEY_SEAM_PENDING'})
        self.assertEqual(family.HEADER_ENV, {'Authorization': 'BCS_E2E_DRIVER_TOKEN',
                                             'Cookie': 'BCS_E2E_HUMAN_COOKIE'})
        self.assertEqual(family.JUDGE_BLOCKS, {
            'local': {'kind': 'owned-loopback-receiver'},
            'preprod': {'kind': 'preprod-admitted-real-judge'}})
        modes = preprod_obligation_modes()
        self.assertEqual(len(modes), OBLIGATION_COUNT)
        self.assertEqual(set(modes), set(REQUIREMENTS))
        self.assertEqual(sorted(modes.values()).count('MOCK_RECEIVER_LOCAL_ONLY'), 5)
        self.assertEqual(sorted(modes.values()).count(
            'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER'), 4)
        self.assertEqual(sorted(modes.values()).count('LIVE_BEARER_REPLAY'), 13)
        binding = slow_judge_dual_source_binding()
        self.assertEqual(binding['dual_suite'], DUAL_SUITE)
        self.assertEqual(binding['dual_case_id'], DUAL_CASE_ID)
        self.assertEqual(binding['parent'], PARENT)
        self.assertEqual([row['id'] for row in binding['requirements']], list(REQUIREMENTS))
        self.assertEqual([row['adapter_method'] for row in binding['requirements']],
                         list(family.REQUIREMENT_METHODS))
        self.assertEqual(frozen_dual_contract(), json.loads(
            Path(ROOT / 'slow-judge-dual-migration-contract.json').read_text()))
        adaptability = slow_judge_dual_adaptability()
        self.assertEqual(adaptability['case_id'], DUAL_CASE_ID)
        self.assertEqual(adaptability['source_case_id'], SOURCE_CASE_ID)
        self.assertEqual(adaptability['legacy_id'], PARENT)
        self.assertEqual(adaptability['requirement_count'], 22)
        self.assertEqual(adaptability['local_executor_status'], 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(adaptability['preprod_executor_status'],
                         'PREPROD_JOURNEY_SEAM_PENDING')
        self.assertEqual(adaptability['remote_validation'], 'NOT_RUN')
        self.assertFalse(adaptability['grants_release_credit'])
        self.assertIn('blocker', adaptability)
        self.assertIn('MOCK_RECEIVER_LOCAL_ONLY', adaptability['preprod_obligation_modes'])

    def test_mock_gate_inheritance_is_disclosed_and_mainline_owned(self):
        gate = MOCK_GATE_INHERITANCE
        self.assertEqual(gate['ledger_gate'], 'MOCK')
        self.assertEqual(gate['ledger_row'], PARENT)
        self.assertEqual(gate['ledger_file'], 'executor-migration.json')
        self.assertIn('mainline owns', gate['gate_owner'])
        self.assertEqual(gate['receiver_module'], 'bcs_e2e/local_judge_sandbox.py')
        self.assertEqual(gate['receiver_class'], 'OwnedLocalJudgeSandbox')
        self.assertEqual(gate['receiver_reuse'], 'reused-not-rewritten')
        self.assertEqual(len(gate['receiver_surfaces']), 3)
        self.assertEqual(gate['network_requests_this_wave'] if 'network_requests_this_wave'
                         in gate else 0, 0 if 'network_requests_this_wave' in gate else 0)
        self.assertIn('zero sockets', gate['preprod_leg'])
        self.assertIn('preprod', gate['preprod_leg'])
        self.assertEqual(len(gate['not_proven']), 5)
        self.assertFalse(gate['grants_release_credit'])

    def test_owned_local_flat_modules_stay_importable_and_unchanged_in_shape(self):
        import bcs_e2e.local_slow_judge_run as flat_run
        import bcs_e2e.local_slow_judge_reporting as flat_reporting
        import bcs_e2e.local_judge_contract as flat_contract
        import bcs_e2e.local_judge_sandbox as sandbox
        import bcs_e2e.local_config as flat_config

        self.assertTrue(callable(flat_run.local_slow_judge_run))
        self.assertTrue(callable(flat_reporting.publish_slow_judge))
        self.assertTrue(callable(flat_contract.judge_source_binding))
        self.assertTrue(hasattr(sandbox, 'OwnedLocalJudgeSandbox'))
        self.assertTrue(callable(flat_config.prepare_local_bcs_inputs))
        # The ledger row flip (LOCAL_ONLY -> DUAL) is mainline-serial after this
        # wave; the committed row and the flipped row must keep the MOCK gate.
        row = load_executor_migration()[PARENT]
        self.assertIn(row['mode'], ('LOCAL_ONLY', 'DUAL'))
        self.assertEqual(row['preprod_verdict'], 'NOT_RUN')
        self.assertIn('MOCK', row['gates'])




class SlowJudgeDualRegistrationTests(unittest.TestCase):
    """Registration, ledger and backlog-row fences for the dual slow-Judge slice."""

    def test_registration_equivalent_to_planned_slice(self):
        from bcs_e2e.case_command_registration import CASE_COMMANDS
        from bcs_e2e.migration_inventory import migration_plan
        registered = dual.COMMAND in CASE_COMMANDS
        plan = migration_plan()
        planned = any(row.get('execution_command') == dual.COMMAND
                      for row in plan['local_backlog_case_slices'])
        self.assertEqual(registered, planned)
        row = next(row for row in plan['local_backlog_case_slices']
                   if row['case_id'] == dual.DUAL_CASE_ID)
        self.assertEqual(row['suite'], dual.DUAL_SUITE)
        self.assertEqual(row['legacy_id'], PARENT)
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], dual.COMMAND)
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_assertion_count'], OBLIGATION_COUNT)
        self.assertEqual(len(row['requirement_ids']), OBLIGATION_COUNT)
        self.assertEqual(row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
            'state_dir', 'run_id', 'confirm_run_id', 'allow_write'])
        self.assertEqual(row['environment'], family.ENVIRONMENT)
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(row['source_map'], 'slow-judge-dual-migration-contract.json')
        self.assertTrue(any(isinstance(item, dict)
                            and item.get('dual_case_id') == dual.DUAL_CASE_ID
                            for item in plan['additional_source_maps']))

    def test_cli_exposes_the_dual_command_and_ledger_pins_the_dual_admission(self):
        from bcs_e2e.case_command_registration import CASE_COMMANDS
        self.assertIn(dual.COMMAND, CASE_COMMANDS)
        row = load_executor_migration()[PARENT]
        self.assertEqual(row['mode'], 'DUAL')
        self.assertEqual(row['preprod_verdict'], 'NOT_RUN')
        self.assertEqual(row['gates'], ['MOCK'])
        self.assertEqual(row['notes'], [])

    def test_local_registries_expose_the_owned_aliases_without_dual_duplication(self):
        from bcs_e2e.local_story_commands import LOCAL_STORY_COMMANDS
        self.assertIn('local-slow-judge-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-slow-judge-report', LOCAL_STORY_COMMANDS)
        self.assertNotIn('dual-slow-judge-run', LOCAL_STORY_COMMANDS)

    def test_registration_seam_builds_the_frozen_ten_flag_parser(self):
        import argparse
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(dest='command')
        dual.register_slow_judge_dual_case_commands(commands)
        registered = commands.choices[dual.COMMAND]
        expected = set(family.REQUIRED_PARAMETERS) | set(family.OPTIONAL_PARAMETERS)
        self.assertEqual(
            {a.dest for a in registered._actions if a.dest != 'help'}, expected)

    def test_runner_help_exposes_the_command_contract(self):
        import subprocess
        runner = Path(__file__).resolve().parents[1] / 'runner.py'
        completed = subprocess.run(
            [sys.executable, str(runner), dual.COMMAND, '--help'],
            capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(dual.COMMAND, completed.stdout)
        for flag in ('--profile', '--binary-sha256', '--cli-binary',
                     '--leaf-root', '--confirm-run-id', '--allow-write'):
            self.assertIn(flag, completed.stdout)


if __name__ == '__main__':
    unittest.main()
