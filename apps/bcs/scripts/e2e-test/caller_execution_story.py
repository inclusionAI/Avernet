#!/usr/bin/env python3
"""Black-box channel -> real BCS queue -> HTTP provider caller contract.

Starts an isolated test-utils executable with its own SQLite store and ports.
No core calls, database mutation, production ingress or BaaS dependencies.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import queue
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        # Group setup may emit unrelated welcome/inject messages. Track only
        # this story's user sends, including any invalid sends that leak through.
        if 'caller-e2e-message' in json.dumps(body.get('message', {})):
            self.server.received.put((body, self.headers.get('Authorization')))
        if self.path == '/sse':
            payload = ('event: chat\ndata: ' + json.dumps({'runId': body['id'], 'seq': 1,
                'state': 'final', 'message': {'role': 'assistant',
                'content': [{'type': 'text', 'text': 'done'}]}}) + '\n\n').encode()
            content_type = 'text/event-stream'
        else:
            payload, content_type = b'{"ok":true}', 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class Story:
    def __init__(self, base, key, provider):
        self.base, self.key, self.provider = base, key, provider
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.checks = 0

    def request(self, method, path, body=None, status=200, headers=None):
        if body is None and method in ('POST', 'PUT', 'PATCH'):
            body = {}
        hdr = {'Content-Type': 'application/json', 'X-Mock-User-Id': 'callere2eowner'}
        hdr.update(headers or {})
        req = urllib.request.Request(self.base + path, method=method, headers=hdr,
            data=None if body is None else json.dumps(body).encode())
        try:
            result = self.http.open(req, timeout=20)
        except urllib.error.HTTPError as error:
            result = error
        with result:
            code, raw = result.code, result.read()
        expected = (status,) if isinstance(status, int) else status
        assert code in expected, f'{method} {path}: expected {status}, got {code}'
        self.checks += 1
        return json.loads(raw) if raw else {}

    def callback(self, account, sender='usera', credential='test-token', kind='1', status=200, **overrides):
        body = {'account': account, 'conversation': 'conv-' + account, 'conversation_type': kind,
            'sender': sender, 'message_id': uuid.uuid4().hex, 'text': 'caller-e2e-message', 'credential': credential}
        body.update(overrides)
        self.request('POST', '/channels/caller-e2e/callback', body, status,
            {'X-E2E-Ingress-Key': self.key})
        return body

    def binding(self, bot, mode='caller', status=200, target=None):
        account = uuid.uuid4().hex
        result = self.request('POST', '/channels/bindings', {
            'channel_type': 'caller-e2e', 'account_ref': account,
            'target': target or {'bot': {'bot_id': bot}}, 'outbound_visibility': 'full_transcript',
            'env': 'local', 'config': {'execution_mode': mode}}, status)
        return account, result

    def policy(self, paused):
        current = self.request('GET', '/admin/message-delivery/policy')
        policy = copy.deepcopy(current['policy'])
        policy['flow_enabled'].update(group=True, system=True)
        policy['defaults'].update(mode='enforce', min_send_interval_ms=1, max_running=8)
        policy['pause_dispatch'] = paused
        self.request('PUT', '/admin/message-delivery/policy',
            {'expected_version': current['version'], 'policy': policy})

    def receive(self):
        body, auth = self.provider.received.get(timeout=30)
        assert auth == 'Bearer ' + self.registration['bcs_to_provider_token'], 'provider auth changed'
        return body

    def finish_callback(self, body):
        # Receipt can precede the real transport acknowledgement being committed.
        for _ in range(100):
            result = self.request('POST', '/bot/events', {'run_id': body['id'], 'state': 'final',
                'message': {'text': 'done'}}, status=(200, 409), headers={
                'X-BCN-Provider-Id': self.registration['provider_id'],
                'Authorization': 'Bearer ' + self.bot['bot_runtime_token']})
            if result.get('error') != 'transport_conflict':
                assert not result.get('error'), 'final callback rejected'
                return
            time.sleep(0.05)
        raise AssertionError('callback transport never became ready')

    def run(self):
        self.request('POST', '/me/ensure-human')
        self.registration = self.request('POST', '/providers', {
            'name': 'Caller E2E', 'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0',
            'webhook_url': f'http://127.0.0.1:{self.provider.server_port}/callback'})
        bot = self.request('POST', '/providers/' + self.registration['provider_id'] + '/bots', {
            'name': 'Caller E2E Bot', 'owners': ['callere2eowner'], 'provider_bot_ref': 'caller-bot'},
            headers={'Authorization': 'Bearer ' + self.registration['provider_admin_token']})
        self.bot = bot
        bot_id = bot['bot_uuid']
        self.request('PUT', '/bots/' + bot_id + '/visibility', {'visibility': 'public'})
        account, binding = self.binding(bot_id)
        group = self.request('POST', '/groups', {'driver_bot': bot_id,
            'participants': [{'bot_uuid': bot_id}, {'bot_uuid': 'human_callere2eowner'}]})
        self.binding(bot_id, target={'group': {'group_id': group['id']}}, status=400)
        _, group_binding = self.binding(bot_id, 'default', target={'group': {'group_id': group['id']}})
        self.request('PATCH', '/channels/bindings/' + group_binding['id'],
            {'config': {'execution_mode': 'caller'}}, status=400)
        self.callback(account, credential=None, status=422)
        self.callback(account, credential='  ', status=422)
        self.callback(account, sender='', status=422)
        self.request('POST', '/channels/caller-e2e/callback', {}, status=401)
        assert self.provider.received.empty(), 'invalid caller reached provider'
        # Queue messages from distinct humans before transport is allowed.
        self.policy(True)
        self.callback(account, credential='test-token-a')
        self.callback(account, sender='userb', credential='test-token-b', kind='2')
        time.sleep(0.5)
        assert self.provider.received.empty(), 'queue pause ignored'
        # Disabling the binding cannot downgrade already accepted queued work.
        self.request('PATCH', '/channels/bindings/' + binding['id'], {'config': {'execution_mode': 'default'}})
        self.policy(False)
        seen = set()
        for _ in range(2):
            body = self.receive()
            ext = body['extensions']
            user = ext['caller_context']['subject']['ref']
            assert ext['caller_context']['subject']['kind'] == 'human'
            assert ext['caller_execution_token'] == {'type': 'iam-token', 'token': 'test-token-' + user[-1]}
            assert 'test-token-' not in json.dumps(body['message']), 'credential copied into message'
            seen.add(user)
            self.finish_callback(body)
        assert seen == {'usera', 'userb'}, 'caller contexts crossed'
        # Disable only new ingress; default messages must not inherit credentials.
        self.request('PATCH', '/channels/bindings/' + binding['id'], {'config': {'execution_mode': 'default'}})
        self.callback(account, credential='<test-token-default>')
        body = self.receive()
        assert 'caller_execution_token' not in body.get('extensions', {})
        self.finish_callback(body)
        self.request('PATCH', '/providers/' + self.registration['provider_id'],
            {'webhook_url': f'http://127.0.0.1:{self.provider.server_port}/sse'},
            headers={'Authorization': 'Bearer ' + self.registration['provider_admin_token']})
        self.request('PATCH', '/channels/bindings/' + binding['id'], {'config': {'execution_mode': 'caller'}})
        self.callback(account, credential='test-token-sse', conversation='sse-conversation')
        streamed = self.receive()
        self.streamed_run = streamed['id']
        assert streamed['extensions']['caller_execution_token'] == {'type': 'iam-token', 'token': 'test-token-sse'}
        # Queue another send, then the launcher restarts BCS. The real in-memory
        # cache is lost while the durable queue survives; it must fail closed.
        self.policy(True)
        self.callback(account, credential='test-token-lost', conversation='lost-conversation')
        print(f'Caller HTTP/queue/provider E2E: {self.checks} HTTP assertions passed', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    provider.received = queue.Queue()
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='bcs-caller-e2e-') as directory:
        root = Path(directory)
        port, key = free_port(), uuid.uuid4().hex
        config = json.loads(subprocess.check_output([binary, '--print-config']))
        config.update({'bind': '127.0.0.1', 'port': port, 'store_messages': True,
            'strict_container_validation': False, 'bcs_endpoint': f'http://127.0.0.1:{port}',
            'auth': {'chain': ['local'], 'allow_mock_headers': True, 'mock_user_id': 'callere2eowner'},
            'gateway_principal': {'signing_key_env': 'CALLER_E2E_SIGNING_KEY'},
            'channels': {'enabled': True, 'providers': {'caller-e2e': {'enabled': True, 'ingress_key': key}}},
            'database': {'type': 'sqlite', 'sqlite': {'path': str(root / 'bcs.db')}},
            'security': {'outbound_url': {'block_private_networks': True, 'allow_loopback': True}},
            'llm': {'type': 'none'}, 'bcsfuse': {'enabled': False}})
        config['logging'].update(default_level='debug', console=True)
        config['secret'] = {'provider': 'env', 'providers': {'env': {'prefix': 'BCS_SECRET_'}}}
        config['bots_base_dir'] = str(root / 'bots')
        config['session_files']['backend'] = {'data_dir': str(root / 'files')}
        config['collaboration']['templates']['base_dir'] = str(root / 'templates')
        config_path = root / 'bcs-config-local.json'
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, SERVER_ENV='local', CALLER_E2E_SIGNING_KEY=uuid.uuid4().hex,
                   BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex)
        # Keep instrumented LLVM_PROFILE_FILE from the runner for real server coverage.
        with (root / 'server.log').open('w') as log:
            process = subprocess.Popen([binary, '-c', str(config_path)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                story = Story(f'http://127.0.0.1:{port}', key, provider)
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError('test BCS exited during startup; ' + (root / 'server.log').read_text()[-2000:])
                    try:
                        story.request('GET', '/health')
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError('test BCS startup timed out')
                story.run()
                for _ in range(100):
                    with sqlite3.connect(f'file:{root / "bcs.db"}?mode=ro', uri=True) as db:
                        streamed_status = db.execute('SELECT status FROM bcs_message_deliveries WHERE run_id = ?', (story.streamed_run,)).fetchone()
                    if streamed_status == ('completed',):
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError('SSE final did not complete the queued run')
                with sqlite3.connect(f'file:{root / "bcs.db"}?mode=ro', uri=True) as db:
                    queued = db.execute("SELECT delivery_id FROM bcs_message_deliveries WHERE status = 'queued'").fetchall()
                assert len(queued) == 1, 'expected one paused caller delivery'
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=30)
                process = subprocess.Popen([binary, '-c', str(config_path)], cwd=root, env=env, stdout=log, stderr=log)
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError('test BCS exited during cache-loss restart')
                    try:
                        story.request('GET', '/health')
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError('cache-loss restart timed out')
                story.policy(False)
                for _ in range(150):
                    with sqlite3.connect(f'file:{root / "bcs.db"}?mode=ro', uri=True) as db:
                        status = db.execute('SELECT status FROM bcs_message_deliveries WHERE delivery_id = ?', queued[0]).fetchone()[0]
                    if status == 'failed':
                        break
                    time.sleep(0.2)
                else:
                    raise AssertionError('missing caller credential did not fail closed')
                assert provider.received.empty(), 'cache loss fell back to provider identity'
                print('Durable queue/cache-loss restart fails closed: PASS', flush=True)
            finally:
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=30)
                provider.shutdown()
            # Read-only inspection: no test writes to the queue or canonical stores.
            with sqlite3.connect(f'file:{root / "bcs.db"}?mode=ro', uri=True) as db:
                durable = '\n'.join(db.iterdump())
            assert 'test-token' not in durable, 'raw caller token persisted in SQLite'
            for log_path in root.rglob('*.log'):
                assert 'test-token' not in log_path.read_text(), 'raw caller token logged'
            print('Caller credential persistence and log redaction: PASS', flush=True)


if __name__ == '__main__':
    main()
