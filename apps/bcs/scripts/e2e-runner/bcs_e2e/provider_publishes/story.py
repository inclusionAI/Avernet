"""42 ordered loopback exchanges; sanitized field receipts, no retries or cleanup."""
import hashlib
from http.client import HTTPConnection
import json
import time
import uuid
from urllib.parse import quote, urlsplit

from ..local_provider_storage import OwnedLocalProviderStorage
from ..profile import identifier, unique_keys
from ..run_state import RunStateError, write
from .checks import evaluate


def require(value, reason):
    if not value:
        raise RunStateError(reason)


class ProviderPublishesStory:
    def __init__(self, runtime, identities, sink, contract):
        self.runtime, self.identities, self.sink = runtime, identities, sink
        self.contract = contract
        self.secrets = {}
        self.context = {'provider_slug': 'e2e-provider-' + hashlib.sha256(runtime.run_id.encode()).hexdigest()[:24],
                        'provider_bot_ref': runtime.run_id,
                        'organization_code': 'e2e-org-' + hashlib.sha256(runtime.run_id.encode()).hexdigest()[:24]}
        self.context['renamed_slug'] = self.context['provider_slug'] + '-renamed'
        self.path = runtime.root / 'provider-publishes-case.json'
        self.report = {'run_id': runtime.run_id, 'status': 'RUNNING', 'operations': [],
                       'assertions': [], 'retention': {}, 'remote_validation': 'NOT_RUN',
                       'grants_release_credit': False}
        self.started = False
        self.storage = OwnedLocalProviderStorage(runtime)

    def save(self):
        text = json.dumps(self.report, ensure_ascii=True)
        forbidden = list(self.secrets.values()) + [v[1] for v in self.identities._entries.values()]
        forbidden += [urlsplit(self.sink.url).path]
        require(not any(s and s in text for s in forbidden), 'S04 report contains a private value')
        write(self.path, self.report)

    def body(self, operation):
        c = self.context
        return {
            'register': {'name': 'E2E Provider', 'slug': c['provider_slug'], 'webhook_url': self.sink.url,
                         'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0',
                         'coordination': {'mode': 'native_tool'}},
            'duplicate-slug-conflict': {'name': 'Duplicate E2E Provider', 'slug': c['provider_slug'],
                                       'auth': {'mode': 'static_bearer'}},
            'update': {'name': 'E2E Provider Updated', 'slug': c['renamed_slug'], 'protocol_version': '2.0'},
            'bot-publish': {'name': 'Provider Review Agent', 'summary': 'Reviews release plans',
                            'owners': [self.identities.human], 'provider_bot_ref': c['provider_bot_ref'],
                            'domains': ['release'], 'skills': ['review'], 'scopes': ['local']},
            'bot-update': {'name': 'Provider Review Agent (Senior)', 'domains': ['release', 'audit'], 'scopes': []},
            'attributes-write-denied': {'user_visibility': 'public'},
            'org-create': {'organization_code': c['organization_code'], 'name': 'E2E release organization',
                           'description': 'provider-managed release team'},
            'org-update': {'name': 'E2E release organization updated'},
            'member-add': {'role': 'reviewer'}, 'member-profile': {'name': 'E2E organization bot'},
            'admin-start': {'target_bot_uuid': c.get('provider_bot_uuid'), 'message': {'role': 'user',
                           'content': [{'type': 'text', 'text': 'Review the E2E release plan'}]}, 'detach': True},
            'agentpass-negative': {},
            'unknown-run-callback': {'run_id': 'missing-provider-run-' + self.runtime.run_id,
                                    'state': 'final', 'message': {'text': 'late provider result'}},
            'takeover-denied': {'bot_id': self.identities._entries['QA'][0], 'provider_bot_ref': c['provider_bot_ref']},
        }.get(operation)

    def exchange(self, spec, request_id):
        require(self.runtime.state == 'RUNNING' and self.runtime.child.poll() is None,
                'S04 owned process no longer live')
        name = spec['id']
        path = spec['path']
        for k, v in self.context.items():
            if isinstance(v, str):
                path = path.replace('${' + k + '}', quote(v, safe=''))
        require('${' not in path, 'S04 unresolved owned identity')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Request-ID': request_id}
        if name not in ('register', 'duplicate-slug-conflict') and 'slug' not in name:
            principal = 'runtime' if name in ('agentpass-negative', 'unknown-run-callback') else 'admin'
            require(principal in self.secrets, 'S04 principal unavailable')
            headers['Authorization'] = 'Bearer ' + self.secrets[principal]
        if name in ('agentpass-negative', 'unknown-run-callback', 'admin-start', 'admin-read'):
            headers['X-BCN-Provider-Id'] = self.context['provider_id']
        if name == 'admin-start':
            self.sink.arm(self.context['provider_id'], self.context['provider_bot_uuid'], self.secrets['downlink'])
        data = self.body(name)
        payload = json.dumps(data).encode() if data is not None else None
        connection = HTTPConnection('127.0.0.1', self.runtime.port, timeout=5)
        try:
            deadline = time.monotonic() + 5
            connection.request(spec['method'], path, body=payload, headers=headers)
            sock = connection.sock
            response = connection.getresponse()
            raw = bytearray()
            with response:
                while True:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'S04 response deadline')
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(4096, 65537 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    require(len(raw) <= 65536, 'S04 response byte budget')
                require(response.length in (None, 0), 'S04 truncated response')
                status = response.status
            value = json.loads(raw, object_pairs_hook=unique_keys) if raw else {}
            require(isinstance(value, dict), 'S04 response object required')
            return status, value
        except Exception:
            raise RunStateError('S04 exchange unconfirmed; no retry or cleanup') from None
        finally:
            connection.close()

    def capture(self, name, body):
        def credential(key, field):
            value = body.get(field)
            require(isinstance(value, str) and 0 < len(value) <= 4096 and value.isascii()
                    and all(32 < ord(c) < 127 for c in value), 'S04 credential admission unavailable')
            require(value not in self.secrets.values(), 'S04 credential reuse')
            self.secrets[key] = value
        if name == 'register':
            credential('admin', 'provider_admin_token')
            credential('downlink', 'bcs_to_provider_token')
            self.context['provider_id'] = identifier(body.get('provider_id'), 'S04 provider ID')
        elif name == 'bot-publish':
            credential('runtime', 'bot_runtime_token')
            bot = identifier(body.get('bot_uuid'), 'S04 bot ID')
            require(bot not in {v[0] for v in self.identities._entries.values()}, 'S04 borrowed bot')
            self.context['provider_bot_uuid'] = bot
        elif name == 'slug-read':
            # Store only the exact public projection, never arbitrary response fields.
            keys = {'auth_mode', 'created_at', 'enabled', 'name', 'protocol_version', 'provider_id', 'slug', 'updated_at'}
            require(set(body) == keys, 'S04 nonpublic slug projection')
            self.context['legacy_projection'] = dict(body)
        elif name == 'admin-start':
            data = body.get('data', {})
            self.context['admin_run_id'] = identifier(data.get('run_id'), 'S04 admin run ID')

    def run(self):
        require(not self.started and not self.path.exists(), 'S04 story cannot repeat')
        self.started = True
        before = self.storage.fixture_snapshot(self.identities._entries, self.identities.human)
        self.storage.fresh_namespace(self.context['organization_code'])
        self.save()
        try:
            for spec in self.contract['operations']:
                name = spec['id']
                record = {'operation': name, 'status': 'INTENDED', 'request_id': 'e2e-s04-' + uuid.uuid4().hex}
                self.report['operations'].append(record)
                self.save()  # Intent precedes bytes; unknown outcomes are never retried.
                status, body = self.exchange(spec, record['request_id'])
                record.update(status='ACKNOWLEDGED', http_status=status)
                rows = [r for r in self.contract['requirements'] if r['operation'] == name]
                for row in rows:
                    # Status is recorded even if capture/admission later fails.
                    if row['kind'] != 'status':
                        continue
                    passed = evaluate(row, status, body, self.context)
                    self.report['assertions'].append({'id': row['id'], 'status': 'PASS' if passed else 'FAIL',
                        'source_line': row['line'], 'request_id': record['request_id'], 'predicate': row['kind']})
                    self.save()
                    require(passed, 'S04 status mismatch')
                self.capture(name, body)
                for row in rows:
                    if row['kind'] == 'status':
                        continue
                    passed = evaluate(row, status, body, self.context)
                    self.report['assertions'].append({'id': row['id'], 'status': 'PASS' if passed else 'FAIL',
                        'source_line': row['line'], 'request_id': record['request_id'], 'predicate': row['kind']})
                    self.save()
                    require(passed, 'S04 field mismatch')
                body.clear()  # Never persist the response or registration credentials.
                record['status'] = 'VERIFIED'
                self.save()
            after = self.storage.fixture_snapshot(self.identities._entries, self.identities.human)
            require(before == after, 'S04 preserved fixtures changed')
            p, b, org = (self.context[k] for k in ('provider_id', 'provider_bot_uuid', 'organization_code'))
            retired = self.storage.bot(p, b, self.runtime.run_id, self.secrets['runtime'])
            require(retired['bot']['is_deleted'] == 1 and retired['binding']['disabled'] == 1,
                    'S04 retirement not persisted')
            member = self.storage.organization(p, org, b)
            require(member['organization']['disabled'] == 0 and member['members'] == [
                {'organization_code': org, 'bot_uuid': b, 'role': 'reviewer', 'disabled': 1}],
                'S04 retained organization/member disposition mismatch')
            downstream = self.sink.summary()
            require(downstream['failed'] is False and downstream['received'] == 1
                    and downstream['rejected'] == 1 and downstream['engine'] == 'NOT_STARTED',
                    'S04 controlled downstream rejection unconfirmed')
            self.report.update(status='PASS', fixtures_unchanged=True, downstream=downstream,
                retention={'provider_id': p, 'provider': 'RETAINED_ENABLED', 'organization_code': org,
                           'organization': 'RETAINED', 'bot_uuid': b, 'bot': 'SOFT_DELETED_BINDING_DISABLED',
                           'member': 'LOGICALLY_REMOVED_HISTORY_RETAINED', 'database': 'RETAINED',
                           'admin_run': 'RETAINED_NEGATIVE_DELIVERY_NO_ENGINE', 'cleanup': 'NOT_RUN'})
        except Exception as error:
            self.report.update(status='BLOCKED', error_type=type(error).__name__)
            if self.report['operations'] and self.report['operations'][-1]['status'] != 'VERIFIED':
                record = self.report['operations'][-1]
                record['status'] = 'UNKNOWN' if record['status'] == 'INTENDED' else 'UNCONFIRMED'
            self.report['retention'].update(database='RETAINED', cleanup='NOT_RUN', uncertain_outcome=True)
        finally:
            existing = {r['id'] for r in self.report['assertions']}
            self.report['assertions'] += [{'id': r['id'], 'status': 'NOT_RUN', 'source_line': r['line']}
                                         for r in self.contract['requirements'] if r['id'] not in existing]
            self.save()
            self.secrets.clear()
            self.context.pop('legacy_projection', None)
            self.report['credential_memory'] = 'CLEARED_EXECUTOR_REFERENCES_ONLY'
            self.save()
        return self.report
