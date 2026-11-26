#!/usr/bin/env python3
"""Real signed HTTP -> production application/SQL -> loopback A2A peer.

Only eligibility/delegation/gate publications use the isolated trusted fixture
boundary. No business command, SQL mutation, or unit contract is called here.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
import uuid
from a2a_story import Peer
from group_session_ws_probe import _sign_jwt

PREFIXES = ('/api/v1/collaboration/agent-invoke', '/openapi/v1/collaboration/agent-invoke')
TENANT = 'agent-invoke-e2e'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Story:
    def __init__(self, base, feed, key, signing, db_path, peer):
        self.base, self.feed, self.key, self.signing = base, feed, key, signing
        self.db_path, self.peer = db_path, peer
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.checks, self.observed = 0, []
        self.facts = {}
        self.scenarios = []
        self.owner = 'invokee2eowner'
        self.source_owner = 'invokee2esource'

    def principal(self, kind='user', subject=None, tenant=TENANT):
        if kind == 'user':
            value = {'subject': {'id': subject or self.owner, 'username': subject or self.owner,
                                'tenant_id': tenant}}
        elif kind == 'bot':
            value = {'bot': {'bot_uuid': self.source, 'agent_code': subject or 'invoke-source-code',
                            'owner_id': self.owner, 'app_id': 7, 'tenant': tenant}}
        else:
            value = {'app': {'app_id': subject or 7, 'app_name': 'synthetic-inventory',
                            'owners': self.owner, 'tenant': tenant, 'app_type': 'THIRD_PARTY'}}
        claims = {'iss': 'gateway', 'aud': 'bcs', 'iat': int(time.time()), 'exp': int(time.time()) + 120,
                  'principals': [{'type': kind, 'tenant': tenant, **value}]}
        return _sign_jwt(header={'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}, claims=claims, signing_key=self.signing)

    def request(self, method, path, body=None, *, status=200, actor='user', subject=None,
                tenant=TENANT, token=None, key=None, base=None, extra=None):
        headers = {'Content-Type': 'application/json', 'X-Mock-User-Id': self.owner}
        if actor:
            headers['X-Avernet-Principal'] = self.principal(actor, subject, tenant)
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if key:
            headers['Idempotency-Key'] = key
        headers.update(extra or {})
        request = urllib.request.Request((base or self.base) + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            result = self.http.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            result = error
        with result:
            code, raw = result.code, result.read()
            cache = result.headers.get('cache-control')
        expected = (status,) if isinstance(status, int) else status
        # Never put response bodies or headers containing credentials in logs.
        assert code in expected, f'{method} {path}: expected {expected}, received {code}'
        self.checks += 1
        self.observed.append({'method': method, 'path': path.split('?')[0], 'status': code})
        value = json.loads(raw) if raw else {}
        if path.startswith(PREFIXES):
            assert cache == 'no-store' and value.get('request_id'), 'public envelope/cache boundary changed'
            assert value['code'] == code * 100
            return value['data']
        return value

    def publish(self, command, **fields):
        return self.request('POST', '/publish', {'command': command, **fields}, actor=None,
                            base=self.feed, extra={'X-Fixture-Key': self.key})

    def fact(self, bot, **changes):
        previous = self.facts.get(bot, {'binding_generation': 1, 'eligibility_generation': 1,
                                       'eligible': True, 'active': True, 'hidden': False, 'expected_revision': 0})
        value = {**previous, **changes}
        result = self.publish('fact', bot_id=bot, ttl_ms=240_000, **value)
        self.facts[bot] = {**value, 'expected_revision': result['revision']}
        return result

    def gates(self, writer, runtime):
        current = self.publish('inspect')
        return self.publish('gates', expected_revision=current['revision'], writer=writer, runtime=runtime)

    def query(self, suffix, bot=None, peer=None, direction='inbound', **params):
        values = {'bot_id': bot or self.target}
        if direction:
            values['direction'] = direction
        if peer:
            values['peer_bot_id'] = peer
        return suffix + '?' + urlencode({**values, **params})

    def mutation(self, prefix, suffix, body, *, key=None, **options):
        options.setdefault('status', 201 if suffix in ('/grants', '/requests') else 200)
        return self.request('POST', prefix + suffix, body, key=key or uuid.uuid4().hex, **options)

    def sql(self, sql, args=()):
        # Read-only observation of durable effects; no fixture writes here.
        with sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True) as db:
            return db.execute(sql, args).fetchall()

    def setup(self):
        self.request('POST', '/publish', {'command': 'inspect'}, base=self.feed, actor=None, status=401)
        self.request('POST', '/me/ensure-human', actor=None)
        provider = self.request('POST', '/providers', {'name': 'Agent invocation source',
            'webhook_url': self.peer.base + '/must-not-deliver', 'auth': {'mode': 'agentpass'},
            'protocol_version': '2.0'}, actor=None)
        admin = provider['provider_admin_token']
        source = self.request('POST', f"/providers/{provider['provider_id']}/bots", {
            'name': 'Invocation source', 'provider_bot_ref': 'invoke-source-code', 'owners': [self.source_owner]},
            token=admin, actor=None)
        self.source = source['bot_uuid']
        provider = self.request('POST', '/providers', {'name': 'Agent invocation target',
            'webhook_url': self.peer.base + '/legacy-must-not-deliver',
            'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0'}, actor=None)
        admin = provider['provider_admin_token']
        self.targets = []
        for index in range(5):
            bot = self.request('POST', f"/providers/{provider['provider_id']}/bots", {
                'name': f'Invocation target {index}', 'provider_bot_ref': f'invoke-target-{index}',
                'owners': [self.owner], 'agent_card': self.peer.card},
                token=admin, actor=None)
            self.targets.append(bot['bot_uuid'])
        self.target = self.targets[0]
        assert self.sql('SELECT agent_code FROM bcs_bots WHERE bot_uuid=?', (self.source,)) == [('invoke-source-code',)]
        for bot in [self.source, *self.targets]:
            self.fact(bot)
        assert self.publish('inspect')['writer'] is False
        prefix = PREFIXES[0]
        self.mutation(prefix, '/grants', self.grant_body(), status=503)
        self.request('POST', prefix + '/invocations', self.invoke_body(), actor='bot', status=503)
        self.gates(True, True)

    def grant_body(self, target=None, version=0):
        return {'from_bot_id': self.source, 'to_bot_id': target or self.target,
                'expected_version': version, 'reason': 'explicit Agent invocation E2E'}

    def invoke_body(self, target=None):
        return {'to_bot_id': target or self.target, 'message': 'A2A question AgentInvoke live dispatch'}

    def invoke(self, prefix, *, status=202, **changes):
        return self.request('POST', prefix + '/invocations', {**self.invoke_body(), **changes}, actor='bot', status=status)

    def run(self):
        self.setup()
        from agent_invoke_story_cases import exercise
        exercise(self)


def verify_startup_isolation(binary, path, config, env, db_path, feed_port):
    for label in ['nonlocal', 'nonloopback', 'zero-port', 'missing-capability', 'short-capability']:
        rejected = dict(config)
        rejected_env = dict(env)
        port = feed_port
        if label == 'nonlocal':
            rejected_env.update(SERVER_ENV='prod', REAL_SERVER_ENV='prod', ALIPAY_APP_ENV='prod')
        elif label == 'nonloopback':
            rejected['bind'] = '0.0.0.0'
        elif label == 'zero-port':
            port = 0
        elif label == 'missing-capability':
            rejected_env.pop('BCS_AGENT_INVOKE_FIXTURE_KEY')
        else:
            rejected_env['BCS_AGENT_INVOKE_FIXTURE_KEY'] = 'short'
        path.write_text(json.dumps(rejected))
        process = subprocess.run([binary, '-c', str(path), '--agent-invoke-fixture-port', str(port)],
            cwd=path.parent, env=rejected_env, capture_output=True, timeout=30)
        assert process.returncode != 0, f'{label} synthetic host accepted'
        assert not db_path.exists(), f'{label} synthetic host touched the database'
    path.write_text(json.dumps(config))


def run(binary, report):
    result = {'status': 'failed', 'checks': 0, 'operations': []}
    peer = Peer()
    peer.mode = 'message'
    peer.start()
    try:
        with tempfile.TemporaryDirectory(prefix='bcs-agent-invoke-e2e-') as directory:
            root = Path(directory)
            port, feed_port = free_port(), free_port()
            signing, fixture_key = uuid.uuid4().hex, uuid.uuid4().hex
            config = json.loads(subprocess.check_output([binary, '--print-config']))
            config.update({'bind': '127.0.0.1', 'port': port, 'store_messages': True,
                'bcs_endpoint': f'http://127.0.0.1:{port}', 'strict_container_validation': False,
                'auth': {'chain': ['local'], 'allow_mock_headers': True, 'mock_user_id': 'invokee2eowner'},
                'gateway_principal': {'signing_key_env': 'AGENT_INVOKE_E2E_SIGNING_KEY'},
                'database': {'type': 'sqlite', 'sqlite': {'path': str(root / 'bcs.db')}},
                'security': {'outbound_url': {'block_private_networks': True, 'allow_loopback': True}},
                'llm': {'type': 'none'}, 'bcsfuse': {'enabled': False}})
            config['secret'] = {'provider': 'env', 'providers': {'env': {'prefix': 'BCS_SECRET_'}}}
            config['bots_base_dir'] = str(root / 'bots')
            config['session_files']['backend']['data_dir'] = str(root / 'session-files')
            config['collaboration']['templates']['base_dir'] = str(root / 'templates')
            (root / 'catalog-labels.toml').write_bytes((Path(__file__).resolve().parents[2] / 'configs/catalog-labels.toml').read_bytes())
            path = root / 'config.json'
            path.write_text(json.dumps(config))
            env = {**os.environ, 'SERVER_ENV': 'local', 'REAL_SERVER_ENV': 'local', 'ALIPAY_APP_ENV': 'local',
                   'AGENT_INVOKE_E2E_SIGNING_KEY': signing, 'BCS_AGENT_INVOKE_FIXTURE_KEY': fixture_key,
                   'BCS_SECRET_BCN_GROUP_SESSION_WS_JWT': uuid.uuid4().hex}
            verify_startup_isolation(binary, path, config, env, root / 'bcs.db', feed_port)
            with (root / 'server.log').open('w') as log:
                process = subprocess.Popen([binary, '-c', str(path), '--agent-invoke-fixture-port', str(feed_port)],
                    cwd=root, env=env, stdout=log, stderr=log)
                story = Story(f'http://127.0.0.1:{port}', f'http://127.0.0.1:{feed_port}', fixture_key,
                              signing, root / 'bcs.db', peer)
                try:
                    for _ in range(150):
                        assert process.poll() is None, 'fixture server exited: ' + (root / 'server.log').read_text()[-2000:]
                        try:
                            story.request('GET', '/health', actor=None)
                            break
                        except (OSError, urllib.error.URLError):
                            time.sleep(0.1)
                    else:
                        raise AssertionError('AgentInvoke companion did not become ready')
                    story.scenarios.append('startup-isolation')
                    story.run()
                    result['status'] = 'passed'
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                    process.wait(timeout=30)
                    result.update(checks=story.checks, operations=story.observed, scenarios=story.scenarios,
                                  peer_deliveries=len(peer.requests), process_exit=process.returncode)
                    if result['status'] == 'passed':
                        assert process.returncode == 0, 'AgentInvoke companion shutdown failed'
    finally:
        peer.close()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2) + '\n')
    print(f"AgentInvoke E2E: {result['checks']} live HTTP/feed assertions, {result['peer_deliveries']} real A2A peer deliveries")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=lambda s: str(Path(s).resolve()))
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    run(args.binary, args.report)
