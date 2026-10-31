"""Source-bound parent Stories cannot be replaced by pilot slices or soft PASS."""
import copy
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import plan
from bcs_e2e.inventory import (
    KNOWN_UNMAPPED_LEGACY_IDS,
    current_manifest,
    legacy_inventory_with_executor_migration,
    load_executor_migration,
    reviewed_release_inventory,
)
from bcs_e2e.local_caller_execution_contract import caller_execution_source_binding
from bcs_e2e.local_draft_group_contract import draft_group_source_binding
from bcs_e2e.local_nested_fixed_loop_contract import (
    nested_fixed_loop_source_binding)
from bcs_e2e.local_result_queue_contract import result_queue_source_binding
from bcs_e2e.local_custom_collaboration_case import (
    CUSTOM_COLLABORATION_REQUIREMENTS, DENSITY_REQUIREMENTS, DENSITY_SPECS,
    custom_collaboration_source_binding as owned_custom_collaboration_source_binding)
from bcs_e2e.local_operator_cli_contract import (
    collaboration_team_source_binding,
    custom_collaboration_source_binding,
    sessions_services_source_binding,
)
from bcs_e2e.local_story_commands import CASES
from bcs_e2e.migration_inventory import SOURCES, migration_plan, validate_contract
from bcs_e2e.profile import load
from bcs_e2e.run_state import RunStateError
from bcs_e2e.draft_group_density.registration_proposal import (  # noqa: E402
    NOTE as DRAFT_GROUP_DENSITY_NOTE)
from bcs_e2e.result_queue_density.registration_proposal import (  # noqa: E402
    NOTE as RESULT_QUEUE_DENSITY_NOTE)
from bcs_e2e.operator_cli_density.registration_proposal import (  # noqa: E402
    NOTE as OPERATOR_CLI_DENSITY_NOTE)
from bcs_e2e.agent_network_density.registration_proposal import (  # noqa: E402
    NOTE as AGENT_NETWORK_DENSITY_NOTE)
from bcs_e2e.fixed_loop_density.registration_proposal import (  # noqa: E402
    FIXED_ROW_NOTE_PROPOSAL, NESTED_ROW_NOTE_PROPOSAL)


OWNER_ISOLATION_PROVIDER_NOTE = "row #35 story_provider_owner_isolation: register the 24 source-anchored obligation roster (PROVIDER-OWNER.*; contract provider_owner_contract.py; upstream provider_scope_story.py frozen at f181ba448a8bd5844c9517d8543ff3b6bb9d66bb with story_checks=45; shell step map provider-owner-isolation-migration-contract.json; known-unmapped backlog parent outside the reviewed release denominator); r2 owned-local live leg rid 20261031-owner-isolation-local-live-r2-provider-owner receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r2/provider-owner-stdout.json status=BLOCKED completed 0/24 error_type=LOCAL_PROVIDER_OWNER_BLOCKED (tier=local entrypoint=owned-local credential_kind=owned_local_ephemeral_principal case DUAL-PROVIDER-OWNER-01 contract dual-provider-owner-case-run-v1 all 24 steps NOT_RUN): the story child registers provider prv_59ca9519 and bot bot_e38133ac, bootstraps the foreign Human, POST /providers/{id}/bots answers 200, then story request 8 PUT /bots/bot_09741ff7/visibility expects 200 and receives 403, the child raises AssertionError at provider_scope_story.py L117 and exits 1, the executor fail-closes at 0/24, and the full stderr traceback is retained as bounded evidence while the receipt error_detail stays null by design; this is the contract-frozen drift gap OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT (provider registration records created_by but mints no bcs-edge-permission authority edges on the owned-local runtime; the story frozen at f181ba44 predates the bot-authority model c6f001209 #2625; poolab preprod green there because the preprod runtime seeds the edges; story source apps/bcs/scripts/e2e-test/provider_scope_story.py exists locally and not on upstream dev; the BLOCKED receipt is the contract expectation, not a regression); preprod stays REMOTE_UNSUPPORTED by design (PREPROD_PROVIDER_ADMIN_FACE_ABSENT, WORKBENCH_WS_MOCK_ADMISSION) and the wp2 preprod disclosure verdict ~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod/provider-owner/20261031-migration-wp2-provider-owner-preprod-disclosure-verdict.json stays BLOCKED (PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED, credential_admission BLOCKED PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE); the r1 root holds no provider-owner local leg receipt; notes-only integration, the dual-provider-owner-run envelope stays untouched; committed verdict stays DUAL/NOT_RUN with notes 0 to 1; remote_validation=NOT_RUN and grants_release_credit=false"
OWNER_ISOLATION_SESSION_NOTE = "row #36 story_session_owner_permissions: register the 9 source-anchored obligation roster (SESSION-OWNER.*; contract session_owner/session_owner_contract.py; story_checks=60 plus cleanup_checks=4, frozen required assertion inventory 78 = run_http 60 + cleanup_http 4 + shell 1 + structural_python 13; shell step map session-owner-permissions-migration-contract.json; known-unmapped backlog parent outside the reviewed release denominator); r1 receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r1/session-owner-stdout.json is the preprod-tier probe leg and is NOT a local live leg (tier=preprod id=dual-session-owner-preprod-r7 entrypoint=gateway api_face=public-v1 credential_kind=preprod_human_cookie_route_probe run_id 20261031-owner-isolation-local-live-r1-session-owner): status=BLOCKED completed 0/9 error PREPROD_SESSION_OWNER_FULL_STORY_NOT_EXECUTED, preprod probe BLOCKED PREPROD_PROBE_BASELINE_UNEXPECTED, four named gaps PREPROD_STATION_BOOTSTRAP_FACE_ABSENT PREPROD_HUMAN_PRINCIPAL_FAMILY_SINGLE PREPROD_PRINCIPAL_MINTING_OPERATOR_KEY PREPROD_PARTICIPANT_ROUTES_UNBOUND, mirrored by wp2 verdict ~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod/session-owner/20261031-owner-isolation-local-live-r1-session-owner-verdict.json (BLOCKED, remote_validation=NOT_RUN, grants_release_credit=false); r2 owned-local live leg rid 20261031-owner-isolation-local-live-r2-session-owner receipt ~/.codex/bcs-e2e/20261031-owner-isolation-local-live-r2/session-owner-stdout.json status=PASS completed 9/9 LOCAL_SESSION_OWNER_PASS (tier=local entrypoint=owned-local credential_kind=owned_local_ephemeral_gateway_principals case DUAL-SESSION-OWNER-01 contract dual-session-owner-case-run-v1): journal observed_assertion_count=64 against the frozen required inventory 78 (run_http 60 + cleanup_http 4 + shell 1 + structural_python 13 per contract UPSTREAM_ASSERTIONS), requirement_statuses 9/9 PASS, cleanup disposition SESSION_GROUP_AND_OWNED_BOTS_DELETED with zero process leak; notes-only integration, the dual-session-owner-run envelope stays untouched; committed verdict stays DUAL/NOT_RUN with notes 0 to 1 (preprod verdict stays NOT_RUN honestly; neither leg grants release credit); remote_validation=NOT_RUN and grants_release_credit=false"


class MigrationInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name)
        for name in SOURCES:
            shutil.copyfile(ROOT.parent / 'e2e-test' / name, self.source / name)
        self.contract = json.loads((ROOT / 'migration-contracts.json').read_text())

    def test_original_source_and_complete_three_parent_inventory_match(self):
        self.assertEqual(validate_contract(self.contract, source_root=self.source), self.contract)
        result = migration_plan()
        self.assertEqual(result['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(result['mapped_story_count'], 3)
        self.assertEqual([len(s['steps']) for s in result['stories']], [16, 5, 1])
        self.assertEqual([len(s['requirements']) for s in result['stories']], [17, 5, 10])
        self.assertEqual(result['migrated_story_count'], 0)
        self.assertEqual(result['passed_assertion_count'], 0)
        self.assertFalse(result['ready_to_execute'])
        self.assertTrue(all(s['migration_status'] == 'NOT_MIGRATED' for s in result['stories']))

    def test_collaboration_team_source_binding_freezes_the_full_parent_map(self):
        binding = collaboration_team_source_binding()
        expected_ids = (
            'CLI-COLLAB-TEAM.protect-visibility',
            'CLI-COLLAB-TEAM.friend-request-reject',
            'CLI-COLLAB-TEAM.group-create-read',
            'CLI-COLLAB-TEAM.group-add-member',
            'CLI-COLLAB-TEAM.group-list',
            'CLI-COLLAB-TEAM.group-fuse',
            'CLI-COLLAB-TEAM.group-complete',
            'CLI-COLLAB-TEAM.group-terminate',
        )
        self.assertEqual(binding['parent'], 'story_cli_operator_builds_collaboration_team')
        self.assertEqual([row['id'] for row in binding['requirements']], list(expected_ids))
        self.assertEqual(len(binding['requirements']), 8)
        self.assertTrue(all(row['expectation'] for row in binding['requirements']))
        for mutation in ('remove', 'command', 'expectation'):
            value = copy.deepcopy(binding)
            if mutation == 'remove':
                value['requirements'].pop()
            elif mutation == 'command':
                value['requirements'][0]['command'] = 'bcs-cli foreign'
            else:
                value['requirements'][0]['expectation'] = 1
            with self.subTest(mutation=mutation), \
                 patch('bcs_e2e.local_operator_cli_contract.read', return_value=value), \
                 self.assertRaises(RunStateError):
                collaboration_team_source_binding()

    def test_migration_plan_exposes_collaboration_team_map_without_migrated_story_credit(self):
        result = migration_plan()
        row = next(r for r in result['additional_source_maps']
                   if r['parent'] == 'story_cli_operator_builds_collaboration_team')
        self.assertEqual(len(row['requirements']), 8)
        self.assertEqual(result['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(result['migrated_story_count'], 0)
        self.assertEqual(result['mapped_story_count'], 3)
        self.assertEqual(result['passed_assertion_count'], 0)

    def test_sessions_services_source_binding_freezes_four_business_obligations(self):
        binding = sessions_services_source_binding()
        expected_ids = (
            'CLI-SESSION-SERVICE.session-lifecycle',
            'CLI-SESSION-SERVICE.member-chat-lifecycle',
            'CLI-SESSION-SERVICE.session-complete',
            'CLI-SESSION-SERVICE.service-roundtrip',
        )
        self.assertEqual(binding['parent'], 'story_cli_operator_runs_sessions_and_services')
        self.assertEqual([row['id'] for row in binding['requirements']], list(expected_ids))
        self.assertTrue(all(row['expectation'] for row in binding['requirements']))
        for mutation in ('remove', 'command', 'expectation'):
            value = copy.deepcopy(binding)
            if mutation == 'remove':
                value['requirements'].pop()
            elif mutation == 'command':
                value['requirements'][0]['command'] = 'bcs-cli foreign command'
            else:
                value['requirements'][0]['expectation'] = 1
            with self.subTest(mutation=mutation), \
                 patch('bcs_e2e.local_operator_cli_contract.read', return_value=value), \
                 self.assertRaises(RunStateError):
                sessions_services_source_binding()

    def test_migration_plan_exposes_sessions_services_map_without_story_credit(self):
        result = migration_plan()
        row = next(r for r in result['additional_source_maps']
                   if r['parent'] == 'story_cli_operator_runs_sessions_and_services')
        self.assertEqual(len(row['requirements']), 4)
        self.assertEqual(result['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(result['migrated_story_count'], 0)
        self.assertEqual(result['mapped_story_count'], 3)
        self.assertEqual(result['passed_assertion_count'], 0)

    def test_session_service_parent_case_closes_all_four_local_obligations(self):
        result = migration_plan()
        local = next(r for r in result['local_registered_executors']
                     if r['case_id'] == 'LOCAL-SESSION-SERVICE-PARENT-01')
        self.assertEqual(local['legacy_id'], 'story_cli_operator_runs_sessions_and_services')
        self.assertEqual(local['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(local['required_assertion_count'], 44)
        self.assertEqual(local['execution_command'], 'execute_local_session_service_parent_case')
        self.assertEqual(local['requirement_ids'], [
            'CLI-SESSION-SERVICE.session-lifecycle',
            'CLI-SESSION-SERVICE.member-chat-lifecycle',
            'CLI-SESSION-SERVICE.session-complete',
            'CLI-SESSION-SERVICE.service-roundtrip',
        ])
        self.assertEqual(local['canonical_reporting'], 'INTEGRATED_LOCAL')
        self.assertEqual(local['remote_validation'], 'NOT_RUN')
        self.assertFalse(local['grants_release_credit'])
        superseded = {'LOCAL-SESSION-SERVICE-LIFECYCLE-01',
                      'LOCAL-SESSION-SERVICE-MEMBER-CHAT-01',
                      'LOCAL-SESSION-SERVICE-COMPLETE-01',
                      'LOCAL-SESSION-SERVICE-ROUNDTRIP-01'}
        self.assertFalse(any(r['case_id'] in superseded for r in result['local_partial_executors']))
        parent = next(r for r in result['cross_environment_product_cli_case_slices']
                      if r['case_id'] == 'LOCAL-SESSION-SERVICE-PARENT-01')
        self.assertEqual(parent['local_executor_status'], 'LOCAL_FULL_PARENT_EXECUTOR')
        self.assertEqual(parent['remote_validation'], 'NOT_RUN')
        self.assertFalse(parent['grants_release_credit'])
        self.assertIn('preprod', parent['blocker'])

    def test_custom_collaboration_source_binding_freezes_validate_create_and_trial_paths(self):
        binding = custom_collaboration_source_binding()
        self.assertEqual(binding['parent'], 'story_cli_operator_creates_custom_collaboration')
        self.assertEqual([row['id'] for row in binding['requirements']],
                         ['CUSTOM-COLLABORATION.validate-yaml',
                          'CUSTOM-COLLABORATION.create-persistent-group',
                          'CUSTOM-COLLABORATION.trial-one-shot-lifecycle'])
        self.assertEqual([row['command'] for row in binding['requirements']],
                         ['bcs-cli collaboration validate <yaml>',
                          'bcs-cli collaboration create <yaml> --driver <driver> '
                          '--binding planner=<driver> --binding reviewer=<reviewer> '
                          '--context <context> --topic <topic>',
                          'bcs-cli create-group + session add-member + collaborate permission '
                          '+ collaborate run + collaborate query --graph/--pending '
                          '+ collaborate respond guard + session messages '
                          '+ run cancel/session read/group delete'])
        self.assertEqual(binding['requirements'][0]['expectation'],
                         'Product CLI validation reports valid=true with exactly two '
                         'participants and two nodes, without persisting resources '
                         'or exposing YAML contents.')
        self.assertEqual(binding['requirements'][1]['expectation'],
                         'Product CLI creates the exact two-role custom collaboration '
                         'Group with its initial Session handle; CLI readback reports '
                         'driver, participants, and group_strategy=state_machine '
                         'before cleanup.')
        self.assertEqual(binding['requirements'][2]['expectation'],
                         'Product CLI creates the owned temporary chat Group, joins the '
                         'exact Human session member, confirms current-session '
                         'state-machine permission, starts the one-shot Run with fixed '
                         'bindings/input, reads the exact graph, pending Human, guarded '
                         'respond refusal, restored state-machine history, then cancels '
                         'the Run and deletes the temporary Group while the Session '
                         'remains running.')
        self.assertEqual([row['decision_id'] for row in binding['convergence_decisions']],
                         ['custom-collaboration-trial-group-domain-transport',
                          '20261030-s14-session-membership-permission-rechained',
                          '20261030-s14-session-domain-run-start-restored'])
        owned = owned_custom_collaboration_source_binding()
        self.assertEqual([row['id'] for row in owned['requirements']],
                         list(CUSTOM_COLLABORATION_REQUIREMENTS + DENSITY_REQUIREMENTS))
        self.assertEqual(len(owned['density_requirements']), len(DENSITY_SPECS))
        self.assertEqual(owned['density_requirements'][0]['id'], 'CUSTOM-COLLABORATION.yaml-valid')
        self.assertEqual(owned['density_requirements'][-1]['id'], 'CUSTOM-COLLABORATION.persistent-cleanup')
        for scenario in ('remove', 'command', 'expectation', 'unknown_key',
                         'malformed_decision'):
            value = copy.deepcopy(binding)
            if scenario == 'remove':
                value['requirements'].pop()
            elif scenario == 'command':
                value['requirements'][0]['command'] = 'bcs-cli foreign command'
            elif scenario == 'expectation':
                value['requirements'][0]['expectation'] = 1
            elif scenario == 'unknown_key':
                value['foreign_additive_section'] = True
            else:
                value['convergence_decisions'] = [{'decision_id': 'missing-decision-text'}]
            with self.subTest(mutation=scenario), \
                 patch('bcs_e2e.local_operator_cli_contract.read', return_value=value), \
                 self.assertRaises(RunStateError):
                custom_collaboration_source_binding()

    def test_custom_collaboration_full_local_case_never_creates_remote_credit(self):
        result = migration_plan()
        local = next(r for r in result['local_registered_executors']
                     if r['case_id'] == 'LOCAL-CUSTOM-COLLABORATION-LIFECYCLE-01')
        self.assertEqual(local['legacy_id'], 'story_cli_operator_creates_custom_collaboration')
        self.assertEqual(local['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(local['required_assertion_count'],
                         len(CUSTOM_COLLABORATION_REQUIREMENTS + DENSITY_REQUIREMENTS))
        self.assertEqual(local['requirement_ids'],
                         list(CUSTOM_COLLABORATION_REQUIREMENTS + DENSITY_REQUIREMENTS))
        self.assertEqual(local['canonical_reporting'], 'INTEGRATED_LOCAL')
        self.assertEqual(local['execution_command'], 'run_custom_collaboration_case')
        self.assertEqual(local['remote_validation'], 'NOT_RUN')
        self.assertFalse(local['grants_release_credit'])
        superseded = {'LOCAL-CUSTOM-COLLAB-VALIDATE-01',
                      'LOCAL-CUSTOM-COLLAB-CREATE-01',
                      'LOCAL-CUSTOM-COLLAB-TRIAL-01'}
        self.assertFalse(any(r['case_id'] in superseded for r in result['local_partial_executors']))
        custom_slice = next(r for r in result['cross_environment_product_cli_case_slices']
                            if r['case_id'] == 'LOCAL-CUSTOM-COLLABORATION-LIFECYCLE-01')
        self.assertEqual(custom_slice['local_executor_status'], 'LOCAL_FULL_PARENT_EXECUTOR')
        self.assertEqual(custom_slice['remote_validation'], 'NOT_RUN')
        self.assertFalse(custom_slice['grants_release_credit'])
        self.assertIn('preprod', custom_slice['blocker'])
        self.assertEqual(result['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(result['migrated_story_count'], 0)
        self.assertEqual(result['passed_assertion_count'], 0)

    def test_workspace_full_flow_supersedes_all_increment_slices(self):
        result = migration_plan()
        flow = next(row for row in result['local_registered_executors']
                    if row['case_id'] == 'LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01')
        self.assertEqual(len(flow['requirement_ids']), 17)
        self.assertEqual(flow['canonical_reporting'], 'INTEGRATED_LOCAL')
        self.assertEqual(flow['report_command'], 'local-workspace-report')
        self.assertEqual(flow['execution_command'], 'local-workspace-run --with-incident-delete')
        self.assertEqual(flow['required_assertion_count'], 17)
        self.assertFalse(flow['grants_release_credit'])
        workspace_partials = {
            'LOCAL-WORKSPACE-API-01', 'LOCAL-WORKSPACE-API-CLI-01',
            'LOCAL-WORKSPACE-CONFIG-01', 'LOCAL-WORKSPACE-MESSAGING-01',
            'LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01'}
        self.assertFalse(any(row['case_id'] in workspace_partials
                             for row in result['local_partial_executors']))
        slow_judge = next(row for row in result['local_registered_executors']
                          if row['case_id'] == 'LOCAL-SLOW-JUDGE-01')
        self.assertEqual(slow_judge['report_command'], 'local-slow-judge-report')
        self.assertEqual(len(slow_judge['requirement_ids']), 22)
        self.assertFalse(slow_judge['grants_release_credit'])
        self.assertFalse(result['ready_to_execute'])

    def test_local_migration_set_covers_the_reviewed_release_parent_stories(self):
        result = migration_plan()
        self.assertEqual(len(result['local_registered_executors']), 31)
        self.assertEqual(result['local_partial_executors'], [])
        all_inventory = {row['id'] for row in current_manifest()['legacy_inventory']}
        known_unmapped = set(KNOWN_UNMAPPED_LEGACY_IDS)
        expected_parents = all_inventory - known_unmapped
        actual_parents = {row['legacy_id'] for row in result['local_registered_executors']}
        self.assertEqual(actual_parents, expected_parents)
        self.assertEqual(all_inventory - expected_parents, known_unmapped)
        duplicate_cases = [row['case_id'] for row in result['local_registered_executors']]
        self.assertEqual(len(duplicate_cases), len(set(duplicate_cases)))
        self.assertTrue(all(row['remote_validation'] == 'NOT_RUN'
                            and row['grants_release_credit'] is False
                            for row in result['local_registered_executors']))

    def test_shared_identity_helpers_and_final_cli_friend_outcome_are_explicit(self):
        self.assertIn('common.sh', self.contract['sources'])
        friend = self.contract['stories'][1]
        requirement = next(r for r in friend['requirements'] if r['id'] == 'FRIEND.cli-accept')
        self.assertIn("PM is present in CEO's final CLI friend list", requirement['expectation'])
        path = self.source / 'common.sh'
        source = path.read_text()
        self.assertIn('BOT_DATA_DIR', source)
        path.write_text(source.replace('BOT_DATA_DIR', 'OTHER_BOT_DATA_DIR', 1))
        with self.assertRaises(RunStateError): validate_contract(self.contract, source_root=self.source)

    def test_plan_is_offline_and_does_not_acquire_leases_or_change_pilot_inventory(self):
        manifest = current_manifest()
        with patch('bcs_e2e.cli.ReadClient', side_effect=AssertionError('no network')), \
             patch('bcs_e2e.prepare.PoolLease.acquire', side_effect=AssertionError('no lease')):
            result = plan(load(ROOT / 'profiles/local.mock.json'))
        self.assertEqual(result['cases'], manifest['cases'])
        self.assertFalse(result['ready_to_execute'])
        migration = result['next_story_migrations']
        self.assertEqual(migration['status'], 'PLANNED_NOT_EXECUTABLE')
        self.assertEqual(migration['migrated_story_count'], 0)
        self.assertEqual([len(s['required_bot_roles']) for s in migration['stories']], [4, 5, 2])

    def test_any_source_change_requires_review_including_nested_helper_changes(self):
        for name in SOURCES:
            path = self.source / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n# source drift\n')
            with self.subTest(name=name), self.assertRaises(RunStateError):
                validate_contract(self.contract, source_root=self.source)
            path.write_bytes(original)

    def test_missing_reordered_or_foreign_parent_step_is_rejected(self):
        for edit in ('remove', 'reverse', 'wrong-source', 'duplicate', 'foreign'):
            contract = copy.deepcopy(self.contract)
            steps = contract['stories'][0]['steps']
            if edit == 'remove': steps.pop()
            elif edit == 'reverse': steps.reverse()
            elif edit == 'wrong-source': steps[0]['source'] = 'friends.sh'
            elif edit == 'duplicate': steps.append(steps[0])
            else: steps[0]['function'] = 'run_pre01'
            with self.subTest(edit=edit), self.assertRaises(RunStateError):
                validate_contract(contract, source_root=self.source)

    def test_each_business_requirement_and_role_cannot_be_silently_dropped(self):
        for index, story in enumerate(self.contract['stories']):
            for field in ('requirements', 'required_bot_roles'):
                for position in range(len(story[field])):
                    contract = copy.deepcopy(self.contract)
                    contract['stories'][index][field].pop(position)
                    with self.subTest(index=index, field=field, position=position), self.assertRaises(RunStateError):
                        validate_contract(contract, source_root=self.source)

    def test_fake_migration_assertion_count_or_duplicate_requirement_never_admitted(self):
        for edit in ('pass', 'count', 'duplicate', 'unbound'):
            contract = copy.deepcopy(self.contract)
            story = contract['stories'][2]
            if edit == 'pass': story['migration_status'] = 'MIGRATED'
            elif edit == 'count': story['passed_assertion_count'] = 10
            elif edit == 'duplicate': story['requirements'].append(story['requirements'][0])
            else: story['requirements'][0]['functions'] = ['run_pre03']
            with self.subTest(edit=edit), self.assertRaises(RunStateError):
                validate_contract(contract, source_root=self.source)

    def test_path_traversal_symlink_missing_and_oversized_sources_are_rejected(self):
        contract = copy.deepcopy(self.contract)
        contract['sources']['../external.sh'] = contract['sources'].pop('group.sh')
        with self.assertRaises(RunStateError): validate_contract(contract, source_root=self.source)
        path = self.source / 'group.sh'
        original = path.read_bytes(); path.unlink()
        with self.assertRaises(RunStateError): validate_contract(self.contract, source_root=self.source)
        path.symlink_to(ROOT.parent / 'e2e-test/group.sh')
        with self.assertRaises(RunStateError): validate_contract(self.contract, source_root=self.source)
        path.unlink(); path.write_bytes(b'x' * 1_048_577)
        with self.assertRaises(RunStateError): validate_contract(self.contract, source_root=self.source)
        path.write_bytes(original)

    def test_inventory_cannot_claim_partial_map_is_already_migrated(self):
        manifest = current_manifest()
        next(s for s in manifest['legacy_inventory'] if s['id'] == 'story_session_file_workspace')['migration_status'] = 'MIGRATED'
        with patch('bcs_e2e.inventory.current_manifest', return_value=manifest), self.assertRaises(RunStateError):
            migration_plan()

    def test_caller_execution_is_one_independent_local_slice_with_no_release_credit(self):
        result = migration_plan()
        self.assertEqual(len(result['local_registered_executors']), 31)
        slices = result['local_independent_slices']
        self.assertEqual(len(slices), 4)
        row = slices[0]
        self.assertEqual(row['suite'], 'owned-local-caller-execution-v1')
        self.assertEqual(row['case_id'], 'LOCAL-CALLER-EXEC-01')
        self.assertEqual(row['case_version'], 1)
        self.assertEqual(row['legacy_id'], 'caller_execution_story')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(row['required_assertion_count'], 9)
        self.assertEqual(row['requirement_ids'], [
            'LOCAL-CALLER-EXEC.provider-bot-binding', 'LOCAL-CALLER-EXEC.binding-mode-guard',
            'LOCAL-CALLER-EXEC.ingress-rejection', 'LOCAL-CALLER-EXEC.queue-pause',
            'LOCAL-CALLER-EXEC.caller-context-token', 'LOCAL-CALLER-EXEC.downgrade-no-inherit',
            'LOCAL-CALLER-EXEC.sse-token', 'LOCAL-CALLER-EXEC.cache-loss-fail-closed',
            'LOCAL-CALLER-EXEC.token-redaction'])
        self.assertEqual(row['canonical_reporting'], 'LOCAL_RUNTIME_BLACKBOX')
        self.assertEqual(row['execution_command'], 'local-caller-execution-run')
        self.assertEqual(row['report_command'], 'local-caller-execution-report')
        self.assertEqual(row['required_parameters'], ['binary', 'binary_sha256', 'state_dir',
                                                      'run_id', 'confirm_run_id',
                                                      'allow_local_processes',
                                                      'allow_fixture_write', 'allow_write'])
        self.assertEqual(row['binary_kind'], 'bcs-caller-e2e-test-utils')
        self.assertEqual(row['auto_build'], False)
        self.assertEqual(row['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                              'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(row['release_parent_denominator'], False)
        self.assertEqual(row['independent_slice'], True)
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertEqual(row['grants_release_credit'], False)
        self.assertEqual(row['source_map'], 'caller-execution-migration-contract.json')
        self.assertEqual(set(row), {'suite', 'case_id', 'case_version', 'legacy_id', 'status',
                                    'required_assertion_count', 'requirement_ids',
                                    'canonical_reporting', 'execution_command', 'report_command',
                                    'required_parameters', 'binary_kind', 'auto_build',
                                    'environment', 'release_parent_denominator',
                                    'independent_slice', 'remote_validation',
                                    'grants_release_credit', 'source_map'})
        # The slice never joins the reviewed-release-case release coverage registry.
        self.assertNotIn('LOCAL-CALLER-EXEC-01', {case.case_id for case in CASES})
        for suite in ('preprod-pilot-v1', 'owned-local-trusted-team-v1'):
            manifest = current_manifest(suite)
            self.assertNotIn('caller_execution_story',
                             {entry['id'] for entry in manifest['legacy_inventory']})
            self.assertNotIn('LOCAL-CALLER-EXEC-01',
                             {case['id'] for case in manifest['cases']})
        self.assertEqual(caller_execution_source_binding()['parent'], 'caller_execution_story')

    def test_draft_group_is_the_second_independent_local_slice_with_no_release_credit(self):
        result = migration_plan()
        self.assertEqual(len(result['local_independent_slices']), 4)
        row = result['local_independent_slices'][1]
        self.assertEqual(row['suite'], 'owned-local-draft-group-v1')
        self.assertEqual(row['case_id'], 'LOCAL-DRAFT-GROUP-01')
        self.assertEqual(row['case_version'], 1)
        self.assertEqual(row['legacy_id'], 'collaboration_draft_group_story')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(row['required_assertion_count'], 17)
        self.assertEqual(row['requirement_ids'], [
            'LOCAL-DRAFT-GROUP.human-registration',
            'LOCAL-DRAFT-GROUP.provider-collaborator-friendship',
            'LOCAL-DRAFT-GROUP.immutable-source-upload',
            'LOCAL-DRAFT-GROUP.share-reference-issuance',
            'LOCAL-DRAFT-GROUP.v1-preview-byte-fidelity',
            'LOCAL-DRAFT-GROUP.shared-validation-binding',
            'LOCAL-DRAFT-GROUP.grant-negative-matrix',
            'LOCAL-DRAFT-GROUP.revision-input-guards',
            'LOCAL-DRAFT-GROUP.browser-identity-chain',
            'LOCAL-DRAFT-GROUP.originator-selection',
            'LOCAL-DRAFT-GROUP.group-launch-input',
            'LOCAL-DRAFT-GROUP.browser-context-submission-guards',
            'LOCAL-DRAFT-GROUP.impersonation-rejection',
            'LOCAL-DRAFT-GROUP.group-launch-binding',
            'LOCAL-DRAFT-GROUP.draft-immutability',
            'LOCAL-DRAFT-GROUP.share-revocation',
            'LOCAL-DRAFT-GROUP.cleanup-disposition'])
        self.assertEqual(row['canonical_reporting'], 'LOCAL_RUNTIME_BLACKBOX')
        self.assertEqual(row['execution_command'], 'local-draft-group-run')
        self.assertEqual(row['report_command'], 'local-draft-group-report')
        self.assertEqual(row['required_parameters'], ['binary', 'binary_sha256', 'state_dir',
                                                      'run_id', 'confirm_run_id',
                                                      'allow_local_processes',
                                                      'allow_fixture_write', 'allow_write'])
        self.assertEqual(row['binary_kind'], 'bcs')
        self.assertEqual(row['auto_build'], False)
        self.assertEqual(row['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                              'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(row['release_parent_denominator'], False)
        self.assertEqual(row['independent_slice'], True)
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertEqual(row['grants_release_credit'], False)
        self.assertEqual(row['source_map'], 'draft-group-migration-contract.json')
        self.assertEqual(set(row), {'suite', 'case_id', 'case_version', 'legacy_id', 'status',
                                    'required_assertion_count', 'requirement_ids',
                                    'canonical_reporting', 'execution_command', 'report_command',
                                    'required_parameters', 'binary_kind', 'auto_build',
                                    'environment', 'release_parent_denominator',
                                    'independent_slice', 'remote_validation',
                                    'grants_release_credit', 'source_map'})
        # The slice never joins the reviewed-release-case release coverage registry.
        self.assertNotIn('LOCAL-DRAFT-GROUP-01', {case.case_id for case in CASES})
        for suite in ('preprod-pilot-v1', 'owned-local-trusted-team-v1'):
            manifest = current_manifest(suite)
            self.assertNotIn('collaboration_draft_group_story',
                             {entry['id'] for entry in manifest['legacy_inventory']})
            self.assertNotIn('LOCAL-DRAFT-GROUP-01',
                             {case['id'] for case in manifest['cases']})
        self.assertEqual(draft_group_source_binding()['parent'], 'collaboration_draft_group_story')

    def test_result_queue_is_the_third_independent_local_slice_with_no_release_credit(self):
        result = migration_plan()
        self.assertEqual(len(result['local_independent_slices']), 4)
        row = result['local_independent_slices'][2]
        self.assertEqual(row['suite'], 'owned-local-result-queue-v1')
        self.assertEqual(row['case_id'], 'LOCAL-RESULT-QUEUE-01')
        self.assertEqual(row['case_version'], 1)
        self.assertEqual(row['legacy_id'], 'story_state_machine_result_queue')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(row['required_assertion_count'], 18)
        self.assertEqual(row['requirement_ids'], [
            'RESULT-QUEUE.human-registration',
            'RESULT-QUEUE.provider-registration',
            'RESULT-QUEUE.recipient-bot-pair',
            'RESULT-QUEUE.recipient-visibility-harness',
            'RESULT-QUEUE.delivery-policy-baseline',
            'RESULT-QUEUE.delivery-policy-replacement',
            'RESULT-QUEUE.policy-update-cas',
            'RESULT-QUEUE.provider-callout-harness',
            'RESULT-QUEUE.final-callback-admission',
            'RESULT-QUEUE.result-delivery-ledger',
            'RESULT-QUEUE.group-and-session',
            'RESULT-QUEUE.state-machine-definition',
            'RESULT-QUEUE.state-machine-run-admission',
            'RESULT-QUEUE.one-shot-node-delivery',
            'RESULT-QUEUE.durable-result-rows',
            'RESULT-QUEUE.scheduled-result-delivery',
            'RESULT-QUEUE.bound-context-and-unique-history',
            'RESULT-QUEUE.run-completion-and-cleanup-disposition'])
        self.assertEqual(row['canonical_reporting'], 'LOCAL_RUNTIME_BLACKBOX')
        self.assertEqual(row['execution_command'], 'local-result-queue-run')
        self.assertEqual(row['report_command'], 'local-result-queue-report')
        self.assertEqual(row['required_parameters'], ['binary', 'binary_sha256', 'state_dir',
                                                      'run_id', 'confirm_run_id',
                                                      'allow_local_processes',
                                                      'allow_fixture_write', 'allow_write'])
        self.assertEqual(row['binary_kind'], 'bcs')
        self.assertEqual(row['auto_build'], False)
        self.assertEqual(row['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                              'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(row['release_parent_denominator'], False)
        self.assertEqual(row['independent_slice'], True)
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertEqual(row['grants_release_credit'], False)
        self.assertEqual(row['source_map'], 'result-queue-migration-contract.json')
        self.assertEqual(set(row), {'suite', 'case_id', 'case_version', 'legacy_id', 'status',
                                    'required_assertion_count', 'requirement_ids',
                                    'canonical_reporting', 'execution_command', 'report_command',
                                    'required_parameters', 'binary_kind', 'auto_build',
                                    'environment', 'release_parent_denominator',
                                    'independent_slice', 'remote_validation',
                                    'grants_release_credit', 'source_map'})
        # The slice never joins the reviewed-release-case release coverage registry; its parent stays a
        # known unmapped backlog row inside the 41-story legacy inventory.
        self.assertNotIn('LOCAL-RESULT-QUEUE-01', {case.case_id for case in CASES})
        for suite in ('preprod-pilot-v1', 'owned-local-trusted-team-v1'):
            manifest = current_manifest(suite)
            self.assertNotIn('LOCAL-RESULT-QUEUE-01',
                             {case['id'] for case in manifest['cases']})
        legacy = current_manifest()['legacy_inventory']
        self.assertIn('story_state_machine_result_queue', {entry['id'] for entry in legacy})
        self.assertEqual(result_queue_source_binding()['parent'], 'story_state_machine_result_queue')

    def test_nested_fixed_loop_is_the_fourth_independent_local_slice_with_no_release_credit(self):
        result = migration_plan()
        self.assertEqual(len(result['local_independent_slices']), 4)
        row = result['local_independent_slices'][3]
        self.assertEqual(row['suite'], 'owned-local-nested-fixed-loop-v1')
        self.assertEqual(row['case_id'], 'LOCAL-NESTED-FIXED-LOOP-01')
        self.assertEqual(row['case_version'], 1)
        self.assertEqual(row['legacy_id'], 'story_nested_fixed_loop_workflows')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(row['required_assertion_count'], 16)
        self.assertEqual(row['requirement_ids'][0], 'NESTED-LOOP.nested-bot-loop-definition')
        self.assertEqual(row['requirement_ids'][-1],
                         'NESTED-LOOP.main-required-args-and-two-story-orchestration')
        self.assertEqual(row['canonical_reporting'], 'LOCAL_RUNTIME_BLACKBOX')
        self.assertEqual(row['execution_command'], 'local-nested-fixed-loop-run')
        self.assertEqual(row['report_command'], 'local-nested-fixed-loop-report')
        self.assertEqual(row['required_parameters'],
                        ['binary', 'binary_sha256', 'cli_binary', 'driver', 'state_dir',
                         'run_id', 'confirm_run_id', 'allow_local_processes',
                         'allow_fixture_write', 'allow_write'])
        self.assertEqual(row['binary_kind'], 'bcs')
        self.assertEqual(row['auto_build'], False)
        self.assertEqual(row['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                              'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(row['release_parent_denominator'], False)
        self.assertEqual(row['independent_slice'], True)
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertEqual(row['grants_release_credit'], False)
        self.assertEqual(row['source_map'], 'nested-fixed-loop-migration-contract.json')
        self.assertEqual(set(row), {'suite', 'case_id', 'case_version', 'legacy_id', 'status',
                                    'required_assertion_count', 'requirement_ids',
                                    'canonical_reporting', 'execution_command', 'report_command',
                                    'required_parameters', 'binary_kind', 'auto_build',
                                    'environment', 'release_parent_denominator',
                                    'independent_slice', 'remote_validation',
                                    'grants_release_credit', 'source_map'})
        self.assertNotIn('LOCAL-NESTED-FIXED-LOOP-01', {case.case_id for case in CASES})
        legacy = current_manifest()['legacy_inventory']
        self.assertIn('story_nested_fixed_loop_workflows', {entry['id'] for entry in legacy})
        self.assertEqual(nested_fixed_loop_source_binding()['parent'],
                         'story_nested_fixed_loop_workflows')

    def test_backlog_registry_has_ten_independent_rows_outside_release_credit(self):
        result = migration_plan()
        rows = result['local_backlog_case_slices']
        self.assertEqual(len(rows), 10)
        self.assertEqual([row['case_id'] for row in rows], [
            'LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01',
            'LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01',
            'LOCAL-SESSION-OWNER-01',
            'LOCAL-DRAFT-DELIVERY-01',
            'LOCAL-PROVIDER-OWNER-01',
            'DUAL-GROUP-CONTEXT-CLI-01',
            'DUAL-AUTHORITY-JOURNEY-01',
            'DUAL-SERVICE-POLICY-01',
            'DUAL-PROVIDER-PUBLISHES-01',
            'DUAL-SLOW-JUDGE-01',
        ])
        registered = {row['case_id'] for row in result['local_registered_executors']}
        for row in rows:
            with self.subTest(case_id=row['case_id']):
                self.assertNotIn(row['case_id'], registered)
                self.assertFalse(row['release_parent_denominator'])
                self.assertTrue(row['independent_slice'])
                self.assertEqual(row['remote_validation'], 'NOT_RUN')
                self.assertFalse(row['grants_release_credit'])


class ExecutorMigrationInventoryTests(unittest.TestCase):
    def test_all_41_legacy_rows_join_one_orthogonal_status_source(self):
        legacy = legacy_inventory_with_executor_migration()
        self.assertEqual(len(legacy), 41)
        self.assertEqual({row['migration_status'] for row in legacy},
                         {'NOT_MIGRATED'})
        self.assertTrue(all(
            set(row['executor_migration']) == {
                'mode', 'preprod_verdict', 'gates', 'notes'}
            for row in legacy))
        counts = Counter(
            (row['executor_migration']['mode'],
             row['executor_migration']['preprod_verdict'])
            for row in legacy)
        self.assertEqual(counts, Counter({
            ('DUAL', 'PASS'): 19,
            ('DUAL', 'BLOCKED_INVENTORIED'): 11,
            ('DUAL', 'NOT_RUN'): 10,
            ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'): 1,
        }))
        gated = {
            row['id'] for row in legacy
            if row['executor_migration']['gates']}
        self.assertEqual(gated, {
            'story_user_receives_group_event_webhooks',
            'story_provider_callback_survives_slow_judge',
        })
        rows = {row['id']: row['executor_migration'] for row in legacy}
        self.assertEqual(
            rows['story_user_runs_structured_collaboration']['notes'],
            ['STATION_DEFECT_R5_CAVEAT', 'DUAL_STRUCTURED_DENSITY_R12_PREPROD_PASS_20261031',
             'STRUCTURED_COLLAB_DENSITY_26_OBLIGATIONS_20261031'])
        self.assertEqual(
            rows['story_user_builds_trusted_team']['notes'],
            ['FRESH_FIXTURE_R5G'])
        self.assertEqual(
            rows['story_cli_operator_manages_group_context']['notes'],
            ['ROSTER_LAG', 'GROUP_CONTEXT_DENSITY_9_OBLIGATIONS_20261031'])
        self.assertEqual(rows['test_ep_ensure_bot'], {
            'mode': 'EDGE_OBLIGATION_IN_FAMILY',
            'preprod_verdict': 'NOT_APPLICABLE',
            'gates': [],
            'notes': ['WP-B2 族内近似口径如实注：mode 维持 EDGE_OBLIGATION_IN_FAMILY 不变；owned-local 族内分派 per-case 义务 1，回放 LOCAL-EDGE-ENSURE-BOT-01 全绿（migration-coverage-matrix-20261030.tsv 记 REVIEWED-30-SLICED 全绿）；预发 r7 edge-read 家族 rc=0 PASS（admission 叶族内近似覆盖，非独立精确腿）；本行不增资、不改判、不从族近似冒领独立腿信用。'],
        })
        self.assertEqual(rows['story_provider_operator_manages_subbots'], {
            'mode': 'DUAL',
            'preprod_verdict': 'NOT_RUN',
            'gates': [],
            'notes': ['DUAL_SUBBOTS_DENSITY_D4_131_LEAVES'],
        })
        self.assertEqual(rows['story_collaboration_draft_delivery'], {
            'mode': 'DUAL',
            'preprod_verdict': 'NOT_RUN',
            'gates': [],
            'notes': ['DRAFT_DELIVERY_DENSITY_24_OBLIGATIONS_20261031'],
        })
        self.assertEqual(rows['story_retired_service_policy'], {
            'mode': 'DUAL',
            'preprod_verdict': 'NOT_RUN',
            'gates': [],
            'notes': [
                'SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031',
                'SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031',
            ],
        })
        self.assertEqual(rows['story_cli_operator_builds_collaboration_team'], {
            'mode': 'DUAL',
            'preprod_verdict': 'BLOCKED_INVENTORIED',
            'gates': [],
            'notes': ['COLLAB_TEAM_DENSITY_22_OBLIGATIONS_20261031'],
        })
        self.assertEqual(rows['test_ep_error_and_admission_branches']['notes'], ['Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 6 密度叶 == parsed 7（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/error-admission-branches-stdout.json（status=PASS，remote_validation=NOT_RUN，grants_release_credit=false）；wpc 本地轮 20261031-migration-wpc-local-owned-edge-3 junit 行 LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01 PASS 1/1；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing'])
        self.assertEqual(rows['test_ep_connect_private_and_discover_branches']['notes'], ['Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 7 密度叶 == parsed 8（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/connect-private-discover-stdout.json（status=PASS，remote_validation=NOT_RUN）；wpc 本地轮 junit 行 LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01 PASS 1/1；产品在案 PD-PRIVATE-CREATE-500（preprod 500 vs 契约 403）与 PD-DISCOVER-BRANCH-DRIFT（org 分支 403 vs 契约 400、role 包装 invalid_operation）在密度分类中如实披露；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing'])
        self.assertEqual(rows['test_ep_friend_connections_friend_request_and_list']['notes'], ['Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 2 义务 + 1 密度叶 == parsed 3（dual-edge-permission-density-v1）；r5 读腿收据 20261031-regression-r5-edge-read/friend-connections-stdout.json 与 r7 状态腿 20261031-regression-r7-preprod/edge-read/friend-connections-state/edge-friend-connections-run.json（公共 face PUBLIC-EDGE-FRIEND-CONNECTIONS-LIST-01，contract dual-edge-read-case-run-v1，status=PASS，remote_validation=NOT_RUN）；本地读轮留存在本族为 LEG_ABSENT（读片走 dual-edge-read-run 族）；annex 叶跨族引证 r5 mutation 轮 error-admission 收据 create 行（friend_connection_mutation.create 6 行中 3 行 201 且 request id 回显）；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing'])
        self.assertEqual(rows['test_ep_friend_connections_friend_accept_reject_cancel']['notes'], ['Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 1 密度叶 == parsed 2（dual-edge-permission-density-v1）；r5 双腿收据 20261031-regression-r5-edge-mutation/accept-reject-cancel-stdout.json（status=PASS，remote_validation=NOT_RUN）；wpc 本地轮 junit 行 LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01 PASS 1/1；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing'])
        self.assertEqual(rows['test_ep_friend_connections_mutual_auto_approve']['notes'], ['Wave-D3 密度叠加层 dual-edge-permission-density-v1 离线闭合：Frozen 1 义务 + 7 密度叶 == parsed 8（dual-edge-permission-density-v1）；r5 与 r6 双腿收据 20261031-regression-r5-edge-mutation/ 与 20261031-regression-r6-edge-mutation/ 下 mutual-auto-approve-stdout.json（双轮 status=PASS，remote_validation=NOT_RUN；r5/r6 peer bot 不同，口径 一致）；wpc 本地轮 junit 行 LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01 PASS 1/1；身份交叉核对 member=20261010_gekng5hk:39464 出现在双轮 admissions 方向键；22 叶族内 ENROLLMENT=NOT_ENROLLED fail-close，本行不获得任何密度执行信用；committed verdict 维持 PASS 现状不变，笔记增量仅补水注记；⏳pending-landing'])
        self.assertEqual(
            rows['story_authority_user_journey']['notes'],
            ['AUTHORITY_DENSITY_42_OBLIGATIONS_20261031'])
        self.assertEqual(
            rows['story_user_validates_external_channel_setup']['notes'],
            ['row #10 story_user_validates_external_channel_setup: register 7 source-anchored density obligations under the retained CHANNEL.* parent envelope (13 parsed = 6 legacy parents + 7 atoms; residual_count_gap=0); notes-only integration, the dual-channel-bindings-run envelope and its r8 conclusions stay untouched'])
        self.assertEqual(
            rows['story_cli_operator_validates_channel_management']['notes'],
            ['row #11 story_cli_operator_validates_channel_management: register 7 source-anchored density obligations under the retained CHANNEL-CLI.* parent envelope (16 parsed = 9 legacy parents + 7 atoms; residual_count_gap=0); preprod keeps BLOCKED_INVENTORIED with the disabled-bridge divergence registered as a true business judgment (not a defect) and the sim DingTalk design red disclosed; remote_validation=NOT_RUN and grants_release_credit=false'])

    def test_reviewed_view_keeps_status_join_and_roster_facts(self):
        reviewed = reviewed_release_inventory()
        self.assertEqual(len(reviewed), 31)
        self.assertTrue(all('executor_migration' in row for row in reviewed))
        counts = Counter(
            (row['executor_migration']['mode'],
             row['executor_migration']['preprod_verdict'])
            for row in reviewed)
        self.assertEqual(counts, Counter({
            ('DUAL', 'PASS'): 18,
            ('DUAL', 'BLOCKED_INVENTORIED'): 8,
            ('DUAL', 'NOT_RUN'): 4,
            ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'): 1,
        }))
        ids = {row['id'] for row in reviewed}
        self.assertNotIn(
            'story_cli_operator_manages_group_context', ids)
        self.assertIn('story_fixed_loop_human_editor_workflow', ids)

    def test_manifests_do_not_duplicate_executor_migration(self):
        for suite in (
                'preprod-pilot-v1',
                'preprod-retained-v1',
                'preprod-retained-file-v1',
                'owned-local-trusted-team-v1'):
            with self.subTest(suite=suite):
                raw = current_manifest(suite)['legacy_inventory']
                self.assertEqual(len(raw), 41)
                self.assertTrue(all(
                    'executor_migration' not in row for row in raw))

    def test_missing_or_extra_status_row_fails_loudly_at_join(self):
        statuses = load_executor_migration()
        missing = dict(statuses)
        missing.pop('story_human_manages_delivery_policy')
        extra = dict(statuses)
        extra['story_unexpected_status_row'] = {
            'mode': 'DUAL', 'preprod_verdict': 'PASS',
            'gates': [], 'notes': [],
        }
        for value, message in (
                (missing, 'join is incomplete: missing=1 extra=0'),
                (extra, 'join is incomplete: missing=0 extra=1')):
            with (
                self.subTest(message=message),
                patch('bcs_e2e.inventory.load_executor_migration',
                      return_value=value),
                self.assertRaisesRegex(RunStateError, message),
            ):
                legacy_inventory_with_executor_migration()


    def test_wpb_density_closures_lock_face_notes_on_rows_4_12_15(self):
        statuses = load_executor_migration()
        locks = (
            ('story_user_runs_and_shares_sessions',
             'WPB_SESSION_SHARING_87_OBLIGATIONS_20261031'),
            ('story_cli_operator_runs_sessions_and_services',
             'WPB_SESSION_SERVICE_44_OBLIGATIONS_20261031'),
            ('story_session_file_workspace',
             'WPB_SESSION_FILE_12_REQUIREMENTS_20261031'),
        )
        for story_id, token in locks:
            with self.subTest(story_id=story_id):
                self.assertIn(story_id, statuses)
                row = statuses[story_id]
                self.assertEqual(row['mode'], 'DUAL')
                self.assertEqual(row['preprod_verdict'], 'PASS')
                self.assertIn(token, row['notes'])
                self.assertEqual(len(row['notes']), 1)

    def test_wpb2_edge_family_face_notes_lock_rows(self):
        statuses = load_executor_migration()
        face = json.loads(
            (ROOT / 'executor-migration.json').read_text(encoding='utf-8'))
        face_by_id = {row['id']: row['executor_migration']
                      for row in face['rows']}
        locks = (
            ('test_ep_admission', 'DUAL', 'PASS'),
            ('test_ep_friend_connections_friend_requests_list', 'DUAL', 'PASS'),
            ('test_ep_bot_search', 'DUAL', 'PASS'),
            ('test_ep_ensure_bot', 'EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'),
            ('test_ep_friend_connections_friend_revoke', 'DUAL', 'BLOCKED_INVENTORIED'),
            ('test_ep_friend_connections_full_lifecycle', 'DUAL', 'BLOCKED_INVENTORIED'),
            ('test_ep_tc_friend_auth_sync_readdressed_principal', 'DUAL', 'NOT_RUN'),
            ('test_ep_dept_scope_parses_object_friend_ext_entries', 'DUAL', 'NOT_RUN'),
        )
        for case_id, mode, verdict in locks:
            with self.subTest(case_id=case_id):
                row = statuses[case_id]
                self.assertEqual(row['mode'], mode)
                self.assertEqual(row['preprod_verdict'], verdict)
                self.assertEqual(row['gates'], [])
                self.assertEqual(len(row['notes']), 1)
                self.assertEqual(row['notes'], face_by_id[case_id]['notes'])

    def test_provider_publishes_density_locks_row_7_face_note(self):
        statuses = load_executor_migration()
        row = statuses['story_provider_operator_publishes_agent']
        self.assertEqual(row['mode'], 'DUAL')
        self.assertEqual(row['preprod_verdict'], 'PASS')
        self.assertEqual(row['gates'], [])
        self.assertEqual(
            row['notes'],
            ['PROVIDER_PUBLISHES_DENSITY_98_OBLIGATIONS_20261031'])


    def test_result_queue_density_locks_row_20_face_note(self):
        statuses = load_executor_migration()
        face = json.loads(
            (ROOT / 'executor-migration.json').read_text(encoding='utf-8'))
        face_by_id = {row['id']: row['executor_migration']
                      for row in face['rows']}
        row = statuses['story_state_machine_result_queue']
        self.assertEqual(row['mode'], 'DUAL')
        self.assertEqual(row['preprod_verdict'], 'BLOCKED_INVENTORIED')
        self.assertEqual(row['gates'], [])
        self.assertEqual(row['notes'], [RESULT_QUEUE_DENSITY_NOTE])
        self.assertEqual(
            face_by_id['story_state_machine_result_queue']['notes'],
            [RESULT_QUEUE_DENSITY_NOTE])

    def test_draft_group_launch_density_locks_row_40_face_note(self):
        statuses = load_executor_migration()
        face = json.loads(
            (ROOT / 'executor-migration.json').read_text(encoding='utf-8'))
        face_by_id = {row['id']: row['executor_migration']
                      for row in face['rows']}
        row = statuses['story_collaboration_draft_group_launch']
        self.assertEqual(row['mode'], 'DUAL')
        self.assertEqual(row['preprod_verdict'], 'BLOCKED_INVENTORIED')
        self.assertEqual(row['gates'], [])
        self.assertEqual(row['notes'], [DRAFT_GROUP_DENSITY_NOTE])
        self.assertEqual(
            face_by_id['story_collaboration_draft_group_launch']['notes'],
            [DRAFT_GROUP_DENSITY_NOTE])


    def test_operator_cli_density_locks_row_9_face_note(self):
        statuses = load_executor_migration()
        face = json.loads(
            (ROOT / 'executor-migration.json').read_text(encoding='utf-8'))
        face_by_id = {row['id']: row['executor_migration']
                      for row in face['rows']}
        row = statuses['story_operator_coordinates_with_cli']
        self.assertEqual(row['mode'], 'DUAL')
        self.assertEqual(row['preprod_verdict'], 'BLOCKED_INVENTORIED')
        self.assertEqual(row['gates'], [])
        self.assertEqual(row['notes'], [OPERATOR_CLI_DENSITY_NOTE])
        self.assertEqual(
            face_by_id['story_operator_coordinates_with_cli']['notes'],
            [OPERATOR_CLI_DENSITY_NOTE])

    def test_operator_cli_density_note_closes_the_inventory_fail_close_fence(self):
        from bcs_e2e import inventory as inventory_module
        live_counts = inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS
        self.assertIn(OPERATOR_CLI_DENSITY_NOTE,
                      inventory_module.EXECUTOR_MIGRATION_NOTES)
        self.assertEqual(live_counts[OPERATOR_CLI_DENSITY_NOTE], 1)
        self.assertEqual(len(inventory_module.EXECUTOR_MIGRATION_NOTES),
                         sum(live_counts.values()))

    def test_agent_network_density_locks_row_1_face_note(self):
        statuses = load_executor_migration()
        face = json.loads(
            (ROOT / 'executor-migration.json').read_text(encoding='utf-8'))
        face_by_id = {row['id']: row['executor_migration']
                      for row in face['rows']}
        row = statuses['story_user_prepares_agent_network']
        self.assertEqual(row['mode'], 'DUAL')
        self.assertEqual(row['preprod_verdict'], 'PASS')
        self.assertEqual(row['gates'], [])
        self.assertEqual(row['notes'], [AGENT_NETWORK_DENSITY_NOTE])
        self.assertEqual(
            face_by_id['story_user_prepares_agent_network']['notes'],
            [AGENT_NETWORK_DENSITY_NOTE])

    def test_agent_network_density_note_closes_the_inventory_fail_close_fence(self):
        from bcs_e2e import inventory as inventory_module
        live_counts = inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS
        self.assertIn(AGENT_NETWORK_DENSITY_NOTE,
                      inventory_module.EXECUTOR_MIGRATION_NOTES)
        self.assertEqual(live_counts[AGENT_NETWORK_DENSITY_NOTE], 1)
        self.assertEqual(len(inventory_module.EXECUTOR_MIGRATION_NOTES),
                         sum(live_counts.values()))


    def test_owner_isolation_face_notes_lock_rows_35_and_36_loader_and_face(self):
        statuses = load_executor_migration()
        face = json.loads(
            (ROOT / 'executor-migration.json').read_text(encoding='utf-8'))
        face_by_id = {row['id']: row['executor_migration']
                      for row in face['rows']}
        locks = (
            ('story_provider_owner_isolation', OWNER_ISOLATION_PROVIDER_NOTE),
            ('story_session_owner_permissions', OWNER_ISOLATION_SESSION_NOTE),
        )
        for story_id, note in locks:
            with self.subTest(story_id=story_id):
                row = statuses[story_id]
                self.assertEqual(row['mode'], 'DUAL')
                self.assertEqual(row['preprod_verdict'], 'NOT_RUN')
                self.assertEqual(row['gates'], [])
                self.assertEqual(len(row['notes']), 1)
                self.assertEqual(row['notes'], [note])
                self.assertEqual(face_by_id[story_id]['notes'], [note])

    def test_owner_isolation_notes_close_the_inventory_fail_close_fence(self):
        from bcs_e2e import inventory as inventory_module
        live_counts = inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS
        for note in (OWNER_ISOLATION_PROVIDER_NOTE,
                     OWNER_ISOLATION_SESSION_NOTE):
            with self.subTest(note_prefix=note[:48]):
                self.assertIn(note, inventory_module.EXECUTOR_MIGRATION_NOTES)
                self.assertEqual(live_counts[note], 1)
        self.assertEqual(len(inventory_module.EXECUTOR_MIGRATION_NOTES),
                         sum(live_counts.values()))

    def test_fixed_loop_density_locks_rows_18_and_19_face_notes(self):
        statuses = load_executor_migration()
        face = json.loads(
            (ROOT / 'executor-migration.json').read_text(encoding='utf-8'))
        face_by_id = {row['id']: row['executor_migration']
                      for row in face['rows']}
        locks = (
            ('story_fixed_loop_human_editor_workflow', FIXED_ROW_NOTE_PROPOSAL),
            ('story_nested_fixed_loop_workflows', NESTED_ROW_NOTE_PROPOSAL),
        )
        for story_id, note in locks:
            with self.subTest(story_id=story_id):
                row = statuses[story_id]
                self.assertEqual(row['mode'], 'DUAL')
                self.assertEqual(row['preprod_verdict'], 'BLOCKED_INVENTORIED')
                self.assertEqual(row['gates'], [])
                self.assertEqual(len(row['notes']), 1)
                self.assertEqual(row['notes'], [note])
                self.assertEqual(face_by_id[story_id]['notes'], [note])

    def test_fixed_loop_density_notes_close_the_inventory_fail_close_fence(self):
        from bcs_e2e import inventory as inventory_module
        live_counts = inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS
        for note in (FIXED_ROW_NOTE_PROPOSAL, NESTED_ROW_NOTE_PROPOSAL):
            with self.subTest(note_prefix=note[:48]):
                self.assertIn(note, inventory_module.EXECUTOR_MIGRATION_NOTES)
                self.assertEqual(live_counts[note], 1)
        self.assertEqual(len(inventory_module.EXECUTOR_MIGRATION_NOTES),
                         sum(live_counts.values()))
