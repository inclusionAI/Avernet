"""Aggregator regressions; mocks replace execution, never product evidence."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcs_e2e import local_story_catalog as roster
from bcs_e2e import local_story_commands as suite
from bcs_e2e.cli import main
from bcs_e2e.run_state import RunStateError
from bcs_e2e.inventory import REVIEWED_RELEASE_PARENT_COUNT
from tests.test_local_template_runtime import approved_asset


class CoverageSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.listing = suite.list_cases()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / 'test-binary'
        self.binary.write_bytes(b'unit-test-only binary, never executed')
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.panel = self.root / 'test-panel.js'
        self.panel.write_bytes(b'/* unit-test-only bundle, never served */' * 50)
        asset = approved_asset(self.root)
        self.kwargs = dict(template_dir=asset.directory, template_manifest=asset.manifest_path,
                           template_manifest_sha256=asset.manifest_sha256, binary=self.binary, binary_sha256=self.digest,
                           cli_binary=self.binary, cli_binary_sha256=self.digest,
                           state_dir=self.root / 'suite', run_id='coverage-test',
                           confirm_run_id='coverage-test', allow_local_processes=True,
                           allow_fixture_write=True, allow_write=True, panel_asset=self.panel)
        patcher = patch.object(suite, 'list_cases', return_value=self.listing)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_suite(self, execute=None, **overrides):
        with patch.object(suite, '_execute', side_effect=execute or self.pass_case) as call:
            result = suite.local_coverage_suite(**(self.kwargs | overrides))
        return result, call

    @staticmethod
    def pass_case(case, kwargs, panel):
        path = kwargs['state_dir']
        path.mkdir()
        (path / 'sentinel.txt').write_text(case.case_id)
        return {'status': 'PASS'}, 0

    def test_all_reviewed_release_parents_summary_report_and_unique_directories(self):
        result, call = self.run_suite()
        self.assertEqual(result['status'], 'LOCAL_COVERAGE_SUITE_PASS')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['counts']['PASS'], REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual([row['case_id'] for row in result['cases']], [case.case_id for case in suite.CASES])
        self.assertEqual(len({row['state_dir'] for row in result['cases']}), REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(len({row['run_id'] for row in result['cases']}), REVIEWED_RELEASE_PARENT_COUNT)
        for row in result['cases']:
            self.assertEqual((Path(row['state_dir']) / 'sentinel.txt').read_text(), row['case_id'])
        self.assertEqual(result['binaries']['bcs']['sha256'], self.digest)
        self.assertEqual(result['binaries']['cli']['sha256'], self.digest)
        self.assertEqual(json.loads(Path(result['outputs']['summary']).read_text()), result)
        self.assertIn('NOT_RUN', Path(result['outputs']['report']).read_text())
        self.assertFalse(result['grants_release_credit'])
        self.assertFalse(result['automatic_cleanup'])

    def test_exception_and_nonzero_continue_through_last_case(self):
        def execute(case, kwargs, panel):
            outcome = self.pass_case(case, kwargs, panel)
            if case == suite.CASES[0]:
                raise RuntimeError('private secret must not be echoed')
            if case == suite.CASES[1]:
                return {'status': 'BLOCKED', 'error_type': 'AssertionError'}, 2
            return outcome
        result, call = self.run_suite(execute)
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(result['exit_code'], 2)
        self.assertEqual(result['counts']['PASS'], REVIEWED_RELEASE_PARENT_COUNT - 2)
        self.assertEqual(result['counts']['FAILED'], 1)
        self.assertEqual(result['counts']['BLOCKED'], 1)
        self.assertEqual(result['cases'][-1]['status'], 'PASS')
        self.assertIn('RuntimeError', result['cases'][0]['failure_reason'])
        self.assertIn('AssertionError', result['cases'][1]['failure_reason'])
        self.assertNotIn('private secret', json.dumps(result))
        self.assertTrue((Path(result['cases'][0]['state_dir']) / 'sentinel.txt').exists())

    def test_keyboard_interrupt_retains_state_and_marks_unstarted_cases(self):
        def execute(case, kwargs, panel):
            self.pass_case(case, kwargs, panel)
            raise KeyboardInterrupt()
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        result, call = self.run_suite(execute)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['exit_code'], 130)
        self.assertEqual(result['counts']['CANCELLED'], REVIEWED_RELEASE_PARENT_COUNT)
        self.assertTrue(Path(result['cases'][0]['state_dir']).exists())
        self.assertFalse(result['cases'][1]['attempted'])
        self.assertFalse(Path(result['cases'][1]['state_dir']).exists())
        self.assertIn('not_started', result['cases'][1]['failure_reason'])
        self.assertEqual(before, {sig: signal.getsignal(sig) for sig in before})

    def test_returned_cancelled_and_wrapped_cancellation_stop_dispatch(self):
        for mode in ('return', 'wrapped', 'canonical', 'signal'):
            with self.subTest(mode=mode):
                def execute(case, kwargs, panel):
                    if mode == 'signal':
                        os.kill(os.getpid(), signal.SIGTERM)
                    if mode == 'return':
                        return {'status': 'CANCELLED'}, 2
                    if mode == 'canonical':
                        return {'status': 'BLOCKED', 'canonical_summary': {'status': 'CANCELLED'}}, 2
                    try:
                        raise KeyboardInterrupt()
                    except KeyboardInterrupt:
                        raise RuntimeError('finalization failed')
                result, call = self.run_suite(execute, state_dir=self.root / mode)
                self.assertEqual(call.call_count, 1)
                self.assertEqual(result['exit_code'], 130)

    def test_structured_effect_cancellation_is_not_suite_cancellation(self):
        report = {
            'status': 'LOCAL_STRUCTURED_PASS',
            'case': {'status': 'LOCAL_STRUCTURED_PASS',
                     'effects': {'explicit': {'status': 'CANCELLED'}}},
            'canonical_summary': {'status': 'LOCAL_SUITE_PASS'},
        }
        self.assertEqual(suite._outcome(report, 0), {'status': 'PASS', 'failure_reason': None})

    def test_case_directory_collision_is_blocked_without_overwrite(self):
        collision = self.kwargs['state_dir'] / 'cases' / suite.CASES[1].case_id
        def execute(case, kwargs, panel):
            if case == suite.CASES[0]:
                collision.mkdir()
                (collision / 'sentinel').write_text('preexisting')
            return self.pass_case(case, kwargs, panel)
        result, call = self.run_suite(execute)
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT - 1)
        self.assertEqual(result['cases'][1]['status'], 'BLOCKED')
        self.assertEqual(result['cases'][1]['failure_reason'], 'case_state_dir_already_exists')
        self.assertEqual((collision / 'sentinel').read_text(), 'preexisting')
        self.assertEqual(result['cases'][-1]['status'], 'PASS')

    def test_case_symlink_collision_cannot_alias_another_state(self):
        def execute(case, kwargs, panel):
            if case == suite.CASES[0]:
                target = self.kwargs['state_dir'] / 'cases' / suite.CASES[1].case_id
                target.symlink_to(kwargs['state_dir'], target_is_directory=True)
            return self.pass_case(case, kwargs, panel)
        result, call = self.run_suite(execute)
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT - 1)
        self.assertEqual(result['cases'][1]['status'], 'BLOCKED')
        self.assertEqual((Path(result['cases'][0]['state_dir']) / 'sentinel.txt').read_text(), suite.CASES[0].case_id)

    def test_repeated_suite_never_overwrites_previous_reports(self):
        result, _ = self.run_suite()
        saved = Path(result['outputs']['summary']).read_bytes()
        with self.assertRaises(RunStateError), patch.object(suite, '_execute') as call:
            suite.local_coverage_suite(**self.kwargs)
        call.assert_not_called()
        self.assertEqual(Path(result['outputs']['summary']).read_bytes(), saved)

    def test_reject_existing_empty_root_or_symlink(self):
        target = self.root / 'existing'
        target.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        for path in (target, alias):
            with self.subTest(path=path), self.assertRaises(RunStateError):
                self.run_suite(state_dir=path)
        self.assertEqual(list(target.iterdir()), [])

    def test_missing_cli_only_blocks_cli_cases_and_continues(self):
        result, call = self.run_suite(cli_binary=None, cli_binary_sha256=None)
        cli_count = sum(case.cli for case in suite.CASES)
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT - cli_count)
        self.assertEqual(result['counts']['BLOCKED'], cli_count)
        self.assertEqual(result['cases'][-1]['status'], 'PASS')

    def test_wrong_bcs_sha_starts_nothing_and_reports_every_case(self):
        result, call = self.run_suite(binary_sha256='0' * 64)
        call.assert_not_called()
        self.assertEqual(result['counts']['BLOCKED'], REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(result['exit_code'], 2)
        self.assertIn('sha256_mismatch', result['cases'][0]['failure_reason'])

    def test_missing_or_wrong_panel_blocks_network_only(self):
        for mode in ('missing', 'wrong-sha'):
            with self.subTest(mode=mode):
                result, call = self.run_suite(
                    state_dir=self.root / mode,
                    panel_asset=self.root / 'absent.js' if mode == 'missing' else self.panel,
                    panel_sha256=None if mode == 'missing' else '0' * 64)
                self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT - 1)
                self.assertEqual(result['counts']['BLOCKED'], 1)
                self.assertEqual(result['cases'][3]['status'], 'BLOCKED')
                self.assertEqual(result['cases'][-1]['status'], 'PASS')

    def test_cancellation_at_last_checkpoint_cannot_report_pass(self):
        publish = suite._publish
        sent = False
        def checkpoint(root, summary, *, finished):
            nonlocal sent
            if not sent and all(row['status'] == 'PASS' for row in summary['cases']):
                sent = True
                os.kill(os.getpid(), signal.SIGTERM)
            publish(root, summary, finished=finished)
        with patch.object(suite, '_publish', side_effect=checkpoint):
            result, call = self.run_suite()
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['exit_code'], 130)
        self.assertTrue(result['cancellation_requested'])

    def test_missing_templates_are_blocked_not_skipped(self):
        result, call = self.run_suite(template_manifest=None, template_manifest_sha256=None)
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT - 1)
        self.assertEqual(result['cases'][14]['status'], 'BLOCKED')
        self.assertEqual(result['cases'][-1]['status'], 'PASS')
        self.assertIn('approved_template_manifest', result['cases'][14]['failure_reason'])

    def test_missing_executor_is_blocked_and_following_cases_execute(self):
        def execute(case, kwargs, panel):
            if case == suite.CASES[0]:
                raise ModuleNotFoundError('not available')
            return self.pass_case(case, kwargs, panel)
        result, call = self.run_suite(execute)
        self.assertEqual(call.call_count, REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(result['cases'][0]['status'], 'BLOCKED')
        self.assertEqual(result['cases'][-1]['status'], 'PASS')

    def test_permissions_and_confirmation_required_before_writes(self):
        for key, value in (('allow_local_processes', False), ('allow_fixture_write', False),
                           ('allow_write', False), ('confirm_run_id', 'another-run')):
            with self.subTest(key=key), self.assertRaises(RunStateError):
                self.run_suite(**{key: value})
        self.assertFalse(self.kwargs['state_dir'].exists())

    def test_explicit_list_cli_no_runtime_or_directory(self):
        output = io.StringIO()
        with patch.object(suite, '_execute') as execute, contextlib.redirect_stdout(output):
            rc = main(['local-coverage-suite', '--list-cases'])
        execute.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(output.getvalue())['cases']), REVIEWED_RELEASE_PARENT_COUNT)
        self.assertFalse(self.kwargs['state_dir'].exists())

    def test_cli_run_parameter_forwarding_and_exit_code(self):
        argv = ['local-coverage-suite']
        for key, value in self.kwargs.items():
            argv.append('--' + key.replace('_', '-'))
            if value is not True:
                argv.append(str(value))
        with patch.object(suite, '_execute', side_effect=self.pass_case), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv), 0)

    def test_report_write_failure_never_returns_success(self):
        with patch.object(suite, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_suite()


class BindingTests(unittest.TestCase):
    def test_zero_exit_cannot_hide_blocked_or_noncanonical_report(self):
        for report in ({'status': 'BLOCKED'}, {'status': 'unknown'},
                       {'status': 'LOCAL_SUITE_PASS', 'canonical_summary': {'status': 'BLOCKED'}}):
            with self.subTest(report=report):
                self.assertNotEqual(suite._outcome(report, 0)['status'], 'PASS')

    def test_live_inventory_exact_mapping(self):
        listing = suite.list_cases()
        self.assertEqual(listing['status'], 'READY')
        self.assertEqual(listing['case_count'], REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(len({case.case_id for case in suite.CASES}), REVIEWED_RELEASE_PARENT_COUNT)
        for row in listing['cases']:
            self.assertEqual(row['status'], 'READY')
            self.assertTrue(row['command'])

    def test_drift_duplicate_or_unknown_inventory_is_blocked(self):
        original = roster.migration_plan()
        rows = original['local_registered_executors']
        for replacement in (rows[:-1], rows + [rows[0]],
                            [dict(rows[0], execution_command='unreviewed')] + rows[1:]):
            with self.subTest(count=len(replacement)), patch.object(
                    roster, 'migration_plan', return_value={'local_registered_executors': replacement}):
                self.assertEqual(suite.list_cases()['status'], 'BLOCKED')

    def test_every_existing_command_dispatches_exact_parameters(self):
        kwargs = dict(binary=Path('/test/bcs'), binary_sha256='a' * 64,
                      state_dir=Path('/test/state'), run_id='suite-01', confirm_run_id='suite-01',
                      allow_local_processes=True, allow_fixture_write=True, allow_write=True)
        from bcs_e2e.local_story_commands import register_local_story_commands
        import argparse
        parser = argparse.ArgumentParser()
        register_local_story_commands(parser.add_subparsers(dest='command'))
        for case in suite.CASES:
            if case.function:
                continue
            values = dict(kwargs)
            if case.cli:
                values.update(cli_binary=Path('/test/cli'), cli_binary_sha256='b' * 64)
            if case.command == 'local-structured-run':
                values.update(template_dir=Path('/test/templates'), template_manifest=Path('/test/manifest.json'),
                              template_manifest_sha256='d' * 64)
            def dispatch(args, *, write_reports):
                argv = [case.command]
                for name, value in vars(args).items():
                    if name == 'command' or value is False:
                        continue
                    argv.append('--' + name.replace('_', '-'))
                    if value is not True:
                        argv.append(str(value))
                parsed = parser.parse_args(argv)
                self.assertEqual(parsed.state_dir, values['state_dir'])
                if case.command == 'local-workspace-run':
                    self.assertTrue(parsed.with_incident_delete)
                return {'status': 'PASS'}, 0
            with self.subTest(case=case.case_id), patch(
                    'bcs_e2e.local_story_commands.dispatch_local_story_command', side_effect=dispatch):
                self.assertEqual(suite._execute(case, values, {'path': '/test/panel.js', 'sha256': 'c' * 64})[1], 0)

    def test_edge_runtime_is_one_case_and_closed_before_return(self):
        for case in (case for case in suite.CASES if case.command.startswith('local-edge-')):
            with self.subTest(case=case.case_id), tempfile.TemporaryDirectory() as root:
                runtime = MagicMock()
                runtime.root = Path(root)
                runtime._record.return_value = dict(status='STOPPED', returncode=0, forced_stop=False)
                identities = MagicMock()
                identities.summary.return_value = {'credential_memory': 'CLEARED'}
                execution = MagicMock()
                execution.snapshot.return_value = dict(case_id=case.case_id, status='PASS', error_type=None,
                                                       completed_count=1, total_count=1)
                with contextlib.ExitStack() as stack:
                    owned = stack.enter_context(patch('bcs_e2e.owned_bcs.OwnedBcsProcess'))
                    owned.return_value.__enter__.return_value = runtime
                    identity = stack.enter_context(patch('bcs_e2e.local_role_identity.OwnedLocalRoleIdentity'))
                    identity.return_value.__enter__.return_value = identities
                    stack.enter_context(patch('bcs_e2e.local_fixtures.OwnedLocalFixtures'))
                    stack.enter_context(patch('bcs_e2e.inventory.executor_digest', return_value='e' * 64))
                    for name in ('OwnedEdgeReadAdapter', 'OwnedEdgeSearchAdapter', 'OwnedBotSearchFixtureAdmission'):
                        stack.enter_context(patch('bcs_e2e.owned_edge_read_adapter.' + name))
                    for name in ('OwnedEdgeFriendFixtureAdmission', 'OwnedEdgeFriendMutationsAdapter'):
                        stack.enter_context(patch('bcs_e2e.local_edge_friend_mutations_case.' + name))
                    runs = [stack.enter_context(patch('bcs_e2e.' + name, return_value=execution)) for name in (
                        'edge_read_case.execute_edge_read_case', 'edge_search_case.execute_edge_search_case',
                        'case_executor.execute_case')]
                    result = suite._edge_run(case, dict(binary=Path('/test/bcs'), binary_sha256='a' * 64,
                                                       state_dir=Path(root), run_id='suite-edge'))
                    self.assertEqual(result['status'], 'PASS')
                    self.assertEqual(sum(run.call_count for run in runs), 1)
                    owned.return_value.__exit__.assert_called_once()
                    identity.return_value.__exit__.assert_called_once()
                    self.assertEqual(owned.call_args.kwargs['root'], Path(root))
                    self.assertFalse(owned.call_args.kwargs['allow_ambient_local_identity'])


if __name__ == '__main__':
    unittest.main()
