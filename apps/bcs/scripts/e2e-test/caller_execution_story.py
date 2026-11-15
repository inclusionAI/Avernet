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
import re
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
from urllib.parse import urlencode
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


TRACE_QUERY_MARKER = 'redact-this-value'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.traces.put((self.path, self.headers.get('X-E2E-Trace-Id'), self.headers.get('X-E2E-Span-Id')))
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
    def __init__(self, base, key, provider, environment='local'):
        self.base, self.key, self.provider = base, key, provider
        self.environment = environment
        self.delivered = []
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.checks = 0

    def request(self, method, path, body=None, status=200, headers=None):
        if body is None and method in ('POST', 'PUT', 'PATCH'):
            body = {}
        hdr = {'Content-Type': 'application/json', 'X-Mock-User-Id': 'callere2eowner',
               'X-E2E-Trace-Id': 'caller-network-e2e'}
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
            'env': self.environment, 'config': {'execution_mode': mode}}, status)
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
        self.delivered.append(body)
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
        trace_query = urlencode({
            'trace_secret': TRACE_QUERY_MARKER,
        })
        self.registration = self.request('POST', '/providers', {
            'name': 'Caller E2E', 'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0',
            'webhook_url': f'http://127.0.0.1:{self.provider.server_port}/callback?{trace_query}'})
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


def verify_network_traces(path, provider):
    raw = path.read_text()
    records = json.loads(raw)
    servers = [record for record in records if record['kind'] == 'http_server']
    clients = [record for record in records if record['kind'] == 'http_client']
    assert servers and clients, 'live server did not record both HTTP directions'
    assert all(record['trace_id'] == 'caller-network-e2e' for record in servers), 'incoming trace header lost'
    assert all(record['endpoint'] != '/health' for record in servers), 'health probe created a business trace'
    assert all('?' not in record['endpoint'] for record in records), 'query leaked into trace metadata'
    assert TRACE_QUERY_MARKER not in raw and 'test-token' not in raw, 'trace metadata contains credentials'
    assert any(record['outcome'] == 'Http(401)' for record in servers), 'business auth failure was not traced'
    assert any(record['outcome'] == 'Http(422)' for record in servers), 'business validation failure was not traced'
    indexed = {record['span_id']: record for record in clients}
    delivered = []
    while not provider.traces.empty():
        endpoint, trace_id, span_id = provider.traces.get_nowait()
        assert span_id and int(span_id) in indexed, 'Provider received no matching client trace'
        record = indexed[int(span_id)]
        assert record['trace_id'] == trace_id, 'client trace propagation changed on the wire'
        assert record['outcome'] == 'Http(200)', 'successful Provider call recorded an incorrect result'
        assert record['request_size'] > 0, 'Provider request size was not measured'
        assert record['response_size'] > 0, 'Provider response size was not measured'
        assert record['endpoint'] == f'http://127.0.0.1:{provider.server_port}' + endpoint.split('?')[0]
        delivered.append(endpoint)
    assert any(endpoint.startswith('/callback?') for endpoint in delivered), 'query sanitization was not exercised'
    assert '/sse' in delivered, 'streaming Provider delivery was not traced'
    print(f'Live network tracing: {len(servers)} server and {len(clients)} client records verified', flush=True)


def verify_caller_token_logs(root, story):
    diagnostic_lines = []
    for log_path in root.rglob('*.log'):
        for line in log_path.read_text().splitlines():
            line = re.sub(r'\x1b\[[0-9;]*m', '', line)
            if 'TEMP_PREPUB_CALLER_TOKEN' in line:
                diagnostic_lines.append(line)
            else:
                assert 'test-token' not in line, 'raw caller token escaped the diagnostic record'
    if story.environment == 'local':
        assert not diagnostic_lines, 'caller token diagnostic enabled outside preproduction'
        return
    expected = {
        body['id']: body['extensions']['caller_execution_token']['token']
        for body in story.delivered
        if 'caller_execution_token' in body.get('extensions', {})
    }
    observed = {}
    for line in diagnostic_lines:
        if line.startswith('{'):
            record = json.loads(line)
            run_id = record['run_id']
            value = json.loads(record['caller_token_json'])
        else:
            run = re.search(r'\brun_id=([^\s\x1b]+)', line)
            quoted_match = re.search(r'\bcaller_token_json=("(?:[^"\\]|\\.)*")', line)
            assert run and quoted_match, 'caller diagnostic missing correlation or JSON-quoted token'
            run_id = run[1]
            value = json.loads(quoted_match[1])
        # Console, text and JSON outputs may contain the same event.
        assert run_id not in observed or observed[run_id] == value, 'conflicting caller diagnostic values'
        observed[run_id] = value
    assert len(expected) == 3, 'expected two queued callers and one SSE caller'
    assert observed == expected, 'diagnostic tokens differ from the Provider wire values'
    print('Preproduction caller diagnostic matches actual Provider receipt: PASS', flush=True)


