"""Run with python -m unittest discover -s <this directory> -v."""
import json
import os
import pty
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parents[1]


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'agency-agents'
        (self.repo / 'engineering').mkdir(parents=True)
        (self.repo / 'design').mkdir()
        for path, name in [('engineering/backend.md', 'Backend Architect'),
                           ('design/reviewer.md', '代码审查员')]:
            (self.repo / path).write_text(
                f'---\nname: {name}\ndescription: >-\n  Reviews APIs safely.\nemoji: 🏗️\n---\n'
                '# Role\n\nKeep this complete.\n```md\n## Code example\n```\n', encoding='utf-8')
        self.config = self.root / 'models.json'
        self.config.write_text(json.dumps({
            'models': {'providers': {'demo': {'baseUrl': 'https://models.example/v1',
                'apiKey': '${DEMO_API_KEY}', 'models': [{'id': 'test'}]}}},
            'agents': {'defaults': {'model': {'primary': 'demo/test'}, 'workspace': '/never-use'},
                       'list': [{'id': 'unrelated'}]},
            'channels': {'telegram': {'enabled': True}},
            'plugins': {'load': {'paths': ['/never-load']}},
            'env': {'UNWANTED': 'do-not-inherit'}}))
        self.state = self.root / 'instances'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        shutil.copy(SCRIPT_DIR / 'tests/fake_openclaw.py', self.bin / 'openclaw')
        (self.bin / 'openclaw').chmod(0o755)
        self.calls = self.root / 'calls.jsonl'
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        FAKE_CALLS=str(self.calls), BCS_REGISTER_TOKEN='test-token',
                        OPENCLAW_STATE_DIR='/must-not-use', OPENCLAW_CONFIG_PATH='/must-not-use',
                        BCS_BOT_ID='inherited-wrong-id', BOT_TYPE='service',
                        PI_CODING_AGENT_DIR='/must-not-use-auth',
                        DEMO_API_KEY='test-only')
        self.registrations = []
        self.onboards = []
        self.http_status = 201
        self.registration_statuses = []
        self.bare_response = False
        self.onboard_results = []
        self.onboard_http_status = 200
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                route = urlsplit(self.path)
                if route.path == '/register':
                    parent.registrations.append(parse_qs(route.query))
                    number = len(parent.registrations)
                    result = {'bot_uuid': f'bot-{number}', 'bot_token': f'bot-token-{number}'}
                    body = result if parent.bare_response else {'code': 'ok', 'data': result}
                    code = (parent.registration_statuses.pop(0)
                            if parent.registration_statuses else parent.http_status)
                elif route.path == '/bots/onboard':
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    parent.onboards.append((self.headers.get('Authorization'), payload))
                    body = (parent.onboard_results.pop(0) if parent.onboard_results
                            else {'bot_uuid': self.headers.get('Authorization', '').removeprefix('Bearer reconnected-'),
                                  'onboarded': True})
                    code = parent.onboard_http_status
                else:
                    body, code = {}, 404
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}'
        self.base_port = self.free_port_block()

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def free_port_block(self):
        for port in range(28000, 60000, 40):
            sockets = []
            try:
                for candidate in (port, port + 20):
                    sock = socket.socket()
                    sockets.append(sock)
                    sock.bind(('127.0.0.1', candidate))
                return port
            except OSError:
                pass
            finally:
                for sock in sockets:
                    sock.close()
        self.fail('no test ports available')

    def command(self, profiles=None, extra=(), omit=()):
        self.assertTrue((SCRIPT_DIR / 'agency_launcher.py').is_file(),
                        'multi-profile launcher has not been implemented')
        command = [sys.executable, str(SCRIPT_DIR / 'agency_launcher.py'),
                   '--agency-dir', str(self.repo), '--model-config', str(self.config),
                   '--state-dir', str(self.state), '--bcs-endpoint', self.endpoint,
                   '--base-port', str(self.base_port), '--startup-timeout', '4']
        for option in omit:
            index = command.index(option)
            del command[index:index + 2]
        for profile in profiles or ['engineering/backend.md', 'design/reviewer.md']:
            command.extend(['--profile', profile])
        return command + list(extra)

    def launch(self, profiles=None, extra=(), omit=()):
        proc = subprocess.Popen(self.command(profiles, extra, omit), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop_process, proc)
        return proc

    def stop_process(self, proc):
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
        try:
            output, _ = proc.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate(timeout=5)
            self.fail('launcher did not stop owned children')
        return output.decode()

    def wait_ready(self, proc):
        output = b''
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 8192)
                output += chunk
                if b'ALL CONNECTED' in output:
                    return output.decode()
                if not chunk:
                    break
        self.fail('launcher did not connect: ' + output.decode())

    def launch_interactive(self, profiles, answers, extra=()):
        master, slave = pty.openpty()
        proc = subprocess.Popen(self.command(profiles, extra), env=self.env,
                                stdin=slave, stdout=slave, stderr=slave,
                                close_fds=True)
        os.close(slave)
        self.addCleanup(self.stop_interactive, proc, master)
        os.write(master, ''.join(f'{answer}\n' for answer in answers).encode())
        return proc, master

    def stop_interactive(self, proc, master):
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        try:
            os.close(master)
        except OSError:
            pass

    def read_interactive_until(self, proc, master, marker=b'ALL CONNECTED', timeout=15):
        output = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    output += os.read(master, 8192)
                except OSError:
                    break
                if marker in output:
                    return output.decode(errors='replace')
            if proc.poll() is not None:
                break
        self.fail('interactive launcher did not reach marker: ' + output.decode(errors='replace'))

    def test_profile_decline_continues_to_global_bcs_reuse_prompt(self):
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)
        source = self.repo / 'engineering/backend.md'
        original_workspace = next(self.state.glob('backend-*/workspace/SOUL.md')).read_text()
        source.write_text(source.read_text() + '\nChanged source role.\n')

        proc, master = self.launch_interactive(['backend'], ['n', 'n'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(output.lower().count('re-register all existing bcs sessions'), 1)
        self.assertEqual(len(self.registrations), registrations_before)
        self.assertEqual(next(self.state.glob('backend-*/workspace/SOUL.md')).read_text(), original_workspace)
        self.assertNotIn('Changed source role.', next(self.state.glob('backend-*/profile.md')).read_text())

    def test_unchanged_profile_still_gets_one_global_bcs_question(self):
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)

        proc, master = self.launch_interactive(['backend'], ['n'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertNotIn('Source profile changed', output)
        self.assertEqual(output.lower().count('re-register all existing bcs sessions'), 1)
        self.assertEqual(len(self.registrations), registrations_before)

    def test_declined_profile_with_global_reregister_uses_saved_profile(self):
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        registrations_before = len(self.registrations)
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text().replace(
            'name: Backend Architect', 'name: New Backend Architect') + '\nChanged source role.\n')
        workspace = next(self.state.glob('backend-*/workspace/SOUL.md'))
        original_workspace = workspace.read_text()

        proc, master = self.launch_interactive(['backend'], ['n', 'y'])
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

        proc, master = self.launch_interactive(['backend', 'reviewer'], ['y', 'n', 'n'])
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

        proc, master = self.launch_interactive(['backend', 'reviewer'], ['y', 'y', 'y'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.registrations), registrations_before + 2)
        self.assertEqual(len(list(self.state.glob('*/.bcs/session.previous.*.json'))), 2)
        self.assertEqual(len(list(self.state.glob('*/bcs-reregistration.pending.json'))), 0)
        self.assertIn('Changed source role.', next(self.state.glob('backend-*/workspace/SOUL.md')).read_text())
        self.assertIn('Changed source role.', next(self.state.glob('reviewer-*/workspace/SOUL.md')).read_text())

    def test_noninteractive_overwrite_requires_yes_and_reregister_requires_explicit_flag(self):
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text() + '\nChanged source role.\n')
        result = self.failed_run(['backend'])
        self.assertIn('--yes', result.stderr)
        self.assertEqual(len(self.registrations), 1)
        proc = self.launch(['backend'], ['--yes'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn('Changed source role.', next(self.state.glob('backend-*/workspace/SOUL.md')).read_text())
        self.assertEqual(len(self.registrations), 1)

        source.write_text(source.read_text() + '\nChanged again.\n')
        proc = self.launch(['backend'], ['--yes', '--reregister-bcs'])
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
        self.assertEqual(len(logs), 1, 'installation diagnostics must not be discarded')
        self.assertIn(str(logs[0].resolve()), result.stderr)
        self.assertIn('simulated secret', logs[0].read_text())
        self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)

    def test_config_conflict_recovers_installed_plugin_and_restart_is_clean(self):
        self.env['FAKE_CONFIG_CAS'] = '1'
        proc = self.launch(['backend'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.registrations), 1)
        self.assertEqual(len(list(self.state.glob('*/enabled'))), 1)
        logs = list(self.state.glob('*/openclaw-plugins-install.log'))
        self.assertEqual(len(logs), 1)
        self.assertIn('config changed since last load', logs[0].read_text())
        proc = self.launch(['backend'])
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
        self.failed_run(['backend'])
        self.assertEqual(self.registrations, [])
        self.assertEqual(list(self.state.glob('*/enabled')), [])

    def test_config_conflict_with_failed_enable_still_fails(self):
        self.env['FAKE_CONFIG_CAS'] = '1'
        self.env['FAKE_FAIL_ENABLE'] = '1'
        self.failed_run(['backend'])
        self.assertEqual(self.registrations, [])
        self.assertEqual(len(list(self.state.glob('*/openclaw-plugins-enable.log'))), 1)

    def test_ambiguous_registration_is_not_retried_even_across_runs(self):
        self.http_status = 500
        for _ in range(2):
            result = subprocess.run(self.command(['backend']), env=self.env,
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('test-token', result.stdout + result.stderr)
        self.assertEqual(len(self.registrations), 1)

    def test_unconfirmed_capabilities_do_not_stop_authenticated_gateways(self):
        self.onboard_results = [
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
        proc = self.launch(['backend'])
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
        proc = self.launch(['backend'])
        output, _ = proc.communicate(timeout=12)
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertNotIn(b'ALL CONNECTED', output)
        self.assertEqual(len(list(self.state.glob('*/stopped'))), 1)

    def failed_run(self, profiles=None, extra=()):
        result = subprocess.run(self.command(profiles, extra), env=self.env,
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('ALL CONNECTED', result.stdout)
        return result

    def test_duplicate_and_malformed_profiles_fail_before_side_effects(self):
        self.failed_run(['backend', 'engineering/backend.md'])
        (self.repo / 'engineering/backend.md').write_text('---\nname: [bad\n---\nrole')
        self.failed_run(['backend'])
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
        self.failed_run(['backend'])
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_changed_profile_does_not_overwrite_or_register(self):
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text() + '\nChanged role.\n')
        previous_calls = self.calls.read_text()
        self.failed_run(['backend'])
        self.assertEqual(self.calls.read_text(), previous_calls)
        self.assertEqual(len(self.registrations), 1)
        snapshot = next(self.state.glob('*/profile.md'))
        self.assertNotIn('Changed role.', snapshot.read_text())

    def test_changed_endpoint_does_not_reuse_credentials(self):
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        previous_calls = self.calls.read_text()
        self.failed_run(['backend'], ['--bcs-endpoint', self.endpoint + '/another-network'])
        self.assertEqual(self.calls.read_text(), previous_calls)
        self.assertEqual(len(self.registrations), 1)

    def test_concurrent_launcher_is_refused_without_disturbing_first(self):
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        previous_calls = self.calls.read_text()
        self.failed_run(['backend'])
        self.assertIsNone(proc.poll())
        self.assertEqual(self.calls.read_text(), previous_calls)
        self.assertEqual(len(self.registrations), 1)
        self.stop_process(proc)

    def test_registration_auth_refusal_can_be_retried(self):
        self.http_status = 401
        self.failed_run(['backend'])
        self.assertEqual(list(self.state.glob('*/registration.pending.json')), [])
        self.http_status = 201
        self.registration_statuses = []
        self.env.pop('BCS_REGISTER_TOKEN')
        token = self.root / 'register-token'
        token.write_text('token-file-secret\n')
        proc = self.launch(['backend'], ['--token-file', str(token)])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(self.registrations[-1]['token'], ['token-file-secret'])
        self.assertNotIn('token-file-secret', output)

    def setup_defaults(self):
        home = self.root / 'home'
        (home / '.openclaw').mkdir(parents=True)
        shutil.copy(self.config, home / '.openclaw/openclaw.json')
        self.env['HOME'] = str(home)
        self.env['FAKE_AGENCY_SOURCE'] = str(self.repo)
        self.git_calls = self.root / 'git-calls.jsonl'
        self.env['FAKE_GIT_CALLS'] = str(self.git_calls)
        shutil.copy(SCRIPT_DIR / 'tests/fake_git.py', self.bin / 'git')
        (self.bin / 'git').chmod(0o755)
        return home

    def test_token_argument_overrides_environment_without_leaking(self):
        proc = self.launch(['backend'], ['--token', 'argument-sensitive'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(self.registrations[0]['token'], ['argument-sensitive'])
        self.assertNotIn('argument-sensitive', output)
        self.assertNotIn('test-token', self.calls.read_text())

    def test_defaults_clone_once_and_read_default_openclaw_model(self):
        home = self.setup_defaults()
        default_config = home / '.openclaw/openclaw.json'
        original = default_config.read_bytes()
        omit = ('--agency-dir', '--model-config', '--state-dir')
        proc = self.launch(['backend'], ['--token', 'argument-sensitive'], omit)
        self.wait_ready(proc)
        self.stop_process(proc)
        self.state = home / '.bcs/agency'
        self.assertTrue((self.state / 'agency-agents/engineering/backend.md').is_file())
        config = json.loads(next(self.state.glob('backend-*/openclaw.json')).read_text())
        self.assertEqual(config['agents']['defaults']['model']['primary'], 'demo/test')
        self.assertEqual(list(config['channels']), ['bcs'])
        self.assertEqual(default_config.read_bytes(), original)
        self.env.pop('BCS_REGISTER_TOKEN')
        proc = self.launch(['backend'], omit=omit)
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
        command = self.command(['backend'], omit=('--agency-dir',))
        result = subprocess.run(command, env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('clone', result.stderr.lower())
        self.assertFalse((self.state / 'agency-agents').exists())
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())
        self.assertNotIn('unsafe-server-output', result.stdout + result.stderr)
        self.env.pop('FAKE_CLONE_FAIL')
        proc = self.launch(['backend'], omit=('--agency-dir',))
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertTrue((self.state / 'agency-agents/.git').is_dir())

    def test_missing_default_model_fails_before_clone_or_registration(self):
        home = self.setup_defaults()
        (home / '.openclaw/openclaw.json').unlink()
        command = self.command(['backend'], omit=('--agency-dir', '--model-config'))
        result = subprocess.run(command, env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('model', result.stderr.lower())
        self.assertFalse(self.git_calls.exists())
        self.assertEqual(self.registrations, [])

    def test_explicit_agency_dir_never_runs_git(self):
        home = self.setup_defaults()
        (home / '.openclaw/openclaw.json').write_text('invalid default ignored by explicit --model-config')
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertFalse(self.git_calls.exists())

    def test_existing_non_repository_cache_is_not_overwritten(self):
        self.setup_defaults()
        cache = self.state / 'agency-agents'
        cache.mkdir(parents=True)
        (cache / 'keep.txt').write_text('existing user data')
        result = subprocess.run(self.command(['backend'], omit=('--agency-dir',)),
                                env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((cache / 'keep.txt').read_text(), 'existing user data')
        calls = [json.loads(line) for line in self.git_calls.read_text().splitlines()]
        self.assertFalse(any(call['args'][0] == 'clone' for call in calls))
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_token_and_token_file_are_mutually_exclusive(self):
        result = subprocess.run(self.command(['backend'], ['--token', 'argument-sensitive',
                                    '--token-file', str(self.root / 'unused')]),
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('argument-sensitive', result.stderr)
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_open_websocket_without_authenticated_session_is_not_ready(self):
        self.env['FAKE_UNAUTHENTICATED'] = '1'
        proc = self.launch(['backend'])
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

        result = subprocess.run(self.command(extra=['--reregister-bcs']), env=self.env,
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
        proc = self.launch(['backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 1)


if __name__ == '__main__':
    unittest.main()
