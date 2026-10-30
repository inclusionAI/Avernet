import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.provider_publishes import dual_case_run as dual
from bcs_e2e.provider_publishes.contract import CLI_SHA256, SERVER_SHA256
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError


class DualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.profile = self.root / 'profile.json'
        self.profile.write_text(json.dumps(dual.profile_for('owned-local')))
        self.state = self.root / 'fresh-state'
        self.args = ['--profile', str(self.profile), '--binary', str(self.root / 'bcs'),
            '--binary-sha256', SERVER_SHA256, '--cli-binary', str(self.root / 'bcs-cli'),
            '--cli-binary-sha256', CLI_SHA256, '--state-dir', str(self.state),
            '--run-id', 's04-test', '--confirm-run-id', 's04-test', '--allow-write']
        self.kw = dict(profile_path=self.profile, binary=self.root/'bcs', binary_sha256=SERVER_SHA256,
            cli_binary=self.root/'bcs-cli', cli_binary_sha256=CLI_SHA256, state_dir=self.state,
            run_id='s04-test', confirm_run_id='s04-test', allow_write=True)

    def test_missing_each_binary_pair_argument_nonzero_without_writes(self):
        for flag in ('--binary', '--binary-sha256', '--cli-binary', '--cli-binary-sha256'):
            with self.subTest(flag=flag):
                argv = list(self.args)
                i = argv.index(flag)
                del argv[i:i + 2]
                before = sorted(p.name for p in self.root.iterdir())
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                    dual.main(argv)
                self.assertNotEqual(cm.exception.code, 0)
                self.assertFalse(self.state.exists())
                self.assertEqual(before, sorted(p.name for p in self.root.iterdir()))

    def test_absent_binary_nonzero_without_state(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(dual.main(self.args), 0)
        self.assertFalse(self.state.exists())

    def test_missing_pair_programmatically_rejected_before_write(self):
        for key in ('binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256'):
            kw = dict(self.kw, **{key: None})
            with self.subTest(key=key), self.assertRaises(RunStateError):
                dual.dual_provider_publishes_case_run(**kw)
            self.assertFalse(self.state.exists())

    def test_missing_cli_is_rejected_after_valid_server_admission(self):
        from bcs_e2e.provider_publishes.contract import validate_binary
        def validate(path, digest, expected, label):
            if label == 'BCS':
                return path
            return validate_binary(path, digest, expected, label)
        with patch.object(dual, 'validate_binary', side_effect=validate), self.assertRaises(RunStateError):
            dual.dual_provider_publishes_case_run(**self.kw)
        self.assertFalse(self.state.exists())

    def test_bad_hash_or_symlink_rejected_before_write(self):
        executable = self.root / 'exe'
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o700)
        link = self.root / 'link'
        link.symlink_to(executable)
        for path, digest in ((executable, '0'*64), (executable, SERVER_SHA256), (link, SERVER_SHA256)):
            with self.subTest(path=path, digest=digest), self.assertRaises(RunStateError):
                dual.validate_binary(path, digest, SERVER_SHA256, 'BCS')
        self.assertFalse(self.state.exists())

    def test_write_permission_and_confirmation(self):
        for changes in ({'allow_write': False}, {'confirm_run_id': 'other'}):
            with self.assertRaises(ConfigError):
                dual.dual_provider_publishes_case_run(**dict(self.kw, **changes))
        self.assertFalse(self.state.exists())

    def test_existing_state_preserved(self):
        self.state.mkdir()
        (self.state/'keep').write_text('evidence')
        with patch.object(dual, 'validate_binary', side_effect=lambda p, *_: p), self.assertRaises(ConfigError):
            dual.dual_provider_publishes_case_run(**self.kw)
        self.assertEqual((self.state/'keep').read_text(), 'evidence')

    def test_invalid_profile_no_state(self):
        self.profile.write_text(json.dumps(dict(dual.profile_for('preprod'), unexpected='bad')))
        with patch.object(dual, 'validate_binary', side_effect=lambda p, *_: p), self.assertRaises(ConfigError):
            dual.dual_provider_publishes_case_run(**self.kw)
        self.assertFalse(self.state.exists())

    def test_preprod_zero_network_even_when_environment_has_credentials(self):
        self.profile.write_text(json.dumps(dual.profile_for('preprod')))
        with patch.object(dual, 'validate_binary', side_effect=lambda p, *_: p), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch.object(dual, 'local_provider_publishes_run', side_effect=AssertionError('local forbidden')), \
             patch.dict('os.environ', {'BCS_E2E_HUMAN_COOKIE': 'not-a-real-credential'}):
            result = dual.dual_provider_publishes_case_run(**self.kw)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason_class'], dual.PREPROD_REASON)
        self.assertEqual(result['preprod_blocker']['network_requests'], 0)
        self.assertIsNone(result['preprod_blocker']['observed_status'])
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['remote_entry_status'], 'BLOCKED-at-entry')
        self.assertEqual(result['obligation_summary']['not_run'], 98)
        self.assertFalse(result['grants_release_credit'])
        self.assertNotIn('not-a-real-credential', (self.state/'provider-publishes-dual-run.json').read_text())

    def test_case_level_pass_cannot_fabricate_field_passes(self):
        local = {'status': 'PASS', 'case': {'assertions': []}}
        with patch.object(dual, 'validate_binary', side_effect=lambda p, *_: p), \
             patch.object(dual, 'local_provider_publishes_run', return_value=local):
            result = dual.dual_provider_publishes_case_run(**self.kw)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['obligation_summary']['passed'], 0)

    def test_duplicate_missing_or_failed_obligation_cannot_pass(self):
        for mutate in ('duplicate', 'missing', 'fail'):
            assertions = [{'id': r, 'status': 'PASS'} for r in dual.REQUIREMENTS]
            if mutate == 'duplicate':
                assertions[-1] = dict(assertions[0])
            elif mutate == 'missing':
                assertions.pop()
            else:
                assertions[20]['status'] = 'FAIL'
            kw = dict(self.kw, state_dir=self.root/mutate)
            with patch.object(dual, 'validate_binary', side_effect=lambda p, *_: p), \
                 patch.object(dual, 'local_provider_publishes_run', return_value={'status': 'PASS', 'case': {'assertions': assertions}}):
                result = dual.dual_provider_publishes_case_run(**kw)
            self.assertEqual(result['status'], 'BLOCKED')