def verify_business_logs(root, traces, streamed_run):
    """Verify production formatters after the process drains its async writers."""
    records = {str(record['span_id']): record for record in json.loads(traces.read_text())}

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in ('e2e_trace_id', 'e2e_span_id'):
                assert key not in result, f'duplicate correlation field: {key}'
            result[key] = value
        return result

    rows = [json.loads(line, object_pairs_hook=unique_fields)
            for line in (root / 'business.json.log').read_text().splitlines()]
    text = (root / 'business.text.log').read_text().splitlines()
    correlated = [row for row in rows if 'e2e_trace_id' in row]
    assert correlated, 'selected business logs have no provider identity'
    for row in rows:
        assert ('e2e_trace_id' in row) == ('e2e_span_id' in row), 'partial identity pair'
        if 'e2e_span_id' not in row:
            continue
        assert row.get('log_class') == 'business_key', 'unselected event enriched'
        assert row['target'] != 'bcn_otel', 'OTel event enriched'
        record = records[row['e2e_span_id']]
        assert row['e2e_trace_id'] == record['trace_id'], 'log identity disagrees with network record'
        if row.get('event_code') == 'http.request.response_ready':
            assert record['kind'] == 'http_server'
            assert record['outcome'] == f'Http({row["status"]})', 'log status disagrees with response'
            assert isinstance(row['status'], int) and isinstance(row['duration_ms'], (int, float))
            suffix = f'e2e_trace_id={row["e2e_trace_id"]} e2e_span_id={row["e2e_span_id"]}'
            matching = [line for line in text if 'http.request.response_ready' in line
                        and f'request_id={row["request_id"]} ' in line]
            assert len(matching) == 1 and matching[0].endswith(suffix), 'text/JSON identities differ'
    responses = [row for row in correlated if row.get('event_code') == 'http.request.response_ready']
    assert {200, 401, 422} <= {row['status'] for row in responses}, 'success/auth/validation logs not covered'
    starts = [row for row in rows if row.get('message') == 'http.request.started']
    assert starts and all('e2e_trace_id' not in row for row in starts), 'ordinary request logs enriched'
    health = [row for row in rows if row.get('event_code') == 'http.request.response_ready'
              and row.get('route') == '/health']
    assert health and all('e2e_trace_id' not in row for row in health), 'excluded health scope enriched'
    closes = [row for row in rows if row.get('event_code') == 'sse.closed'
              and row.get('run_id') == streamed_run]
    assert len(closes) == 1, 'expected exactly one streaming close summary'
    assert closes[0]['reason'] == 'terminal' and closes[0]['saw_terminal'] is True
    assert closes[0]['frames'] == 1, 'diagnostics changed delivered SSE frames'
    print(f'Live business log correlation: {len(correlated)} selected events; text/JSON, excluded scopes and SSE verified', flush=True)


def run_story(binary, environment):
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    provider.received = queue.Queue()
    provider.traces = queue.Queue()
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
        # Both production formatters see the same real request events. Keep
        # outputs inside this process fixture; no formatter/core calls in tests.
        config['logging']['outputs'].extend([
            {'name': f'business-{fmt}', 'path': str(root), 'file': f'business.{fmt}.log',
             'level': 'debug', 'format': fmt, 'targets': ['*']}
            for fmt in ('text', 'json')
        ])
        config['secret'] = {'provider': 'env', 'providers': {'env': {'prefix': 'BCS_SECRET_'}}}
        config['bots_base_dir'] = str(root / 'bots')
        config['session_files']['backend'] = {'data_dir': str(root / 'files')}
        config['collaboration']['templates']['base_dir'] = str(root / 'templates')
        config_path = root / f'bcs-config-{environment}.json'
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, SERVER_ENV=environment, CALLER_E2E_SIGNING_KEY=uuid.uuid4().hex,
                   BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex)
        # Keep instrumented LLVM_PROFILE_FILE from the runner for real server coverage.
        with (root / 'server.log').open('w') as log:
            traces = root / 'network-traces.json'
            process = subprocess.Popen([binary, '-c', str(config_path), '--trace-output', str(traces)],
                cwd=root, env=env, stdout=log, stderr=log)
            try:
                story = Story(f'http://127.0.0.1:{port}', key, provider, environment)
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
                assert process.returncode == 0, 'traced BCS did not shut down cleanly'
                verify_network_traces(traces, provider)
                verify_business_logs(root, traces, story.streamed_run)
                traced_lines = len((root / 'business.json.log').read_text().splitlines())
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
            restarted = [json.loads(line) for line in
                         (root / 'business.json.log').read_text().splitlines()[traced_lines:]]
            assert any(row.get('log_class') == 'business_key' for row in restarted)
            assert all('e2e_trace_id' not in row and 'e2e_span_id' not in row for row in restarted), \
                'provider-free restart fabricated or retained a log identity'
            # Read-only inspection: no test writes to the queue or canonical stores.
            with sqlite3.connect(f'file:{root / "bcs.db"}?mode=ro', uri=True) as db:
                durable = '\n'.join(db.iterdump())
            assert 'test-token' not in durable, 'raw caller token persisted in SQLite'
            verify_caller_token_logs(root, story)
            print(f'Caller credential persistence and {environment} log policy: PASS', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--environment', choices=('local', 'pre'))
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    environments = (args.environment,) if args.environment else ('local', 'pre')
    for environment in environments:
        run_story(binary, environment)


if __name__ == '__main__':
    main()
