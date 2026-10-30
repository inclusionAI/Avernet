"""Collaboration-team owned-local admission and frozen canonical reporting."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.collaboration_team_contract import (
    ADAPTER_METHODS,
    CASE_ID,
    REQUIREMENT_IDS,
    SUITE,
    collaboration_team_source_binding,
)
from bcs_e2e.inventory import current_manifest, executor_digest
from bcs_e2e.local_operator_cli_contract import COLLABORATION_TEAM_PARENT as PARENT
from bcs_e2e.local_collaboration_team_reporting import (
    _business_evidence,
    build_collaboration_team_report,
    publish_collaboration_team,
)
from bcs_e2e.local_collaboration_team_run import _admit_friendship, local_collaboration_team_run
from bcs_e2e.run_state import RunStateError, read, write


def profile():
    return {
        'schema_version': 1,
        'contract': 'dual-collaboration-team-cli-profile-v1',
        'environment': {'tier': 'local', 'id': 'local', 'expected_bcs_env': 'local'},
        'entrypoint': {'kind': 'singlebox', 'origin': 'http://127.0.0.1:21000',
                        'api_face': 'legacy', 'manifest_face': 'legacy'},
        'human': {'user_id': 'human-1'},
        'actors': {
            name: {'bot_id': value, 'owner_id': 'human-1', 'dedicated': True,
                    'visibility': 'public'}
            for name, value in {'engineering': 'eng', 'customer_service': 'cs',
                                 'driver': 'pm', 'qa': 'qa'}.items()
        },
        'auth': {
            'kind': 'owned-local-bot-tokens',
            'engineering_token_env': 'BCS_E2E_ENG_BOT_TOKEN',
            'customer_service_token_env': 'BCS_E2E_CS_BOT_TOKEN',
            'driver_token_env': 'BCS_E2E_PM_BOT_TOKEN',
            'qa_token_env': 'BCS_E2E_QA_BOT_TOKEN',
        },
        'relationships': {
            'visibility': 'owned-cs-protect-then-public-restore',
            'friendship': 'owned-eng-cs-request-then-reject',
            'group': 'two-owned-pm-qa-groups',
            'cleanup': 'none-runtime-or-operator-created',
            'retention': 'request-and-group-history',
        },
        'policy': {'fixture_pool': 'trusted-team', 'exclusive_use_confirmed': True,
                   'timeout_seconds': 30, 'max_response_bytes': 65_536,
                   'failure_retention_hours': 24},
        'cli': {'transport': 'product-cli', 'command_family': 'collaboration-team',
                 'api_face': 'legacy'},
    }


def case():
    data = (
        {'customer_service_id': 'cs', 'protected': True, 'restored_public': False},
        {'request_id': 'request-1', 'requester': 'eng', 'target': 'cs',
         'customer_service_id': 'cs', 'request_status': 'pending',
         'rejection_acknowledged': True,
         'requester_final_list_contains_target': False,
         'rejected_request_identifiable': True, 'restored_public': True},
        {'group_id': 'group-1', 'driver': 'pm', 'participants': ['pm', 'eng']},
        {'group_id': 'group-1', 'added_member': 'qa',
         'participant_ids': ['pm', 'eng', 'qa']},
        {'group_id': 'group-1', 'cli_returned': 2, 'api_item_count': 2,
         'cli_contains_group': True, 'api_contains_group': True},
        {'group_id': 'group-1',
         'requested_participant_bot_ids': ['pm', 'eng', 'qa'],
         'recommendation_present': True},
        {'group_id': 'group-1', 'completed': True},
        {'group_id': 'group-2', 'terminated': True, 'completed': True},
    )

    def request(index, transport='PRODUCT_CLI'):
        value = {'transport': transport,
                 'operation': f'operation-{index}', 'caller_role': 'driver',
                 'credential_kind': 'owned_local_bot_token',
                 'remote_validation': 'NOT_RUN',
                 'grants_release_credit': False}
        if transport == 'PRODUCT_CLI':
            value.update({'exit_status': 0, 'command_arguments': ['friend', 'list']})
        else:
            value.update({'http_status': 200, 'route_declared': '/bots/pm/groups'})
        return value

    return {
        'schema_version': 1, 'case_id': CASE_ID, 'parent_id': PARENT,
        'status': 'PASS',
        'completed_count': 8, 'total_count': 8, 'ready_to_execute': True,
        'error_type': None, 'error_detail': None,
        'steps': [{
            'requirement_id': REQUIREMENT_IDS[index], 'method': ADAPTER_METHODS[index],
            'status': 'PASS', 'data': data[index], 'error_type': None,
            'error_gap': None, 'error_detail': None,
            'evidence': {
                'case_operation': f'operation-{index}',
                'requests': [request(index, 'PRODUCT_HTTP' if index == 4 else 'PRODUCT_CLI')],
                'transport': 'PRODUCT_HTTP' if index == 4 else 'PRODUCT_CLI',
                'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
            },
        } for index in range(8)],
    }


class FakeRuntime:
    def __init__(self, root, record):
        self.root = root
        self.record = record

    def _record(self):
        return self.record


class CollaborationTeamLocalRunTests(unittest.TestCase):
    def test_permissions_and_fresh_directory_precede_runtime_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
                'bcs_e2e.local_collaboration_team_run.OwnedBcsProcess') as process:
            arguments = dict(binary=Path('/unused'), binary_sha256='b'*64,
                             cli_binary=Path('/unused-cli'), cli_binary_sha256='c'*64,
                             state_dir=Path(tmp)/'run', run_id='collab-run',
                             confirm_run_id='collab-run',
                             allow_local_processes=True, allow_fixture_write=True,
                             allow_write=True)
            for key, value in (('allow_local_processes', False),
                               ('allow_fixture_write', False),
                               ('allow_write', False), ('confirm_run_id', 'other')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    local_collaboration_team_run(**arguments | {key: value})
            process.assert_not_called()

    def test_friendship_prerequisite_uses_the_created_request_id(self):
        class Identity:
            def actor_id(self, role):
                return f'bot-{role.lower()}'

            _entries = {'PM': ('bot-pm', 'pm-token'), 'ENG': ('bot-eng', 'eng-token')}

        calls = []
        responses = [
            (201, {'success': True, 'data': {
                'id': 'request_1', 'from_bot': 'bot-pm', 'to_bot': 'bot-eng',
                'status': 'pending'}}),
            (200, {'success': True, 'data': {'status': 'accepted'}}),
        ]

        def fake_request(_runtime, method, path, *, token, body=b''):
            calls.append((method, path, token, body))
            return responses.pop(0)

        with patch('bcs_e2e.local_collaboration_team_run._request', fake_request):
            _admit_friendship(object(), Identity(), 'PM', 'ENG')
        self.assertEqual([row[1] for row in calls], [
            '/friends/request', '/friends/requests/request_1/accept'])
        self.assertEqual(calls[0][3], b'{"to_bot": "bot-eng"}')
        self.assertEqual(calls[1][3], b'{}')

    def test_business_evidence_rejects_reused_termination_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp)/'run').resolve()
            binding = {'run_id': 'collab-run',
                       'executor_digest': executor_digest(),
                       'source_binding': collaboration_team_source_binding()}
            valid = case()
            valid.update(run_id='collab-run', executor_digest=binding['executor_digest'])
            self.assertTrue(_business_evidence(root, binding, valid))
            invalid = case()
            invalid['steps'][7]['data']['group_id'] = \
                invalid['steps'][6]['data']['group_id']
            self.assertFalse(_business_evidence(root, binding, invalid))

    def test_canonical_publish_and_offline_report_reject_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp)/'run').resolve()
            root.mkdir()
            runtime = {'schema_version': 1, 'run_id': 'collab-run',
                       'origin': 'http://127.0.0.1:21000', 'pid': 1,
                       'returncode': 0, 'binary_sha256': 'b'*64, 'ready': False,
                       'forced_stop': False, 'status': 'STOPPED',
                       'ownership': 'live-child-handle-only'}
            binding = {
                'schema_version': 1,
                'contract': 'owned-local-collaboration-team-binding-v1',
                'suite': SUITE, 'case_id': CASE_ID, 'case_version': 1,
                'run_id': 'collab-run', 'executor_digest': executor_digest(),
                'bcs_binary_sha256': 'b'*64, 'cli_binary_sha256': 'c'*64,
                'store_messages': False,
                'source_binding': collaboration_team_source_binding(),
                'selected_requirement_ids': list(REQUIREMENT_IDS),
                'adapter_methods': list(ADAPTER_METHODS),
                'legacy_inventory': current_manifest()['legacy_inventory'],
                'runtime_dir': str(root),
            }
            frozen_case = case()
            frozen_case.update(run_id='collab-run', executor_digest=binding['executor_digest'],
                               binding_contract=binding['contract'],
                               profile_contract='dual-collaboration-team-cli-profile-v1')
            flow = {'schema_version': 1, 'status': 'LOCAL_COLLABORATION_TEAM_PASS',
                    'run_id': 'collab-run', 'case_id': CASE_ID,
                    'executor_digest': binding['executor_digest'],
                    'case': frozen_case, 'shutdown': runtime,
                    'credentials': {'credential_memory': 'CLEARED'},
                    'remote_validation': 'NOT_RUN',
                    'profile_contract': 'dual-collaboration-team-cli-profile-v1',
                    'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
            write(root/'collaboration-team-binding.json', binding)
            write(root/'collaboration-team-flow-report.json', flow)
            write(root/'bcs-config.json', {'schema_version': 1})
            write(root/'owned-bcs.json', runtime)
            write(root/'local-fixtures.json', {'schema_version': 1})
            write(root/'collaboration-team-profile.json', profile())
            write(root/'local-collaboration-team.json', frozen_case)

            published = publish_collaboration_team(FakeRuntime(root, runtime),
                                                    binding, flow)
            self.assertEqual(published['status'], 'LOCAL_SUITE_PASS')
            summary = published['summary']
            self.assertEqual(summary['reports']['expected_assertion_count'], 8)
            self.assertEqual(summary['reports']['observed_assertion_count'], 8)
            self.assertTrue((root/'reports/summary.json').is_file())
            self.assertTrue((root/'reports/junit.xml').is_file())
            self.assertTrue((root/'reports/summary.md').is_file())
            self.assertFalse((root/'collaboration-team-publication-pending.json').exists())

            build = build_collaboration_team_report(root)
            self.assertEqual(build['status'], 'LOCAL_SUITE_PASS')
            self.assertEqual(build['requirements'],
                             [{'id': name, 'status': 'PASS'} for name in REQUIREMENT_IDS])
            path = root/'local-collaboration-team.json'
            original = read(path)
            value = original | {'status': 'BLOCKED'}
            write(path, value)
            drifted = build_collaboration_team_report(root)
            self.assertNotEqual(drifted['status'], 'LOCAL_SUITE_PASS')
            self.assertEqual(drifted['integrity']['status'], 'BLOCKED')
            write(path, original)
            self.assertEqual(build_collaboration_team_report(root), build)

            with patch('http.client.HTTPConnection.request',
                       side_effect=AssertionError('offline report contacted a service')):
                self.assertEqual(build_collaboration_team_report(root), build)


if __name__ == '__main__':
    unittest.main()
