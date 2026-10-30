"""Reviewed retained File v2 through the real composition and synthetic boundaries."""
from __future__ import annotations

import contextlib
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.file_transport import FileTransportError, RemoteFileClient
from bcs_e2e.pool import pool_path, pool_scope
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.retained_file_remote import (CONTRACT, ROUTE_CONTRACT, FileRetainedReview,
    RemoteRetainedFileAdapter, validate_file_review)
from bcs_e2e.retained_remote import _OPERATION_LIMITS as CONTROL_OPERATION_LIMITS
from bcs_e2e.retention_quota_v2 import CONTRACT as QUOTA_CONTRACT, COSTS
from bcs_e2e.run_state import read, write
from test_file_transport import Reply, envelope
import test_retained_remote as fixtures
from bcs_e2e.inventory import current_manifest


class RetainedFileRemoteTests(unittest.TestCase):
    command = fixtures.RetainedRemoteTests.command
    process = fixtures.RetainedRemoteTests.process

    def setUp(self):
        fixtures.RetainedRemoteTests.setUp(self)
        self.profile['file_transfer'] = {'allowed_storage_origins': ['https://storage.example.test']}
        write(self.profile_path, self.profile)
        self.receipt['profile_digest'] = fingerprint(self.profile)
        write(self.receipt_path, self.receipt)
        now = int(time.time())
        self.review = {
            'schema_version': 1, 'contract': CONTRACT, 'profile_digest': fingerprint(self.profile),
            'run_id': self.run_id, 'case_id': 'RETAIN-FILE-01',
            'reviewed_at': now - 1, 'expires_at': now + 1800,
            'supervisor': self.profile['human']['user_id'],
            'effect_review': {
                'reference': 'synthetic-effect-review', 'sha256': 'd' * 64,
                'deployment_commit': 'a' * 40, 'isolated_test_runtime': True,
                'no_operational_channels': True, 'implicit_bot_execution_allowed': True,
            },
            'file_effect_review': {
                'storage_backend': 'baas',
                'allowed_storage_origins': self.profile['file_transfer']['allowed_storage_origins'],
                'storage_key_prefix': 'pre/team_claw', 'max_upload_bytes': 4096,
                'completion': {'implicit_share': True, 'share_ttl_days': 15},
                'notifications': [
                    {'bot_id': self.profile['actors']['driver']['bot_id'],
                     'operation': 'Inject', 'allowed': True},
                    {'bot_id': self.profile['actors']['member']['bot_id'],
                     'operation': 'Inject', 'allowed': True},
                ],
                'explicit_share_allowed': True,
                'source_review': {'reference': 'synthetic-file-source-review', 'sha256': 'f' * 64},
                'route_contract': copy.deepcopy(ROUTE_CONTRACT),
            },
            'retention_quota': self.quota(),
        }
        write(self.review_path, self.review)
        self.file_calls = []
        self.target_mode = 'good'
        self.expire_after = None
        self.lose_operation = None
        self.wrong_backend = False
        self.anonymous_route_blocked = False

    def quota(self, **changes):
        value = {'contract': QUOTA_CONTRACT, 'registry_id': 'test-inventory',
            'mode': 'initialize', 'expected_digest': 'absent',
            'max_groups': 10, 'max_sessions': 20, 'max_files': 10,
            'max_uploaded_bytes': 40960, 'max_share_mints': 20,
            'baseline_groups': 0, 'baseline_sessions': 0, 'baseline_files': 0,
            'baseline_uploaded_bytes': 0, 'baseline_share_mints': 0,
            'inventory_reference': 'test-inventory-review', 'inventory_sha256': 'e' * 64,
            'file_inventory_reference': 'test-file-inventory-review',
            'file_inventory_sha256': 'f' * 64}
        value.update(changes)
        return value

    def args(self):
        return ['retained-run', '--profile', str(self.profile_path), '--state-dir', str(self.state),
                '--run-id', self.run_id, '--case', 'RETAIN-FILE-01', '--allow-write',
                '--confirm-run-id', self.run_id, '--retained-review', str(self.review_path),
                '--observer-binding', str(self.receipt_path), '--mcporter-binary', str(self.executable),
                '--allow-read', '--allow-observer-read', '--serial-preflight']

    def signed_target(self, session_id: str, filename: str) -> str:
        key = f"pre/team_claw/{session_id}/{'1' * 32}/{filename}"
        return 'https://storage.example.test/' + quote(key, safe='') + '?Signature=synthetic'

    @contextlib.contextmanager
    def boundaries(self):
        def prepare(client, session_id, body):
            self.file_calls.append('file.prepare')
            data, record = self.adapter.file_prepare(session_id, body)
            file_id = data['file_id']
            target = {'file_id': file_id, 'mode': 'single', 'method': 'PUT',
                      'upload_url': self.signed_target(session_id, body['file_name'])}
            if self.target_mode == 'proxy':
                target['upload_url'] = self.profile['entrypoint']['origin'] + f'/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content'
            elif self.target_mode == 'multipart':
                target = {'file_id': file_id, 'mode': 'multipart', 'method': 'PUT',
                          'part_size': body['size'], 'part_count': 1,
                          'parts': [{'part_number': 1, 'upload_url': target['upload_url']}]}
            elif self.target_mode == 'method':
                target['method'] = 'POST'
            elif self.target_mode == 'origin':
                target['upload_url'] = target['upload_url'].replace('storage.example.test', 'other.example.test')
            elif self.target_mode == 'prefix':
                key = f"pre/other/{session_id}/{'1' * 32}/{body['file_name']}"
                target['upload_url'] = 'https://storage.example.test/' + quote(key, safe='')
            elif self.target_mode == 'double-encoded':
                key = f"pre%252Fteam_claw%252F{session_id}%252F{'1' * 32}%252F{body['file_name']}"
                target['upload_url'] = 'https://storage.example.test/' + key
            client.prepared[(session_id, file_id)] = {
                'target': copy.deepcopy(target), 'size': body['size'], 'mime_type': body['mime_type']}
            if self.expire_after == 'prepare':
                frozen = self.state / 'retained-review.json'
                changed = read(frozen); changed['expires_at'] = changed['reviewed_at']; write(frozen, changed)
            if self.lose_operation == 'prepare':
                raise FileTransportError('lost prepare').with_record(record)
            return target, record

        def upload(_client, session_id, file_id, content):
            self.file_calls.append('file.upload')
            return self.adapter.file_upload(session_id, file_id, content)

        def complete(_client, session_id, file_id):
            self.file_calls.append('file.complete')
            result = self.adapter.file_complete(session_id, file_id)
            if self.wrong_backend:
                result[0]['storage_backend'] = 'local'
            if self.lose_operation == 'complete':
                raise FileTransportError('lost complete').with_record(result[1])
            return result

        delegates = {
            'list': lambda _c, session: self.adapter.file_list(session),
            'metadata': lambda _c, session, file: self.adapter.file_metadata(session, file),
            'download': lambda _c, session, file: self.adapter.file_download(session, file),
            'share': lambda _c, session, file: self.adapter.file_share(session, file),
            'shared_download': lambda _c, capability: self.adapter.shared_file_download(capability),
        }
        def tracked(name, function):
            def call(*args):
                self.file_calls.append('file.' + name)
                return function(*args)
            return call
        def anonymous_probe(_client):
            record = {'operation': 'file.anonymous_route_probe', 'method': 'GET',
                      'path': '/api/v1/collaboration/sessions/shared-file/content?[REDACTED]',
                      'http_status': 200 if self.anonymous_route_blocked else 404}
            if self.anonymous_route_blocked:
                raise FileTransportError('synthetic login interception').with_record(record)
            return {'status': 'TOKEN_VALIDATION_REACHED', 'file_access': 'NOT_TESTED'}, record
        with fixtures.RetainedRemoteTests.boundaries(self), \
             patch.object(RemoteFileClient, 'probe_anonymous_route', anonymous_probe), \
             patch.object(RemoteFileClient, 'prepare', prepare), \
             patch.object(RemoteFileClient, 'upload', upload), \
             patch.object(RemoteFileClient, 'complete', complete), \
             patch.object(RemoteFileClient, 'list', tracked('list', delegates['list'])), \
             patch.object(RemoteFileClient, 'metadata', tracked('metadata', delegates['metadata'])), \
             patch.object(RemoteFileClient, 'download', tracked('download', delegates['download'])), \
             patch.object(RemoteFileClient, 'share', tracked('share', delegates['share'])), \
             patch.object(RemoteFileClient, 'shared_download', tracked('shared_download', delegates['shared_download'])):
            yield

    def test_anonymous_login_interception_blocks_before_run_pool_transfer_or_quota(self):
        self.anonymous_route_blocked = True
        with self.boundaries():
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertFalse(result['prepared'])
        self.assertEqual(result['preflight']['anonymous_file_route']['status'], 'BLOCKED')
        self.assertFalse(self.state.exists())
        self.assertFalse(pool_path(pool_scope(self.profile)).exists())
        self.assertFalse(pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json').exists())
        self.assertFalse(any(kind == 'write' for kind, _ in self.http))
        self.assertEqual(self.file_calls, [])

    def test_direct_file_executor_cannot_skip_continuous_serial_admission(self):
        from bcs_e2e.retained import run_retained_case
        review = FileRetainedReview(self.profile, self.run_id, 'RETAIN-FILE-01', self.review_path)
        with patch('bcs_e2e.retained.reserve_retention_quota') as quota, \
             patch('bcs_e2e.retained.remote_file_adapter_factory') as adapter, \
             self.assertRaisesRegex(ConfigError, 'continuously held serial'):
            run_retained_case(self.profile, self.state, self.run_id, 'RETAIN-FILE-01',
                              _remote_review=review, _executable=self.executable)
        quota.assert_not_called(); adapter.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_success_uses_shared_steps_baas_ob_and_canonical_retained_report(self):
        with self.boundaries():
            rc, result = self.command(self.args())
        self.assertEqual(rc, 0, result)
        self.assertEqual((result['functional'], result['status']), ('PASS', 'PASS'))
        self.assertEqual(result['execution_status'], 'UNCONFIRMED')
        self.assertEqual(result['cleanup'], 'RETAINED')
        self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(result['retained_review']['contract'], CONTRACT)
        effects = result['retained_review']['file_effects']
        self.assertEqual(effects['completion_implicit_share']['outcome'], 'NOT_VERIFIED')
        self.assertEqual(effects['completion_notifications']['delivery'], 'NOT_VERIFIED')
        self.assertEqual(result['retention_quota']['cost'], COSTS['RETAIN-FILE-01'])
        self.assertEqual(self.file_calls, ['file.prepare', 'file.upload', 'file.complete', 'file.list',
                                           'file.metadata', 'file.download', 'file.share', 'file.shared_download'])
        self.assertEqual(len(read(self.state / 'retention.json')['resources']), 4)
        summary = read(self.state / 'reports/summary.json')
        self.assertEqual(summary['status'], 'RETAINED_CASE_PASS')
        self.assertEqual(summary['scope']['suite'], 'preprod-retained-file-v1')
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest('preprod-retained-file-v1')['legacy_inventory']))
        self.assertEqual(result['case_version'], 2)
        queries = [call for call in self.calls if call[2].endswith('.db_query')]
        self.assertEqual(len(queries), 1)
        self.assertEqual(json.loads(queries[0][4])['table'], 'bcs_session_files')

    def test_bad_review_quota_and_origin_fail_before_http_state_or_pool(self):
        mutations = []
        bad = copy.deepcopy(self.review); bad['contract'] = 'supervised-retained-control-v1'; mutations.append(('contract', bad))
        bad = copy.deepcopy(self.review); bad['retention_quota'] = copy.deepcopy(self.review['retention_quota']); bad['retention_quota']['contract'] = 'retained-resource-quota-v1'; mutations.append(('quota', bad))
        bad = copy.deepcopy(self.review); bad['file_effect_review']['allowed_storage_origins'] = []; mutations.append(('review-origin', bad))
        for name, value in mutations:
            with self.subTest(name=name):
                write(self.review_path, value)
                with patch('bcs_e2e.retained_serial.PoolLease._guard') as guard, \
                     patch('bcs_e2e.transport.ReadClient.read') as http:
                    rc, _ = self.command(self.args())
                self.assertEqual(rc, 2); guard.assert_not_called(); http.assert_not_called()
                self.assertFalse(self.state.exists())
        write(self.review_path, self.review)
        profile = copy.deepcopy(self.profile); profile.pop('file_transfer')
        write(self.profile_path, profile)
        value = copy.deepcopy(self.review); value['profile_digest'] = fingerprint(profile); write(self.review_path, value)
        with patch('bcs_e2e.retained_serial.PoolLease._guard') as guard, patch('bcs_e2e.transport.ReadClient.read') as http:
            rc, _ = self.command(self.args())
        self.assertEqual(rc, 2); guard.assert_not_called(); http.assert_not_called(); self.assertFalse(self.state.exists())

    def test_review_contract_is_strict_and_control_v1_cannot_admit_file(self):
        edits = [
            {'storage_backend': 'local'}, {'max_upload_bytes': 4095},
            {'storage_key_prefix': ''}, {'storage_key_prefix': '/pre/team_claw'},
            {'storage_key_prefix': 'pre/../team_claw'}, {'storage_key_prefix': 'pre/%2F/team_claw'}, {'storage_key_prefix': 'pre/team claw'},
            {'storage_key_prefix': 'pre/team?claw'}, {'storage_key_prefix': 'a' * 513},
            {'completion': {'implicit_share': True, 'share_ttl_days': 14}},
            {'completion': {'implicit_share': 1, 'share_ttl_days': 15}},
            {'completion': {'implicit_share': True, 'share_ttl_days': 15.0}},
            {'notifications': self.review['file_effect_review']['notifications'][:1]},
            {'notifications': [{**self.review['file_effect_review']['notifications'][0], 'allowed': 1},
                               self.review['file_effect_review']['notifications'][1]]},
            {'explicit_share_allowed': False}, {'route_contract': {'api_face': 'public-v1', 'resource': 'file'}},
            {'source_review': {'reference': 'source', 'sha256': 'bad'}},
        ]
        for edit in edits:
            value = copy.deepcopy(self.review); value['file_effect_review'].update(edit)
            with self.subTest(edit=edit), self.assertRaises(ConfigError):
                validate_file_review(value, self.profile, self.run_id, 'RETAIN-FILE-01')
        old = copy.deepcopy(self.review); old['contract'] = 'supervised-retained-control-v1'; old.pop('file_effect_review')
        with self.assertRaises(ConfigError):
            validate_file_review(old, self.profile, self.run_id, 'RETAIN-FILE-01')

    def test_bad_targets_are_rejected_before_put_or_complete_including_double_encoding(self):
        for mode in ('proxy', 'multipart', 'method', 'origin', 'prefix', 'double-encoded'):
            with self.subTest(mode=mode):
                self.state = self.root / ('run-' + mode)
                self.run_id = 'run-' + mode
                self.profile['policy']['fixture_pool'] = 'pool-' + mode
                write(self.profile_path, self.profile)
                self.review['run_id'] = self.run_id
                self.review['profile_digest'] = fingerprint(self.profile)
                self.review['retention_quota'] = self.quota()
                self.receipt['profile_digest'] = fingerprint(self.profile)
                write(self.review_path, self.review); write(self.receipt_path, self.receipt)
                self.target_mode = mode; self.file_calls = []
                before_files = len(self.adapter.files)
                with self.boundaries(): rc, result = self.command(self.args())
                self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'FAIL')
                self.assertEqual(self.file_calls, ['file.prepare'])
                self.assertEqual(len(self.adapter.files), before_files + 1)
                self.assertEqual(self.adapter.deleted_files, set())

    def test_wrong_backend_and_bytes_fail(self):
        self.wrong_backend = True
        with self.boundaries(): rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'FAIL')
        self.assertNotIn('file.list', self.file_calls)

        # The finite adapter rejects non-deterministic bytes before its actual File client.
        review = FileRetainedReview(self.profile, self.run_id, 'RETAIN-FILE-01', self.review_path)
        raw = RemoteRetainedFileAdapter.__new__(RemoteRetainedFileAdapter)
        raw.review = review; raw.profile = self.profile; raw._binding = Mock(); raw._binding.validate.return_value = None
        raw._adapter = Mock(); raw._adapter.files.prepared = {}; raw._read = Mock()
        raw._remaining = {'file_upload': 1}; raw._preflight_done = True; raw._preflight_attempted = True; raw._uploaded = set()
        with self.assertRaises(ConfigError): raw.file_upload('s', 'f', b'wrong')
        raw._adapter.file_upload.assert_not_called()

    def test_wrong_persisted_parent_blocks_ob_assertion(self):
        original_process = self.process
        def wrong_process(command, **kwargs):
            result = original_process(command, **kwargs)
            if command[2].endswith('.db_query'):
                args = json.loads(command[4])
                if args.get('table') == 'bcs_session_files':
                    payload = json.loads(result.stdout)
                    payload['data'][0]['session_id'] = 'other-session'
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), '')
            return result
        with patch.object(self, 'process', side_effect=wrong_process), self.boundaries():
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'PASS')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['evidence_status'], 'BLOCKED')
        self.assertIn('file.download', self.file_calls)  # Shared business steps remain unchanged.

    def test_lost_prepare_or_complete_is_not_retried_deleted_or_refunded(self):
        for operation in ('prepare', 'complete'):
            with self.subTest(operation=operation):
                self.state = self.root / ('lost-' + operation)
                self.run_id = 'lost-' + operation
                self.profile['policy']['fixture_pool'] = 'lost-pool-' + operation
                write(self.profile_path, self.profile)
                self.review['run_id'] = self.run_id; self.review['profile_digest'] = fingerprint(self.profile)
                self.review['retention_quota'] = self.quota()
                self.receipt['profile_digest'] = fingerprint(self.profile)
                write(self.review_path, self.review); write(self.receipt_path, self.receipt)
                self.lose_operation = operation; self.file_calls = []
                with self.boundaries(): rc, result = self.command(self.args())
                self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'FAIL')
                self.assertEqual(self.file_calls.count('file.' + operation), 1)
                self.assertEqual(self.adapter.deleted_files, set())
                journal = read(pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json'))
                self.assertEqual(journal['reservations'][0]['cost'], COSTS['RETAIN-FILE-01'])

    def test_review_expiry_after_prepare_and_product_401_stop_next_writes(self):
        self.expire_after = 'prepare'
        with self.boundaries(): rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'FAIL')
        self.assertEqual(self.file_calls, ['file.prepare'])

        self.state = self.root / 'denied'; self.run_id = 'denied'; self.file_calls = []
        self.profile['policy']['fixture_pool'] = 'denied-pool'; write(self.profile_path, self.profile)
        self.review['run_id'] = self.run_id; self.review['profile_digest'] = fingerprint(self.profile)
        self.review['retention_quota'] = self.quota(); self.receipt['profile_digest'] = fingerprint(self.profile)
        write(self.review_path, self.review); write(self.receipt_path, self.receipt)
        self.expire_after = None
        def denied(_client, _session, _body):
            self.file_calls.append('file.prepare')
            raise FileTransportError('file operation returned HTTP 401').with_record({'operation': 'file.prepare', 'http_status': 401})
        with self.boundaries(), patch.object(RemoteFileClient, 'prepare', denied):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertEqual(result['functional'], 'FAIL')
        self.assertEqual(self.file_calls, ['file.prepare'])

    def test_file_adapter_has_no_control_or_cleanup_surface_and_group_session_adapter_is_unchanged(self):
        review = FileRetainedReview(self.profile, self.run_id, 'RETAIN-FILE-01', self.review_path)
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'synthetic-cookie'}):
            adapter = RemoteRetainedFileAdapter(self.profile, review, observer_client=None, binding=Mock())
        allowed = set(adapter._remaining)
        self.assertEqual(allowed, {'group_create', 'session_create', 'file_prepare', 'file_upload',
            'file_complete', 'file_list', 'file_metadata', 'file_download', 'file_share',
            'shared_file_download'})
        for name in ('group_read', 'group_update', 'session_read', 'session_update', 'session_token',
                     'session_ws_connect', 'session_ws_close', 'file_delete', 'quiesce_execution'):
            self.assertFalse(hasattr(adapter, name), name)
        self.assertFalse(any(name.startswith('file_') for name in CONTROL_OPERATION_LIMITS))

    def test_encoded_direct_oss_path_and_transport_never_forward_browser_credentials(self):
        opener = Mock()
        filename = f'e2e-{self.run_id}.txt'
        signed = self.signed_target('owned:session', filename)
        target = {'file_id': 'f-1', 'mode': 'single', 'method': 'PUT', 'upload_url': signed}
        opener.open.side_effect = [Reply(target, 201), Reply(b'', 204)]
        profile = copy.deepcopy(self.profile)
        profile['auth'] = {'header_env': {'Cookie': 'TEST_COOKIE', 'Authorization': 'TEST_AUTH'}}
        with patch.dict(os.environ, {'TEST_COOKIE': 'cookie-secret', 'TEST_AUTH': 'auth-secret'}, clear=True), \
             patch('bcs_e2e.file_transport.build_opener', return_value=opener):
            client = RemoteFileClient(profile)
            client.prepare('owned:session', {'file_name': filename, 'size': 7, 'mime_type': 'text/plain'})
            client.upload('owned:session', 'f-1', b'payload')
        request = opener.open.call_args_list[1].args[0]
        self.assertEqual(request.full_url, signed)
        self.assertNotIn('/', request.full_url.split('storage.example.test/', 1)[1].split('?', 1)[0])
        for header in ('Cookie', 'Authorization', 'Origin', 'Referer', 'X-request-id'):
            self.assertNotIn(header, request.headers)


if __name__ == '__main__':
    unittest.main()
