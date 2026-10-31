"""Bot-authority user journey slice: guards, offline projection, live evidence."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_authority_contract import (
    CALLS, CASE_ID, COMPLETION, EXPECTED_HTTP_CALLS, JOURNAL, PRIMARY, REQUIREMENTS,
    STAFF_B, STAFF_MINE, STATUS, SUITE, authority_source_binding)
from bcs_e2e.local_authority_reporting import (
    FORBIDDEN_JOURNAL_SUBSTRINGS, OUTPUTS, SOURCES, STRICT_AUTH, _bound, _project,
    _render, build_authority_report, publish_authority_report)
from bcs_e2e.local_authority_run import (
    BINARY_KIND, EXPECTED_RESOURCES, FORBIDDEN_JOURNAL_SUBSTRINGS as RUN_FORBIDDEN,
    local_authority_run, validate_authority_binary)
from bcs_e2e.inventory import (
    KNOWN_UNMAPPED_LEGACY_IDS, current_manifest, executor_digest)
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import RunStateError, read

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
OWNER_HUMAN = 'e2ehumanoffline00000000'
OWNER_BOT = 'authority-owner-bot-fixed-uuid'
TARGET_BOT = 'authority-target-bot-fixed-uuid'
NEVER_USER = 'never-seen-offline-fixed'
NOW_EXPIRES = 1800000000000


def executable_binary(directory: Path) -> tuple[Path, str]:
    binary = directory / 'bcs'
    binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    binary.chmod(0o755)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def run_kwargs(state_dir: Path, binary: Path, digest: str, **changes) -> dict:
    base = dict(binary=binary, binary_sha256=digest, state_dir=state_dir,
                run_id='authority-test', confirm_run_id='authority-test',
                allow_local_processes=True, allow_fixture_write=True, allow_write=True)
    base.update(changes)
    return base


def synthetic_binding(executor: str, sha: str, run_id: str, root: Path) -> dict:
    source = authority_source_binding()
    return {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
            'run_id': run_id, 'executor_digest': executor, 'binary_kind': BINARY_KIND,
            'bcs_binary_sha256': sha, 'auto_build': False, 'store_messages': False,
            'source_binding': source, 'selected_requirement_ids': list(REQUIREMENTS),
            'legacy_inventory': current_manifest()['legacy_inventory'],
            'environment': source['environment'], 'runtime_dir': str(root.resolve())}


def synthetic_case(executor: str, binding: dict, *, http_call_count=EXPECTED_HTTP_CALLS,
                   created=None, never_user=NEVER_USER) -> dict:
    created = created if created is not None else [OWNER_BOT, TARGET_BOT]
    ctx = {'resources': {'created': created}, 'owner_human_id': OWNER_HUMAN,
           'never_user_id': never_user}
    case = {'schema_version': 1, 'run_id': binding['run_id'], 'case_id': CASE_ID,
            'status': STATUS, 'executor_digest': executor,
            'binding_digest': fingerprint(binding),
            'source_binding_digest': fingerprint(binding['source_binding']),
            'owner_human_id': OWNER_HUMAN, 'owner_actor_id': 'human_' + OWNER_HUMAN,
            'target_user_id': STAFF_B, 'target_actor_id': 'human_' + STAFF_B,
            'mine_user_id': STAFF_MINE, 'mine_actor_id': 'human_' + STAFF_MINE,
            'never_user_id': never_user, 'http_call_count': http_call_count,
            'resources': {'created': list(created), 'deleted': [], 'unknown': []},
            'resource_disposition': 'TWO_BOTS_CREATED_AND_RETAINED',
            'operations': [], 'assertions': []}
    for requirement in REQUIREMENTS:
        method, template, actor = PRIMARY[requirement]
        row = {'operation': requirement, 'method': method,
               'path': _render(template, ctx) if template else None,
               'actor': actor, 'status': 'VERIFIED', 'transitions': ['INTENDED', 'VERIFIED'],
               'calls': []}
        for index, (call_method, call_template, expect, label, query) in enumerate(CALLS[requirement]):
            row['calls'].append({'method': call_method, 'path': _render(call_template, ctx),
                                 'expect_status': expect, 'actor': label, 'query': query,
                                 'submitted_request_id': 'e2e-authority-' + uuid_hex(index, requirement),
                                 'status': 'VERIFIED', 'transitions': ['INTENDED', 'VERIFIED'],
                                 'http_status': expect,
                                 'observed': synthetic_call_observation(requirement, index, created)})
        row['observed'] = synthetic_operation_observation(requirement, created, case['owner_human_id'])
        case['operations'].append(row)
        case['assertions'].append({'id': requirement, 'status': 'PASS'})
    return case


def uuid_hex(index: int, requirement: str) -> str:
    return hashlib.sha256((requirement + ':' + str(index)).encode()).hexdigest()[:20]


def synthetic_call_observation(requirement: str, index: int, created: list):
    if requirement == 'AUTHORITY.trusted-registration':
        if index % 2 == 0:
            return {'token_minted': True}
        return {'bot_registered': True, 'bot_uuid': created[0] if index == 1 else created[1]}
    if requirement == 'AUTHORITY.ownership-read':
        return {'bot_id': created[0], 'owner_user_id': OWNER_HUMAN}
    if requirement == 'AUTHORITY.empty-manager-page':
        return {'total': 0}
    if requirement == 'AUTHORITY.owner-not-manager-polarity':
        return {'error_code': 'owner_not_manager'}
    if requirement == 'AUTHORITY.manager-add-list-remove-idempotent':
        return [{'added': 1}, {'total': 1, 'items': [STAFF_B]},
                {'removed': 1}, {'removed': 0}][index]
    if requirement == 'AUTHORITY.manager-restore':
        return {'added': 1}
    if requirement == 'AUTHORITY.mine-ensured-human-read':
        return {'mine_user_id': STAFF_MINE}
    if requirement == 'AUTHORITY.mine-ensured-manager-add':
        return {'added': 1}
    if requirement == 'AUTHORITY.never-seen-manager-unknown':
        return {'error_code': 'bot_authority_human_unknown'}
    if requirement == 'AUTHORITY.mine-ensured-manager-remove':
        return {'removed': 1}
    if requirement == 'AUTHORITY.transfer-initiate-refresh':
        return {'status': 'pending', 'target_owner_user_id': STAFF_B,
                'expires_at': NOW_EXPIRES} if index == 0 else {'expires_at': NOW_EXPIRES + 100}
    if requirement == 'AUTHORITY.transfer-read-accept':
        return {'status': 'pending'} if index == 0 else {'status': 'accepted'}
    if requirement == 'AUTHORITY.transferred-ownership':
        return {'bot_id': created[0], 'owner_user_id': STAFF_B}
    if requirement == 'AUTHORITY.transferred-manager-ledger':
        return {'owner_user_id': STAFF_B, 'manager_items': [OWNER_HUMAN]}
    if requirement == 'AUTHORITY.transfer-inboxes':
        return {'contains_bot': True}
    raise AssertionError(requirement)


def synthetic_operation_observation(requirement: str, created: list, owner_human: str):
    if requirement == 'AUTHORITY.trusted-registration':
        return {'bots': list(created)}
    if requirement == 'AUTHORITY.ownership-read':
        return {'bot_id': created[0], 'owner_user_id': owner_human}
    if requirement == 'AUTHORITY.empty-manager-page':
        return {'total': 0}
    if requirement == 'AUTHORITY.owner-not-manager-polarity':
        return {'error_code': 'owner_not_manager'}
    if requirement == 'AUTHORITY.manager-add-list-remove-idempotent':
        return {'added': 1, 'total': 1, 'removed': 1, 'removed_absent': 0}
    if requirement == 'AUTHORITY.manager-restore':
        return {'added': 1}
    if requirement == 'AUTHORITY.mine-ensured-human-read':
        return {'mine_user_id': STAFF_MINE}
    if requirement == 'AUTHORITY.mine-ensured-manager-add':
        return {'added': 1}
    if requirement == 'AUTHORITY.never-seen-manager-unknown':
        return {'error_code': 'bot_authority_human_unknown'}
    if requirement == 'AUTHORITY.mine-ensured-manager-remove':
        return {'removed': 1}
    if requirement == 'AUTHORITY.transfer-initiate-refresh':
        return {'status': 'pending', 'target_owner_user_id': STAFF_B,
                'initiated_expires_at': NOW_EXPIRES, 'refreshed_expires_at': NOW_EXPIRES + 100}
    if requirement == 'AUTHORITY.transfer-read-accept':
        return {'read_status': 'pending', 'accept_status': 'accepted'}
    if requirement == 'AUTHORITY.transferred-ownership':
        return {'bot_id': created[0], 'owner_user_id': STAFF_B}
    if requirement == 'AUTHORITY.transferred-manager-ledger':
        return {'owner_user_id': STAFF_B, 'manager_items': [OWNER_HUMAN]}
    if requirement == 'AUTHORITY.transfer-inboxes':
        return {'received_contains_bot': True, 'sent_contains_bot': True}
    if requirement == 'AUTHORITY.story-scoped-disposition':
        return {'http_call_count': EXPECTED_HTTP_CALLS, 'bots_retained': EXPECTED_RESOURCES}
    raise AssertionError(requirement)


def synthetic_runtime_record(binding: dict, *, status='STOPPED', returncode=0) -> dict:
    return {'schema_version': 1, 'run_id': binding['run_id'], 'status': status,
            'origin': 'http://127.0.0.1:39999', 'pid': None,
            'returncode': returncode if status == 'STOPPED' else None,
            'binary_sha256': binding['bcs_binary_sha256'], 'ready': status == 'STOPPED',
            'forced_stop': False,
            'ownership': 'live-child-handle-only; saved PID never authorizes stop'}


def synthetic_flow(binding: dict, case: dict, runtime_record: dict, *, flow_status=STATUS) -> dict:
    return {'schema_version': 1, 'status': flow_status, 'run_id': binding['run_id'],
            'case_id': CASE_ID, 'suite': SUITE, 'executor_digest': binding['executor_digest'],
            'binary_kind': BINARY_KIND, 'bcs_binary_sha256': binding['bcs_binary_sha256'],
            'auto_build': False, 'case': case, 'shutdown': runtime_record,
            'credentials': {'credential_memory': 'NOT_CAPTURED', 'fixtures': 'NONE',
                            'role_identity': 'NONE', 'ambient_local_identity': False,
                            'strict_mock_header_admission': True},
            'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
            'ready_to_execute': False}


def plant_evidence(root: Path, binding: dict, case: dict, flow: dict,
                   runtime_record: dict, config: dict) -> None:
    (root / 'authority-binding.json').write_text(json.dumps(binding, indent=2), encoding='utf-8')
    (root / 'authority-flow-report.json').write_text(json.dumps(flow, indent=2), encoding='utf-8')
    (root / 'owned-bcs.json').write_text(json.dumps(runtime_record, indent=2), encoding='utf-8')
    (root / 'bcs-config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    (root / 'local-authority.json').write_text(json.dumps(case, indent=2), encoding='utf-8')


def planted_states(root: Path, **case_changes):
    executor = executor_digest()
    sha = 'a' * 64
    binding = synthetic_binding(executor, sha, 'authority-offline-1', root)
    case = synthetic_case(executor, binding)
    case.update(case_changes)
    runtime_record = synthetic_runtime_record(binding)
    flow = synthetic_flow(binding, case, runtime_record)
    config = {'bind': '127.0.0.1', 'port': 1, 'store_messages': False, 'auth': dict(STRICT_AUTH)}
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    plant_evidence(root, binding, case, flow, runtime_record, config)
    return binding, case, flow, runtime_record, config


class FakeAuthorityRuntime:
    instances: list = []
    final_status = 'STOPPED'
    final_returncode = 0
    config_auth = dict(STRICT_AUTH)

    def __init__(self, *, binary, binary_sha256, root, run_id, allow_ambient_local_identity=True):
        self.init_kwargs = {'binary': Path(binary), 'binary_sha256': binary_sha256,
                            'root': Path(root), 'run_id': run_id,
                            'allow_ambient_local_identity': allow_ambient_local_identity}
        FakeAuthorityRuntime.instances.append(self)
        self.root = Path(root).resolve()
        self.run_id = run_id
        self.binary_sha256 = binary_sha256
        self.provider_sink = None
        self.store_messages = False
        self.inputs = SimpleNamespace(origin='http://127.0.0.1:39999',
                                      human_id='e2ehuman' + hashlib.sha256(run_id.encode()).hexdigest()[:16],
                                      config_path=self.root / 'bcs-config.json',
                                      environment={'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE': 'x' * 32})
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        (self.root / 'bcs-config.json').write_text(json.dumps(
            {'bind': '127.0.0.1', 'port': 39999, 'store_messages': False,
             'auth': self.config_auth}, indent=2), encoding='utf-8')
        self._persist('STARTING')

    def _persist(self, status: str) -> None:
        ready = status == 'STOPPED'
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.run_id, 'status': status,
             'origin': self.inputs.origin, 'pid': None,
             'returncode': self.final_returncode if ready else None,
             'binary_sha256': self.binary_sha256, 'ready': ready, 'forced_stop': False,
             'ownership': 'live-child-handle-only; saved PID never authorizes stop'},
            sort_keys=True, indent=2), encoding='utf-8')

    def _record(self) -> dict:
        return read(self.root / 'owned-bcs.json')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._persist(self.final_status)
        return False


class FakeAuthorityAdapter:
    raise_error: Exception | None = None
    poison_key: bool = False

    def __init__(self, runtime):
        self.runtime = runtime

    def run(self) -> dict:
        binding = read(self.runtime.root / 'authority-binding.json')
        case = synthetic_case(binding['executor_digest'], binding)
        if self.poison_key:
            case['journal_note_bot_token'] = 'leaked-credential-hint'
        if self.raise_error is not None:
            raise self.raise_error
        (self.runtime.root / JOURNAL).write_text(json.dumps(case, sort_keys=True, indent=2) + "\n",
                                                 encoding='utf-8')
        return copy.deepcopy(case)


class RenderGuardTests(unittest.TestCase):
    def test_rendered_matrix_replaces_every_identity_token(self):
        case = {'resources': {'created': [OWNER_BOT, TARGET_BOT]}, 'owner_human_id': OWNER_HUMAN,
                'never_user_id': NEVER_USER}
        rendered = _render('/api/v1/collaboration/bots/{bot_id}/managers/{owner_user_id}', case)
        self.assertEqual(rendered, '/api/v1/collaboration/bots/' + OWNER_BOT + '/managers/' + OWNER_HUMAN)
        all_tokens = ('/api/v1/collaboration/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}'
                      '?x={target_user_id}&y={mine_user_id}&z={never_user_id}')
        self.assertNotIn('{', _render(all_tokens, case))

    def test_leftover_and_unbound_placeholders_are_refused(self):
        case = {'resources': {'created': [OWNER_BOT, TARGET_BOT]}, 'owner_human_id': OWNER_HUMAN,
                'never_user_id': NEVER_USER}
        with self.assertRaises(RunStateError):
            _render('/api/v1/collaboration/bots/{rogue_token}', case)
        empty = {'resources': {'created': ['', TARGET_BOT]}, 'owner_human_id': OWNER_HUMAN,
                 'never_user_id': NEVER_USER}
        with self.assertRaises(RunStateError):
            _render('/api/v1/collaboration/bots/{bot_id}/ownership', empty)


class BoundGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'state'
        planted_states(self.root)

    def test_frozen_binding_validates(self):
        self.assertEqual(_bound(self.root), read(self.root / 'authority-binding.json'))

    def test_missing_binding_refuses_before_any_projection(self):
        (self.root / 'authority-binding.json').unlink()
        with self.assertRaises(RunStateError):
            _bound(self.root)

    def test_binding_tamper_matrix_is_rejected(self):
        original = read(self.root / 'authority-binding.json')
        mutations = {
            'contract': lambda value: value.__setitem__('contract', 'owned-local-rogue-v1'),
            'suite': lambda value: value.__setitem__('suite', 'rogue'),
            'case-id': lambda value: value.__setitem__('case_id', 'LOCAL-ROGUE-01'),
            'run-id': lambda value: value.__setitem__('run_id', ''),
            'runtime-dir': lambda value: value.__setitem__('runtime_dir', '/elsewhere'),
            'binary-kind': lambda value: value.__setitem__('binary_kind', 'rogue'),
            'sha-length': lambda value: value.__setitem__('bcs_binary_sha256', 'a' * 63),
            'sha-hex': lambda value: value.__setitem__('bcs_binary_sha256', 'z' + 'a' * 63),
            'executor-digest': lambda value: value.__setitem__('executor_digest', 'rogue'),
            'requirements': lambda value: value['selected_requirement_ids'].pop(),
            'environment': lambda value: value['environment'].__setitem__('preprod', 'SUPPORTED'),
            'auto-build': lambda value: value.__setitem__('auto_build', True),
            'store-messages': lambda value: value.__setitem__('store_messages', True),
            'parent': lambda value: value['source_binding'].__setitem__('parent', 'story_rogue'),
            'denominator': lambda value: value['source_binding'].__setitem__('release_parent_denominator', False),
            'legacy-inventory-reviewed-row': lambda value: value['legacy_inventory'].remove(
                next(row for row in value['legacy_inventory']
                     if row['id'] == 'story_human_manages_delivery_policy')),
        }
        for mutation, apply in mutations.items():
            value = copy.deepcopy(original)
            apply(value)
            (self.root / 'authority-binding.json').write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(mutation=mutation), self.assertRaises(RunStateError):
                _bound(self.root)
        (self.root / 'authority-binding.json').write_text(json.dumps(original), encoding='utf-8')
        self.assertEqual(_bound(self.root), original)
        # Backlog rows stay exempt from the reviewed-denominator freeze by
        # design (5aae6c12d): dropping one must keep the binding valid.
        value = copy.deepcopy(original)
        backlog_id = sorted(KNOWN_UNMAPPED_LEGACY_IDS)[0]
        value['legacy_inventory'].remove(
            next(row for row in value['legacy_inventory'] if row['id'] == backlog_id))
        (self.root / 'authority-binding.json').write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(_bound(self.root), value)
        (self.root / 'authority-binding.json').write_text(json.dumps(original), encoding='utf-8')

    def test_executor_drift_refuses_the_binding(self):
        with patch('bcs_e2e.local_authority_reporting.executor_digest', return_value='rogue-digest'):
            with self.assertRaises(RunStateError):
                _bound(self.root)


class ProjectionGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'state'
        self.binding, self.case, self.flow, self.runtime_record, self.config = planted_states(self.root)

    def _replant(self, case, flow, runtime_record=None, config=None):
        plant_evidence(self.root, self.binding, case, flow,
                       runtime_record or self.runtime_record, config or self.config)

    def test_valid_evidence_projects_to_pass(self):
        projected = _project(self.root, self.binding)
        self.assertEqual(projected['status'], 'PASS')
        self.assertEqual(projected['functional'], 'PASS')
        self.assertEqual(projected['cleanup'], 'TWO_BOTS_CREATED_AND_RETAINED')
        self.assertEqual([row['id'] for row in projected['requirements']], list(REQUIREMENTS))
        self.assertTrue(all(row['status'] == 'PASS' for row in projected['requirements']))

    def test_semantic_variant_matrix_is_rejected(self):
        variants = {
            'foreign-path': lambda case: case['operations'][1]['calls'][0].__setitem__(
                'path', '/api/v1/collaboration/bots/foreign-bot/ownership'),
            'unrendered-row-path-template': lambda case: case['operations'][1].__setitem__(
                'path', '/api/v1/collaboration/bots/{bot_id}/ownership'),
            'duplicate-request-id': lambda case: case['operations'][1]['calls'][0].__setitem__(
                'submitted_request_id', case['operations'][2]['calls'][0]['submitted_request_id']),
            'wrong-http-status': lambda case: case['operations'][3]['calls'][0].__setitem__('http_status', 200),
            'unknown-observed-key': lambda case: case['operations'][6]['observed'].__setitem__('rogue', True),
            'wrong-observed-literal': lambda case: case['operations'][2]['observed'].__setitem__('total', 1),
            'window-shrink': lambda case: case['operations'][10]['observed'].__setitem__(
                'refreshed_expires_at', NOW_EXPIRES - 10),
            'window-gap-too-large': lambda case: case['operations'][10]['observed'].__setitem__(
                'refreshed_expires_at', NOW_EXPIRES + 300000),
            'resources-tamper': lambda case: case['resources'].__setitem__('created', [OWNER_BOT]),
            'actor-mismatch': lambda case: case['operations'][1].__setitem__('actor', 'rogue'),
            'method-mismatch': lambda case: case['operations'][1].__setitem__('method', 'POST'),
            'query-mismatch': lambda case: case['operations'][6]['calls'][0].__setitem__('query', {'limit': '5'}),
            'transitions-malformed': lambda case: case['operations'][4]['transitions'].append('ROGUE'),
            'call-status-intended': lambda case: case['operations'][4]['calls'][0].__setitem__('status', 'INTENDED'),
            'call-expectation-mismatch': lambda case: case['operations'][4]['calls'][0].__setitem__(
                'expect_status', 201),
            'submitted-request-empty': lambda case: case['operations'][1]['calls'][0].__setitem__(
                'submitted_request_id', ''),
            'operation-order-swap': lambda case: case['operations'].reverse(),
            'assertions-missing': lambda case: case['assertions'].pop(),
            'owner-actor-mismatch': lambda case: case.__setitem__('owner_actor_id', 'human_rogue'),
            'target-actor-mismatch': lambda case: case.__setitem__('target_actor_id', 'human_rogue'),
            'never-user-empty': lambda case: case.__setitem__('never_user_id', ''),
            'identity-crossed': lambda case: case.__setitem__('mine_user_id', STAFF_B),
            'case-status-wrong': lambda case: case.__setitem__('status', 'LOCAL_AUTHORITY_FAIL'),
            'http-call-count-short': lambda case: case.__setitem__('http_call_count', 23),
            'binding-digest-wrong': lambda case: case.__setitem__('binding_digest', '0' * 64),
        }
        for name, mutate in variants.items():
            value = copy.deepcopy(self.case)
            mutate(value)
            flow = synthetic_flow(self.binding, value, self.runtime_record)
            with self.subTest(variant=name):
                self._replant(value, flow)
                self.assertNotEqual(_project(self.root, self.binding)['status'], 'PASS')

    def test_cancelled_flow_projects_cancelled(self):
        flow = synthetic_flow(self.binding, {}, self.runtime_record, flow_status='CANCELLED')
        self._replant({}, flow)
        (self.root / JOURNAL).unlink()
        self.assertEqual(_project(self.root, self.binding)['status'], 'CANCELLED')

    def test_flow_runtime_and_journal_disagreement_refuse(self):
        flow = synthetic_flow(self.binding, {}, self.runtime_record)
        self._replant(self.case, flow)
        with self.assertRaises(RunStateError):
            _project(self.root, self.binding)
        mismatch = synthetic_runtime_record(self.binding, status='FAILED_STOP', returncode=1)
        flow = synthetic_flow(self.binding, self.case, mismatch)
        self._replant(self.case, flow, runtime_record=mismatch)
        self.assertNotEqual(_project(self.root, self.binding)['status'], 'PASS')

    def test_credentials_variants_refuse(self):
        variants = {
            'credential-memory-cleared': lambda flow: flow['credentials'].__setitem__('credential_memory', 'CLEARED'),
            'ambient-identity': lambda flow: flow['credentials'].__setitem__('ambient_local_identity', True),
            'mock-admission-relaxed': lambda flow: flow['credentials'].__setitem__(
                'strict_mock_header_admission', False),
            'remote-validation-ran': lambda flow: flow.__setitem__('remote_validation', 'PRE'),
            'release-status-qualified': lambda flow: flow.__setitem__('full_release_status', 'PRE_QUALIFIED'),
            'suite-mismatch': lambda flow: flow.__setitem__('suite', 'rogue'),
        }
        for name, mutate in variants.items():
            flow = synthetic_flow(self.binding, self.case, self.runtime_record)
            mutate(flow)
            with self.subTest(variant=name):
                self._replant(self.case, flow)
                self.assertNotEqual(_project(self.root, self.binding)['status'], 'PASS')

    def test_configuration_and_source_set_corners(self):
        config = {'bind': '127.0.0.1', 'port': 1, 'store_messages': True, 'auth': dict(STRICT_AUTH)}
        self._replant(self.case, self.flow, config=config)
        self.assertNotEqual(_project(self.root, self.binding)['status'], 'PASS')
        config = {'bind': '127.0.0.1', 'port': 1, 'store_messages': False,
                  'auth': dict(STRICT_AUTH, allow_mock_headers=False)}
        self._replant(self.case, self.flow, config=config)
        self.assertNotEqual(_project(self.root, self.binding)['status'], 'PASS')
        (self.root / 'owned-bcs.json').unlink()
        with self.assertRaises(RunStateError):
            _project(self.root, self.binding)


class PublicationGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'state'
        self.binding, self.case, self.flow, self.runtime_record, self.config = planted_states(self.root)
        self.runtime = SimpleNamespace(root=self.root, _record=lambda: self.runtime_record)

    def test_publication_writes_pending_outputs_completion_and_cleanly_removes_pending(self):
        canonical = publish_authority_report(self.runtime, self.binding, self.flow)
        self.assertEqual(canonical['status'], 'LOCAL_SUITE_PASS')
        self.assertFalse((self.root / 'authority-publication-pending.json').exists())
        completion = read(self.root / 'authority-completion.json')
        self.assertEqual(completion['contract'], COMPLETION)
        self.assertEqual(completion['binding_digest'], fingerprint(self.binding))
        for name in SOURCES:
            self.assertTrue((self.root / name).is_file(), name)
        for name in OUTPUTS:
            self.assertTrue((self.root / name).is_file(), name)
        self.assertEqual(build_authority_report(self.root), canonical['summary'])

    def test_offline_projection_rejects_gaps_and_drift(self):
        summary = build_authority_report(self.root)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        publish_authority_report(self.runtime, self.binding, self.flow)
        self.assertEqual(build_authority_report(self.root)['status'], 'LOCAL_SUITE_PASS')
        (self.root / 'authority-publication-pending.json').write_text('{}', encoding='utf-8')
        summary = build_authority_report(self.root)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        (self.root / 'authority-publication-pending.json').unlink()
        (self.root / 'authority-completion.json').unlink()
        summary = build_authority_report(self.root)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        linked = self.root.parent / 'linked'
        linked.symlink_to(self.root)
        with self.assertRaises(RunStateError):
            build_authority_report(linked)

    def test_changed_evidence_after_publication_blocks(self):
        publish_authority_report(self.runtime, self.binding, self.flow)
        flow = read(self.root / 'authority-flow-report.json')
        flow['status'] = 'LOCAL_AUTHORITY_FAIL'
        (self.root / 'authority-flow-report.json').write_text(json.dumps(flow), encoding='utf-8')
        summary = build_authority_report(self.root)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_summary_carries_the_release_denominator_contract(self):
        canonical = publish_authority_report(self.runtime, self.binding, self.flow)
        summary = canonical['summary']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['suite'], SUITE)
        self.assertEqual(summary['scope']['selected_cases'], [CASE_ID])
        self.assertEqual(summary['scope']['release_parent_denominator'], True)
        self.assertEqual(summary['scope']['independent_slice'], False)
        self.assertEqual(summary['scope']['legacy_inventory_count'],
                         len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['reports']['expected_assertion_count'], len(REQUIREMENTS))
        self.assertEqual(summary['reports']['observed_assertion_count'], len(REQUIREMENTS))
        self.assertEqual(summary['ready_to_execute'], False)
        self.assertTrue(summary['verification_boundary']['not_proven'])
        self.assertEqual(summary['target'], {'tier': 'local', 'environment_id': self.binding['run_id']})


class FakeLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeAuthorityRuntime.instances = []
        FakeAuthorityRuntime.final_status = 'STOPPED'
        FakeAuthorityRuntime.final_returncode = 0
        FakeAuthorityRuntime.config_auth = dict(STRICT_AUTH)
        FakeAuthorityAdapter.raise_error = None
        FakeAuthorityAdapter.poison_key = False

    def _run(self, state_dir, **changes):
        with patch('bcs_e2e.local_authority_run.OwnedBcsProcess', FakeAuthorityRuntime), \
                patch('bcs_e2e.local_authority_run.OwnedLocalAuthority', FakeAuthorityAdapter):
            return local_authority_run(**run_kwargs(state_dir, self.binary, self.digest, **changes))

    def test_positive_offline_lifecycle_publishes_the_owned_evidence_chain(self):
        state = self.root / 'state'
        result = self._run(state)
        self.assertEqual(result['status'], STATUS)
        self.assertEqual(result['case_id'], CASE_ID)
        self.assertFalse(result['ready_to_execute'])
        names = {'authority-binding.json', 'authority-flow-report.json', 'bcs-config.json',
                 'owned-bcs.json', JOURNAL, 'authority-completion.json',
                 'reports/summary.json', 'reports/junit.xml', 'reports/summary.md'}
        self.assertTrue(all((state / name).is_file() for name in names),
                        sorted(p.name for p in state.rglob('*')))
        self.assertFalse((state / 'authority-publication-pending.json').exists())
        binding = read(state / 'authority-binding.json')
        self.assertEqual(binding['contract'], SUITE)
        self.assertEqual(binding['selected_requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['binary_kind'], BINARY_KIND)
        self.assertEqual(binding['auto_build'], False)
        self.assertEqual(binding['store_messages'], False)
        self.assertEqual(binding['runtime_dir'], str(state.resolve()))
        self.assertEqual(binding['source_binding'], authority_source_binding())
        self.assertEqual(binding['legacy_inventory'], current_manifest()['legacy_inventory'])
        case = read(state / JOURNAL)
        self.assertEqual(result['case'], case)
        self.assertEqual(case['status'], STATUS)
        self.assertEqual(case['http_call_count'], EXPECTED_HTTP_CALLS)
        self.assertEqual(case['resources']['created'], [OWNER_BOT, TARGET_BOT])
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['shutdown']['returncode'], 0)
        self.assertEqual(read(state / 'authority-flow-report.json')['status'], STATUS)
        canonical = result['canonical_summary']
        self.assertEqual(canonical['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(canonical['requirements'],
                         [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS])
        self.assertEqual(build_authority_report(state), canonical)
        completion = read(state / 'authority-completion.json')
        self.assertEqual(completion['contract'], COMPLETION)
        self.assertEqual(completion['binding_digest'], fingerprint(binding))
        journal_text = (state / JOURNAL).read_text()
        for forbidden in FORBIDDEN_JOURNAL_SUBSTRINGS:
            self.assertNotIn(forbidden, journal_text)

    def test_permissions_confirm_and_fresh_state_precede_launch(self):
        state = self.root / 'state'
        with patch('bcs_e2e.local_authority_run.OwnedBcsProcess') as proc, \
                patch('bcs_e2e.local_authority_run.OwnedLocalAuthority'):
            for key, value in (('allow_local_processes', False), ('allow_fixture_write', False),
                               ('allow_write', False), ('confirm_run_id', 'other')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    self._run(state, **{key: value})
            state.mkdir()
            with self.assertRaises(RunStateError):
                local_authority_run(**run_kwargs(state, self.binary, self.digest))
            proc.assert_not_called()

    def test_invalid_run_id_is_refused_before_any_state(self):
        state = self.root / 'state'
        with patch('bcs_e2e.local_authority_run.OwnedBcsProcess') as proc:
            with self.assertRaises(ValueError):
                self._run(state, run_id='authority run id', confirm_run_id='authority run id')
            proc.assert_not_called()

    def test_adapter_failure_and_shutdown_dispositions_gate_the_pass(self):
        for name, adapter_error, final_status, final_code, expected in (
                ('adapter-blocked', RunStateError('authority blocked'), 'STOPPED', 0, 'BLOCKED'),
                ('runtime-failed-stop', None, 'FAILED_STOP', 1, 'BLOCKED'),
                ('runtime-nonzero-stop', None, 'STOPPED', 130, 'BLOCKED')):
            with self.subTest(variant=name):
                FakeAuthorityAdapter.raise_error = adapter_error
                FakeAuthorityRuntime.final_status = final_status
                FakeAuthorityRuntime.final_returncode = final_code
                state = self.root / ('state-' + name)
                result = self._run(state)
                self.assertEqual(result['status'], expected)
                record = read(state / 'owned-bcs.json')
                self.assertEqual(record['status'], final_status)
                self.assertEqual(record['returncode'], final_code if final_status == 'STOPPED' else None)
                summary = build_authority_report(state)
                self.assertEqual(summary['status'], expected)

    def test_cancellation_canonicalises_without_pass_claims(self):
        FakeAuthorityAdapter.raise_error = KeyboardInterrupt()
        state = self.root / 'state-cancel'
        result = self._run(state)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['error_type'], 'KeyboardInterrupt')
        self.assertFalse((state / JOURNAL).exists())
        summary = build_authority_report(state)
        self.assertEqual(summary['status'], 'CANCELLED')

    def test_journal_credential_leak_blocks_the_run(self):
        FakeAuthorityAdapter.poison_key = True
        state = self.root / 'state-leak'
        result = self._run(state)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('bot_token', (state / JOURNAL).read_text())

    def test_strict_configuration_and_retained_resources_gate_the_pass(self):
        FakeAuthorityRuntime.config_auth = dict(STRICT_AUTH, allow_mock_headers=False)
        state = self.root / 'state-config'
        self.assertEqual(self._run(state)['status'], 'BLOCKED')
        FakeAuthorityRuntime.config_auth = dict(STRICT_AUTH)
        original = FakeAuthorityAdapter.run

        def single_bot(adapter, runtime):
            binding = read(runtime.root / 'authority-binding.json')
            case = synthetic_case(binding['executor_digest'], binding, created=[OWNER_BOT])
            (runtime.root / JOURNAL).write_text(json.dumps(case, sort_keys=True, indent=2) + "\n",
                                                encoding='utf-8')
            return copy.deepcopy(case)

        def short_matrix(adapter, runtime):
            binding = read(runtime.root / 'authority-binding.json')
            case = synthetic_case(binding['executor_digest'], binding, http_call_count=23)
            (runtime.root / JOURNAL).write_text(json.dumps(case, sort_keys=True, indent=2) + "\n",
                                                encoding='utf-8')
            return copy.deepcopy(case)

        try:
            FakeAuthorityAdapter.run = single_bot
            self.assertEqual(self._run(self.root / 'state-bots')['status'], 'BLOCKED')
            FakeAuthorityAdapter.run = short_matrix
            self.assertEqual(self._run(self.root / 'state-count')['status'], 'BLOCKED')
        finally:
            FakeAuthorityAdapter.run = original

    def test_executor_drift_fence_refuses_the_pass(self):
        state = self.root / 'state-drift'
        seen = {'count': 0}

        def drifting():
            seen['count'] += 1
            return 'drift-second-digest' if seen['count'] == 2 else 'drift-first-digest'

        with patch('bcs_e2e.local_authority_run.OwnedBcsProcess', FakeAuthorityRuntime), \
                patch('bcs_e2e.local_authority_run.OwnedLocalAuthority', FakeAuthorityAdapter), \
                patch('bcs_e2e.local_authority_run.executor_digest', side_effect=drifting), \
                patch('bcs_e2e.local_authority_reporting.executor_digest', side_effect=drifting):
            result = local_authority_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(read(state / 'authority-binding.json')['executor_digest'], 'drift-first-digest')

    def test_binary_validation_fences_the_frozen_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / 'real'
            real.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            real.chmod(0o755)
            self.assertEqual(validate_authority_binary(real,
                                                       hashlib.sha256(real.read_bytes()).hexdigest()), real)
            link = base / 'link'
            link.symlink_to(real)
            with self.assertRaises(RunStateError):
                validate_authority_binary(link, hashlib.sha256(real.read_bytes()).hexdigest())
            plain = base / 'plain'
            plain.write_text('not executable', encoding='utf-8')
            with self.assertRaises(RunStateError):
                validate_authority_binary(plain, hashlib.sha256(plain.read_bytes()).hexdigest())
            with self.assertRaises(RunStateError):
                validate_authority_binary(real, '0' * 64)
            with self.assertRaises(RunStateError):
                validate_authority_binary(base / 'missing', '0' * 64)


class CommandGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeAuthorityRuntime.instances = []
        FakeAuthorityRuntime.final_status = 'STOPPED'
        FakeAuthorityRuntime.final_returncode = 0
        FakeAuthorityRuntime.config_auth = dict(STRICT_AUTH)
        FakeAuthorityAdapter.raise_error = None
        FakeAuthorityAdapter.poison_key = False

    def _cli(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(argv)
        return json.loads(out.getvalue()), rc

    def test_run_command_refuses_unconfirmed_execution(self):
        state = self.root / 'state'
        report, rc = self._cli(['local-authority-run', '--binary', str(self.binary),
                                '--binary-sha256', self.digest, '--state-dir', str(state),
                                '--run-id', 'authority-cli-test', '--confirm-run-id', 'authority-cli-test'])
        self.assertEqual(rc, 2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertFalse(state.exists())

    def test_report_command_refuses_evidence_gaps(self):
        state = self.root / 'state'
        state.mkdir()
        report, rc = self._cli(['local-authority-report', '--state-dir', str(state)])
        self.assertEqual(rc, 2)
        self.assertEqual(report['status'], 'BLOCKED')

    def test_report_command_renders_only_outside_original_evidence(self):
        with patch('bcs_e2e.local_authority_run.OwnedBcsProcess', FakeAuthorityRuntime), \
                patch('bcs_e2e.local_authority_run.OwnedLocalAuthority', FakeAuthorityAdapter):
            state = self.root / 'state-published'
            result = local_authority_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], STATUS)
        report, rc = self._cli(['local-authority-report', '--state-dir', str(state)])
        self.assertEqual(rc, 0)
        self.assertEqual(report['status'], 'LOCAL_SUITE_PASS')
        outside = self.root / 'rendered'
        report, rc = self._cli(['local-authority-report', '--state-dir', str(state),
                                '--output-dir', str(outside)])
        self.assertEqual(rc, 0)
        self.assertTrue((outside / 'summary.json').is_file())
        inside, rc = self._cli(['local-authority-report', '--state-dir', str(state),
                                '--output-dir', str(state / 'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual(inside['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'), 'approved real BCS required')
class AuthorityLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name) / 'run'
        cls.result = local_authority_run(**run_kwargs(cls.root, Path(BINARY), SHA,
                                                      run_id='authority-live-1',
                                                      confirm_run_id='authority-live-1'))
        if cls.result['status'] != STATUS:
            raise AssertionError(json.dumps(cls.result, indent=2)[:2000])

    def test_sixteen_obligations_pass_against_the_owned_runtime(self):
        result, summary = self.result, self.result['canonical_summary']
        case = result['case']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(case['status'], STATUS)
        self.assertEqual(case['http_call_count'], EXPECTED_HTTP_CALLS)
        self.assertEqual([row['id'] for row in case['assertions']], list(REQUIREMENTS))
        self.assertTrue(all(row['status'] == 'PASS' for row in case['assertions']))
        self.assertEqual(case['resource_disposition'], 'TWO_BOTS_CREATED_AND_RETAINED')
        created = case['resources']['created']
        self.assertEqual(len(created), EXPECTED_RESOURCES)
        self.assertNotEqual(created[0], created[1])
        self.assertTrue(all(isinstance(item, str) and item for item in created))
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['shutdown']['returncode'], 0)

    def test_evidence_chain_publishes_and_reverifies_offline(self):
        state = self.root
        names = {'authority-binding.json', 'authority-flow-report.json', 'bcs-config.json',
                 'owned-bcs.json', JOURNAL, 'authority-completion.json',
                 'reports/summary.json', 'reports/junit.xml', 'reports/summary.md'}
        self.assertTrue(all((state / name).is_file() for name in names),
                        sorted(p.name for p in state.rglob('*')))
        self.assertFalse((state / 'authority-publication-pending.json').exists())
        config = json.loads((state / 'bcs-config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['auth'], STRICT_AUTH)
        journal_text = (state / JOURNAL).read_text(encoding='utf-8')
        for forbidden in RUN_FORBIDDEN:
            self.assertNotIn(forbidden, journal_text)
        summary = self.result['canonical_summary']
        self.assertEqual(summary['requirements'],
                         [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS])
        self.assertEqual(summary['reports']['expected_assertion_count'], len(REQUIREMENTS))
        self.assertEqual(summary['reports']['observed_assertion_count'], len(REQUIREMENTS))
        self.assertEqual(summary['scope']['release_parent_denominator'], True)
        self.assertEqual(summary['scope']['independent_slice'], False)
        self.assertEqual(summary['ready_to_execute'], False)
        self.assertTrue(summary['verification_boundary']['not_proven'])
        completion = read(state / 'authority-completion.json')
        self.assertEqual(completion['contract'], COMPLETION)
        self.assertEqual(completion['binding_digest'],
                         fingerprint(read(state / 'authority-binding.json')))
        junit = ET.parse(state / 'reports/junit.xml').getroot()
        self.assertEqual([junit.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped')],
                         ['1', '0', '0', '0'])
        markdown = (state / 'reports/summary.md').read_text(encoding='utf-8')
        for requirement in REQUIREMENTS:
            self.assertIn(requirement, markdown)
        with patch('http.client.HTTPConnection.request', side_effect=AssertionError('offline')):
            self.assertEqual(build_authority_report(state), self.result['canonical_summary'])

    def test_changed_evidence_and_semantic_variants_are_rejected(self):
        state = self.root
        originals = {name: (state / name).read_bytes()
                     for name in (*SOURCES, 'authority-completion.json')}
        try:
            case = read(state / JOURNAL)
            raw = (state / 'authority-flow-report.json').read_bytes()
            (state / 'authority-flow-report.json').write_bytes(raw + b' ')
            summary = build_authority_report(state)
            self.assertNotEqual(summary['status'], 'LOCAL_SUITE_PASS')
            self.assertTrue(summary['integrity']['issues'])
            (state / 'authority-flow-report.json').write_bytes(originals['authority-flow-report.json'])
            variants = {
                'foreign-path': lambda value: value['operations'][1]['calls'][0].__setitem__(
                    'path', '/api/v1/collaboration/bots/foreign-bot/ownership'),
                'duplicate-request-id': lambda value: value['operations'][1]['calls'][0].__setitem__(
                    'submitted_request_id', value['operations'][2]['calls'][0]['submitted_request_id']),
                'observed-literal': lambda value: value['operations'][2]['observed'].__setitem__('total', 1),
                'window-shrink': lambda value: value['operations'][10]['observed'].__setitem__(
                    'refreshed_expires_at',
                    value['operations'][10]['observed']['initiated_expires_at'] - 10),
                'retained-resource': lambda value: value['resources']['created'].pop(),
                'actor-mismatch': lambda value: value['operations'][1].__setitem__('actor', 'rogue'),
            }
            for name, mutate in variants.items():
                value = copy.deepcopy(case)
                mutate(value)
                with self.subTest(variant=name):
                    (state / JOURNAL).write_text(json.dumps(value), encoding='utf-8')
                    summary = build_authority_report(state)
                    self.assertNotEqual(summary['status'], 'LOCAL_SUITE_PASS')
        finally:
            for name, raw in originals.items():
                (state / name).write_bytes(raw)
        self.assertEqual(build_authority_report(state), self.result['canonical_summary'])

    def test_report_command_renders_offline_and_refuses_overwrite(self):
        state = self.root
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(['local-authority-report', '--state-dir', str(state)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'LOCAL_SUITE_PASS')
        outside = self.root / 'rendered'
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['local-authority-report', '--state-dir', str(state),
                       '--output-dir', str(outside)])
        self.assertEqual(rc, 0)
        self.assertTrue((outside / 'summary.json').is_file())
        inside, rc = self._cli(['local-authority-report', '--state-dir', str(state),
                                '--output-dir', str(state / 'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual(inside['status'], 'BLOCKED')

    def _cli(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(argv)
        return json.loads(out.getvalue()), rc


if __name__ == '__main__':
    unittest.main()
