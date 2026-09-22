"""Regression tests for launcher lifecycle and user choices."""
import json
import subprocess
import unittest

from launcher_test_support import LauncherFixture


class LauncherTest(LauncherFixture):

    def test_profile_decline_continues_to_global_bcs_reuse_prompt(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)
        source = self.repo / 'engineering/backend.md'
        original_workspace = next(self.state.glob('backend-*/workspace/SOUL.md')).read_text()
        source.write_text(source.read_text() + '\nChanged source role.\n')

        proc, master = self.launch_interactive(['engineering/backend'], ['n', 'n'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(output.lower().count('re-register all existing bcs sessions'), 1)
        self.assertEqual(len(self.registrations), registrations_before)
        self.assertEqual(next(self.state.glob('backend-*/workspace/SOUL.md')).read_text(), original_workspace)
        self.assertNotIn('Changed source role.', next(self.state.glob('backend-*/profile.md')).read_text())

    def test_unchanged_profile_still_gets_one_global_bcs_question(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)

        proc, master = self.launch_interactive(['engineering/backend'], ['n'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertNotIn('Source profile changed', output)
        self.assertEqual(output.lower().count('re-register all existing bcs sessions'), 1)
        self.assertEqual(len(self.registrations), registrations_before)

    def test_declined_profile_with_global_reregister_uses_saved_profile(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text().replace(
            'name: Backend Architect', 'name: New Backend Architect') + '\nChanged source role.\n')
        workspace = next(self.state.glob('backend-*/workspace/SOUL.md'))
        original_workspace = workspace.read_text()

        proc, master = self.launch_interactive(['engineering/backend'], ['n', 'y'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.registrations), registrations_before + 1)
        self.assertEqual(self.registrations[-1]['bot-name'], ['Backend Architect'])
        self.assertEqual(workspace.read_text(), original_workspace)
        self.assertEqual(len(list(self.state.glob('backend-*/.bcs/session.previous.*.json'))), 1)

    def test_multiple_profiles_have_individual_profile_prompts_and_one_global_bcs_prompt(self):
        proc = self.launch()
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)
        sources = [self.repo / 'engineering/backend.md', self.repo / 'design/reviewer.md']
        workspaces = [next(self.state.glob('backend-*/workspace/SOUL.md')),
                      next(self.state.glob('reviewer-*/workspace/SOUL.md'))]
        originals = [path.read_text() for path in workspaces]
        for source in sources:
            source.write_text(source.read_text() + '\nChanged source role.\n')

        proc, master = self.launch_interactive(['engineering/backend', 'design/reviewer'], ['y', 'n', 'n'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(output.lower().count('re-register all existing bcs sessions'), 1)
        self.assertEqual(len(self.registrations), registrations_before)
        self.assertIn('Changed source role.', workspaces[0].read_text())
        self.assertEqual(workspaces[1].read_text(), originals[1])

    def test_global_bcs_reregistration_applies_to_all_existing_sessions(self):
        proc = self.launch()
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)
        sources = [self.repo / 'engineering/backend.md', self.repo / 'design/reviewer.md']
        for source in sources:
            source.write_text(source.read_text() + '\nChanged source role.\n')

        proc, master = self.launch_interactive(['engineering/backend', 'design/reviewer'], ['y', 'y', 'y'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.registrations), registrations_before + 2)
        self.assertEqual(len(list(self.state.glob('*/.bcs/session.previous.*.json'))), 2)
        self.assertEqual(len(list(self.state.glob('*/bcs-reregistration.pending.json'))), 0)
        self.assertIn('Changed source role.', next(self.state.glob('backend-*/workspace/SOUL.md')).read_text())
        self.assertIn('Changed source role.', next(self.state.glob('reviewer-*/workspace/SOUL.md')).read_text())

    def test_noninteractive_overwrite_requires_yes_and_reregister_requires_explicit_flag(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text() + '\nChanged source role.\n')
        result = self.failed_run(['engineering/backend'])
        self.assertIn('--overwrite-profile', result.stderr)
        self.assertEqual(len(self.registrations), 1)
        proc = self.launch(['engineering/backend'], ['--overwrite-profile'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn('Changed source role.', next(self.state.glob('backend-*/workspace/SOUL.md')).read_text())
        self.assertEqual(len(self.registrations), 1)

        source.write_text(source.read_text() + '\nChanged again.\n')
        proc = self.launch(['engineering/backend'], ['--overwrite-profile', '--reregister'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 2)

    def test_two_profiles_are_isolated_connected_and_reused(self):
        proc = self.launch()
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.registrations), 2)
        self.assertEqual(len(self.onboards), 2)
        instances = sorted(self.state.glob('*/instance.json'))
        self.assertEqual(len(instances), 2)
        configs, ports, uuids = [], [], []
        for record in instances:
            folder = record.parent
            cfg = json.loads((folder / 'openclaw.json').read_text())
            configs.append(cfg)
            ports.append(cfg['gateway']['port'])
            session = json.loads((folder / '.bcs/session.json').read_text())
            uuids.append(session['bot_uuid'])
            self.assertEqual(cfg['channels']['bcs']['botId'], session['bot_uuid'])
            self.assertEqual(cfg['bindings'], [{'agentId': 'main', 'match': {'channel': 'bcs'}}])
            self.assertEqual(list(cfg['channels']), ['bcs'])
            self.assertNotIn('env', cfg)
            self.assertNotIn('load', cfg['plugins'])
            self.assertEqual(cfg['agents']['list'][0]['id'], 'main')
            self.assertEqual(cfg['agents']['defaults']['model']['primary'], 'demo/test')
            self.assertTrue((folder / 'stopped').exists())
            self.assertIn('## Code example', (folder / 'workspace/SOUL.md').read_text())
            self.assertEqual((folder / '.bcs/session.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((folder / 'openclaw.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(ports), [self.base_port, self.base_port + 20])
        self.assertEqual(len(set(uuids)), 2)
        for auth, payload in self.onboards:
            self.assertTrue(auth.startswith('Bearer reconnected-bot-'))
            self.assertEqual(payload['summary'], 'Reviews APIs safely.')
            self.assertEqual(payload['skills'], [])
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        for call in calls:
            self.assertEqual(call['state'], call['bot_data'])
            self.assertEqual(call['ignore_credentials'], '1')
            self.assertFalse(call['has_registration_proof'])
            self.assertEqual(call['pi_agent_dir'], call['state'] + '/agents/main/agent')
            self.assertEqual(call['openclaw_agent_dir'], call['state'] + '/agents/main/agent')
            self.assertNotIn('restart', call['args'])
        for secret in ['test-token', 'bot-token-', 'must-not-print-probe-token']:
            self.assertNotIn(secret, output)
        self.env.pop('BCS_REGISTER_TOKEN')
        proc = self.launch(profiles=['design/reviewer.md', 'engineering/backend.md'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 2, 'rerun must reuse identities')

    def test_bad_profile_preflight_has_no_registration_or_cli_side_effect(self):
        result = subprocess.run(self.command(['engineering/backend.md', '../outside.md']),
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_plugin_failure_does_not_register_or_start_gateway(self):
        self.env['FAKE_FAIL_INSTALL'] = '1'
        result = subprocess.run(self.command(), env=self.env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.registrations, [])
        self.assertNotIn('simulated secret', result.stdout + result.stderr)
        logs = list(self.state.glob('*/openclaw-plugins-install.log'))
        self.assertTrue(logs, 'installation diagnostics must not be discarded')
        self.assertTrue(any(str(log.resolve()) in result.stderr for log in logs))
        for log in logs:
            self.assertIn('simulated secret', log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_config_conflict_recovers_installed_plugin_and_restart_is_clean(self):
        self.env['FAKE_CONFIG_CAS'] = '1'
        proc = self.launch(['engineering/backend'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.registrations), 1)
        self.assertEqual(len(list(self.state.glob('*/enabled'))), 1)
        logs = list(self.state.glob('*/openclaw-plugins-install.log'))
        self.assertEqual(len(logs), 1)
        self.assertIn('config changed since last load', logs[0].read_text())
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        installs = [call for call in calls if call['args'][:2] == ['plugins', 'install']]
        self.assertEqual(len(installs), 1, 'must not blindly retry/reinstall a present plugin')
        self.assertTrue(all('--force' not in call['args'] and
                            '--dangerously-force-unsafe-install' not in call['args'] for call in calls))
        self.assertEqual(len(self.registrations), 1)

    def test_config_conflict_without_installed_plugin_still_fails(self):
        self.env['FAKE_CONFIG_CAS'] = '1'
        self.env['FAKE_CONFIG_CAS_MISSING'] = '1'
        self.failed_run(['engineering/backend'])
        self.assertEqual(self.registrations, [])
        self.assertEqual(list(self.state.glob('*/enabled')), [])

    def test_config_conflict_with_failed_enable_still_fails(self):
        self.env['FAKE_CONFIG_CAS'] = '1'
        self.env['FAKE_FAIL_ENABLE'] = '1'
        self.failed_run(['engineering/backend'])
        self.assertEqual(self.registrations, [])
        self.assertEqual(len(list(self.state.glob('*/openclaw-plugins-enable.log'))), 1)

    def test_ambiguous_registration_is_not_retried_even_across_runs(self):
        self.http_status = 500
        for _ in range(2):
            result = subprocess.run(self.command(['engineering/backend']), env=self.env,
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('test-token', result.stdout + result.stderr)
        self.assertEqual(len(self.registrations), 1)

    def test_unconfirmed_capabilities_do_not_stop_authenticated_gateways(self):
        self.onboard_results_by_name['Backend Architect'] = [
            {'bot_uuid': 'bot-1', 'onboarded': False, 'message': 'not visible yet'},
            {'bot_uuid': 'bot-1', 'onboarded': False, 'message': 'not visible yet'},
            {'bot_uuid': 'bot-1', 'onboarded': False, 'message': 'not visible yet'},
        ]
        proc = self.launch()
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn('capability metadata pending', output)
        self.assertEqual(len(self.registrations), 2)
        self.assertEqual(len(self.onboards), 4, 'backend retries three times, frontend succeeds')
        pending = list(self.state.glob('backend-*/bcs-onboard-last-response.json'))
        self.assertEqual(len(pending), 1)
        detail = json.loads(pending[0].read_text())
        self.assertEqual(detail['response']['onboarded'], False)
        self.assertEqual(pending[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(self.state.glob('*/stopped'))), 2)

        proc = self.launch()
        second = self.wait_ready(proc) + self.stop_process(proc)
        self.assertNotIn('capability metadata pending', second)
        self.assertEqual(list(self.state.glob('*/bcs-onboard-last-response.json')), [])
        self.assertEqual(len(self.registrations), 2)

    def test_capability_http_failure_is_nonfatal_and_private(self):
        self.onboard_http_status = 503
        proc = self.launch(['engineering/backend'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn('capability metadata pending', output)
        pending = list(self.state.glob('*/bcs-onboard-last-response.json'))
        self.assertEqual(len(pending), 1)
        detail = json.loads(pending[0].read_text())
        self.assertEqual(detail['error'], 'BCS request failed (HTTP 503)')
        self.assertNotIn('bot-token-', output)

    def test_offline_probe_is_failure_and_children_are_stopped(self):
        self.env['FAKE_OFFLINE'] = '1'
        proc = self.launch(['engineering/backend'])
        output, _ = proc.communicate(timeout=12)
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertNotIn(b'ALL CONNECTED', output)
        self.assertEqual(len(list(self.state.glob('*/stopped'))), 1)

    def test_malformed_profiles_fail_before_side_effects(self):
        (self.repo / 'engineering/backend.md').write_text('---\nname: [bad\n---\nrole')
        self.failed_run(['engineering/backend'])
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.registrations, [])

    def test_symlink_escape_is_rejected(self):
        outside = self.root / 'outside.md'
        outside.write_text((self.repo / 'engineering/backend.md').read_text())
        (self.repo / 'engineering/escape.md').symlink_to(outside)
        self.failed_run(['engineering/escape.md'])
        self.assertFalse(self.calls.exists())

    def test_missing_model_is_rejected_before_registration(self):
        self.config.write_text('{}')
        self.failed_run(['engineering/backend'])
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_changed_profile_does_not_overwrite_or_register(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text() + '\nChanged role.\n')
        previous_calls = self.calls.read_text()
        self.failed_run(['engineering/backend'])
        self.assertEqual(self.calls.read_text(), previous_calls)
        self.assertEqual(len(self.registrations), 1)
        snapshot = next(self.state.glob('*/profile.md'))
        self.assertNotIn('Changed role.', snapshot.read_text())

    NEW_ENDPOINT_MESSAGE = 'BCS endpoint has changed and cannot continue without overwriting'

    def move_saved_endpoint(self, pattern, fake_url='ws://old-network.example/ws/bot'):
        record_path = next(self.state.glob(pattern + '/instance.json'))
        record = json.loads(record_path.read_text())
        record['bcs_url'] = fake_url
        record_path.write_text(json.dumps(record))
        return record_path

    def test_changed_endpoint_decline_fails_without_any_side_effect(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.move_saved_endpoint('backend-*')
        session_path = next(self.state.glob('backend-*/.bcs/session.json'))
        session_before = session_path.read_bytes()
        previous_calls = self.calls.read_bytes()
        files_before = {path: path.relative_to(self.state).as_posix() for path in self.state.rglob('*')}
        contents_before = {relative: (self.state / relative).read_bytes()
                           for relative in files_before.values()
                           if (self.state / relative).is_file()}

        proc, master = self.launch_interactive(['engineering/backend'], ['n'])
        output = self.read_interactive_until(proc, master, marker=self.NEW_ENDPOINT_MESSAGE.encode(),
                                             timeout=6)
        proc.wait(timeout=5)
        self.stop_interactive(proc, master)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('Overwrite the saved endpoint configuration', output)
        self.assertIn(self.NEW_ENDPOINT_MESSAGE, output)
        self.assertEqual(session_path.read_bytes(), session_before)
        self.assertEqual(self.calls.read_bytes(), previous_calls)
        self.assertEqual(len(self.registrations), 1)
        self.assertEqual(list(self.state.glob('backend-*/.bcs/session.previous.*.json')), [])
        files_after = {path.relative_to(self.state).as_posix(): path
                       for path in self.state.rglob('*') if path.is_file()}
        self.assertEqual(set(contents_before), set(files_after))
        for relative, path in files_after.items():
            self.assertEqual(path.read_bytes(), contents_before[relative],
                             f'decline must not write {relative}')

    def test_changed_endpoint_yes_reregisters_only_affected_instances(self):
        proc = self.launch()
        self.wait_ready(proc)
        self.stop_process(proc)
        self.move_saved_endpoint('backend-*')
        reviewer_session_path = next(self.state.glob('reviewer-*/.bcs/session.json'))
        reviewer_session = json.loads(reviewer_session_path.read_text())

        proc, master = self.launch_interactive(None, ['y', 'n'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn('Overwrite the saved endpoint configuration', output)
        self.assertEqual(len(self.registrations), 3, 'only the endpoint-mismatched instance re-registers')
        changed_backups = list(self.state.glob('backend-*/.bcs/session.previous.*.json'))
        self.assertEqual(len(changed_backups), 1)
        self.assertEqual(json.loads(changed_backups[0].read_text())['bot_uuid'], 'bot-1')
        self.assertEqual(json.loads(next(self.state.glob('backend-*/.bcs/session.json')).read_text())['bot_uuid'], 'bot-3')
        self.assertEqual(json.loads(reviewer_session_path.read_text()), reviewer_session,
                         'an unchanged session must not be touched')
        self.assertNotIn('bot-token-', output)
        self.assertEqual(len(list(self.state.glob('*/stopped'))), 2)

    def test_changed_endpoint_overwrite_flag_works_noninteractively(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.move_saved_endpoint('backend-*')
        result = self.failed_run(['engineering/backend'])
        self.assertIn('--overwrite-endpoint', result.stderr)
        self.assertEqual(len(self.registrations), 1)

        proc = self.launch(['engineering/backend'], ['--overwrite-endpoint'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 2)
        session = json.loads(next(self.state.glob('backend-*/.bcs/session.json')).read_text())
        self.assertEqual(session['bot_uuid'], 'bot-2')
        self.assertTrue(session['bcs_url'].startswith('ws://127.0.0.1'))
        self.assertEqual(list(self.state.glob('backend-*/.bcs/session.previous.*.json')) == [], False)

    def test_endpoint_overwrite_requires_registration_token(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.move_saved_endpoint('backend-*')
        self.env.pop('BCS_REGISTER_TOKEN')
        session_path = next(self.state.glob('backend-*/.bcs/session.json'))
        session_before = session_path.read_bytes()
        result = self.failed_run(['engineering/backend'], ['--overwrite-endpoint'])
        self.assertIn('token', result.stderr.lower())
        self.assertEqual(session_path.read_bytes(), session_before)

    def test_concurrent_launcher_is_refused_without_disturbing_first(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        previous_calls = self.calls.read_text()
        self.failed_run(['engineering/backend'])
        self.assertIsNone(proc.poll())
        self.assertEqual(self.calls.read_text(), previous_calls)
        self.assertEqual(len(self.registrations), 1)
        self.stop_process(proc)

    def test_registration_auth_refusal_can_be_retried(self):
        self.http_status = 401
        self.failed_run(['engineering/backend'])
        self.assertEqual(list(self.state.glob('*/registration.pending.json')), [])
        self.http_status = 201
        self.registration_statuses = []
        self.env.pop('BCS_REGISTER_TOKEN')
        token = self.root / 'register-token'
        token.write_text('token-file-secret\n')
        proc = self.launch(['engineering/backend'], ['--token-file', str(token)])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(self.registrations[-1]['token'], ['token-file-secret'])
        self.assertNotIn('token-file-secret', output)

    def test_token_argument_overrides_environment_without_leaking(self):
        proc = self.launch(['engineering/backend'], ['--token', 'argument-sensitive'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(self.registrations[0]['token'], ['argument-sensitive'])
        self.assertNotIn('argument-sensitive', output)
        self.assertNotIn('test-token', self.calls.read_text())

    def test_defaults_clone_once_and_read_default_openclaw_model(self):
        home = self.setup_defaults()
        default_config = home / '.openclaw/openclaw.json'
        original = default_config.read_bytes()
        omit = ('--agency-dir', '--model-config', '--state-dir')
        proc = self.launch(['engineering/backend'], ['--token', 'argument-sensitive'], omit)
        self.wait_ready(proc)
        self.stop_process(proc)
        self.state_root = home / '.avernet/bcs/agency-agent'
        self.state = self.state_root / 'openclaw'
        self.assertTrue((self.state_root / 'agency-agents/engineering/backend.md').is_file())
        config = json.loads(next(self.state.glob('backend-*/openclaw.json')).read_text())
        self.assertEqual(config['agents']['defaults']['model']['primary'], 'demo/test')
        self.assertEqual(list(config['channels']), ['bcs'])
        self.assertEqual(default_config.read_bytes(), original)
        self.env.pop('BCS_REGISTER_TOKEN')
        proc = self.launch(['engineering/backend'], omit=omit)
        self.wait_ready(proc)
        self.stop_process(proc)
        calls = [json.loads(line) for line in self.git_calls.read_text().splitlines()]
        clones = [call for call in calls if call['args'][0] == 'clone']
        self.assertEqual(len(clones), 1)
        self.assertIn('https://github.com/msitarzewski/agency-agents.git', clones[0]['args'])
        self.assertTrue(all(not call['has_registration_proof'] for call in calls))
        self.assertFalse(any('pull' in call['args'] or 'fetch' in call['args'] for call in calls))
        self.assertEqual(len(self.registrations), 1)

    def test_clone_failure_cleans_partial_checkout_and_can_retry(self):
        self.setup_defaults()
        self.env['FAKE_CLONE_FAIL'] = '1'
        command = self.command(['engineering/backend'], omit=('--agency-dir',))
        result = subprocess.run(command, env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('clone', result.stderr.lower())
        self.assertFalse((self.state_root / 'agency-agents').exists())
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())
        self.assertNotIn('unsafe-server-output', result.stdout + result.stderr)
        self.env.pop('FAKE_CLONE_FAIL')
        proc = self.launch(['engineering/backend'], omit=('--agency-dir',))
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertTrue((self.state_root / 'agency-agents/.git').is_dir())

    def test_missing_default_model_fails_before_clone_or_registration(self):
        home = self.setup_defaults()
        (home / '.openclaw/openclaw.json').unlink()
        command = self.command(['engineering/backend'], omit=('--agency-dir', '--model-config'))
        result = subprocess.run(command, env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('model', result.stderr.lower())
        self.assertFalse(self.git_calls.exists())
        self.assertEqual(self.registrations, [])

    def test_explicit_agency_dir_never_runs_git(self):
        home = self.setup_defaults()
        (home / '.openclaw/openclaw.json').write_text('invalid default ignored by explicit --model-config')
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertFalse(self.git_calls.exists())

    def test_existing_non_repository_cache_is_not_overwritten(self):
        self.setup_defaults()
        cache = self.state_root / 'agency-agents'
        cache.mkdir(parents=True)
        (cache / 'keep.txt').write_text('existing user data')
        result = subprocess.run(self.command(['engineering/backend'], omit=('--agency-dir',)),
                                env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((cache / 'keep.txt').read_text(), 'existing user data')
        calls = [json.loads(line) for line in self.git_calls.read_text().splitlines()]
        self.assertFalse(any(call['args'][0] == 'clone' for call in calls))
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_token_and_token_file_are_mutually_exclusive(self):
        result = subprocess.run(self.command(['engineering/backend'], ['--token', 'argument-sensitive',
                                    '--token-file', str(self.root / 'unused')]),
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('argument-sensitive', result.stderr)
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_open_websocket_without_authenticated_session_is_not_ready(self):
        self.env['FAKE_UNAUTHENTICATED'] = '1'
        proc = self.launch(['engineering/backend'])
        output = b''
        try:
            output, _ = proc.communicate(timeout=7)
        except subprocess.TimeoutExpired:
            output = self.stop_process(proc).encode()
        self.assertNotIn(b'ALL CONNECTED', output,
                         'an open WebSocket does not mean bot.connect authenticated')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.onboards, [])

    def test_one_gateway_failure_stops_the_other(self):
        self.env['FAKE_EXIT_GATEWAY'] = 'reviewer'
        self.env['FAKE_FAIL_GATEWAY_AFTER_PEER'] = 'backend-*'
        proc = self.launch()
        output, _ = proc.communicate(timeout=12)
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertNotIn(b'ALL CONNECTED', output)
        self.assertEqual(len(list(self.state.glob('backend-*/stopped'))), 1)

    def test_partial_batch_reregistration_keeps_all_old_sessions(self):
        proc = self.launch()
        self.wait_ready(proc)
        self.stop_process(proc)
        session_paths = sorted(self.state.glob('*/.bcs/session.json'))
        old_sessions = {path: path.read_bytes() for path in session_paths}
        registrations_before = len(self.registrations)
        self.registration_statuses = [201, 503]

        result = subprocess.run(self.command(extra=['--reregister']), env=self.env,
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.registrations), registrations_before + 2)
        self.assertEqual({path: path.read_bytes() for path in session_paths}, old_sessions)
        self.assertEqual(list(self.state.glob('*/.bcs/session.previous.*.json')), [])
        pending = list(self.state.glob('*/bcs-reregistration.pending.json'))
        self.assertEqual(len(pending), 2)
        details = [json.loads(path.read_text()) for path in pending]
        self.assertEqual(sum('new_session' in detail for detail in details), 1)
        self.assertNotIn('bot-token-', result.stdout + result.stderr)

    def test_legacy_bare_registration_response(self):
        self.http_status = 200
        self.bare_response = True
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 1)


if __name__ == '__main__':
    unittest.main()
