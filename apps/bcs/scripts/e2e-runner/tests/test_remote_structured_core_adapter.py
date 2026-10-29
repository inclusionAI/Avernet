"""Public structured-collaboration core slice and adapter contracts."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_structured_contract import structured_source_binding
from bcs_e2e.local_structured_core_contract import (
    CASE_ID,
    CORE_REQUIREMENT_IDS,
    DEFINITION_YAML,
    execute_structured_core_case,
    structured_core_adaptability,
    structured_core_case_plan,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_structured_core_adapter import RemoteStructuredCoreAdapter
from bcs_e2e.route_bindings import (
    STRUCTURED_GROUP_DEFINITION_PATH,
    STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH,
    STRUCTURED_GROUP_DELETE_PATH,
    STRUCTURED_GROUP_PATH,
    STRUCTURED_GROUP_RUN_START_PATH,
    STRUCTURED_GROUPS_CREATE_PATH,
    STRUCTURED_RUN_CANCEL_PATH,
    STRUCTURED_RUN_GRAPH_PATH,
    STRUCTURED_RUN_HUMAN_RESPOND_PATH,
    STRUCTURED_RUN_NODE_PATH,
    STRUCTURED_RUN_PENDING_HUMAN_PATH,
    STRUCTURED_RUN_PATH,
    STRUCTURED_RUN_RERUN_PATH,
    STRUCTURED_DIRECT_PATHS,
    _EDGE_CASES,
)
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ObservationError, ReadClient
from bcs_e2e.write_transport import WriteClient, WriteTransportError

COOKIE = 'human-cookie-private-value'
TOKEN = 'driver-bot-private-token'
DRIVER = 'driver-bot'
MEMBER = 'member-bot'
GROUP = 'group-1'
DEFINITION = 'definition-1'
RUN = 'run-1'
ORIGIN = 'http://127.0.0.1:21000'

BINDING_ROUTES = (
    ('structured.group.create', STRUCTURED_GROUPS_CREATE_PATH, 'authenticated_bot'),
    ('structured.group.read', STRUCTURED_GROUP_PATH, 'human_cookie'),
    ('structured.definition.read', STRUCTURED_GROUP_DEFINITION_PATH, 'human_cookie'),
    ('structured.definition.patch', STRUCTURED_GROUP_DEFINITION_PATH, 'human_cookie'),
    ('structured.definition.upgrade.missing', STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH, 'human_cookie'),
    ('structured.run.start', STRUCTURED_GROUP_RUN_START_PATH, 'human_cookie'),
    ('structured.run.read', STRUCTURED_RUN_PATH, 'human_cookie'),
    ('structured.run.graph.read', STRUCTURED_RUN_GRAPH_PATH, 'human_cookie'),
    ('structured.run.node.read', STRUCTURED_RUN_NODE_PATH, 'human_cookie'),
    ('structured.run.pending.human.nodes', STRUCTURED_RUN_PENDING_HUMAN_PATH, 'human_cookie'),
    ('structured.run.human.response.denied', STRUCTURED_RUN_HUMAN_RESPOND_PATH, 'human_cookie'),
    ('structured.run.cancel', STRUCTURED_RUN_CANCEL_PATH, 'human_cookie'),
    ('structured.run.rerun.aborted.denied', STRUCTURED_RUN_RERUN_PATH, 'human_cookie'),
    ('structured.group.delete', STRUCTURED_GROUP_DELETE_PATH, 'human_cookie'),
)

REVIEWED_OPERATION = {
    'STRUCTURED.group-create': 'structured.group.create',
    'STRUCTURED.group-read': 'structured.group.read',
    'STRUCTURED.definition-read': 'structured.definition.read',
    'STRUCTURED.definition-patch': 'structured.definition.patch',
    'STRUCTURED.upgrade-missing': 'structured.definition.upgrade.missing',
    'STRUCTURED.run-start': 'structured.run.start',
    'STRUCTURED.run-read': 'structured.run.read',
    'STRUCTURED.graph-read': 'structured.run.graph.read',
    'STRUCTURED.node-read': 'structured.run.node.read',
    'STRUCTURED.pending-human-empty': 'structured.run.pending.human.nodes',
    'STRUCTURED.human-response-denied': 'structured.run.human.response.denied',
    'STRUCTURED.run-cancel': 'structured.run.cancel',
    'STRUCTURED.run-terminal': 'structured.run.read',
    'STRUCTURED.rerun-aborted-denied': 'structured.run.rerun.aborted.denied',
    'STRUCTURED.group-delete': 'structured.group.delete',
}


DIRECT_BINDING_ROUTES = (
    # Station-direct preprod: ACE enforces the Human cookie on structured
    # Group create; the community Singlebox contract keeps the Bot bearer.
    ('structured.group.create', STRUCTURED_DIRECT_PATHS['structured.group.create'],
     'human_cookie'),
    ('structured.group.read', STRUCTURED_DIRECT_PATHS['structured.group.read'],
     'human_cookie'),
    ('structured.definition.read',
     STRUCTURED_DIRECT_PATHS['structured.definition.read'], 'human_cookie'),
    ('structured.definition.patch',
     STRUCTURED_DIRECT_PATHS['structured.definition.patch'], 'human_cookie'),
    ('structured.definition.upgrade.missing',
     STRUCTURED_DIRECT_PATHS['structured.definition.upgrade.missing'], 'human_cookie'),
    ('structured.run.start', STRUCTURED_DIRECT_PATHS['structured.run.start'],
     'human_cookie'),
    ('structured.run.read', STRUCTURED_DIRECT_PATHS['structured.run.read'],
     'human_cookie'),
    ('structured.run.graph.read',
     STRUCTURED_DIRECT_PATHS['structured.run.graph.read'], 'human_cookie'),
    ('structured.run.node.read',
     STRUCTURED_DIRECT_PATHS['structured.run.node.read'], 'human_cookie'),
    ('structured.run.pending.human.nodes',
     STRUCTURED_DIRECT_PATHS['structured.run.pending.human.nodes'], 'human_cookie'),
    ('structured.run.human.response.denied',
     STRUCTURED_DIRECT_PATHS['structured.run.human.response.denied'], 'human_cookie'),
    ('structured.run.cancel', STRUCTURED_DIRECT_PATHS['structured.run.cancel'],
     'human_cookie'),
    ('structured.run.rerun.aborted.denied',
     STRUCTURED_DIRECT_PATHS['structured.run.rerun.aborted.denied'], 'human_cookie'),
    ('structured.group.delete', STRUCTURED_DIRECT_PATHS['structured.group.delete'],
     'human_cookie'),
)


def base_profile(*, gateway=False, direct=False):
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment'].update(id='preprod-structured-core' if gateway or direct
                                  else 'local-singlebox-structured-core')
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id=DRIVER, owner_id='owner-1')
    value['actors']['member'].update(bot_id=MEMBER, owner_id='owner-1')
    value['auth']['header_env'].update(Authorization='BCS_E2E_DRIVER_BOT_TOKEN',
                                       Cookie='BCS_E2E_HUMAN_COOKIE')
    bindings = {}
    operation_routes = DIRECT_BINDING_ROUTES if direct else BINDING_ROUTES
    response_mode = 'plain-json' if direct else 'public-envelope'
    for operation, path, auth in operation_routes:
        binding = {'method': _EDGE_CASES[operation][0], 'path': path,
                   'api_face': 'internal-v1', 'auth': auth,
                   'response': response_mode, 'traceparent': 'disabled'}
        if auth == 'authenticated_bot' or operation == 'structured.group.create':
            binding['caller_actor_id'] = DRIVER
        bindings[operation] = binding
    if gateway:
        # Station Gateway face: ACE also enforces the Human cookie on
        # structured Group create; the community bearer stays Singlebox-local.
        bindings['structured.group.create']['auth'] = 'human_cookie'
    value['edge_case_bindings'] = bindings
    if direct:
        value['entrypoint'].update(kind='bcs-direct',
                                   origin='https://bcs-pre.example.com',
                                   manifest_face='legacy')
        value['environment'].update(tier='preprod', expected_bcs_env='pre',
                                 lifecycle='remote-attach', deployment='managed',
                                 provider='internal')
        value['observer'].update(kind='alisddbquery', server='observ',
                                 database='agentclawdb',
                                 environment_binding_confirmed=True)
    elif gateway:
        value['entrypoint'].update(kind='gateway', origin='https://teamclawgw.example.com')
        value['environment'].update(tier='preprod', expected_bcs_env='pre',
                                 lifecycle='remote-attach', deployment='managed',
                                 provider='internal')
        value['observer'].update(kind='alisddbquery', server='observ',
                                 database='agentclawdb',
                                 environment_binding_confirmed=True)
    return validate(value)


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


def envelope(request, data, *, code=20000, message='OK', http=200):
    request_id = next((value for name, value in request.headers.items()
                       if name.lower() == 'x-request-id'), 'structured-core-request')
    return Reply(json.dumps({'code': code, 'message': message,
                             'request_id': request_id, 'data': data}).encode(),
                 code=http, headers={'x-request-id': request_id})


def respond(request, data=None, *, code=20000, message='OK', http=200):
    return envelope(request, {} if data is None else data,
                    code=code, message=message, http=http)


def station(data, code=200):
    return Reply(json.dumps(data).encode(), code=code)


class StationDirectRecorder:
    """Deterministic station-native plain-JSON responder."""

    def __init__(self):
        self.requests = []
        self.published = {}
        self.cancelled = False

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        parsed = urlsplit(request.full_url)
        method, path, query = request.method, parsed.path, parsed.query
        if method == 'POST' and path == STRUCTURED_DIRECT_PATHS['structured.group.create']:
            create_body = json.loads(request.data)
            if create_body['participant_bindings'] != {
                    'driver': {'source': 'manual', 'bot_ids': [DRIVER]}}:
                raise AssertionError(
                    'station create must bind only the reviewed driver slot')
            self.published['group'] = GROUP
            return station({'id': GROUP})
        if method == 'GET' and path == STRUCTURED_DIRECT_PATHS[
                'structured.group.read'].format(group_id=GROUP):
            return station({'id': GROUP, 'group_strategy': 'state_machine'})
        if method == 'GET' and path == STRUCTURED_DIRECT_PATHS[
                'structured.definition.read'].format(group_id=GROUP):
            return station({'default_definition': {'id': DEFINITION, 'version': 1},
                            'definition_yaml': 'instruction: Draft a concise release recommendation.',
                            'yaml_source': 'original',
                            'participant_bindings': {}})
        if method == 'PATCH' and path == STRUCTURED_DIRECT_PATHS[
                'structured.definition.patch'].format(group_id=GROUP):
            return station({'default_definition': {'id': DEFINITION, 'version': 2},
                            'definition_yaml': 'instruction: Draft and verify a concise release recommendation.',
                            'yaml_source': 'original',
                            'participant_bindings': {}})
        if method == 'POST' and path == STRUCTURED_DIRECT_PATHS[
                'structured.definition.upgrade.missing'].format(group_id=GROUP):
            return station({'error': 'not_found',
                            'message': f'collaboration definition not found: {DEFINITION}@102'},
                           code=404)
        if method == 'POST' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.start'].format(group_id=GROUP):
            run = {'run_id': RUN, 'group_id': GROUP, 'status': 'running'}
            self.published['run'] = run
            return station({'run': run, 'nodes': [{'node_id': 'answer'}]}, code=202)
        if method == 'GET' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.read'].format(run_id=RUN):
            return station({'run': {'run_id': RUN,
                                    'status': 'aborted' if self.cancelled else 'running'}})
        if method == 'GET' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.graph.read'].format(run_id=RUN):
            return station({'run': {'run_id': RUN},
                            'nodes': [{'node_id': 'answer'}]})
        if method == 'GET' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.node.read'].format(run_id=RUN):
            return station({'node': {'node_id': 'answer', 'run_id': RUN}})
        if method == 'GET' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.pending.human.nodes'].format(run_id=RUN):
            return station([])
        if method == 'POST' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.human.response.denied'].format(run_id=RUN):
            return station({'error': 'invalid_request',
                            'message': 'node answer is not a human_input node'}, code=400)
        if method == 'POST' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.cancel'].format(run_id=RUN):
            self.cancelled = True
            return station({'run': {'run_id': RUN, 'status': 'aborted'}})
        if method == 'POST' and path == STRUCTURED_DIRECT_PATHS[
                'structured.run.rerun.aborted.denied'].format(run_id=RUN):
            return station({'error': 'conflict',
                            'message': 'source state-machine Run must be failed before rerun'},
                           code=409)
        route = STRUCTURED_DIRECT_PATHS['structured.group.delete'].format(
            group_id=GROUP, driver_actor_id=DRIVER)
        if method == 'DELETE' and parsed.path == route.split('?', 1)[0]:
            if query != 'bot_id=' + DRIVER:
                raise AssertionError('unexpected station delete query ' + query)
            return station({'deleted': True, 'id': GROUP})
        raise AssertionError('unexpected station request ' + method + ' ' + path + query)


class OpenRecorder:
    """Deterministic reviewed-route responder for the ordered core flow."""

    def __init__(self):
        self.requests = []
        self.group_id = GROUP
        self.definition_id = DEFINITION
        self.run_id = RUN
        self.base_version = 1
        self.patched_version = 2
        self.missing_version = 102
        self.run_group_id = GROUP
        self.cancelled = False

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        method = request.method
        parsed = urlsplit(request.full_url)
        path = parsed.path
        query = parsed.query
        group_route = STRUCTURED_GROUP_PATH.format(group_id=self.group_id)
        definition_route = STRUCTURED_GROUP_DEFINITION_PATH.format(
            group_id=self.group_id)
        run_route = STRUCTURED_RUN_PATH.format(run_id=self.run_id)
        if method == 'POST' and path == STRUCTURED_GROUPS_CREATE_PATH:
            return respond(request, {'id': self.group_id}, message='created')
        if method == 'GET' and path == group_route:
            return respond(request, {'id': self.group_id,
                                       'group_strategy': 'state_machine'})
        if method == 'GET' and path == definition_route:
            return respond(request, {'default_definition': {
                'id': self.definition_id, 'version': self.base_version},
                'yaml_source': 'original'})
        if method == 'PATCH' and path == definition_route:
            return respond(request, {'default_definition': {
                'id': self.definition_id, 'version': self.patched_version},
                'definition_yaml': 'instruction: '
                + 'Draft and verify a concise release recommendation.'},
                message='patched Draft and verify')
        if (method == 'POST'
                and path == STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH.format(
                    group_id=self.group_id)):
            return respond(request, {}, code=40400,
                           message=f'definition {self.definition_id} '
                                   f'@{self.missing_version} not found',
                           http=404)
        if (method == 'POST'
                and path == STRUCTURED_GROUP_RUN_START_PATH.format(
                    group_id=self.group_id)):
            return respond(request, {'run': {'run_id': self.run_id,
                                               'group_id': self.run_group_id},
                                       'nodes': [{'node_id': 'answer'}]},
                           code=20200, message='started', http=202)
        if method == 'GET' and path == run_route:
            return respond(request, {'run': {'run_id': self.run_id,
                                               'status': 'aborted'
                                               if self.cancelled else 'running'}})
        if method == 'GET' and path == STRUCTURED_RUN_GRAPH_PATH.format(
                run_id=self.run_id):
            return respond(request, {'run': {'run_id': self.run_id},
                                       'nodes': [{'node_id': 'answer'}]})
        if method == 'GET' and path == STRUCTURED_RUN_NODE_PATH.format(
                run_id=self.run_id):
            return respond(request, {'node': {'node_id': 'answer',
                                                'run_id': self.run_id}})
        if method == 'GET' and path == STRUCTURED_RUN_PENDING_HUMAN_PATH.format(
                run_id=self.run_id):
            return respond(request, [])
        if method == 'POST' and path == STRUCTURED_RUN_HUMAN_RESPOND_PATH.format(
                run_id=self.run_id):
            return respond(request, {}, code=40000,
                           message='answer is not a human_input node', http=400)
        if method == 'POST' and path == STRUCTURED_RUN_CANCEL_PATH.format(
                run_id=self.run_id):
            self.cancelled = True
            return respond(request, {'run': {'run_id': self.run_id,
                                               'status': 'aborted'}})
        if method == 'POST' and path == STRUCTURED_RUN_RERUN_PATH.format(
                run_id=self.run_id):
            return respond(request, {}, code=40900,
                           message='state machine run must be failed before rerun',
                           http=409)
        if method == 'DELETE' and path == group_route:
            if query != 'bot_id=' + DRIVER:
                raise AssertionError('unexpected delete query ' + query)
            return respond(request, {'deleted': True, 'group_id': self.group_id})
        raise AssertionError('unexpected request ' + method + ' ' + path + query)


class RemoteStructuredCoreContractTests(unittest.TestCase):
    def test_case_guard_accepts_the_structured_core_slice(self):
        guard = ROOT.parents[3] / 'scripts/ci/e2e_runner_case_guard.py'
        files = (ROOT / 'bcs_e2e/local_structured_core_contract.py',
                 ROOT / 'bcs_e2e/remote_structured_core_adapter.py')
        result = subprocess.run([sys.executable, str(guard), *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_slice_freezes_the_reviewed_15_source_obligations(self):
        plan = structured_core_case_plan()
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_runs_structured_collaboration')
        self.assertEqual(tuple(row.requirement_id for row in plan.requirements),
                         CORE_REQUIREMENT_IDS)
        self.assertEqual(len(plan.requirements), 15)
        self.assertEqual(len(plan.adapter_methods), 15)
        source_rows = {row['id']: row
                       for row in structured_source_binding()['requirements']}
        for requirement in plan.requirements:
            source = source_rows[requirement.requirement_id]
            operation = REVIEWED_OPERATION[requirement.requirement_id]
            self.assertEqual(requirement.source_method, source['method'])
            self.assertEqual(requirement.source_method, _EDGE_CASES[operation][0])
            self.assertEqual(requirement.source_path, source['path'])
            self.assertEqual(requirement.actor, source['actor'])
            self.assertEqual(requirement.actor,
                             'CEO' if _EDGE_CASES[operation][2] == 'authenticated_bot'
                             else 'HUMAN')
            self.assertEqual(requirement.public_path, _EDGE_CASES[operation][1])

    def test_bindings_declare_the_exact_reviewed_contract(self):
        value = base_profile()
        for operation, path, auth in BINDING_ROUTES:
            binding = value['edge_case_bindings'][operation]
            self.assertEqual(binding['path'], path)
            self.assertEqual(binding['api_face'], 'internal-v1')
            self.assertEqual(binding['auth'], auth)
            self.assertEqual(binding['response'], 'public-envelope')
            self.assertEqual(binding['traceparent'], 'disabled')
        self.assertEqual(value['edge_case_bindings']['structured.group.create']['caller_actor_id'],
                         DRIVER)

    def test_binding_drift_fails_before_network(self):
        def create_binding(value):
            return value['edge_case_bindings']['structured.group.create']

        mutations = {
            'method': lambda v: v['edge_case_bindings']['structured.group.read'].update(method='POST'),
            'path': lambda v: v['edge_case_bindings']['structured.run.read'].update(
                path=STRUCTURED_RUN_PATH.format(run_id='run-2')),
            'placeholder': lambda v: v['edge_case_bindings']['structured.group.delete'].update(
                path='/api/v1/groups/{group_id}'),
            'face': lambda v: v['edge_case_bindings']['structured.run.read'].update(api_face='public-v1'),
            'auth': lambda v: v['edge_case_bindings']['structured.run.cancel'].update(auth='unauthenticated'),
            'create-auth': lambda v: v['edge_case_bindings']['structured.group.create'].update(auth='human_cookie'),
            'response': lambda v: v['edge_case_bindings']['structured.run.start'].update(response='plain-json'),
            'caller': lambda v: create_binding(v).update(caller_actor_id=MEMBER),
            'caller-missing': lambda v: create_binding(v).pop('caller_actor_id'),
            'manifest': lambda v: v['entrypoint'].update(manifest_face='legacy'),
            'entrypoint': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
            'unknown-operation': lambda v: v['edge_case_bindings'].update(structured_ghost_read={
                'method': 'GET', 'path': '/api/v1/ghost', 'api_face': 'internal-v1',
                'auth': 'human_cookie', 'response': 'public-envelope',
                'traceparent': 'disabled'}),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_station_preprod_structured_create_binds_the_human_cookie(self):
        for flag in ('gateway', 'direct'):
            with self.subTest(entrypoint=flag):
                value = base_profile(**{flag: True})
                self.assertEqual(
                    value['edge_case_bindings']['structured.group.create']['auth'],
                    'human_cookie')
                value['edge_case_bindings']['structured.group.create']['auth'] = (
                    'authenticated_bot')
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_a_bound_caller_mismatch_blocks_the_authenticated_bot_create(self):
        value = base_profile()
        value['edge_case_bindings']['structured.group.create']['caller_actor_id'] = MEMBER
        with self.assertRaises(ConfigError):
            validate(value)

    def test_the_slice_stays_uncredited_and_unrun(self):
        adaptability = structured_core_adaptability()
        self.assertEqual(adaptability['case_id'], CASE_ID)
        self.assertEqual(adaptability['legacy_id'], 'story_user_runs_structured_collaboration')
        self.assertEqual(adaptability['requirement_ids'], list(CORE_REQUIREMENT_IDS))
        self.assertEqual(adaptability['local_executor_status'], 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(adaptability['remote_validation'], 'NOT_RUN')
        self.assertFalse(adaptability['grants_release_credit'])
        self.assertIn('singlebox-public-slice', adaptability['environments'])
        self.assertIn('preprod-public-slice', adaptability['environments'])
        row = next(item for item in migration_plan()['public_v1_case_slices']
                   if item['case_id'] == CASE_ID)
        self.assertEqual(row, adaptability)


class RemoteStructuredCoreAdmissionTests(unittest.TestCase):
    def test_missing_bindings_fail_in_the_adapter_before_network(self):
        value = base_profile()
        value['edge_case_bindings'].pop('structured.run.node.read')
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            with self.assertRaises(RunStateError):
                RemoteStructuredCoreAdapter(value)
            factory.open.assert_not_called()
            write_factory.open.assert_not_called()

    def test_unadmitted_entrypoints_are_refused(self):
        for kind, tier in (('mock', 'local'), ('singlebox', 'preprod'),
                           ('gateway', 'local')):
            with self.subTest(entrypoint=(kind, tier)):
                value = base_profile()
                value['entrypoint'].update(kind=kind)
                value['environment'].update(tier=tier)
                with self.assertRaises(RunStateError):
                    RemoteStructuredCoreAdapter(value)

    def test_obligation_order_is_enforced_before_the_first_request(self):
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener'):
            adapter = RemoteStructuredCoreAdapter(base_profile())
            with self.assertRaises(RunStateError):
                adapter.read_structured_group()
            factory.open.assert_not_called()


class CreateWithoutIdentity(OpenRecorder):
    """Create acknowledgement that omits the Group identity."""

    def __call__(self, request, timeout=None):
        reply = super().__call__(request, timeout)
        if (request.method == 'POST'
                and request.full_url == ORIGIN + STRUCTURED_GROUPS_CREATE_PATH):
            body = json.loads(reply.getvalue())
            body['data'] = {'label': 'no identity'}
            return Reply(json.dumps(body).encode(), code=reply.code,
                         headers=reply.headers)
        return reply


class RemoteStructuredCoreExecutionTests(unittest.TestCase):
    def run_case(self, recorder=None):
        recorder = recorder if recorder is not None else OpenRecorder()
        environment = {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                       'BCS_E2E_HUMAN_COOKIE': COOKIE}
        with patch.dict('os.environ', environment), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = recorder
            write_factory.return_value.open.side_effect = recorder
            return (execute_structured_core_case(
                RemoteStructuredCoreAdapter(base_profile())),
                recorder.requests)

    def test_ordered_flow_executes_all_15_reviewed_obligations(self):
        execution, requests = self.run_case()
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 15)
        self.assertEqual(execution.total_count, 15)
        self.assertIsNone(execution.error_type)
        self.assertEqual([row.status for row in execution.steps], ['PASS'] * 15)
        self.assertEqual([request.method for request in requests],
                         ['POST', 'GET', 'GET', 'PATCH', 'POST', 'POST',
                          'GET', 'GET', 'GET', 'GET', 'POST', 'POST',
                          'GET', 'POST', 'DELETE'])
        paths = [request.full_url.split(ORIGIN, 1)[1] for request in requests]
        self.assertEqual(paths, [
            STRUCTURED_GROUPS_CREATE_PATH,
            STRUCTURED_GROUP_PATH.format(group_id=GROUP),
            STRUCTURED_GROUP_DEFINITION_PATH.format(group_id=GROUP),
            STRUCTURED_GROUP_DEFINITION_PATH.format(group_id=GROUP),
            STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH.format(group_id=GROUP),
            STRUCTURED_GROUP_RUN_START_PATH.format(group_id=GROUP),
            STRUCTURED_RUN_PATH.format(run_id=RUN),
            STRUCTURED_RUN_GRAPH_PATH.format(run_id=RUN),
            STRUCTURED_RUN_NODE_PATH.format(run_id=RUN),
            STRUCTURED_RUN_PENDING_HUMAN_PATH.format(run_id=RUN),
            STRUCTURED_RUN_HUMAN_RESPOND_PATH.format(run_id=RUN),
            STRUCTURED_RUN_CANCEL_PATH.format(run_id=RUN),
            STRUCTURED_RUN_PATH.format(run_id=RUN),
            STRUCTURED_RUN_RERUN_PATH.format(run_id=RUN),
            STRUCTURED_GROUP_DELETE_PATH.format(group_id=GROUP, driver_actor_id=DRIVER),
        ])
        for request in requests:
            lower = {name.lower() for name in request.headers}
            self.assertIn('cookie', lower)
            self.assertIn('accept', lower)
        create = requests[0]
        self.assertIn('authorization', {name.lower() for name in create.headers})
        self.assertIn('content-type', {name.lower() for name in create.headers})
        for request in requests[1:]:
            self.assertNotIn('authorization', {name.lower() for name in request.headers})

    def test_station_direct_profile_uses_native_routes_and_plain_json(self):
        profile = base_profile(direct=True)
        self.assertEqual(profile['entrypoint']['kind'], 'bcs-direct')
        self.assertEqual(profile['entrypoint']['manifest_face'], 'legacy')
        for operation, path, auth in DIRECT_BINDING_ROUTES:
            binding = profile['edge_case_bindings'][operation]
            self.assertEqual(binding['path'], path)
            self.assertEqual(binding['response'], 'plain-json')
            self.assertEqual(binding['auth'], auth)
        recorder = StationDirectRecorder()
        environment = {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                       'BCS_E2E_HUMAN_COOKIE': COOKIE}
        with patch.dict('os.environ', environment),              patch('bcs_e2e.transport.build_opener') as factory,              patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = recorder
            write_factory.return_value.open.side_effect = recorder
            execution = execute_structured_core_case(
                RemoteStructuredCoreAdapter(profile))
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 15)
        self.assertFalse('X-Request-ID' in recorder.requests[1].headers)
        self.assertEqual(urlsplit(recorder.requests[0].full_url).path,
                         STRUCTURED_DIRECT_PATHS['structured.group.create'])
        body = json.loads(recorder.requests[0].data)
        self.assertEqual(body['participant_bindings']['driver'],
                         {'source': 'manual', 'bot_ids': [DRIVER]})
        self.assertEqual(urlsplit(recorder.requests[-1].full_url).path,
                         STRUCTURED_DIRECT_PATHS['structured.group.delete'].split('?', 1)[0].format(
                             group_id=GROUP))
        self.assertNotIn(COOKIE, json.dumps(execution.snapshot(), sort_keys=True))
        self.assertNotIn(TOKEN, json.dumps(execution.snapshot(), sort_keys=True))

        recorder = OpenRecorder()
        environment = {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                       'BCS_E2E_HUMAN_COOKIE': COOKIE}
        value = base_profile(gateway=True)
        with patch.dict('os.environ', environment), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = recorder
            write_factory.return_value.open.side_effect = recorder
            execution = execute_structured_core_case(
                RemoteStructuredCoreAdapter(value))
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 15)
        self.assertEqual(recorder.requests[0].full_url.split('https://teamclawgw.example.com', 1)[1],
                         STRUCTURED_GROUPS_CREATE_PATH)

    def test_execution_evidence_exposes_no_credential(self):
        execution, _ = self.run_case()
        serialized = json.dumps(execution.snapshot(), sort_keys=True)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn(TOKEN, serialized)
        for step in execution.steps:
            self.assertEqual(step.evidence['identity'],
                             'AUTHENTICATED_DRIVER_BOT' if step.requirement_id
                             == CORE_REQUIREMENT_IDS[0] else 'HUMAN_COOKIE')
            self.assertTrue(step.evidence['credentials_sent'])
            self.assertEqual(step.evidence['authorization_sent'],
                             step.requirement_id == CORE_REQUIREMENT_IDS[0])

    def test_business_failures_block_the_case_without_partial_credit_on_failure(self):
        recorder = OpenRecorder()
        recorder.run_group_id = 'another-group'
        execution, requests = self.run_case(recorder)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertLess(execution.completed_count, 15)
        self.assertEqual(execution.steps[5].status, 'NOT_RUN')
        self.assertEqual(execution.steps[5].error_type, 'RunStateError')

    def test_group_create_without_identity_blocks_with_zero_credit(self):
        execution, _ = self.run_case(CreateWithoutIdentity())
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_pending_human_nodes_projection_accepts_only_the_empty_array(self):
        profile_value = base_profile()
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            adapter = RemoteStructuredCoreAdapter(profile_value)
            client = adapter.read_client
            factory.return_value.open.side_effect = (
                lambda request, timeout=None: respond(request, data=[]))
            observation = client.read('structured.run.pending.human.nodes',
                                      run_id=RUN)
        self.assertEqual(observation.data, {'items': []})
        self.assertTrue(observation.evidence['credentials_sent'])
        self.assertFalse(observation.evidence['authorization_sent'])

        def pending(request, timeout=None):
            return respond(request, [{'node_id': 'answer'}])

        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(base_profile())
            factory.return_value.open.side_effect = pending
            with self.assertRaises(ObservationError):
                client.read('structured.run.pending.human.nodes', run_id=RUN)

    def test_structured_group_and_run_routes_require_bound_identifiers(self):
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(base_profile())
            with self.assertRaises(ObservationError):
                client.read('structured.group.read')
            with self.assertRaises(ObservationError):
                client.read('structured.group.read', group_id='')
            with self.assertRaises(ObservationError):
                client.read('structured.run.read')
            with self.assertRaises(ObservationError):
                client.read('structured.run.read', run_id='')
            factory.open.assert_not_called()

    def test_unstructured_operations_are_refused_by_the_read_transport(self):
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(base_profile())
            with self.assertRaises(ObservationError):
                client.read('structured.core.summary')
            factory.open.assert_not_called()

    def test_write_transport_admits_only_reviewed_structured_routes(self):
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.write_transport.build_opener') as factory:
            client = WriteClient(base_profile())
            with self.assertRaises(WriteTransportError):
                client.structured_call('structured.core.summary')
            with self.assertRaises(WriteTransportError):
                client.structured_call('structured.group.read')
            with self.assertRaises(WriteTransportError):
                client.structured_call('structured.group.read',
                                       group_id=GROUP)
            factory.open.assert_not_called()

    def test_negative_structured_assertions_require_explicit_error_admission(self):
        recorder = OpenRecorder()
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.write_transport.build_opener') as factory:
            client = WriteClient(base_profile())
            factory.return_value.open.side_effect = recorder
            with self.assertRaises(WriteTransportError):
                client.structured_call('structured.run.human.response.denied',
                                       run_id=RUN,
                                       body=dict(content='semantic probe'),
                                       expected=(400,), parse_error=False)
        self.assertEqual(recorder.requests[-1].method, 'POST')

    def test_repeated_obligation_invocation_is_refused(self):
        execution, _ = self.run_case()
        self.assertEqual(execution.status, 'PASS')
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = OpenRecorder()
            write_factory.return_value.open.side_effect = OpenRecorder()
            adapter = RemoteStructuredCoreAdapter(base_profile())
            adapter.create_structured_group()
            with self.assertRaises(RunStateError):
                adapter.create_structured_group()

    def test_the_frozen_definition_yaml_and_bodies_are_preserved(self):
        plan = structured_core_case_plan()
        self.assertIn('bot_id: "${BOT_CEO_UUID}"', DEFINITION_YAML)
        self.assertIn('graph_mode: acyclic', DEFINITION_YAML)
        self.assertIn('instruction: Draft a concise release recommendation.',
                      DEFINITION_YAML)
        self.assertIn('binding: driver', DEFINITION_YAML)
        self.assertEqual(DEFINITION_YAML.count('\n'), 17)
        for requirement in plan.requirements:
            self.assertTrue(requirement.expectation)


if __name__ == '__main__':
    unittest.main()
