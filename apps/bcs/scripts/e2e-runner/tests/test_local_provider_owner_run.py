"""Behavioral guards for the Provider owner-isolation owned-local Run lifecycle.

Landed verbatim from the provider-owner staging payload by the orchestrator
landing recipe; the runner root is located dynamically. Every Run here is a fake-owned-runtime
offline rehearsal: the frozen black-box story child is replaced by a seam, no
BCS server, provider mint or live HTTP happens, and nothing credential-bearing
is captured.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def _runner_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / 'provider-owner-migration-contract.json').is_file() and (parent / 'bcs_e2e').is_dir():
            return parent
    raise AssertionError('provider-owner runner root not found')


ROOT = _runner_root()
sys.path.insert(0, str(ROOT))
from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.inventory import current_manifest, executor_digest
from bcs_e2e.local_provider_owner_reporting import build_provider_owner_report
from bcs_e2e.local_provider_owner_run import (BINARY_KIND, OWNER_ID, STORY_CHECKS,
                                              STORY_ERR_LOG_NAME, STORY_LOG_NAME,
                                              _child_environment, _parse_story_marker,
                                              _redaction_gate, _story_argv,
                                              local_provider_owner_run,
                                              validate_provider_owner_binary)
from bcs_e2e.local_story_commands import CASES, LOCAL_STORY_COMMANDS
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import fingerprint
from bcs_e2e.provider_owner_contract import (CASE_ID, MARKER_KEYS, REQUIREMENTS,
                                              STATUS, STORY_SOURCE, SUITE,
                                              provider_owner_source_binding)
from bcs_e2e.run_state import RunStateError, read

GOOD_STDOUT = b'Provider scope story: 45 live API assertions passed\n'
DRIFT_STDERR = (b'Traceback (most recent call last):\n'
                b'  File "provider_scope_story.py", line 117, in run\n'
                b'AssertionError: PUT /bots/{bot_uuid}/visibility: expected 200, '
                b'received 403 owner or manager role required\n')
RUNTIME_ORIGIN = 'http://127.0.0.1:39999'


def story_seam(stdout=GOOD_STDOUT, stderr=b'', exit_code=0, timed_out=False, error=None):
    if error is not None:
        def launch(*_args, **_kwargs):
            raise error
        return launch

    def launch(*_args, **_kwargs):
        return {'exit_code': exit_code, 'timed_out': timed_out, 'duration_seconds': 0.42,
                'stdout': stdout, 'stderr': stderr, 'process': None}
    return launch


def executable_binary(directory: Path) -> tuple[Path, str]:
    binary = directory / 'bcs'
    binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    binary.chmod(0o755)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def run_kwargs(state_dir: Path, binary: Path, digest: str, **changes) -> dict:
    base = dict(binary=binary, binary_sha256=digest, state_dir=state_dir, run_id='provider-owner-test',
                confirm_run_id='provider-owner-test', allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
    base.update(changes)
    return base


class FakeOwnedRuntime:
    instances: list = []
    enter_error: Exception | None = None
    final_status = 'STOPPED'
    final_returncode = 0

    def __init__(self, *, binary, binary_sha256, root, run_id, allow_ambient_local_identity=True):
        self.init_kwargs = {'binary': Path(binary), 'binary_sha256': binary_sha256, 'root': Path(root),
                            'run_id': run_id, 'allow_ambient_local_identity': allow_ambient_local_identity}
        FakeOwnedRuntime.instances.append(self)
        self.root = Path(root).resolve()
        self.inputs = SimpleNamespace(origin=RUNTIME_ORIGIN)
        if self.enter_error is not None:
            raise self.enter_error

    def _persist(self, status: str) -> None:
        ready = status == 'STOPPED'
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.init_kwargs['run_id'], 'status': status,
             'origin': RUNTIME_ORIGIN, 'pid': None, 'returncode': self.final_returncode,
             'binary_sha256': self.init_kwargs['binary_sha256'], 'ready': ready,
             'forced_stop': False}, sort_keys=True, indent=2), encoding='utf-8')

    def __enter__(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self._persist('STARTING')
        return self

    def __exit__(self, *_args):
        self._persist(self.final_status)
        return False


class ProviderOwnerRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []

    def _success_run(self, **overrides):
        story = story_seam(**overrides.pop('story', {}))
        state = overrides.pop('state_dir', None) or (self.root / 'state')
        with patch('bcs_e2e.local_provider_owner_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_provider_owner_run._launch_story', story), \
                patch('bcs_e2e.local_provider_owner_run.file_sha256', return_value=self.digest):
            return state, local_provider_owner_run(**run_kwargs(state, self.binary, self.digest, **overrides))

    def test_success_run_publishes_the_black_box_child_receipt_chain(self):
        state, result = self._success_run()
        self.assertEqual(result['status'], STATUS)
        self.assertEqual(result['suite'], SUITE)
        self.assertEqual(result['case_id'], CASE_ID)
        self.assertEqual(result['ready_to_execute'], False)
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
        self.assertNotIn('human_id', result)
        self.assertNotIn('mock_child', result)
        names = {'provider-owner-binding.json', 'provider-owner-flow-report.json', 'provider-owner-child.json',
                 'provider-owner.json', 'owned-bcs.json', 'provider-owner-completion.json',
                 STORY_LOG_NAME, STORY_ERR_LOG_NAME, 'reports/summary.json',
                 'reports/junit.xml', 'reports/summary.md'}
        self.assertTrue(all((state / name).is_file() for name in names),
                        sorted(p.name for p in state.rglob('*')))
        binding = read(state / 'provider-owner-binding.json')
        self.assertEqual(binding['contract'], SUITE)
        self.assertEqual(binding['case_id'], CASE_ID)
        self.assertEqual(binding['owner_id'], OWNER_ID)
        self.assertEqual(binding['story_checks'], STORY_CHECKS)
        self.assertEqual(binding['binary_kind'], BINARY_KIND)
        self.assertEqual(binding['auto_build'], False)
        self.assertEqual(binding['bcs_binary_sha256'], self.digest)
        self.assertEqual(binding['executor_digest'], executor_digest())
        self.assertEqual(binding['selected_requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['environment'], {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(binding['source_binding']['parent'], 'story_provider_owner_isolation')
        self.assertTrue(binding['source_binding']['release_parent_denominator'] is False)
        self.assertTrue(binding['source_binding']['independent_slice'] is True)
        self.assertTrue(binding['source_binding']['grants_release_credit'] is False)
        self.assertEqual(binding['source_binding']['environment'], binding['environment'])
        self.assertEqual(binding['legacy_inventory'], current_manifest()['legacy_inventory'])
        self.assertEqual(len(binding['legacy_inventory']), 41)
        self.assertNotIn('human_id', binding)
        self.assertNotIn('mock_child', binding)
        runtime = FakeOwnedRuntime.instances[0].init_kwargs
        self.assertEqual(runtime['allow_ambient_local_identity'], True)
        self.assertEqual(runtime['binary_sha256'], self.digest)
        self.assertEqual(runtime['root'], state)
        child = read(state / 'provider-owner-child.json')
        self.assertEqual(child['argv'],
                         [sys.executable, str(STORY_SOURCE), '--base-url', RUNTIME_ORIGIN, '--owner', OWNER_ID])
        self.assertNotIn('Bearer', json.dumps(child))
        self.assertEqual(child['markers'], dict.fromkeys(MARKER_KEYS, True))
        self.assertEqual(child['observed_checks'], 45)
        self.assertEqual(child['redaction_gate'], True)
        self.assertEqual(child['exit_code'], 0)
        self.assertEqual((state / STORY_LOG_NAME).read_bytes(), GOOD_STDOUT)
        self.assertEqual((state / STORY_ERR_LOG_NAME).read_bytes(), b'')
        case = read(state / 'provider-owner.json')
        self.assertEqual(case['status'], STATUS)
        self.assertEqual(case['owner_id'], OWNER_ID)
        self.assertEqual([row['id'] for row in case['requirements']], list(REQUIREMENTS))
        self.assertEqual(len(case['requirements']), 24)
        self.assertTrue(all(row['status'] == 'PASS' for row in case['requirements']))
        self.assertEqual(case['story_lines'],
                         {row['id']: list(row['story_lines'])
                          for row in provider_owner_source_binding()['requirements']})
        self.assertEqual(case['story_markers'], {'observed': [True], 'http_checks': 45})
        self.assertEqual(case['runtime_shutdown'], {'status': 'STOPPED', 'returncode': 0})
        self.assertEqual(case['resource_disposition'], 'STORY_SCOPED_TEMPORARY_DIRECTORY')
        self.assertEqual(case['credentials']['credential_memory'], 'NOT_CAPTURED')
        flow = read(state / 'provider-owner-flow-report.json')
        self.assertEqual(flow['case'], case)
        self.assertEqual(flow['child_receipt'], child)
        self.assertEqual(flow['credentials']['credential_memory'], 'NOT_CAPTURED')
        self.assertEqual(read(state / 'owned-bcs.json')['status'], 'STOPPED')
        summary = build_provider_owner_report(state)
        self.assertEqual(summary, result['canonical_summary'])
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['suite'], SUITE)
        self.assertEqual(summary['scope']['selected_cases'], [CASE_ID])
        self.assertEqual(summary['scope']['legacy_inventory_count'],
                         len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['scope']['independent_slice'], True)
        self.assertEqual(summary['scope']['release_parent_denominator'], False)
        self.assertEqual(summary['scope']['environment'], binding['environment'])
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['scope']['manifest_digest'], fingerprint(binding))
        self.assertEqual(summary['runner']['executor_digest'], executor_digest())
        self.assertEqual(summary['runner']['required_arguments'], ['--binary', '--binary-sha256'])
        self.assertEqual(summary['reports']['expected_assertion_count'], 24)
        self.assertEqual(summary['reports']['observed_assertion_count'], 24)
        self.assertTrue(all(row['status'] == 'PASS' for row in summary['requirements']))
        not_proven = '\n'.join(summary['verification_boundary']['not_proven'])
        self.assertIn('OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT', not_proven)
        self.assertIn('PREPROD_PROVIDER_ADMIN_FACE_ABSENT', not_proven)
        self.assertIn('WORKBENCH_WS_MOCK_ADMISSION', not_proven)
        self.assertEqual(summary['integrity']['status'], 'PASS')
        self.assertEqual(summary['ready_to_execute'], False)
        completion = read(state / 'provider-owner-completion.json')
        self.assertEqual(completion['contract'], 'owned-local-provider-owner-completion-v1')
        self.assertEqual(completion['binding_digest'], fingerprint(binding))
        self.assertEqual(set(completion['sources']),
                         {'provider-owner-binding.json', 'provider-owner-flow-report.json',
                          'provider-owner-child.json', 'owned-bcs.json', 'provider-owner.json',
                          STORY_LOG_NAME, STORY_ERR_LOG_NAME})
        self.assertEqual(set(completion['outputs']),
                         {'reports/summary.json', 'reports/junit.xml', 'reports/summary.md'})

    def test_wrong_checks_count_and_failure_variants_block(self):
        variants = {
            'nonzero-exit': {'exit_code': 130},
            'missing-marker': {'stdout': b''},
            'wrong-checks': {'stdout': b'Provider scope story: 44 live API assertions passed\n'},
            'zero-checks': {'stdout': b'Provider scope story: 0 live API assertions passed\n'},
            'timed-out': {'exit_code': -9, 'timed_out': True},
            'deadline-raised': {'error': RunStateError('provider-owner story exceeded its 900-second deadline')},
        }
        for name, story_overrides in variants.items():
            with self.subTest(variant=name):
                state, result = self._success_run(story=story_overrides,
                                                  state_dir=self.root / f'state-{name}')
                self.assertEqual(result['status'], 'BLOCKED', name)
                self.assertFalse((state / 'provider-owner.json').exists(), name)
                summary = build_provider_owner_report(state)
                self.assertEqual(summary['status'], 'BLOCKED', name)

    def test_live_owner_edge_drift_reality_blocks_without_pass_claims(self):
        # The frozen story today dies at request 8 (PUT visibility -> 403) on the
        # #2625 authority edges that provider registration never seeds. The
        # executor must surface that reality as BLOCKED with evidence, never as
        # a re-scoped pass and never as invented success.
        state, result = self._success_run(
            story=dict(stdout=b'', stderr=DRIFT_STDERR, exit_code=1),
            state_dir=self.root / 'state-live-drift')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse((state / 'provider-owner.json').exists())
        self.assertEqual((state / STORY_ERR_LOG_NAME).read_bytes(), DRIFT_STDERR)
        self.assertIn(b'owner or manager role required', (state / STORY_ERR_LOG_NAME).read_bytes())
        child = read(state / 'provider-owner-child.json')
        self.assertEqual(child['exit_code'], 1)
        self.assertEqual(child['markers'], dict.fromkeys(MARKER_KEYS, False))
        self.assertEqual(child['observed_checks'], 0)
        self.assertEqual(child['redaction_gate'], True)
        summary = build_provider_owner_report(state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertEqual(summary['cases'][0]['status'], 'BLOCKED')
        self.assertTrue(all(row['status'] == 'NOT_RUN' for row in summary['requirements']))

    def test_credential_bearing_streams_block_and_never_land_in_evidence(self):
        state, result = self._success_run(
            story=dict(stdout=b'Provider scope story: 45 live API assertions passed\n'
                               b'provider_admin_token=abc\n'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse((state / STORY_LOG_NAME).exists())
        self.assertFalse((state / STORY_ERR_LOG_NAME).exists())
        self.assertEqual(read(state / 'provider-owner-child.json')['redaction_gate'], False)

    def test_permissions_fresh_state_and_exact_confirmation_are_enforced(self):
        state = self.root / 'state'
        with patch('bcs_e2e.local_provider_owner_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_provider_owner_run._launch_story', story_seam()):
            with self.assertRaises(RunStateError):
                local_provider_owner_run(**run_kwargs(state, self.binary, self.digest, allow_local_processes=False))
            with self.assertRaises(RunStateError):
                local_provider_owner_run(**run_kwargs(state, self.binary, self.digest, allow_fixture_write=False))
            with self.assertRaises(RunStateError):
                local_provider_owner_run(**run_kwargs(state, self.binary, self.digest, allow_write=False))
            with self.assertRaises(RunStateError):
                local_provider_owner_run(**run_kwargs(state, self.binary, self.digest, confirm_run_id='other'))
            local_provider_owner_run(**run_kwargs(state, self.binary, self.digest))
            # The fresh state-directory fence refuses replay into existing evidence.
            with self.assertRaises(RunStateError):
                local_provider_owner_run(**run_kwargs(state, self.binary, self.digest))

    def test_runtime_startup_failure_surfaces_without_evidence_directory(self):
        state = self.root / 'state-broken'
        FakeOwnedRuntime.enter_error = OSError('runtime bootstrap failed')
        try:
            with patch('bcs_e2e.local_provider_owner_run.OwnedBcsProcess', FakeOwnedRuntime), \
                    patch('bcs_e2e.local_provider_owner_run._launch_story', story_seam()):
                with self.assertRaises(OSError):
                    local_provider_owner_run(**run_kwargs(state, self.binary, self.digest))
            self.assertFalse(state.exists())
        finally:
            FakeOwnedRuntime.enter_error = None

    def test_runtime_shutdown_disposition_gates_the_pass(self):
        FakeOwnedRuntime.final_status = 'FAILED_STOP'
        FakeOwnedRuntime.final_returncode = 1
        try:
            state, result = self._success_run()
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(read(state / 'owned-bcs.json')['status'], 'FAILED_STOP')
            self.assertFalse((state / 'provider-owner.json').exists())
            summary = build_provider_owner_report(state)
            self.assertEqual(summary['status'], 'BLOCKED')
        finally:
            FakeOwnedRuntime.final_status = 'STOPPED'
            FakeOwnedRuntime.final_returncode = 0

    def test_executor_drift_fence_refuses_the_pass(self):
        state = self.root / 'state-drift'
        seen = {'count': 0}

        def drifting():
            seen['count'] += 1
            # Digests drift only while the child ran (call two); publication and
            # the frozen binding stay aligned to the first digest afterwards.
            return 'second-owner-digest' if seen['count'] == 2 else 'first-owner-digest'

        with patch('bcs_e2e.local_provider_owner_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_provider_owner_run._launch_story', story_seam()), \
                patch('bcs_e2e.local_provider_owner_run.executor_digest', side_effect=drifting), \
                patch('bcs_e2e.local_provider_owner_reporting.executor_digest', side_effect=drifting), \
                patch('bcs_e2e.local_provider_owner_run.file_sha256', return_value=self.digest):
            result = local_provider_owner_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        binding = read(state / 'provider-owner-binding.json')
        self.assertEqual(binding['executor_digest'], 'first-owner-digest')
        self.assertTrue((state / 'provider-owner-flow-report.json').is_file())
        self.assertFalse((state / 'provider-owner.json').exists())

    def test_cancellation_canonicalises_without_pass_claims(self):
        state = self.root / 'state-cancel'
        with patch('bcs_e2e.local_provider_owner_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_provider_owner_run._launch_story',
                      story_seam(error=KeyboardInterrupt())):
            result = local_provider_owner_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['error_type'], 'KeyboardInterrupt')
        self.assertFalse((state / 'provider-owner-child.json').exists())
        self.assertFalse((state / 'provider-owner.json').exists())
        summary = build_provider_owner_report(state)
        self.assertEqual(summary['status'], 'CANCELLED')


class ProviderOwnerMarkerTests(unittest.TestCase):
    def test_strict_single_marker_with_checks_count(self):
        self.assertEqual(_parse_story_marker(GOOD_STDOUT.decode()), (True, 45))
        self.assertEqual(_parse_story_marker('Provider scope story: 44 live API assertions passed\n'), (True, 44))
        self.assertEqual(_parse_story_marker(''), (False, 0))
        self.assertEqual(_parse_story_marker('booting runtime\nProvider scope story: 45 live API assertions passed\n'), (False, 0))
        self.assertEqual(_parse_story_marker('Provider scope story: not-a-number\n'), (False, 0))

    def test_redaction_gate_covers_all_credential_likelihoods(self):
        self.assertTrue(_redaction_gate(b'clean', b''))
        for token in (b'bot_token', b'provider_admin_token', b'Bearer '):
            self.assertFalse(_redaction_gate(token, b''))
            self.assertFalse(_redaction_gate(b'', token))

    def test_story_argv_carries_the_base_url_and_the_owner_principal(self):
        argv = _story_argv(RUNTIME_ORIGIN)
        self.assertEqual(argv, [sys.executable, str(STORY_SOURCE), '--base-url', RUNTIME_ORIGIN,
                                '--owner', OWNER_ID])

    def test_child_environment_prepends_the_legacy_probe_path(self):
        legacy = str(STORY_SOURCE.parent)
        base = os.environ.copy()
        base.pop('PYTHONPATH', None)
        with patch.dict(os.environ, base, clear=True):
            self.assertEqual(_child_environment()['PYTHONPATH'], legacy)
        with patch.dict(os.environ, dict(base, PYTHONPATH='/keep/me'), clear=True):
            joined = _child_environment()['PYTHONPATH']
            self.assertTrue(joined.startswith(legacy + os.pathsep))
            self.assertIn('/keep/me', joined)


class ProviderOwnerBinaryValidationTests(unittest.TestCase):
    def test_frozen_regular_executable_with_exact_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary, digest = executable_binary(Path(temporary))
            self.assertEqual(validate_provider_owner_binary(binary, digest), binary)
            with self.assertRaises(RunStateError):
                validate_provider_owner_binary(binary, '0' * 64)

    def test_symlink_nonregular_or_nonexecutable_binaries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / 'real'
            real.write_text('#!/bin/sh\n', encoding='utf-8')
            real.chmod(0o755)
            link = base / 'link'
            link.symlink_to(real)
            with self.assertRaises(RunStateError):
                validate_provider_owner_binary(link, hashlib.sha256(real.read_bytes()).hexdigest())
            plain = base / 'plain'
            plain.write_text('not executable', encoding='utf-8')
            with self.assertRaises(RunStateError):
                validate_provider_owner_binary(plain, hashlib.sha256(plain.read_bytes()).hexdigest())


class ProviderOwnerEvidenceTamperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []
        with patch('bcs_e2e.local_provider_owner_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_provider_owner_run._launch_story', story_seam()), \
                patch('bcs_e2e.local_provider_owner_run.file_sha256', return_value=self.digest):
            self.state = self.root / 'state'
            self.result = local_provider_owner_run(
                **run_kwargs(self.state, self.binary, self.digest))
        self.assertEqual(self.result['status'], STATUS)

    def test_changed_case_cannot_claim_success(self):
        case = read(self.state / 'provider-owner.json')
        tampered = json.loads(json.dumps(case))
        tampered['requirements'].pop()
        (self.state / 'provider-owner.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_provider_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_runtime_disposition_tampering_blocks_the_projection(self):
        owned = read(self.state / 'owned-bcs.json')
        tampered = json.loads(json.dumps(owned))
        tampered['status'] = 'FAILED_STOP'
        (self.state / 'owned-bcs.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_provider_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_missing_child_receipt_is_blocked_not_invented(self):
        (self.state / 'provider-owner-child.json').unlink()
        summary = build_provider_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_publication_gap_and_symlinked_root_are_blocked(self):
        pending = self.state / 'provider-owner-publication-pending.json'
        pending.write_text('{}', encoding='utf-8')
        summary = build_provider_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        pending.unlink()
        linked = self.root / 'linked-state'
        linked.symlink_to(self.state)
        with self.assertRaises(RunStateError):
            build_provider_owner_report(linked)


class ProviderOwnerIndependentSliceFenceTests(unittest.TestCase):
    def test_promoted_slice_uses_dual_commands_without_release_credit(self):
        self.assertNotIn(CASE_ID, {case.case_id for case in CASES})
        self.assertIn('local-provider-owner-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-provider-owner-report', LOCAL_STORY_COMMANDS)
        self.assertIn('dual-provider-owner-run', CASE_COMMANDS)
        plan = migration_plan()
        registered = {row['case_id'] for row in plan['local_registered_executors']}
        independent = {row['case_id'] for row in plan['local_independent_slices']}
        self.assertNotIn(CASE_ID, registered)
        self.assertNotIn(CASE_ID, independent)
        row = next(item for item in plan['local_backlog_case_slices']
                   if item['case_id'] == CASE_ID)
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-provider-owner-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_parameters'], ['profile', 'binary', 'binary_sha256',
                                                        'cli_binary', 'cli_binary_sha256',
                                                        'state_dir', 'run_id',
                                                        'confirm_run_id', 'allow_write'])
        self.assertEqual(row['source_map'], 'provider-owner-isolation-migration-contract.json')
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(row['remote_validation'], 'NOT_RUN')

    def test_frozen_binding_declares_the_unmapped_backlog_parent(self):
        binding = provider_owner_source_binding()
        self.assertEqual(binding['parent'], 'story_provider_owner_isolation')
        self.assertEqual(binding['environment'], {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'})


if __name__ == '__main__':
    unittest.main()
