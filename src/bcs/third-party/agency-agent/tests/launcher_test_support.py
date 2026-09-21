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


class LauncherFixture(unittest.TestCase):
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
        self.state_root = self.root / 'instances'
        self.state = self.state_root / 'openclaw'
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
        self.registration_install_counts = []
        self.onboards = []
        self.http_status = 201
        self.registration_statuses = []
        self.bare_response = False
        self.onboard_results = []
        self.onboard_results_by_name = {}
        self.onboard_http_status = 200
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                route = urlsplit(self.path)
                # Drain every POST body before closing an HTTP/1.0 connection;
                # leaving /register's JSON unread can reset TCP on macOS.
                request_body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                if route.path == '/register':
                    parent.registration_install_counts.append(len(list(parent.state.glob('*/installed'))))
                    parent.registrations.append(parse_qs(route.query))
                    number = len(parent.registrations)
                    result = {'bot_uuid': f'bot-{number}', 'bot_token': f'bot-token-{number}'}
                    body = result if parent.bare_response else {'code': 'ok', 'data': result}
                    code = (parent.registration_statuses.pop(0)
                            if parent.registration_statuses else parent.http_status)
                elif route.path == '/bots/onboard':
                    payload = json.loads(request_body)
                    parent.onboards.append((self.headers.get('Authorization'), payload))
                    responses = parent.onboard_results_by_name.get(payload['name'], parent.onboard_results)
                    body = (responses.pop(0) if responses
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
                   '--state-dir', str(self.state_root), '--bcs-endpoint', self.endpoint,
                   '--base-port', str(self.base_port), '--startup-timeout', '4']
        for option in omit:
            index = command.index(option)
            del command[index:index + 2]
        for profile in (['engineering/backend', 'design/reviewer'] if profiles is None else profiles):
            command.extend(['--profile', profile])
        return command + list(extra)

    def launch(self, profiles=None, extra=(), omit=()):
        proc = subprocess.Popen(self.command(profiles, extra, omit), env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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

    def failed_run(self, profiles=None, extra=()):
        result = subprocess.run(self.command(profiles, extra), env=self.env,
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('ALL CONNECTED', result.stdout)
        return result

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
