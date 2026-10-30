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
        self.assertEqual(len(rows), 6)
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


if __name__ == '__main__':
    unittest.main()
