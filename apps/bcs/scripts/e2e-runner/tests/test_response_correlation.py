"""Actual response ID provenance and error-to-log correlation contracts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.correlation import CorrelatedEvidence, record_response_id
from bcs_e2e.diagnostics import collect_log_diagnostics
from bcs_e2e.file_transport import RemoteFileClient, FileTransportError
from bcs_e2e.ledger import Ledger
from bcs_e2e.transport import ReadClient, ObservationError
from bcs_e2e.write_transport import WriteClient, WriteTransportError
from bcs_e2e.ws_transport import SessionWebSocketClient, WebSocketTransportError
from test_write_transport import profile, Reply


def envelope_profile():
    p = copy.deepcopy(profile())
    p["environment"]["tier"] = "local"
    return p
from test_file_transport import Reply as FileReply
from test_ws_evidence import ReplySocket


class ResponseIdTests(unittest.TestCase):
    def test_both_sources_and_conflict_are_explicit_with_envelope_precedence(self):
        for envelope in ('header-id', 'body-id'):
            record = {}
            self.assertTrue(record_response_id(record, 'header-id', source='HEADER'))
            self.assertEqual(record['response_request_id_source'], 'HEADER')
            self.assertTrue(record_response_id(record, envelope, source='ENVELOPE'))
            self.assertEqual(record['response_header_request_id'], 'header-id')
            self.assertEqual(record['response_envelope_request_id'], envelope)
            self.assertEqual(record['response_request_id'], envelope)
            self.assertEqual(record['response_request_id_source'], 'ENVELOPE')
            self.assertEqual(record['response_request_id_conflict'], envelope != 'header-id')

    def test_missing_headers_do_not_invent_actual_response_id(self):
        for value in (None, ''):
            record = {}
            self.assertFalse(record_response_id(record, value, source='HEADER'))
            self.assertEqual(record, {})

    def test_invalid_and_credential_echoes_never_enter_evidence(self):
        sensitive = ('Bearer test-secret', 'session=test-cookie; flag=1', 'test-secret')
        for source in ('HEADER', 'ENVELOPE'):
            for value in (None, {}, 3, 'bad id', 'line\nbreak', 'x'*129, 'test-secret',
                          'prefix-test-secret-suffix', 'test-cookie', 'token=abc', '\u2603'):
                record = {}
                with self.subTest(source=source, value=value):
                    self.assertFalse(record_response_id(record, value, source=source, sensitive_values=sensitive))
                    self.assertNotIn('response_request_id', record)
                    if isinstance(value, str):
                        self.assertNotIn(value, json.dumps(record))
        record = {}
        self.assertTrue(record_response_id(record, 'server-1', source='HEADER', sensitive_values=sensitive))
        self.assertEqual(record['response_request_id'], 'server-1')

    def test_rewritten_error_header_reaches_ledger_and_exact_diagnostic_query(self):
        for status in (401, 403, 500):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
                    client = WriteClient(profile())
                client.opener = Mock()
                client.opener.open.return_value = Reply(b'private-body', status, {'x-request-id': 'rewritten-server'})
                with self.assertRaises(WriteTransportError) as caught:
                    client.call('group.read', group_id='g1', request_id='submitted-client')
                with Ledger(Path(tmp)/'run.sqlite', 'run', 'a'*64, create=True) as ledger:
                    evidence = CorrelatedEvidence(ledger, 'PRE-01', 1)
                    evidence.record_failure(caught.exception)
                    self.assertEqual(evidence[0]['response_request_id_source'], 'HEADER')
                    self.assertEqual(evidence[0]['http_status'], status)
                    self.assertEqual(ledger.correlations()[0]['response_request_id'], 'rewritten-server')
                    observer = Mock()
                    observer.observe.side_effect = lambda query, **kwargs: {
                        'status': 'OBSERVED', 'source': 'logs', 'query': query.public(),
                        'record_count': 1, 'truncated': False}
                    result = collect_log_diagnostics(ledger, observer, case_id='PRE-01', attempt=1,
                        environment='pre', start_epoch=100, end_epoch=120)
                    self.assertEqual(result['status'], 'PASS')
                    query = observer.observe.call_args.args[0]
                    self.assertEqual(query.request_id, 'rewritten-server')
                    self.assertEqual((query.environment, query.start_epoch, query.end_epoch), ('pre', 100, 120))
                    self.assertNotIn('private-body', json.dumps(evidence))
                self.assertEqual(client.opener.open.call_count, 1)

    def test_expected_negative_and_empty_response_preserve_header_id(self):
        for status in (404, 204):
            with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
                client = WriteClient(profile())
            client.opener = Mock()
            client.opener.open.return_value = Reply(b'', status, {'x-request-id': 'header-only'})
            body, record = client.call('group.verify_deleted', group_id='g1', expected=(status,))
            self.assertIsNone(body)
            self.assertEqual(record['response_request_id'], 'header-only')
            self.assertEqual(record['response_request_id_source'], 'HEADER')

    def test_read_error_records_actual_header_but_not_echoed_cookie(self):
        for returned, accepted in (('read-server', True), ('test-cookie', False)):
            with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
                client = ReadClient(profile())
            client.opener = Mock()
            client.opener.open.return_value = Reply(b'private-body', 403, {'x-request-id': returned})
            with self.assertRaises(ObservationError):
                client.read('environment.read')
            record = client.evidence[0]
            self.assertEqual(record.get('response_request_id'), returned if accepted else None)
            self.assertNotIn('test-cookie', json.dumps(client.evidence))
            self.assertNotIn('private-body', json.dumps(client.evidence))

    def test_file_binary_and_error_header_correlate_without_storage_attribution(self):
        with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
            client = RemoteFileClient(profile())
        for status in (200, 403):
            response = FileReply(b'payload', status)
            response.headers = {'x-request-id': 'file-server'}
            client.opener = Mock()
            client.opener.open.return_value = response
            if status == 200:
                body, record = client.download('s1', 'f1')
                self.assertEqual(body, b'payload')
            else:
                with self.assertRaises(FileTransportError) as caught:
                    client.download('s1', 'f1')
                record = caught.exception.request_records[0]
            self.assertEqual(record['response_request_id'], 'file-server')
            self.assertEqual(record['response_request_id_source'], 'HEADER')
        client.storage_origins.add(('https', 'storage.example.test', 443))
        client.opener.open.return_value = FileReply(b'', 204)
        client.opener.open.return_value.headers = {'x-request-id': 'storage-not-bcs'}
        _, record = client.upload_to_signed_url('https://storage.example.test/signed', b'x')
        self.assertNotIn('response_request_id', record)
        self.assertIsNone(record['submitted_request_id'])

    def test_ws_rejection_keeps_actual_header_without_token_echo(self):
        for returned, accepted in (('ws-server', True), ('test-token', False)):
            sock = ReplySocket()
            with patch('bcs_e2e.ws_transport.socket.create_connection', return_value=sock), \
                 patch('bcs_e2e.ws_transport._read_headers', return_value=('HTTP/1.1 403 Denied', {'x-request-id': returned}, b'')), \
                 self.assertRaises(WebSocketTransportError) as caught:
                SessionWebSocketClient('http://example.test').connect('test-token', 'g1', 's1')
            record = caught.exception.request_records[0]
            self.assertEqual(record.get('response_request_id'), returned if accepted else None)
            self.assertNotIn('test-token', json.dumps(record))
            self.assertTrue(sock.closed)

    def test_invalid_envelope_id_preserves_safe_header_without_secret(self):
        response = Reply({'code': 20000, 'message': 'OK', 'request_id': 'test-cookie', 'data': {}},
                         headers={'x-request-id': 'safe-header'})
        for kind in ('write', 'read'):
            with self.subTest(kind=kind), patch.dict('os.environ', {'BCS_E2E_COOKIE': 'session=test-cookie; flag=1'}):
                client = WriteClient(profile()) if kind == 'write' else ReadClient(profile())
            client.opener = Mock()
            client.opener.open.return_value = response
            if kind == 'write':
                with self.assertRaises(WriteTransportError) as caught:
                    client.call('group.read', group_id='g1')
                record = caught.exception.request_records[0]
            else:
                with self.assertRaises(ObservationError):
                    client.read('environment.read')
                record = client.evidence[0]
            self.assertEqual(record['response_request_id'], 'safe-header')
            self.assertEqual(record['response_request_id_source'], 'HEADER')
            self.assertTrue(record['response_envelope_request_id_rejected'])
            self.assertNotIn('test-cookie', json.dumps(record))

    def test_file_and_read_envelopes_preserve_both_ids_without_claiming_equality(self):
        envelope = {'code': 20000, 'message': 'OK', 'request_id': 'body-id', 'data': {}}
        with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
            reader, files = ReadClient(profile()), RemoteFileClient(envelope_profile())
        reader.opener = Mock()
        reader.opener.open.return_value = Reply(envelope, headers={'x-request-id': 'header-id'})
        record = reader.read('environment.read').evidence
        file_reply = FileReply(envelope)
        file_reply.headers = {'x-request-id': 'header-id'}
        files.opener = Mock()
        files.opener.open.return_value = file_reply
        _, file_record = files.metadata('s1', 'f1')
        for value in (record, file_record):
            self.assertEqual(value['response_request_id'], 'body-id')
            self.assertEqual(value['response_header_request_id'], 'header-id')
            self.assertEqual(value['response_envelope_request_id'], 'body-id')
            self.assertTrue(value['response_request_id_conflict'])
            self.assertEqual(value['response_request_id_source'], 'ENVELOPE')

    def file_client(self):
        with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
            client = RemoteFileClient(profile())
        client.opener = Mock()
        return client

    def test_file_actual_local_auth_headers_not_only_profile_credentials_are_filtered(self):
        with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
            client = RemoteFileClient(envelope_profile())
        client.opener = Mock()
        client.local_auth = Mock()  # Mock only the request-specific signing capability.
        client.local_auth.headers.return_value = {'X-Avernet-Principal': 'local-principal-value'}
        for code in (200, 403):
            reply = FileReply(b'content', code)
            reply.headers = {'x-request-id': 'local-principal-value'}
            client.opener.open.return_value = reply
            if code == 200:
                _, record = client.download('s1', 'f1')
            else:
                with self.assertRaises(FileTransportError) as caught:
                    client.download('s1', 'f1')
                record = caught.exception.request_records[0]
            self.assertTrue(record['response_header_request_id_rejected'])
            self.assertNotIn('local-principal-value', json.dumps(record))
        for operation in ('metadata', 'upload'):
            status = 200 if operation == 'metadata' else 202
            client.opener.open.return_value = FileReply({'code': 20000, 'message': 'OK',
                'request_id': 'local-principal-value', 'data': {'file_id': 'f1', 'status': 'pending'}}, status)
            client.prepared[('s1', 'f1')] = {'size': 1, 'mime_type': 'text/plain', 'target': {
                'method': 'PUT', 'mode': 'single',
                'upload_url': client.base+'/api/v1/collaboration/sessions/s1/files/f1/content'}}
            with self.subTest(operation=operation), self.assertRaises(FileTransportError) as caught:
                if operation == 'metadata':
                    client.metadata('s1', 'f1')
                else:
                    client.upload('s1', 'f1', b'x')
            self.assertTrue(caught.exception.request_records[0]['response_envelope_request_id_rejected'])
            self.assertNotIn('local-principal-value', json.dumps(caught.exception.request_records))
        actual = client.opener.open.call_args.args[0]
        self.assertEqual(actual.get_header('X-avernet-principal'), 'local-principal-value')

    def test_share_token_echo_is_filtered_from_success_and_failure_headers(self):
        client = self.file_client()
        client.share_targets['test-share-token'] = client.base + client._shared_path('test-share-token')
        for status in (200, 403):
            reply = FileReply(b'payload', status)
            reply.headers = {'x-request-id': 'test-share-token'}
            client.opener.open.return_value = reply
            with patch.object(client, 'probe_share_login_required',
                              return_value=({'status': 'EXPECTED_LOGIN_DENIAL'}, {})):
                if status == 200:
                    _, record = client.shared_download('test-share-token')
                else:
                    with self.assertRaises(FileTransportError) as caught:
                        client.shared_download('test-share-token')
                    record = caught.exception.request_records[0]
            self.assertTrue(record['response_header_request_id_rejected'])
            self.assertNotIn('test-share-token', json.dumps(record))
            request = client.opener.open.call_args.args[0]
            self.assertIn('Cookie', request.headers)
            self.assertNotIn('test-share-token', request.headers)

    def test_file_absence_envelope_ids_follow_same_validation_and_precedence(self):
        with patch.dict('os.environ', {'BCS_E2E_COOKIE': 'test-cookie'}):
            client = RemoteFileClient(envelope_profile())
        client.opener = Mock()
        for shared in (False, True):
            for returned in ('file-body-id', 'invalid id', 'test-share-token' if shared else 'test-cookie'):
                reply = FileReply({'code': 40400, 'message': 'Not found', 'request_id': returned,
                    'data': {'error_code': 'shared_file_not_found' if shared else 'session_file_not_found'}}, 404)
                reply.headers = {'x-request-id': 'file-header-id'}
                client.opener.open.return_value = reply
                def verify():
                    return client.verify_shared_deleted('test-share-token') if shared else client.verify_deleted('s1', 'f1')
                with self.subTest(shared=shared, returned=returned):
                    if returned == 'file-body-id':
                        body, record = verify()
                        self.assertIsNone(body)
                        self.assertEqual(record['response_request_id'], returned)
                        self.assertEqual(record['response_request_id_source'], 'ENVELOPE')
                        self.assertTrue(record['response_request_id_conflict'])
                    else:
                        with self.assertRaises(FileTransportError) as caught:
                            verify()
                        record = caught.exception.request_records[0]
                        self.assertEqual(record['response_request_id'], 'file-header-id')
                        self.assertTrue(record['response_envelope_request_id_rejected'])
                        self.assertNotIn(returned, json.dumps(record))

    def test_file_absence_owned_principal_and_private_response_context_do_not_escape(self):
        client = self.file_client()
        client.local_auth = Mock()
        client.local_auth.headers.return_value = {'X-Avernet-Principal': 'local-principal-value'}
        client.opener.open.return_value = FileReply({'code': 40400, 'message': 'Not found',
            'request_id': 'local-principal-value', 'data': {'error_code': 'session_file_not_found'}}, 404)
        with self.assertRaises(FileTransportError) as caught:
            client.verify_deleted('s1', 'f1')
        self.assertNotIn('local-principal-value', json.dumps(caught.exception.request_records))
        client.opener.open.return_value = FileReply(b'payload')
        response = client._send('GET', '/test', operation='test')
        self.assertIn('local-principal-value', response.sensitive_values)
        self.assertNotIn('local-principal-value', repr(response))
        self.assertNotIn('local-principal-value', json.dumps(response.record))

    def test_failure_projection_rejects_unbounded_new_id_fields(self):
        with tempfile.TemporaryDirectory() as tmp, Ledger(Path(tmp)/'run.sqlite', 'run', 'a'*64, create=True) as ledger:
            evidence = CorrelatedEvidence(ledger, 'PRE-01', 1)
            error = WriteTransportError('failure').with_record({'operation': 'group.read',
                'submitted_request_id': 'r1', 'response_header_request_id': 'bad\nvalue',
                'response_envelope_request_id': 'z'*129})
            evidence.record_failure(error)
            self.assertNotIn('response_header_request_id', evidence[0])
            self.assertNotIn('response_envelope_request_id', evidence[0])
            self.assertTrue(evidence[0]['response_header_request_id_rejected'])


if __name__ == '__main__':
    unittest.main()
