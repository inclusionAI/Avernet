"""The Edge friend-mutation source map freezes one parent before any executor exists.

Seven reviewed release parents each keep their historical aggregate obligation.
The two migrated backlog parents now use parsed-granularity obligations: two
for TC friend-auth-sync and four for object/string/garbage department scope.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.edge_friend_mutation_case import (
    BACKLOG_CASE_IDS,
    CASE_IDS,
    PARENTS,
    RELEASE_CASE_IDS,
    SUITE,
    TOTAL_ASSERTION_COUNT,
    _function_body,
    edge_friend_mutation_case_plan,
    edge_friend_mutation_case_spec,
    edge_friend_mutation_source_binding,
)
from bcs_e2e.local_edge_friend_mutation_extras import (
    _MIXED_SCOPE,
    OwnedEdgeFriendMutationExtrasMixin,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError
from bcs_e2e.inventory import current_manifest

MAPPED = (
    ('LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01',
     'test_ep_friend_connections_friend_accept_reject_cancel',
     'EDGE_FRIEND_MUTATIONS.accept-reject-cancel',
     'run_accept_reject_cancel'),
    ('LOCAL-EDGE-FRIEND-REVOKE-01',
     'test_ep_friend_connections_friend_revoke',
     'EDGE_FRIEND_MUTATIONS.revoke',
     'run_revoke'),
    ('LOCAL-EDGE-ENSURE-BOT-01',
     'test_ep_ensure_bot',
     'EDGE_FRIEND_MUTATIONS.ensure-bot',
     'run_ensure_bot'),
    ('LOCAL-EDGE-FRIEND-FULL-LIFECYCLE-01',
     'test_ep_friend_connections_full_lifecycle',
     'EDGE_FRIEND_MUTATIONS.full-lifecycle',
     'run_full_lifecycle'),
    ('LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01',
     'test_ep_friend_connections_mutual_auto_approve',
     'EDGE_FRIEND_MUTATIONS.mutual-auto-approve',
     'run_mutual_auto_approve'),
    ('LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01',
     'test_ep_error_and_admission_branches',
     'EDGE_FRIEND_MUTATIONS.error-admission-branches',
     'run_error_admission_branches'),
    ('LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01',
     'test_ep_connect_private_and_discover_branches',
     'EDGE_FRIEND_MUTATIONS.connect-private-discover',
     'run_connect_private_discover'),
    ('LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01',
     'test_ep_tc_friend_auth_sync_readdressed_principal',
     'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-create-accept',
     'run_tc_friend_request_create_accept'),
    ('LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01',
     'test_ep_dept_scope_parses_object_friend_ext_entries',
     'EDGE_FRIEND_MUTATIONS.dept-scope-mixed-owner-patch',
     'run_dept_scope_mixed_owner_patch'),
)


class EdgeFriendMutationContractTests(unittest.TestCase):

    def test_nine_parents_freeze_the_exact_executed_obligation_set(self):
        self.assertEqual(len(CASE_IDS), 9)
        self.assertEqual(PARENTS, tuple(row[1] for row in MAPPED))
        self.assertEqual(CASE_IDS, tuple(row[0] for row in MAPPED))
        self.assertEqual(RELEASE_CASE_IDS, tuple(row[0] for row in MAPPED[:7]))
        self.assertEqual(BACKLOG_CASE_IDS, tuple(row[0] for row in MAPPED[7:]))
        self.assertEqual(len(set(PARENTS)), 9)
        for case_id, parent, _, _adapter in MAPPED[:7]:
            with self.subTest(case_id=case_id):
                plan = edge_friend_mutation_case_plan(case_id)
                spec = edge_friend_mutation_case_spec(case_id)
                self.assertEqual(plan.parent_id, parent)
                self.assertEqual(len(plan.requirements), 1)
                self.assertEqual(
                    {environment.name for environment in plan.environments},
                    {'owned-local', 'singlebox', 'preprod'})
                binding = edge_friend_mutation_source_binding(case_id)
                self.assertEqual(binding['schema_version'], 1)
                self.assertIn('edge_permission.sh', binding['sources'])
                self.assertEqual(binding['parent'], parent)
                self.assertEqual(
                    [row['id'] for row in binding['requirements']],
                    list(plan.requirement_ids))
                self.assertEqual(spec.adapter_method, _adapter)

    def test_backlog_obligations_reach_parsed_granularity_without_padding(self):
        tc = edge_friend_mutation_case_plan('LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01')
        dept = edge_friend_mutation_case_plan('LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01')
        self.assertEqual(tc.requirement_ids, (
            'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-create-accept',
            'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-principal',
        ))
        self.assertEqual(tc.adapters, {
            'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-create-accept':
                'run_tc_friend_request_create_accept',
            'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-principal':
                'run_tc_friend_auth_sync_readdress',
        })
        self.assertEqual(dept.requirement_ids, (
            'EDGE_FRIEND_MUTATIONS.dept-scope-mixed-owner-patch',
            'EDGE_FRIEND_MUTATIONS.dept-scope-human-no-check',
            'EDGE_FRIEND_MUTATIONS.dept-scope-human-user-scope',
            'EDGE_FRIEND_MUTATIONS.dept-scope-bot-agent-scope',
        ))
        self.assertEqual(dept.adapters, {
            'EDGE_FRIEND_MUTATIONS.dept-scope-mixed-owner-patch':
                'run_dept_scope_mixed_owner_patch',
            'EDGE_FRIEND_MUTATIONS.dept-scope-human-no-check':
                'run_dept_scope_human_no_check',
            'EDGE_FRIEND_MUTATIONS.dept-scope-human-user-scope':
                'run_dept_scope_human_user_scope',
            'EDGE_FRIEND_MUTATIONS.dept-scope-bot-agent-scope':
                'run_dept_scope_bot_agent_scope',
        })
        self.assertEqual(TOTAL_ASSERTION_COUNT, 13)
        for plan in (tc, dept):
            binding = edge_friend_mutation_source_binding(plan.case_id)
            self.assertEqual(binding['parent'], plan.parent_id)
            self.assertEqual(
                [row['id'] for row in binding['requirements']],
                list(plan.requirement_ids))

    def test_function_body_extraction_rejects_missing_or_unterminated_drift(self):
        with self.assertRaises(RunStateError):
            _function_body('unrelated() {\n  echo ok\n}\n', 'test_x')
        with self.assertRaises(RunStateError):
            _function_body('test_x() {\n  echo hi\n', 'test_x')
        body = _function_body(
            'test_x() {\n  echo "/collaboration/friend-connections"\n}\n',
            'test_x')
        self.assertIn('/collaboration/friend-connections', body)

    def test_registered_executors_map_each_parent_without_release_credit(self):
        plan = migration_plan()
        mapped = {row['parent']: row
                  for row in plan['additional_source_maps']}
        expected_counts = {}
        for parent in PARENTS:
            case_id = next(row[0] for row in MAPPED if row[1] == parent)
            case_plan = edge_friend_mutation_case_plan(case_id)
            expected_counts[parent] = len(case_plan.requirements)
            with self.subTest(parent=parent):
                self.assertIn(parent, mapped)
                binding = mapped[parent]
                self.assertEqual(binding['schema_version'], 1)
                self.assertIn('edge_permission.sh', binding['sources'])
                self.assertEqual(
                    len(binding['requirements']), expected_counts[parent])
        rows = [row for row in plan['local_registered_executors']
                if row['case_id'] in set(RELEASE_CASE_IDS)]
        self.assertEqual([row['case_id'] for row in rows], list(RELEASE_CASE_IDS))
        for row, (_, parent, requirement, _) in zip(rows, MAPPED[:7]):
            with self.subTest(case_id=row['case_id']):
                self.assertEqual(row['legacy_id'], parent)
                self.assertEqual(row['suite'], SUITE)
                self.assertEqual(row['case_version'], 1)
                self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
                self.assertEqual(row['required_assertion_count'], 1)
                self.assertEqual(row['requirement_ids'], [requirement])
                self.assertEqual(row['canonical_reporting'], 'INTEGRATED_LOCAL')
                self.assertEqual(
                    row['execution_command'], 'local-edge-friend-mutations-run')
                self.assertEqual(
                    row['report_command'], 'local-edge-friend-mutations-report')
                self.assertEqual(row['remote_validation'], 'NOT_RUN')
                self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertEqual(plan['passed_assertion_count'], 0)

    def test_dept_scope_mixed_patch_carries_all_three_entry_shapes(self):
        no_check = _MIXED_SCOPE['friend_ext']['no_check_scope_friend_deps']
        agent = _MIXED_SCOPE['friend_ext']['view_scope_agent_friend_deps']
        self.assertEqual([
            type(entry).__name__ if isinstance(entry, dict) else 'str'
            for entry in no_check
        ], ['dict', 'str'])
        self.assertTrue(any(
            isinstance(entry, dict) and 'deptNo' in entry for entry in no_check))
        self.assertTrue(any(
            isinstance(entry, str) and entry for entry in no_check))
        self.assertTrue(any(
            isinstance(entry, dict) and 'deptNo' not in entry for entry in agent))

    def test_dept_scope_refusions_keep_parser_outcomes_without_request_rows(self):
        class RefusingAdapter:
            def _begin(self):
                return object()
            def _principal_token(self, **_values):
                return 'principal'
            def _wire(self, **_values):
                return (200 if _values['method'] == 'PATCH' else 403,
                        {'error': 'out_of_scope'})
            def _forwarded_request(self, **_values):
                return 403, {'error': 'out_of_scope'}
            def _token(self, _role):
                return 'owned-bot-token'
            def _finish(self, _row, evidence, data):
                return {'evidence': evidence, 'data': data}
            def _blocked(self, _row, _error):
                pass
        adapter = RefusingAdapter()
        human = OwnedEdgeFriendMutationExtrasMixin.run_dept_scope_human_user_scope(
            adapter)
        self.assertEqual(human['evidence']['human_user_scope_status'], 403)
        self.assertEqual(human['evidence']['human_user_scope_outcome'], 'out_of_scope')
        self.assertIsNone(human['evidence']['human_user_scope_request_id'])
        bot = OwnedEdgeFriendMutationExtrasMixin.run_dept_scope_bot_agent_scope(
            adapter)
        self.assertEqual(bot['evidence']['bot_agent_scope_status'], 403)
        self.assertEqual(bot['evidence']['bot_agent_scope_outcome'], 'out_of_scope')
        self.assertIsNone(bot['evidence']['bot_agent_scope_request_id'])

    def test_backlog_testcases_stay_outside_the_release_parent_registry(self):
        plan = migration_plan()
        registered = {row['case_id'] for row in plan['local_registered_executors']}
        backlog = [row for row in plan['local_backlog_case_slices']
                   if row['suite'] == SUITE]
        self.assertEqual([row['case_id'] for row in backlog], list(BACKLOG_CASE_IDS))
        expected = {
            'LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01': 2,
            'LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01': 4,
        }
        for row in backlog:
            with self.subTest(case_id=row['case_id']):
                self.assertNotIn(row['case_id'], registered)
                self.assertTrue(row['independent_slice'])
                self.assertFalse(row['release_parent_denominator'])
                self.assertFalse(row['grants_release_credit'])
                self.assertEqual(row['status'], 'DUAL_EXECUTION_READY')
                self.assertEqual(row['suite'], SUITE)
                self.assertEqual(row['canonical_reporting'], 'DUAL_EXECUTION_READY')
                self.assertEqual(row['execution_command'],
                                 'dual-edge-friend-mutation-run')
                self.assertEqual(row['report_command'], '')
                self.assertEqual(row['required_parameters'], [
                    'profile', 'station_profile', 'case', 'state_dir',
                    'run_id', 'confirm_run_id', 'allow_write'])
                self.assertEqual(
                    row['required_assertion_count'], expected[row['case_id']])
                self.assertEqual(
                    row['requirement_ids'],
                    list(edge_friend_mutation_case_plan(row['case_id']).requirement_ids))


if __name__ == '__main__':
    unittest.main()
