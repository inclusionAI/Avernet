"""Focused registration and dispatch guards for independent backlog slices."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.local_story_commands import (
    LOCAL_STORY_COMMANDS,
    register_local_story_commands,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError


def parser():
    value = argparse.ArgumentParser()
    register_local_story_commands(value.add_subparsers(dest='command', required=True))
    return value


def _draft_run_args() -> list[str]:
    return [
        'local-draft-delivery-run', '--binary', '/bcs', '--binary-sha256', 'bcs-sha',
        '--cli-binary', '/bcs-cli', '--cli-binary-sha256', 'cli-sha',
        '--state-dir', '/state', '--run-id', 'run-1',
        '--confirm-run-id', 'run-1', '--allow-local-processes',
        '--allow-fixture-write', '--allow-write',
    ]


def run_args(command: str) -> list[str]:
    return [
        command, '--binary', '/bcs', '--binary-sha256', 'bcs-sha',
        '--state-dir', '/state', '--run-id', 'run-1',
        '--confirm-run-id', 'run-1', '--allow-local-processes',
        '--allow-fixture-write', '--allow-write',
    ]


class SessionOwnerCommandTests(unittest.TestCase):
    def test_commands_are_in_the_shared_local_registry(self):
        self.assertIn('local-session-owner-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-session-owner-report', LOCAL_STORY_COMMANDS)
        parsed = parser().parse_args(run_args('local-session-owner-run'))
        self.assertEqual(parsed.state_dir, Path('/state'))
        self.assertTrue(parsed.allow_write)

    def test_run_dispatch_forwards_exact_arguments(self):
        args = parser().parse_args(run_args('local-session-owner-run'))
        for status, expected_rc in (
                ('LOCAL_SESSION_OWNER_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.session_owner.local_session_owner_run.local_session_owner_run',
                    return_value={'status': status}) as execute:
                from bcs_e2e.local_story_commands import dispatch_local_story_command
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(
                binary=Path('/bcs'), binary_sha256='bcs-sha',
                state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
                allow_local_processes=True, allow_fixture_write=True,
                allow_write=True)

    def test_report_dispatch_guards_the_original_evidence_tree(self):
        args = parser().parse_args(
            ['local-session-owner-report', '--state-dir', '/state',
             '--output-dir', '/out/reports'])
        summary = {'status': 'LOCAL_SUITE_PASS'}
        writes = Mock(return_value={'json': '/out/reports/summary.json'})
        with patch(
                'bcs_e2e.session_owner.local_session_owner_reporting.build_session_owner_report',
                return_value=summary) as build:
            from bcs_e2e.local_story_commands import dispatch_local_story_command
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(rc, 0)
        self.assertEqual(report['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(report['outputs'], {'json': '/out/reports/summary.json'})
        build.assert_called_once_with(Path('/state'))
        inside = parser().parse_args(
            ['local-session-owner-report', '--state-dir', '/state',
             '--output-dir', '/state/reports'])
        with patch(
                'bcs_e2e.session_owner.local_session_owner_reporting.build_session_owner_report',
                return_value=summary), self.assertRaisesRegex(
                    RunStateError, 'Session owner'):
            from bcs_e2e.local_story_commands import dispatch_local_story_command
            dispatch_local_story_command(inside, write_reports=Mock())

    def test_session_owner_is_a_backlog_row_without_release_credit(self):
        rows = migration_plan()['local_backlog_case_slices']
        row = next(row for row in rows if row['case_id'] == 'LOCAL-SESSION-OWNER-01')
        self.assertEqual(row['suite'], 'owned-local-session-owner-v1')
        self.assertEqual(row['legacy_id'], 'story_session_owner_permissions')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-session-owner-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
            'state_dir', 'run_id', 'confirm_run_id', 'allow_write'])
        self.assertEqual(row['environment'], {
            'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertFalse(row['grants_release_credit'])



class DraftDeliveryCommandTests(unittest.TestCase):
    def test_commands_are_in_the_shared_local_registry(self):
        self.assertIn('local-draft-delivery-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-draft-delivery-report', LOCAL_STORY_COMMANDS)
        parsed = parser().parse_args(_draft_run_args())
        self.assertEqual(parsed.binary, Path('/bcs'))
        self.assertEqual(parsed.cli_binary, Path('/bcs-cli'))
        self.assertTrue(parsed.allow_fixture_write)

    def test_run_dispatch_forwards_exact_arguments(self):
        args = parser().parse_args(_draft_run_args())
        for status, expected_rc in (
                ('LOCAL_DRAFT_DELIVERY_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.draft_delivery.run.local_draft_delivery_run',
                    return_value={'status': status}) as execute:
                from bcs_e2e.local_story_commands import dispatch_local_story_command
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(
                binary=Path('/bcs'), binary_sha256='bcs-sha',
                cli_binary=Path('/bcs-cli'), cli_binary_sha256='cli-sha',
                state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
                allow_local_processes=True, allow_fixture_write=True,
                allow_write=True)

    def test_report_dispatch_guards_the_original_evidence_tree(self):
        args = parser().parse_args(
            ['local-draft-delivery-report', '--state-dir', '/state',
             '--output-dir', '/out/reports'])
        summary = {'status': 'LOCAL_SUITE_PASS'}
        writes = Mock(return_value={'json': '/out/reports/summary.json'})
        with patch(
                'bcs_e2e.draft_delivery.reporting.build_draft_delivery_report',
                return_value=summary) as build:
            from bcs_e2e.local_story_commands import dispatch_local_story_command
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(rc, 0)
        self.assertEqual(report['outputs'], {'json': '/out/reports/summary.json'})
        build.assert_called_once_with(Path('/state'))
        inside = parser().parse_args(
            ['local-draft-delivery-report', '--state-dir', '/state',
             '--output-dir', '/state/reports'])
        with patch(
                'bcs_e2e.draft_delivery.reporting.build_draft_delivery_report',
                return_value=summary), self.assertRaisesRegex(
                    RunStateError, 'Draft delivery'):
            from bcs_e2e.local_story_commands import dispatch_local_story_command
            dispatch_local_story_command(inside, write_reports=Mock())

    def test_draft_delivery_is_a_backlog_row_without_release_credit(self):
        rows = migration_plan()['local_backlog_case_slices']
        row = next(row for row in rows if row['case_id'] == 'LOCAL-DRAFT-DELIVERY-01')
        self.assertEqual(row['suite'], 'owned-local-draft-delivery-v1')
        self.assertEqual(row['legacy_id'], 'story_collaboration_draft_delivery')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-draft-delivery-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
            'state_dir', 'run_id', 'confirm_run_id', 'allow_write'])
        self.assertEqual(row['environment'], {
            'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertFalse(row['grants_release_credit'])



class ProviderOwnerCommandTests(unittest.TestCase):
    def test_commands_are_in_the_shared_local_registry(self):
        self.assertIn('local-provider-owner-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-provider-owner-report', LOCAL_STORY_COMMANDS)
        parsed = parser().parse_args(run_args('local-provider-owner-run'))
        self.assertEqual(parsed.run_id, 'run-1')
        self.assertTrue(parsed.allow_local_processes)

    def test_run_dispatch_forwards_exact_arguments_and_blocked_status(self):
        args = parser().parse_args(run_args('local-provider-owner-run'))
        for status, expected_rc in (
                ('LOCAL_PROVIDER_OWNER_PASS', 0), ('BLOCKED', 2), ('CANCELLED', 130)):
            with self.subTest(status=status), patch(
                    'bcs_e2e.local_provider_owner_run.local_provider_owner_run',
                    return_value={'status': status}) as execute:
                from bcs_e2e.local_story_commands import dispatch_local_story_command
                report, rc = dispatch_local_story_command(args, write_reports=Mock())
            self.assertEqual((report, rc), ({'status': status}, expected_rc))
            execute.assert_called_once_with(
                binary=Path('/bcs'), binary_sha256='bcs-sha',
                state_dir=Path('/state'), run_id='run-1', confirm_run_id='run-1',
                allow_local_processes=True, allow_fixture_write=True,
                allow_write=True)

    def test_report_dispatch_guards_the_original_evidence_tree(self):
        args = parser().parse_args(
            ['local-provider-owner-report', '--state-dir', '/state',
             '--output-dir', '/out/reports'])
        summary = {'status': 'LOCAL_SUITE_PASS'}
        writes = Mock(return_value={'json': '/out/reports/summary.json'})
        with patch(
                'bcs_e2e.local_provider_owner_reporting.build_provider_owner_report',
                return_value=summary) as build:
            from bcs_e2e.local_story_commands import dispatch_local_story_command
            report, rc = dispatch_local_story_command(args, write_reports=writes)
        self.assertEqual(rc, 0)
        self.assertEqual(report['outputs'], {'json': '/out/reports/summary.json'})
        build.assert_called_once_with(Path('/state'))
        inside = parser().parse_args(
            ['local-provider-owner-report', '--state-dir', '/state',
             '--output-dir', '/state/reports'])
        with patch(
                'bcs_e2e.local_provider_owner_reporting.build_provider_owner_report',
                return_value=summary), self.assertRaisesRegex(
                    RunStateError, 'Provider owner'):
            from bcs_e2e.local_story_commands import dispatch_local_story_command
            dispatch_local_story_command(inside, write_reports=Mock())

    def test_provider_owner_is_a_backlog_row_without_release_credit(self):
        rows = migration_plan()['local_backlog_case_slices']
        row = next(row for row in rows if row['case_id'] == 'LOCAL-PROVIDER-OWNER-01')
        self.assertEqual(row['suite'], 'owned-local-provider-owner-v1')
        self.assertEqual(row['legacy_id'], 'story_provider_owner_isolation')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-provider-owner-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
            'state_dir', 'run_id', 'confirm_run_id', 'allow_write'])
        self.assertEqual(row['environment'], {
            'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertFalse(row['grants_release_credit'])


class GroupContextBacklogTests(unittest.TestCase):
    def test_existing_dual_command_is_reused_without_a_duplicate_local_alias(self):
        self.assertIn('dual-group-context-run', CASE_COMMANDS)
        self.assertNotIn('dual-group-context-run', LOCAL_STORY_COMMANDS)
        self.assertNotIn('local-group-context-run', LOCAL_STORY_COMMANDS)

    def test_group_context_row_is_backlog_with_explicit_report_gap(self):
        rows = migration_plan()['local_backlog_case_slices']
        self.assertEqual(len(rows), 10)
        row = next(row for row in rows if row['case_id'] == 'DUAL-GROUP-CONTEXT-CLI-01')
        self.assertEqual(row['suite'], 'dual-group-context-cli-v1')
        self.assertEqual(row['legacy_id'], 'story_cli_operator_manages_group_context')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['required_assertion_count'], 9)
        self.assertEqual(row['execution_command'], 'dual-group-context-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_parameters'], [
            'profile', 'cli_binary', 'cli_binary_sha256', 'state_dir',
            'run_id', 'confirm_run_id', 'allow_write'])
        self.assertEqual(row['environment'], {
            'local': 'LOCAL_EXECUTOR_ONLY',
            'preprod': 'PROFILE_ASSET_PRESENT_NOT_RUN'})
        self.assertEqual(row['source_map'], 'group-context-migration-contract.json')
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertFalse(row['grants_release_credit'])




class ProviderPublishesBacklogTests(unittest.TestCase):
    def test_provider_publishes_is_a_backlog_row_without_release_credit(self):
        rows = migration_plan()['local_backlog_case_slices']
        row = next(row for row in rows if row['case_id'] == 'DUAL-PROVIDER-PUBLISHES-01')
        self.assertEqual(row['suite'], 'dual-provider-publishes-v1')
        self.assertEqual(row['legacy_id'], 'story_provider_operator_publishes_agent')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-provider-publishes-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_assertion_count'], 98)
        self.assertEqual(len(row['requirement_ids']), 98)
        self.assertEqual(row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
            'state_dir', 'run_id', 'confirm_run_id', 'allow_write'])
        self.assertEqual(row['environment'], {
            'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'BLOCKED_AT_ENTRY_NO_NETWORK'})
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(row['source_map'], 'provider-publishes-migration-contract.json')


class SlowJudgeBacklogTests(unittest.TestCase):
    def test_slow_judge_is_a_backlog_row_without_release_credit(self):
        rows = migration_plan()['local_backlog_case_slices']
        row = next(row for row in rows if row['case_id'] == 'DUAL-SLOW-JUDGE-01')
        self.assertEqual(row['suite'], 'dual-slow-judge-v1')
        self.assertEqual(row['legacy_id'], 'story_provider_callback_survives_slow_judge')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-slow-judge-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_assertion_count'], 22)
        self.assertEqual(len(row['requirement_ids']), 22)
        self.assertEqual(row['required_parameters'], [
            'profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
            'state_dir', 'run_id', 'confirm_run_id', 'allow_write'])
        self.assertEqual(row['environment'], {
            'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_JOURNEY_SEAM_PENDING'})
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(row['source_map'], 'slow-judge-dual-migration-contract.json')

    def test_dual_command_is_registered_without_a_duplicate_local_alias(self):
        from bcs_e2e.case_command_registration import CASE_COMMANDS
        self.assertIn('dual-slow-judge-run', CASE_COMMANDS)
        self.assertNotIn('dual-slow-judge-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-slow-judge-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-slow-judge-report', LOCAL_STORY_COMMANDS)

    def test_provider_publishes_dual_command_is_registered(self):
        from bcs_e2e.case_command_registration import CASE_COMMANDS
        self.assertIn('dual-provider-publishes-run', CASE_COMMANDS)
        self.assertNotIn('dual-provider-publishes-run', LOCAL_STORY_COMMANDS)



class DualEdgePermissionBacklogRegistrationTests(unittest.TestCase):
    def test_command_is_registered_only_in_the_dual_registry(self):
        self.assertIn('dual-edge-permission-backlog-run', CASE_COMMANDS)
        self.assertNotIn('dual-edge-permission-backlog-run', LOCAL_STORY_COMMANDS)
        self.assertNotIn('local-edge-permission-backlog-run', LOCAL_STORY_COMMANDS)

    def test_parser_accepts_the_offline_dual_flags(self):
        from bcs_e2e.case_command_registration import register_case_commands
        root_parser = argparse.ArgumentParser()
        register_case_commands(root_parser.add_subparsers(dest='command', required=True))
        parsed = root_parser.parse_args([
            'dual-edge-permission-backlog-run', '--profile', '/preprod.json',
            '--case', 'tc-friend-auth-sync', '--state-dir', '/state',
            '--run-id', 'run-1', '--confirm-run-id', 'run-1', '--allow-write'])
        self.assertEqual(parsed.command, 'dual-edge-permission-backlog-run')
        self.assertEqual(parsed.profile, Path('/preprod.json'))
        self.assertEqual(parsed.case, 'tc-friend-auth-sync')
        self.assertEqual(parsed.state_dir, Path('/state'))
        self.assertEqual(parsed.run_id, 'run-1')
        self.assertTrue(parsed.allow_write)
        with self.assertRaises(SystemExit):
            root_parser.parse_args([
                'dual-edge-permission-backlog-run', '--profile', '/preprod.json',
                '--case', 'unknown-slice', '--state-dir', '/state',
                '--run-id', 'run-1', '--confirm-run-id', 'run-1'])

    def test_backlog_rows_flip_to_executable_dual_receipts(self):
        rows = migration_plan()['local_backlog_case_slices']
        expected_ids = {'LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01',
                        'LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01'}
        rows = [row for row in rows if row['case_id'] in expected_ids]
        self.assertEqual(len(rows), 2)
        for row in rows:
            with self.subTest(case_id=row['case_id']):
                self.assertEqual(row['status'], 'DUAL_EXECUTION_READY')
                self.assertEqual(row['canonical_reporting'], 'DUAL_EXECUTION_READY')
                self.assertEqual(row['execution_command'],
                                 'dual-edge-friend-mutation-run')
                self.assertEqual(row['report_command'], '')
                self.assertEqual(row['required_parameters'], [
                    'profile', 'station_profile', 'case', 'state_dir', 'run_id',
                    'confirm_run_id', 'allow_write'])
                self.assertEqual(row['remote_validation'], 'NOT_RUN')
                self.assertFalse(row['release_parent_denominator'])
                self.assertTrue(row['independent_slice'])
                self.assertFalse(row['grants_release_credit'])

    def test_executor_migration_rows_flip_to_dual_not_run(self):
        from bcs_e2e.inventory import legacy_inventory_with_executor_migration
        legacy = legacy_inventory_with_executor_migration()
        rows = {row['id']: row['executor_migration'] for row in legacy}
        for case_id in ('test_ep_tc_friend_auth_sync_readdressed_principal',
                        'test_ep_dept_scope_parses_object_friend_ext_entries'):
            with self.subTest(case_id=case_id):
                self.assertEqual(rows[case_id]['mode'], 'DUAL')
                self.assertEqual(rows[case_id]['preprod_verdict'], 'NOT_RUN')
                self.assertEqual(rows[case_id]['gates'], [])
                self.assertEqual(rows[case_id]['notes'], [])

if __name__ == '__main__':
    unittest.main()
