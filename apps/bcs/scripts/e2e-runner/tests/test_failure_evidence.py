"""Denied and failed requests remain traceable without retaining private payloads."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.correlation import CorrelatedEvidence, valid_traceparent
from bcs_e2e.file_transport import RemoteFileClient, FileTransportError
from bcs_e2e.ledger import Ledger
from bcs_e2e.log_observer import BoundedLogObserver
from bcs_e2e.pilot import run_case
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, fingerprint
from bcs_e2e.write_transport import WriteClient, WriteTransportError
from test_write_transport import Reply, profile as remote_profile
from test_file_transport import Reply as FileReply


class FailureEvidenceTests(unittest.TestCase):
    def test_write_denial_timeout_and_invalid_json_keep_attempt_metadata_only(self):
        p = remote_profile()
        p['route_bindings']['group.create']['traceparent'] = 'w3c'
        for failure in (Reply(b'private-response-token', 403), OSError('private-network-URL'),
                        Reply(b'private-invalid-json', 201)):
            with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}):
                client = WriteClient(p)
            client.opener = Mock()
            if isinstance(failure, Exception): client.opener.open.side_effect = failure
            else: client.opener.open.return_value = failure
            with self.assertRaises(WriteTransportError) as caught:
                client.call('group.create', body={'private': 'private-body'}, expected=(200, 201))
            error = caught.exception
            self.assertEqual(len(error.request_records), 1)
            record = error.request_records[0]
            request = client.opener.open.call_args.args[0]
            self.assertEqual(record['submitted_request_id'], request.headers['X-request-id'])
            self.assertEqual(record['submitted_traceparent'], request.headers['Traceparent'])
            self.assertTrue(valid_traceparent(record['submitted_traceparent']))
            self.assertEqual(record['http_status'], None if isinstance(failure, Exception) else failure.code)
            self.assertGreaterEqual(record['elapsed_ms'], 0)
            self.assertEqual(client.opener.open.call_count, 1)
            serialized = str(error) + json.dumps(error.request_records)
            for secret in ('private-response-token', 'private-network-URL', 'private-invalid-json', 'private-cookie', 'private-body'):
                self.assertNotIn(secret, serialized)

    def test_pre_request_validation_does_not_invent_sent_request(self):
        with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}): client = WriteClient(remote_profile())
        with self.assertRaises(WriteTransportError) as caught: client.call('unsupported')
        self.assertEqual(caught.exception.request_records, [])

    def test_storage_failure_redacts_signed_target_and_does_not_invent_product_correlation(self):
        p = remote_profile()
        p['file_transfer'] = {'allowed_storage_origins': ['https://storage.example.test']}
        with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}): client = RemoteFileClient(p)
        client.opener = Mock()
        client.opener.open.return_value = FileReply(b'private-storage-body', 403)
        with self.assertRaises(FileTransportError) as caught:
            client.upload_to_signed_url('https://storage.example.test/private-path?Signature=private-signed-token', b'payload')
        record = caught.exception.request_records[0]
        self.assertEqual(record['path'], '[storage-target]')
        self.assertEqual(record['http_status'], 403)
        self.assertIsNone(record['submitted_request_id'])
        serialized = json.dumps(record)
        for secret in ('private-storage-body', 'private-path', 'private-signed-token', 'private-cookie'):
            self.assertNotIn(secret, serialized)
        request = client.opener.open.call_args.args[0]
        self.assertNotIn('Cookie', request.headers)

    def test_file_json_and_absence_contract_errors_preserve_exact_request_metadata(self):
        p = remote_profile()
        with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}): client = RemoteFileClient(p)
        client.opener = Mock()
        for method, reply in (
                (lambda: client.prepare('s1', {'size': 1}), FileReply(b'private-invalid-json', 201)),
                (lambda: client.verify_deleted('s1', 'f1'), FileReply({'code': 40400, 'message': 'private',
                    'request_id': 'r1', 'data': {'error_code': 'session_not_found'}}, 404))):
            client.opener.open.return_value = reply
            with self.assertRaises(FileTransportError) as caught: method()
            record = caught.exception.request_records[0]
            self.assertEqual(record['http_status'], reply.code)
            self.assertIn('e2e-file-', record['submitted_request_id'])
            self.assertNotIn('private', json.dumps(record))

    def test_shared_case_checkpoints_failed_request_for_optional_logs_and_retains_unknown_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                p = load(ROOT / 'profiles/local.mock.json')
                state = root / 'state'
                prepare_run(p, state, 'failure-evidence')
                adapter = LocalMockAdapter(p)
                with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}): client = WriteClient(remote_profile())
                client.opener = Mock()
                client.opener.open.return_value = Reply(b'private-error-body', 403)
                log_client = Mock()
                log_client.query.side_effect = lambda query, **_: {'success': True, 'truncated': False, 'records': [{
                    'environment': query.environment, 'timestamp': query.start_epoch, 'request_id': query.request_id}]}
                observer = BoundedLogObserver(client=log_client, environment='local', environment_binding_confirmed=True)
                def denied(body): return client.call('group.create', body=body, expected=(200, 201))
                with patch.object(adapter, 'group_create', side_effect=denied), patch.object(adapter, 'group_delete') as delete:
                    report = run_case(p, state, 'failure-evidence', 'PRE-01', log_observer=observer,
                                      adapter_factory=lambda *args, **kwargs: adapter)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(report['cleanup'], 'BLOCKED')
                self.assertEqual(report['diagnostic_status'], 'PASS')
                failed = [record for record in report['evidence'] if record.get('outcome') == 'ERROR']
                self.assertEqual(len(failed), 1)
                self.assertEqual(failed[0]['http_status'], 403)
                self.assertEqual(log_client.query.call_args.args[0].request_id, failed[0]['submitted_request_id'])
                with Ledger(state / 'run.sqlite', 'failure-evidence', fingerprint(p), read_only=True) as ledger:
                    self.assertEqual(ledger.correlations()[0]['request_id'], failed[0]['submitted_request_id'])
                    self.assertEqual(len(ledger.cleanup_plan()['unresolved_handles']), 2)
                delete.assert_not_called()
                self.assertNotIn('private-error-body', json.dumps(report))

    def test_failure_projection_drops_unapproved_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Ledger(Path(tmp) / 'run.sqlite', 'safe', 'a' * 64, create=True) as ledger:
                evidence = CorrelatedEvidence(ledger, 'PRE-01', 1)
                error = WriteTransportError('safe').with_record({'operation': 'group.create',
                    'submitted_request_id': 'safe-request', 'http_status': 403,
                    'headers': {'Cookie': 'private'}, 'body': 'private', 'url': 'https://private.test'})
                evidence.record_failure(error)
                self.assertEqual(evidence[0]['outcome'], 'ERROR')
                self.assertNotIn('private', json.dumps(evidence))

    def test_malformed_server_id_and_correlation_write_failure_preserve_primary_case_report(self):
        for mode in ('bad-response-id', 'ledger-failure'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                    p = load(ROOT / 'profiles/local.mock.json'); state = root / 'state'
                    prepare_run(p, state, 'primary-failure')
                    adapter = LocalMockAdapter(p)
                    with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}): client = WriteClient(remote_profile())
                    client.opener = Mock()
                    client.opener.open.return_value = (Reply({'code': 20100, 'message': 'Created',
                        'request_id': 'x' * 129, 'data': []}, 201) if mode == 'bad-response-id'
                        else Reply(b'private-error-body', 403))
                    original = Ledger.record_correlation
                    def persist(ledger, *args):
                        if mode == 'ledger-failure': raise OSError('private-ledger-error')
                        return original(ledger, *args)
                    with patch.object(adapter, 'group_create', side_effect=lambda body: client.call('group.create', body=body, expected=(200, 201))), \
                         patch.object(Ledger, 'record_correlation', persist):
                        report = run_case(p, state, 'primary-failure', 'PRE-01',
                                          adapter_factory=lambda *args, **kwargs: adapter)
                    self.assertEqual(report['status'], 'FAIL')
                    self.assertEqual(report['cleanup'], 'BLOCKED')
                    self.assertTrue((state / 'report-PRE-01.json').is_file())
                    self.assertEqual(json.loads((state / 'run.json').read_text())['status'],
                                     'QUARANTINED' if mode == 'ledger-failure' else 'FAILED')
                    failed = next(e for e in report['evidence'] if e.get('outcome') == 'ERROR')
                    if mode == 'bad-response-id':
                        self.assertTrue(failed['response_request_id_rejected'])
                        self.assertNotIn('response_request_id', failed)
                        self.assertEqual(failed['correlation_status'], 'RECORDED')
                    else:
                        self.assertEqual(failed['correlation_status'], 'BLOCKED')
                        self.assertEqual(report['diagnostic_status'], 'INCOMPLETE')
                    primary = next(a for a in report['assertions'] if a['id'] == 'PRE-01.execution')
                    self.assertIn('public API response request ID is invalid' if mode == 'bad-response-id' else 'HTTP 403', primary['detail'])
                    self.assertNotIn('private-ledger-error', json.dumps(report))

    def test_failed_download_keeps_product_redirect_and_only_actual_storage_attempts(self):
        p = remote_profile(); p['file_transfer'] = {'allowed_storage_origins': ['https://storage.example.test']}
        for kind in ('storage-denied', 'missing-target', 'disallowed-target'):
            with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}): client = RemoteFileClient(p)
            redirect = FileReply(b'', 302)
            if kind != 'missing-target':
                redirect.headers['Location'] = ('https://storage.example.test/private?Signature=private-token'
                                                if kind == 'storage-denied' else 'https://other.example.test/private')
            client.opener = Mock()
            client.opener.open.side_effect = [redirect, FileReply(b'private-body', 403)]
            with self.assertRaises(FileTransportError) as caught: client.download('s1', 'f1')
            records = caught.exception.request_records
            self.assertEqual([r['http_status'] for r in records], [302, 403] if kind == 'storage-denied' else [302])
            self.assertTrue(records[0]['submitted_request_id'].startswith('e2e-file-'))
            self.assertTrue(records[0]['preceded_failure'])
            if kind == 'storage-denied': self.assertIsNone(records[1]['submitted_request_id'])
            self.assertEqual(client.opener.open.call_count, len(records))
            self.assertNotIn('private', json.dumps(records))

    def test_unconfirmed_delete_and_second_upload_part_failure_keep_completed_requests(self):
        p = remote_profile()
        with patch.dict(os.environ, {'BCS_E2E_COOKIE': 'private-cookie'}): client = RemoteFileClient(p)
        client.opener = Mock()
        envelope = lambda data: {'code': 20000, 'message': 'OK', 'request_id': 'r', 'data': data}
        client.opener.open.return_value = FileReply(envelope({'deleted': False}))
        with self.assertRaises(FileTransportError) as caught: client.delete('s1', 'f1')
        self.assertEqual(caught.exception.request_records[0]['operation'], 'file.delete')
        self.assertEqual(caught.exception.request_records[0]['http_status'], 200)
        base = p['entrypoint']['origin'] + '/sessions/s1/files/f1/content'
        client.prepared[('s1', 'f1')] = {'size': 2, 'mime_type': 'text/plain', 'target': {
            'mode': 'multipart', 'method': 'PUT', 'part_size': 1, 'part_count': 2,
            'parts': [{'part_number': n, 'upload_url': base + f'?part={n}'} for n in (1, 2)]}}
        client.opener.open.side_effect = [FileReply(envelope({'file_id': 'f1', 'status': 'pending'}), 202), FileReply(b'private', 403)]
        with self.assertRaises(FileTransportError) as caught: client.upload('s1', 'f1', b'ab')
        records = caught.exception.request_records
        self.assertEqual([r['http_status'] for r in records], [202, 403])
        self.assertNotEqual(records[0]['submitted_request_id'], records[1]['submitted_request_id'])
        self.assertTrue(records[0]['preceded_failure'])
        self.assertNotIn('private', json.dumps(records))
