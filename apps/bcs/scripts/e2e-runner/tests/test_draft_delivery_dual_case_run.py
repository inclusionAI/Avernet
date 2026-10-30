"""Contract, profile, fail-closed and CLI registration tests for the dual case."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcs_e2e.draft_delivery.dual_case_run as dual
from bcs_e2e.cli import main
from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.draft_delivery import contract
from bcs_e2e.draft_delivery.checks import verify_all_obligations
from bcs_e2e.draft_delivery.contract import (PARENT, REQUIREMENTS, STORY_CHECKS)
from bcs_e2e.draft_delivery.dual_case_run import (
    CASE_ID, CASE_KEYS, DEFAULT_LEAF_ROOT, OBLIGATION_CHECKS, PreprodClosedError,
    PROFILE_CONTRACT, STORY_SOURCE, _bounded_environment, _failure_label, _launch_preprod_story,
    _local_execution, _observed_status, _parse_story_marker, _preprod_argv,
    _preprod_execution, _write_preprod_leaf, dual_draft_delivery_case_run,
    draft_delivery_adaptability, load_draft_delivery_profile, validate_draft_delivery_profile,
)
from bcs_e2e.inventory import load_executor_migration
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError, read

RUN_ID = '20261031-migration-wpe3-draft-delivery-test'
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
COOKIE_CANARY = 'synthetic-cookie-value-never-persisted'
DRIVER_CANARY = 'synthetic-driver-token-never-persisted'
DRAFT_ID = '750c52c6-60e2-4249-8ee5-1fb8cb9402ab'
CONTENT_HASH = '761dc9bc0f748c06ee5152a6ea9dc8bb1c1bba5e385c001930a26c17be43e7b7'
UPDATED_HASH = 'a' * 64
GOOD_MARKER = ('Draft delivery story: ' + str(STORY_CHECKS)
               + ' live HTTP assertions passed').encode()


def local_profile() -> dict:
    return {
        'schema_version': 1,
        'contract': PROFILE_CONTRACT,
        'environment': {'tier': 'local', 'id': 'draft-delivery-local',
                        'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'owned-local'},
        'human': {'user_id': 'e2ehumanabcdef0123456789'},
        'auth': {'kind': 'owned-local'},
        'cli': {'transport': 'owned-local-story', 'command_family': 'collaboration-draft',
                'api_face': 'legacy'},
        'policy': {'exclusive_use_confirmed': True, 'timeout_seconds': 60,
                   'max_response_bytes': 1048576, 'failure_retention_hours': 24},
    }


def preprod_profile() -> dict:
    value = local_profile()
    value['environment'] = {'tier': 'preprod', 'id': '20261031-wpe3-draft-pre',
                            'expected_bcs_env': 'pre'}
    value['entrypoint'] = {'kind': 'bcs-direct', 'origin': 'https://bcs-pre.alipay.com',
                           'api_face': 'legacy', 'manifest_face': 'legacy'}
    value['human'] = {'user_id': '39464'}
    value['auth'] = {'kind': 'preprod-driver-bot-bearer',
                     'header_env': {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}}
    value['cli'] = {'transport': 'product-cli-http', 'command_family': 'collaboration-draft',
                    'api_face': 'legacy'}
    value['policy'] = {'exclusive_use_confirmed': True, 'timeout_seconds': 90,
                       'max_response_bytes': 65536, 'failure_retention_hours': 168}
    return value


def obligation_records() -> dict:
    reference = {'draft_id': DRAFT_ID, 'revision': 1, 'schemaVersion': 2,
                 'card_token_field_present': True, 'card_token_field_length': 43,
                 'http_draft_id': DRAFT_ID, 'http_revision': 1}
    records = {
        'DRAFT-DELIVERY.offline-preview-schema1-snapshot':
            {'transport': 'offline-cli', 'exit_code': 0, 'snapshot_present': True},
        'DRAFT-DELIVERY.offline-preview-retains-schema1':
            {'transport': 'offline-cli', 'schema_version': 1},
        'DRAFT-DELIVERY.offline-preview-preserves-bytes':
            {'transport': 'offline-cli', 'definition_yaml_bytes': 2174,
             'previewed_bytes_equal': True},
        'DRAFT-DELIVERY.author-upload-incident-file':
            {'transport': 'product-cli', 'exit_code': 0, 'saved': True, 'phase': 'ready',
             'draft_id': DRAFT_ID, 'create_stdout_token_field_present': False},
        'DRAFT-DELIVERY.saved-source-2174-bytes':
            {'transport': 'product-cli', 'size_bytes': 2174},
        'DRAFT-DELIVERY.saved-source-validates':
            {'transport': 'product-cli', 'valid': True},
        'DRAFT-DELIVERY.first-revision-immutable':
            {'transport': 'product-cli', 'create_revision': 1, 'replay_exit_code': 0,
             'replay_revision': 1, 'replay_saved': True, 'replay_matched_existing': False,
             'replay_content_hash': CONTENT_HASH, 'conflict_exit_code': 1,
             'conflict_http_status': 409, 'history_revision': 1, 'history_size_bytes': 2174,
             'history_content_hash': CONTENT_HASH},
        'DRAFT-DELIVERY.author-reads-metadata':
            {'transport': 'product-http', 'status': 200},
        'DRAFT-DELIVERY.metadata-head-revision-one':
            {'transport': 'product-http', 'head_revision': 1, 'deleted': False},
        'DRAFT-DELIVERY.card-holder-tokenless-read':
            {'transport': 'product-http', 'status': 200},
        'DRAFT-DELIVERY.shared-read-preserves-hash':
            {'transport': 'product-http', 'content_hash': CONTENT_HASH, 'size_bytes': 2174},
        'DRAFT-DELIVERY.shared-source-no-store':
            {'transport': 'product-http', 'cache_control': 'no-store'},
        'DRAFT-DELIVERY.card-holder-validates-source':
            {'transport': 'product-http', 'status': 200},
        'DRAFT-DELIVERY.shared-validation-binds-rev1':
            {'transport': 'product-http', 'revision': 1, 'valid': True},
        'DRAFT-DELIVERY.public-cannot-delete':
            {'transport': 'product-http', 'public_delete_status': 401,
             'inert_delete_status': 401, 'inert_update_status': 401,
             'head_after': 1, 'deleted_after': False},
        'DRAFT-DELIVERY.author-download-fixed-bytes':
            {'transport': 'product-cli', 'exit_code': 0, 'output_path_in_receipt': True},
        'DRAFT-DELIVERY.download-matches-incident-bytes':
            {'transport': 'product-cli', 'bytes_equal': True, 'size_bytes': 2174},
        'DRAFT-DELIVERY.author-validates-fixed-revision':
            {'transport': 'product-cli', 'exit_code': 0, 'valid': True},
        'DRAFT-DELIVERY.author-appends-revision':
            {'transport': 'product-cli', 'exit_code': 0, 'saved': True, 'phase': 'ready'},
        'DRAFT-DELIVERY.update-advances-exactly-once':
            {'transport': 'product-cli', 'revision': 2, 'head_after': 2,
             'latest_content_hash': UPDATED_HASH},
        'DRAFT-DELIVERY.author-reissues-historical-card':
            {'transport': 'product-cli', 'exit_code': 0, 'phase': 'ready',
             'reference': copy.deepcopy(reference)},
        'DRAFT-DELIVERY.historical-preview-original-bytes':
            {'transport': 'product-cli', 'size_bytes': 2174},
        'DRAFT-DELIVERY.reissued-card-fixed-version':
            {'transport': 'product-cli', 'reference_revision': 1,
             'reference_schema_version': 2, 'other_revision_card_revision': 2},
        'DRAFT-DELIVERY.stale-write-rejected':
            {'transport': 'product-http', 'status': 409, 'error': 'DRAFT_CONFLICT',
             'head_after': 2, 'latest_content_hash': UPDATED_HASH},
        'DRAFT-DELIVERY.revoke-second-revision':
            {'transport': 'product-cli', 'exit_code': 0, 'revoked': True},
        'DRAFT-DELIVERY.revoke-idempotent':
            {'transport': 'product-cli', 'exit_code': 0, 'revoked': True},
        'DRAFT-DELIVERY.revoked-card-unavailable':
            {'transport': 'product-http', 'status': 404,
             'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
        'DRAFT-DELIVERY.old-shared-revision-readable':
            {'transport': 'product-http', 'status': 200, 'content_hash': CONTENT_HASH,
             'size_bytes': 2174, 'cache_control': 'no-store'},
        'DRAFT-DELIVERY.author-deletes-draft':
            {'transport': 'product-cli', 'exit_code': 0, 'deleted': True},
        'DRAFT-DELIVERY.deletion-revokes-all-cards':
            {'transport': 'product-http', 'revision_one_status': 404,
             'revision_two_status': 404, 'metadata_status': 200, 'deleted_flag': True},
        'DRAFT-DELIVERY.late-create-cannot-revive':
            {'transport': 'product-http', 'method': 'PUT', 'status': 404,
             'error': 'DRAFT_REFERENCE_UNAVAILABLE'},
        'DRAFT-DELIVERY.journey-completes':
            {'transport': 'product-http', 'responses_no_store': True,
             'status_matrix': [200, 401, 404, 409], 'token_canary_free': True},
    }
    assert set(records) == {obligation for obligation, _ in OBLIGATION_CHECKS}
    return records


def bearer_evidence() -> dict:
    return {'schema_version': 2, 'draft_id': DRAFT_ID, 'identity_mode': 'bearer',
            'fixture': {'content_hash': CONTENT_HASH, 'updated_content_hash': UPDATED_HASH,
                        'size_bytes': 2174},
            'author': {'uuid': None, 'resolved': True},
            'obligations': obligation_records(),
            'responses': [{'method': 'GET', 'path': '/collaboration/drafts/' + DRAFT_ID,
                           'status': 200, 'cache_control': 'no-store'}],
            'public_v1_face': {'read_envelope_code': 'DRAFT_REFERENCE_UNAVAILABLE',
                               'read_envelope_message': 'Draft reference is unavailable',
                               'validate_envelope_code': 'DRAFT_REFERENCE_UNAVAILABLE',
                               'legacy_tokenless_read_error': 'DRAFT_REFERENCE_UNAVAILABLE'}}


def local_result(*, observed: int = STORY_CHECKS) -> dict:
    return {
        'status': 'LOCAL_DRAFT_DELIVERY_PASS',
        'checks': {'status': 'PASS', 'checked': list(REQUIREMENTS)},
        'child_receipt': {'observed_checks': observed},
        'canonical_summary': {'status': 'LOCAL_SUITE_PASS'},
    }


def executable_binary(root: Path, name: str = 'bcs-fixture') -> tuple[Path, str]:
    binary = root / name
    binary.write_bytes(b'owned-bcs-fixture-bytes')
    binary.chmod(0o700)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


class DraftDeliveryDualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary, self.sha = executable_binary(self.root)
        self.cli_binary, self.cli_sha = executable_binary(self.root, name='bcs-cli-fixture')

    def _write_profile(self, value: dict, name: str) -> Path:
        path = self.root / f'profile-{name}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    # ------------------------------------------------------------- profiles
    def test_both_tier_profiles_validate(self):
        self.assertEqual(validate_draft_delivery_profile(local_profile())['environment']['tier'],
                         'local')
        validated = validate_draft_delivery_profile(preprod_profile())
        self.assertEqual(validated['environment']['tier'], 'preprod')
        self.assertEqual(validated['auth']['header_env'],
                         {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
        self.assertEqual(load_draft_delivery_profile(
            self._write_profile(preprod_profile(), 'valid'))['environment']['id'],
            '20261031-wpe3-draft-pre')

    def test_profile_rejects_shape_placeholders_and_face_drift(self):
        cases = (
            ('unknown-top-level', lambda value: value.__setitem__('credential_files', {})),
            ('schema-version', lambda value: value.__setitem__('schema_version', 2)),
            ('contract', lambda value: value.__setitem__('contract', 'wrong')),
            ('tier', lambda value: value['environment'].__setitem__('tier', 'main')),
            ('bcs-env-binding', lambda value: value['environment'].__setitem__(
                'expected_bcs_env', 'prod')),
            ('local-entrypoint-kind', lambda value: value['entrypoint'].__setitem__('kind', 'bcs-direct')),
            ('preprod-entrypoint-kind', lambda value: value.update(
                {'environment': {'tier': 'preprod', 'id': 'x', 'expected_bcs_env': 'pre'},
                 'entrypoint': {'kind': 'owned-local'}, 'auth': {'kind': 'owned-local'},
                 'cli': {'transport': 'product-cli-http', 'command_family': 'collaboration-draft',
                         'api_face': 'legacy'}})),
            ('preprod-origin-face', lambda value: value['entrypoint'].__setitem__('api_face', 'public-v1')),
            ('human', lambda value: value['human'].__setitem__('user_id', 'user name')),
            ('auth-kind-swap', lambda value: value['auth'].__setitem__('kind', 'owned-local')),
            ('header-env-missing-cookie', lambda value: value['auth']['header_env'].pop('Cookie')),
            ('header-env-lowercase-ref', lambda value: value['auth']['header_env'].__setitem__(
                'Authorization', 'bcs_e2e_driver_token')),
            ('header-env-invalid-ref', lambda value: value['auth']['header_env'].__setitem__(
                'Authorization', 'not-a-valid-env-ref')),
            ('cli-transport-swap', lambda value: value['cli'].__setitem__(
                'transport', 'owned-local-story')),
            ('cli-family', lambda value: value['cli'].__setitem__(
                'command_family', 'collaboration-card')),
            ('cli-face', lambda value: value['cli'].__setitem__('api_face', 'public-v1')),
            ('policy-exclusive', lambda value: value['policy'].__setitem__(
                'exclusive_use_confirmed', False)),
            ('policy-timeout-low', lambda value: value['policy'].__setitem__('timeout_seconds', 0)),
            ('policy-timeout-high', lambda value: value['policy'].__setitem__('timeout_seconds', 121)),
            ('policy-bytes', lambda value: value['policy'].__setitem__('max_response_bytes', 0)),
            ('policy-retention', lambda value: value['policy'].__setitem__(
                'failure_retention_hours', 169)),
        )
        for name, mutate in cases:
            with self.subTest(mutation=name):
                value = copy.deepcopy(preprod_profile() if name != 'local-entrypoint-kind'
                                      else local_profile())
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_draft_delivery_profile(value)

    # ----------------------------------------------------------------- seams
    def test_preprod_argv_keeps_credentials_out_of_the_command_line(self):
        server = self.root / 'bcs-server'
        cli_binary = self.root / 'bcs-cli'
        evidence = self.root / 'evidence.json'
        argv = _preprod_argv(base_url='https://bcs-pre.alipay.com', human='39464',
                             cli_binary=cli_binary, evidence_path=evidence)
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(Path(argv[1]).name == 'story.py' and STORY_SOURCE.is_file())
        self.assertIn('--identity-mode', argv)
        self.assertEqual(argv[argv.index('--identity-mode') + 1], 'bearer')
        self.assertEqual(argv[argv.index('--bearer-token-env') + 1], DRIVER_ENV)
        self.assertIn('--cookie-env', argv)
        self.assertEqual(argv[argv.index('--cookie-env') + 1],
                         'BCS_E2E_HUMAN_COOKIE')
        self.assertIn(str(cli_binary), argv)
        self.assertNotIn(str(server), argv)
        self.assertIn(str(evidence), argv)
        joined = ' '.join(str(item) for item in argv)
        self.assertNotIn(DRIVER_CANARY, joined)
        self.assertNotIn(COOKIE_CANARY, joined)

    def test_bounded_environment_resolves_only_the_named_references(self):
        with patch.dict(os.environ, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: COOKIE_CANARY}):
            child = _bounded_environment({'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
        self.assertEqual(child[DRIVER_ENV], DRIVER_CANARY)
        self.assertEqual(child[COOKIE_ENV], COOKIE_CANARY)

    def test_bounded_environment_fail_closes_on_missing_or_unsafe_credentials(self):
        cases = ({}, {DRIVER_ENV: ''}, {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: 'bad\ncookie'},
                 {DRIVER_ENV: DRIVER_CANARY, COOKIE_ENV: 'x' * 9000})
        for environment in cases:
            with self.subTest(environment=sorted(environment)):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(PreprodClosedError) as ctx:
                        _bounded_environment({'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})
                self.assertEqual(ctx.exception.reason_class,
                                 'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE')
                self.assertIsNone(ctx.exception.observed_status)

    def test_story_diagnosis_parses_status_and_label(self):
        self.assertEqual(_observed_status('nothing'), None)
        self.assertEqual(_observed_status('unexpected HTTP status 409'), 409)
        label = _failure_label('Draft delivery story failed: stale writer: unexpected HTTP status 409\n')
        self.assertEqual(label, 'stale writer: unexpected HTTP status 409')
        self.assertEqual(_failure_label('no label here'), '')
        self.assertEqual(_parse_story_marker(GOOD_MARKER.decode()), (True, STORY_CHECKS))
        self.assertEqual(_parse_story_marker(GOOD_MARKER), (True, STORY_CHECKS))

    # --------------------------------------------------------- executions
    def test_local_execution_admits_only_the_complete_child_receipt(self):
        execution = _local_execution(local_result())
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, len(REQUIREMENTS))
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        self.assertTrue(all(step.status == 'PASS' for step in execution.steps))
        execution.validate()
        for observed in (STORY_CHECKS - 1, 0):
            blocked = _local_execution(local_result(observed=observed))
            self.assertEqual(blocked.status, 'BLOCKED')
            self.assertEqual(blocked.completed_count, 0)
            self.assertEqual(blocked.error_type, 'LOCAL_DRAFT_DELIVERY_BLOCKED')
            self.assertTrue(all(step.status == 'NOT_RUN' for step in blocked.steps))

    def test_preprod_execution_fail_fasts_on_violations(self):
        evidence = bearer_evidence()
        execution = _preprod_execution(evidence, blocked_error='PREPROD_BLOCKED')
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 32)
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        verify_all_obligations(evidence)
        evidence['obligations']['DRAFT-DELIVERY.shared-source-no-store']['cache_control'] = \
            'max-age=60'
        blocked = _preprod_execution(evidence, blocked_error='PREPROD_DRAFT_DELIVERY_STORY_BLOCKED')
        self.assertEqual(blocked.status, 'BLOCKED')
        self.assertEqual(blocked.completed_count, 11)
        self.assertEqual(blocked.steps[11].status, 'NOT_RUN')
        self.assertEqual(blocked.steps[11].error_type,
                         'PREPROD_DRAFT_DELIVERY_OBLIGATION_VIOLATION')
        self.assertTrue(all(step.status == 'NOT_RUN' for step in blocked.steps[12:]))
        incomplete = bearer_evidence()
        incomplete['obligations'].pop('DRAFT-DELIVERY.journey-completes')
        partial = _preprod_execution(incomplete, blocked_error='PREPROD_DRAFT_DELIVERY_STORY_BLOCKED')
        self.assertEqual(partial.status, 'BLOCKED')
        self.assertEqual(partial.completed_count, 0)
        self.assertEqual(partial.steps[0].error_type, 'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED')

    # --------------------------------------------------------- preprod leg
    def _state(self, name: str) -> Path:
        state = self.root / name
        state.mkdir()
        return state

    def _launch_stub(self, *, exit_code=0, stdout=GOOD_MARKER, stderr=b'', timed_out=False,
                     evidence=None):
        def stub(*_args, **_kwargs):
            return {'exit_code': exit_code, 'timed_out': timed_out,
                    'duration_seconds': 0.01, 'stdout': stdout, 'stderr': stderr}
        return stub

    def test_preprod_leg_passes_with_the_frozen_bearer_receipt(self):
        state = self._state('leg-pass')
        evidence_path = state / 'draft-delivery-preprod-evidence.json'
        evidence_path.write_text(json.dumps(bearer_evidence()), encoding='utf-8')
        profile = validate_draft_delivery_profile(preprod_profile())
        with patch.object(dual, '_launch_preprod_story', side_effect=self._launch_stub()):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, cli_binary=self.cli_binary, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 32)
        self.assertEqual(receipt['identity_mode'], 'bearer')
        self.assertEqual(receipt['observed_checks'], STORY_CHECKS)
        self.assertEqual(receipt['redaction_status'], 'PASS')
        self.assertEqual(receipt['checks_status'], 'PASS')
        self.assertIsNone(blocker)
        self.assertNotIn(DRIVER_CANARY, json.dumps(receipt))

    def test_preprod_leg_classifies_every_fail_closed_reason(self):
        state = self._state('leg-classify')
        evidence_path = state / 'draft-delivery-preprod-evidence.json'
        evidence_path.write_text(json.dumps(bearer_evidence()), encoding='utf-8')
        profile = validate_draft_delivery_profile(preprod_profile())
        scenarios = (
            ('deadline', self._launch_stub(timed_out=True),
             'PREPROD_DRAFT_DELIVERY_STORY_DEADLINE'),
            ('blocked-exit', self._launch_stub(exit_code=1, stdout=b'',
                                               stderr=b'Draft delivery story failed: '
                                                        b'stale writer: unexpected HTTP status 409'),
             'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED'),
            ('marker-short', self._launch_stub(
                stdout=b'Draft delivery story: 57 live HTTP assertions passed'),
             'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED'),
            ('redaction', self._launch_stub(
                stdout=GOOD_MARKER + b' leaked readToken value'),
             'PREPROD_DRAFT_DELIVERY_REDACTION_GATE'),
        )
        for name, stub, reason in scenarios:
            with self.subTest(reason=name):
                with patch.object(dual, '_launch_preprod_story', side_effect=stub):
                    execution, receipt, blocker = dual._preprod_leg(
                        profile=profile, cli_binary=self.cli_binary, state_dir=state, run_id=RUN_ID)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(blocker['reason_class'], reason)
                self.assertEqual(receipt['checks_status'], 'BLOCKED')
                self.assertIn('argv', receipt)
        evidence_path.unlink()
        violating = bearer_evidence()
        violating['obligations']['DRAFT-DELIVERY.shared-source-no-store']['cache_control'] = \
            'max-age=60'
        evidence_path.write_text(json.dumps(violating), encoding='utf-8')
        with patch.object(dual, '_launch_preprod_story', side_effect=self._launch_stub()):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, cli_binary=self.cli_binary, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(blocker['reason_class'], 'PREPROD_DRAFT_DELIVERY_OBLIGATION_VIOLATION')
        self.assertTrue(blocker['observed_status'] is None)

    def test_preprod_leg_fail_closes_when_the_story_cannot_start(self):
        state = self._state('leg-closed')
        profile = validate_draft_delivery_profile(preprod_profile())
        closed = PreprodClosedError('PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE',
                                    'environment reference ' + DRIVER_ENV + ' is not loaded')
        with patch.object(dual, '_launch_preprod_story', side_effect=closed):
            execution, receipt, blocker = dual._preprod_leg(
                profile=profile, cli_binary=self.cli_binary, state_dir=state, run_id=RUN_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.error_type, 'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(receipt['checks_status'], 'BLOCKED')
        self.assertEqual(blocker['reason_class'], 'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE')
        self.assertIn('argv', receipt)

    def test_preprod_evidence_leaf_is_machine_readable_and_credential_free(self):
        state = self._state('leaf')
        leaf_root = state / 'leaves'
        report = {'status': 'BLOCKED', 'schema_version': dual.SCHEMA_VERSION,
                  'error_type': 'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED',
                  'execution': {'completed_count': 31, 'total_count': len(REQUIREMENTS),
                                'status': 'BLOCKED'},
                  'preprod_blocker': {
            'reason_class': 'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED', 'observed_status': 409,
            'failure_label': 'stale writer: unexpected HTTP status 409', 'reason': 'detail',
            'story_exit_code': 1, 'story_timed_out': False, 'observed_checks': 31},
                  'profile_digest': 'digest', 'executor_digest': 'executor',
                  'bcs_binary_sha256': 'bcs-digest', 'cli_binary_sha256': 'cli-digest',
            'environment': {'tier': 'preprod', 'id': RUN_ID, 'expected_bcs_env': 'pre',
                            'entrypoint_kind': 'bcs-direct'}}
        receipt = {'exit_code': 1, 'observed_checks': 31, 'timed_out': False,
                   'duration_seconds': 1.0, 'stderr': b'clean child stderr'}
        root = dual._write_preprod_leaf(run_id=RUN_ID, report=report,
                                        story_receipt=receipt, leaf_root=leaf_root)
        self.assertEqual(root, leaf_root)
        slug = RUN_ID.replace('/', '_')
        for name in (slug + '-stdout.json', slug + '-verdict.json', slug + '-stderr.txt'):
            self.assertTrue((leaf_root / name).is_file(), name)
        verdict = read(leaf_root / (slug + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['error_type'], 'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED')
        self.assertEqual(verdict['reason'], 'detail')
        self.assertEqual(verdict['observed_status'], 409)
        self.assertEqual(verdict['story_checks'], STORY_CHECKS)
        self.assertEqual(verdict['obligations_total'], len(REQUIREMENTS))
        self.assertEqual(verdict['obligations_completed'], 31)
        self.assertEqual(verdict['bcs_binary_sha256'], 'bcs-digest')
        self.assertEqual(verdict['cli_binary_sha256'], 'cli-digest')
        self.assertEqual(verdict['remote_validation'], 'NOT_RUN')
        self.assertFalse(verdict['grants_release_credit'])
        stdout_leaf = read(leaf_root / (slug + '-stdout.json'))
        self.assertEqual(stdout_leaf['status'], 'BLOCKED')
        combined = (leaf_root / (slug + '-stdout.json')).read_text(encoding='utf-8') + \
            (leaf_root / (slug + '-verdict.json')).read_text(encoding='utf-8') + \
            (leaf_root / (slug + '-stderr.txt')).read_text(encoding='utf-8')
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)
        dirty = self._state('leaf-dirty') / 'leaves'
        receipt_dirty = dict(receipt, stderr=b'leaked Bearer driver secret')
        dual._write_preprod_leaf(run_id=RUN_ID + '-dirty', report=report,
                                 story_receipt=receipt_dirty, leaf_root=dirty)
        stderr_text = (dirty / (RUN_ID + '-dirty-stderr.txt')).read_text(encoding='utf-8')
        self.assertIn('redaction gate refused', stderr_text)
        self.assertEqual(DEFAULT_LEAF_ROOT.name, '20261031-migration-wpe3-draft-delivery-preprod')

    # ------------------------------------------------------------- entry
    def test_local_tier_reuses_the_owned_local_receipt(self):
        profile_path = self._write_profile(local_profile(), 'local')
        with patch('bcs_e2e.draft_delivery.run.local_draft_delivery_run',
                   return_value=local_result()) as execute:
            report = dual_draft_delivery_case_run(
                profile_path=profile_path, state_dir=self.root / 'entry-local',
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                binary=self.binary, binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha,
                leaf_root=self.root / 'leaves')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['local_owned_run']['status'], 'LOCAL_DRAFT_DELIVERY_PASS')
        self.assertEqual(report['local_owned_run']['checks'], 'PASS')
        self.assertEqual(report['execution']['completed_count'], 32)
        self.assertEqual(report['obligation_summary']['story_checks'], STORY_CHECKS)
        self.assertEqual(report['resource_disposition'], 'DISPOSABLE_DRAFT_TOMBSTONED')
        self.assertEqual(report['credential_kind'], 'owned_local_ephemeral_principal')
        self.assertEqual(report['bcs_binary_sha256'], self.sha)
        self.assertEqual(report['cli_binary_sha256'], self.cli_sha)
        self.assertEqual(report['remote_validation'], 'NOT_RUN')
        self.assertFalse(report['grants_release_credit'])
        self.assertNotIn('preprod_blocker', report)
        execute.assert_called_once()
        written = ''.join(path.read_text(encoding='utf-8')
                          for path in (self.root / 'entry-local').glob('*.json'))
        self.assertNotIn(DRIVER_CANARY, written)
        self.assertNotIn(COOKIE_CANARY, written)
        read(self.root / 'entry-local' / 'draft-delivery-dual-run.json')

    def test_local_tier_blocks_on_an_incomplete_child_receipt(self):
        self._write_profile(local_profile(), 'local')
        with patch('bcs_e2e.draft_delivery.run.local_draft_delivery_run',
                   return_value=local_result(observed=STORY_CHECKS - 1)):
            report = dual_draft_delivery_case_run(
                profile_path=self.root / 'profile-local.json',
                state_dir=self.root / 'entry-local-short', run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID, binary=self.binary,
                binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha, leaf_root=self.root / 'leaves')
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['execution']['completed_count'], 0)

    def test_preprod_tier_passes_without_writing_a_blocked_leaf(self):
        profile_path = self._write_profile(preprod_profile(), 'preprod')
        execution = _local_execution(local_result())
        receipt = {'exit_code': 0, 'timed_out': False, 'duration_seconds': 1.0,
                   'observed_checks': STORY_CHECKS, 'markers': {'draft-delivery-compliance': True},
                   'argv': [sys.executable, str(STORY_SOURCE)], 'identity_mode': 'bearer',
                   'redaction_status': 'PASS', 'checks_status': 'PASS'}
        leaves = self.root / 'leaves-preprod-pass'
        with patch.object(dual, '_preprod_leg', return_value=(execution, receipt, None)):
            report = dual_draft_delivery_case_run(
                profile_path=profile_path, state_dir=self.root / 'entry-preprod',
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                binary=self.binary, binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha, leaf_root=leaves)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['credential_kind'], 'preprod_driver_bot_bearer')
        self.assertEqual(report['environment']['entrypoint_kind'], 'bcs-direct')
        self.assertEqual(report['execution']['completed_count'], 32)
        self.assertNotIn('preprod_blocker', report)
        self.assertNotIn('preprod_evidence_leaf', report)
        self.assertFalse(leaves.exists())
        report_back = read(self.root / 'entry-preprod' / 'draft-delivery-dual-run.json')
        self.assertEqual(report_back['status'], 'PASS')

    def test_preprod_tier_writes_the_blocked_evidence_leaf(self):
        profile_path = self._write_profile(preprod_profile(), 'preprod')
        execution = _preprod_execution(bearer_evidence(), blocked_error='BLOCKED')
        execution = dual.CaseExecution(case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
                                       steps=execution.steps, completed_count=0,
                                       total_count=len(REQUIREMENTS),
                                       error_type='PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE',
                                       ready_to_execute=False)
        receipt = {'exit_code': None, 'timed_out': False, 'duration_seconds': 0.0,
                   'observed_checks': 0, 'markers': {'draft-delivery-compliance': False},
                   'argv': [sys.executable], 'identity_mode': 'bearer', 'checks_status': 'BLOCKED'}
        blocker = {'reason_class': 'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE',
                   'observed_status': None, 'failure_label': '',
                   'reason': 'environment reference ' + DRIVER_ENV + ' is not loaded',
                   'story_exit_code': None, 'story_timed_out': False, 'observed_checks': 0,
                   'credential_refs': ['Authorization', 'Cookie']}
        leaves = self.root / 'leaves-preprod-blocked'
        with patch.object(dual, '_preprod_leg', return_value=(execution, receipt, blocker)):
            report = dual_draft_delivery_case_run(
                profile_path=profile_path, state_dir=self.root / 'entry-preprod-blocked',
                run_id=RUN_ID + '-blocked', allow_write=True, confirm_run_id=RUN_ID + '-blocked',
                binary=self.binary, binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha, leaf_root=leaves)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['preprod_blocker']['reason_class'],
                         'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(report['preprod_blocker']['credential_refs'],
                         ['Authorization', 'Cookie'])
        self.assertEqual(report['environment']['api_face'], 'legacy')
        leaf = Path(report['preprod_evidence_leaf'])
        self.assertTrue(leaf.is_dir())
        slug = (RUN_ID + '-blocked').replace('/', '_')
        verdict = read(leaf / (slug + '-verdict.json'))
        self.assertEqual(verdict['status'], 'BLOCKED')
        self.assertEqual(verdict['error_type'], 'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(verdict['story_checks'], STORY_CHECKS)
        combined = ''.join(path.read_text(encoding='utf-8') for path in leaf.iterdir())
        self.assertNotIn(DRIVER_CANARY, combined)
        self.assertNotIn(COOKIE_CANARY, combined)

    def test_entry_fail_closes_on_lift_time_preprod_errors(self):
        profile_path = self._write_profile(preprod_profile(), 'error')
        closed = PreprodClosedError('PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE',
                                    'environment reference ' + DRIVER_ENV
                                    + ' for header Authorization is not loaded')
        with patch.object(dual, '_preprod_leg', side_effect=closed):
            report = dual_draft_delivery_case_run(
                profile_path=profile_path, state_dir=self.root / 'entry-closed',
                run_id=RUN_ID + '-closed', allow_write=True,
                confirm_run_id=RUN_ID + '-closed', binary=self.binary,
                binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha, leaf_root=self.root / 'leaves-closed')
        self.assertEqual(report['status'], 'BLOCKED')
        execution = report['case']
        self.assertEqual(execution['status'], 'BLOCKED')
        self.assertEqual(execution['error_type'],
                         'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE')
        self.assertEqual(execution['completed_count'], 0)
        self.assertEqual(report['preprod_blocker']['credential_refs'],
                         ['Authorization', 'Cookie'])
        leaf = Path(report['preprod_evidence_leaf'])
        stdout_leaf = read(leaf / ((RUN_ID + '-closed') + '-stdout.json'))
        self.assertEqual(stdout_leaf['status'], 'BLOCKED')
        self.assertEqual(stdout_leaf['preprod_blocker']['reason_class'],
                         'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE')

    def test_entry_enforces_write_gates_and_binary_digests(self):
        profile_path = self._write_profile(local_profile(), 'gates')
        base = dict(profile_path=profile_path, state_dir=self.root / 'gates-state',
                    run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                    binary=self.binary, binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha,
                    leaf_root=self.root / 'leaves-gates')
        with self.assertRaisesRegex(ConfigError, 'exact Run confirmation'):
            dual_draft_delivery_case_run(**{**base, 'confirm_run_id': 'other'})
        with self.assertRaisesRegex(ConfigError, 'write permission'):
            dual_draft_delivery_case_run(**{**base, 'allow_write': False})
        existing = self.root / 'existing-state'
        existing.mkdir()
        with self.assertRaisesRegex(ConfigError, 'fresh private state directory'):
            dual_draft_delivery_case_run(**{**base, 'state_dir': existing})
        wrong = self.root / 'wrong'
        wrong.write_bytes(b'other')
        wrong.chmod(0o700)
        with self.assertRaisesRegex(RunStateError, 'digest differs'):
            dual_draft_delivery_case_run(**{**base, 'binary': wrong,
                                            'binary_sha256': '0' * 64})
        wrong_cli = self.root / 'wrong-cli'
        wrong_cli.write_bytes(b'other-cli')
        wrong_cli.chmod(0o700)
        with self.assertRaisesRegex(RunStateError, 'digest differs'):
            dual_draft_delivery_case_run(**{**base, 'cli_binary': wrong_cli,
                                            'cli_binary_sha256': '0' * 64})

    def test_local_leg_failure_must_not_raise_into_the_report(self):
        profile_path = self._write_profile(local_profile(), 'raising')
        with patch('bcs_e2e.draft_delivery.run.local_draft_delivery_run',
                   side_effect=RunStateError('owned runtime exploded')):
            with self.assertRaisesRegex(ConfigError, 'unavailable'):
                dual_draft_delivery_case_run(
                    profile_path=profile_path, state_dir=self.root / 'entry-raise',
                    run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
                    binary=self.binary, binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha,
                    leaf_root=self.root / 'leaves-raise')

    # ------------------------------------------------------- registration
    def test_migration_plan_and_cli_expose_the_dual_case(self):
        self.assertEqual(CASE_KEYS, ('draft-delivery',))
        self.assertIn('dual-draft-delivery-run', CASE_COMMANDS)
        executor = load_executor_migration()[PARENT]
        self.assertEqual(executor['mode'], 'DUAL')
        self.assertEqual(executor['preprod_verdict'], 'NOT_RUN')
        plan = migration_plan()
        row = next(row for row in plan['local_backlog_case_slices']
                   if row['case_id'] == 'LOCAL-DRAFT-DELIVERY-01')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-draft-delivery-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_assertion_count'], 32)
        self.assertEqual(row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
            'state_dir', 'run_id', 'confirm_run_id', 'allow_write'])
        cross = {case['case_id'] for case in plan['cross_environment_product_cli_case_slices']}
        self.assertIn('DUAL-DRAFT-DELIVERY-01', cross)
        adaptability = draft_delivery_adaptability()
        self.assertEqual(adaptability['case_id'], 'DUAL-DRAFT-DELIVERY-01')
        self.assertEqual(adaptability['source_case_id'], contract.CASE_ID)
        self.assertEqual(adaptability['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(adaptability['required_assertion_count'], STORY_CHECKS)
        self.assertEqual(adaptability['environments'],
                         ['owned-local-full-story', 'preprod-full-story-bcs-direct-legacy'])
        self.assertFalse(adaptability['grants_release_credit'])

    def test_cli_dispatches_the_dual_command_with_the_frozen_signature(self):
        profile_path = self._write_profile(local_profile(), 'cli')
        arguments = [
            'dual-draft-delivery-run', '--profile', str(profile_path),
            '--state-dir', str(self.root / 'cli-state'), '--run-id', RUN_ID,
            '--confirm-run-id', RUN_ID, '--allow-write', '--binary',
            str(self.binary), '--binary-sha256', self.sha, '--cli-binary',
            str(self.cli_binary), '--cli-binary-sha256', self.cli_sha,
        ]
        with patch('bcs_e2e.cli.dual_draft_delivery_case_run',
                   return_value={'status': 'PASS'}) as execute, \
                redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path, state_dir=self.root / 'cli-state',
            run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID,
            binary=self.binary, binary_sha256=self.sha, cli_binary=self.cli_binary, cli_binary_sha256=self.cli_sha)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'PASS')

    def test_cli_parser_requires_both_runtime_binaries(self):
        profile_path = self._write_profile(local_profile(), 'parser')
        for omitted in (('--binary',), ('--binary-sha256',), ('--cli-binary',),
                        ('--cli-binary-sha256',)):
            with self.subTest(omitted=omitted):
                arguments = [
                    'dual-draft-delivery-run', '--profile', str(profile_path),
                    '--state-dir', str(self.root / 'parser-state'), '--run-id', RUN_ID,
                    '--confirm-run-id', RUN_ID, '--allow-write', '--binary',
                    str(self.binary), '--binary-sha256', self.sha, '--cli-binary',
                    str(self.cli_binary), '--cli-binary-sha256', self.cli_sha]
                index = arguments.index(omitted[0])
                arguments[index:index + 2] = []
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                    main(arguments)

    def test_dual_contract_constant_mapping_stays_frozen(self):
        self.assertEqual(dual.CASE_ID, 'DUAL-DRAFT-DELIVERY-01')
        self.assertEqual(dual.SUITE, 'dual-draft-delivery-v1')
        self.assertEqual(dual.SOURCE_CASE_ID, contract.CASE_ID)
        self.assertEqual(dual.PARENT, PARENT)
        self.assertEqual(dual.STORY_CHECKS, STORY_CHECKS)
        self.assertEqual(len(dual.REQUIREMENTS), 32)
        self.assertEqual([obligation for obligation, _ in dual.OBLIGATION_CHECKS],
                         list(REQUIREMENTS))
        self.assertEqual(dual._HEADER_ENV, {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV})


class DuoCookieAdmissionTests(unittest.TestCase):
    """Station-form duo credential on the Draft legacy face."""

    def test_bearer_child_attaches_cookie_beside_author_bearer(self):
        from unittest.mock import patch
        from bcs_e2e.draft_delivery.story import Story
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_TOKEN': 'author-canary',
                                       'BCS_E2E_HUMAN_COOKIE': 'cookie-canary'}):
            story = Story('https://bcs-pre.alipay.com', '39464',
                          Path('/tmp/draft-fixture.yaml'), Path('/tmp/bcs-cli'),
                          identity_mode='bearer', bearer_token_env='BCS_E2E_DRIVER_TOKEN',
                          cookie_env='BCS_E2E_HUMAN_COOKIE')
        headers = story._headers(token='author-canary')
        self.assertEqual(headers['Authorization'], 'Bearer author-canary')
        self.assertEqual(headers['Cookie'], 'cookie-canary')

    def test_register_mode_and_missing_cookie_never_attach(self):
        from bcs_e2e.draft_delivery.story import Story
        story = Story('http://127.0.0.1:1', '39464', Path('/tmp/f.yaml'),
                      Path('/tmp/bcs-cli'), identity_mode='register')
        self.assertNotIn('Cookie', story._headers(token='t'))
        bearer = Story('https://bcs-pre.alipay.com', '39464', Path('/tmp/f.yaml'),
                       Path('/tmp/bcs-cli'), identity_mode='bearer',
                       bearer_token_env='BCS_E2E_DRIVER_TOKEN', cookie_env='')
        self.assertNotIn('Cookie', bearer._headers(token='t'))


if __name__ == '__main__':
    unittest.main()
