from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.file_transport import FileTransportError, RemoteFileClient


class Reply:
    def __init__(self, body, code=200):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.code = code
        self.headers = {}
        self.closed = False

    def read(self, _limit=-1):
        return self.body

    def close(self):
        self.closed = True


def envelope(data, status=200):
    return {"code": {200: 20000, 201: 20100, 202: 20000}[status], "message": "OK", "data": data, "request_id": "r"}


class FileTransportTests(unittest.TestCase):
    def profile(self):
        root = Path(__file__).resolve().parents[1]
        profile = json.loads((root / "profiles/local.singlebox.example.json").read_text())
        profile["file_transfer"] = {"allowed_storage_origins": ["https://storage.example.test"]}
        profile["human"]["user_id"] = "human-local"
        profile["actors"]["driver"]["bot_id"] = "driver-local"
        profile["actors"]["driver"]["owner_id"] = "human-local"
        profile["actors"]["member"]["bot_id"] = "member-local"
        profile["actors"]["member"]["owner_id"] = "human-local"
        return profile

    def test_anonymous_route_probe_sends_no_cookie_and_requires_bcs_not_found(self):
        payload = {'code': 40400, 'message': 'not found', 'request_id': 'request-invalid',
                   'data': {'error_code': 'shared_file_not_found', 'details': {}}}
        opener = Mock(); opener.open.return_value = Reply(payload, 404)
        with patch.dict(os.environ, {'BCS_E2E_LOCAL_COOKIE': 'private-cookie'}), \
             patch('bcs_e2e.file_transport.build_opener', return_value=opener):
            result, record = RemoteFileClient(self.profile()).probe_anonymous_route()
        self.assertEqual(result['status'], 'TOKEN_VALIDATION_REACHED')
        self.assertEqual(record['http_status'], 404)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.method, 'GET')
        for header in ('Cookie', 'Authorization', 'Origin', 'Referer'):
            self.assertNotIn(header, request.headers)
        self.assertEqual(opener.open.call_count, 1)
        self.assertNotIn('e2e-invalid-preflight', record['path'])

    def test_remote_file_transport_uses_verified_tls_context(self):
        context = object()
        opener = Mock()
        with patch.dict(os.environ, {"BCS_E2E_LOCAL_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.tls_context", return_value=context), \
                patch("bcs_e2e.file_transport.HTTPSHandler") as https_handler, \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener) as build:
            RemoteFileClient(self.profile())
        self.assertIs(https_handler.call_args.kwargs["context"], context)
        self.assertIs(build.call_args.args[0], https_handler.return_value)

    def test_anonymous_probe_rejects_http_200_login_redirect_and_unrelated_404(self):
        responses = [Reply({'buserviceErrorCode': 'USER_NOT_LOGIN', 'actionType': 'LOGIN'}, 200),
                     Reply(b'login', 302), Reply(b'not found', 404),
                     Reply({'code': 40400, 'message': 'missing', 'request_id': 'r',
                            'data': {'error_code': 'unrelated'}}, 404)]
        for response in responses:
            opener = Mock(); opener.open.return_value = response
            with self.subTest(status=response.code), \
                 patch.dict(os.environ, {'BCS_E2E_LOCAL_COOKIE': 'private-cookie'}), \
                 patch('bcs_e2e.file_transport.build_opener', return_value=opener):
                with self.assertRaises(FileTransportError) as caught:
                    RemoteFileClient(self.profile()).probe_anonymous_route()
                self.assertEqual(caught.exception.request_records[0]['http_status'], response.code)
                self.assertEqual(opener.open.call_count, 1)
                self.assertNotIn('private-cookie', str(caught.exception.request_records))

    def test_json_raw_file_lifecycle_paths_and_redaction(self):
        replies = [
            Reply(envelope({"file_id": "f-1", "mode": "single", "method": "PUT",
                            "upload_url": self.profile()["entrypoint"]["origin"] +
                            "/api/v1/collaboration/sessions/s-1/files/f-1/content"}, 201), 201),
            Reply(envelope({"file_id": "f-1", "status": "pending"}), 202),
            Reply(b"payload", 200),
            Reply(envelope({"share_url": "http://127.0.0.1:21000/sessions/shared-file/content?token=secret",
                            "share_token": "secret", "expires_at": 1}, 201), 201),
        ]
        opener = type("FakeOpener", (), {})()
        calls = []
        def open_request(request, timeout=None):
            calls.append(request)
            return replies.pop(0)
        opener.open = open_request
        with patch.dict(os.environ, {"BCS_E2E_LOCAL_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(self.profile())
            prepared, _ = client.prepare("s-1", {"file_name": "x.txt", "size": 7, "mime_type": "text/plain"})
            uploaded, _ = client.upload("s-1", "f-1", b"payload")
            downloaded, download_record = client.download("s-1", "f-1")
            shared, _ = client.share("s-1", "f-1")
        self.assertEqual(prepared["file_id"], "f-1")
        self.assertEqual(uploaded["status"], "pending")
        self.assertEqual(downloaded, b"payload")
        self.assertEqual(shared["share_token"], "secret")
        self.assertNotIn("private-cookie", download_record["path"])
        self.assertEqual([request.method for request in calls], ["POST", "PUT", "GET", "POST"])
        self.assertIn("/api/v1/collaboration/sessions/s-1/files/f-1/content", calls[2].full_url)
        self.assertTrue(all("/openapi/" not in request.full_url for request in calls))
        self.assertEqual(calls[3].data, b"{}")
        self.assertEqual(download_record["operation"], "file.download")
        self.assertTrue(download_record["submitted_request_id"].startswith("e2e-file-"))

    def test_file_success_codes_match_http_including_accepted_upload(self):
        with self.client() as (client, _):
            for http, code, valid in ((200, 20000, True), (201, 20100, True), (202, 20000, True),
                                      (201, 20000, False), (200, 20100, False), (202, 20200, False)):
                raw = json.dumps({"code": code, "message": "OK", "request_id": "server-file",
                                  "data": {"file_id": "f-1"}}).encode()
                with self.subTest(http=http, code=code):
                    if valid:
                        data, record = client._decode_json_data(raw, {"http_status": http})
                        self.assertEqual(data["file_id"], "f-1")
                        self.assertEqual(record["response_request_id"], "server-file")
                    else:
                        with self.assertRaises(FileTransportError):
                            client._decode_json_data(raw, {"http_status": http})

    def test_external_signed_url_is_blocked_before_auth_leak(self):
        opener = type("FakeOpener", (), {})()
        opener.open = unittest.mock.Mock()
        with patch.dict(os.environ, {"BCS_E2E_LOCAL_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(self.profile())
            with self.assertRaisesRegex(FileTransportError, "egress allowlist"):
                client.upload_to_signed_url("https://oss.example.test/signed", b"payload")
        opener.open.assert_not_called()

    @contextlib.contextmanager
    def client(self, *replies):
        opener = Mock()
        opener.open.side_effect = replies
        with patch.dict(os.environ, {"BCS_E2E_LOCAL_COOKIE": "private-cookie"}), \
             patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            yield RemoteFileClient(self.profile()), opener

    def test_all_file_operations_use_declared_internal_paths(self):
        with self.client(*[Reply(envelope({"deleted": True}), code) for code in (200, 200, 200, 200)]) as (client, opener):
            client.list("s-1")
            client.metadata("s-1", "f-1")
            client.complete("s-1", "f-1")
            client.delete("s-1", "f-1")
        calls = [call.args[0] for call in opener.open.call_args_list]
        expected = [("GET", "/api/v1/collaboration/sessions/s-1/files"),
                    ("GET", "/api/v1/collaboration/sessions/s-1/files/f-1"),
                    ("POST", "/api/v1/collaboration/sessions/s-1/files/f-1/complete"),
                    ("DELETE", "/api/v1/collaboration/sessions/s-1/files/f-1")]
        from urllib.parse import urlsplit
        self.assertEqual([(c.method, urlsplit(c.full_url).path) for c in calls], expected)
        contract = (Path(__file__).resolve().parents[3] / "api-contracts/v1/internal.yaml").read_text()
        for _method, path in expected:
            declared = path.replace("s-1", "{session_id}").replace("f-1", "{file_id}")
            self.assertIn("  " + declared + ":", contract)

    def test_shared_content_uses_token_only_and_redacts_query(self):
        missing = {"code": 40400, "message": "not found", "request_id": "req-1",
                   "data": {"error_code": "shared_file_not_found"}}
        with self.client(Reply(b"payload"), Reply(missing, 404)) as (client, opener):
            body, record = client.shared_download("issued-secret")
            absent, absence = client.verify_shared_deleted("issued-secret")
        self.assertEqual(body, b"payload")
        self.assertIsNone(absent)
        for call in opener.open.call_args_list:
            request = call.args[0]
            self.assertNotIn("Cookie", request.headers)
            self.assertNotIn("Authorization", request.headers)
            self.assertTrue(request.full_url.endswith("?token=issued-secret"))
        self.assertNotIn("issued-secret", json.dumps([record, absence]))

    def test_preprod_shared_download_uses_login_denial_then_cookie(self):
        profile = self.profile()
        profile["environment"]["tier"] = "preprod"
        profile["entrypoint"]["kind"] = "gateway"
        profile["entrypoint"]["origin"] = "https://gateway.example.test"
        login = {
            "actionType": "LOGIN",
            "buserviceErrorCode": "USER_NOT_LOGIN",
            "decisionBy": "ACE",
        }
        opener = Mock()
        opener.open.side_effect = [Reply(login, 200), Reply(b"payload", 200)]
        with patch.dict(os.environ, {"BCS_E2E_LOCAL_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(profile)
            client.share_targets["issued-secret"] = client.base + client._shared_path("issued-secret")
            body, record = client.shared_download("issued-secret")
        self.assertEqual(body, b"payload")
        self.assertEqual(record["response_projection"]["auth_mode"], "SSO_AND_SHARE_TOKEN")
        self.assertEqual(record["access_checks"][0]["response_projection"]["denial_kind"], "ACE_LOGIN")
        calls = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(calls[0].full_url, calls[1].full_url)
        self.assertNotIn("Cookie", calls[0].headers)
        self.assertEqual(calls[1].headers["Cookie"], "private-cookie")

    def test_preprod_shared_deletion_verification_uses_cookie(self):
        profile = self.profile()
        profile["environment"]["tier"] = "preprod"
        profile["entrypoint"]["kind"] = "gateway"
        profile["entrypoint"]["origin"] = "https://gateway.example.test"
        missing = {"error": "NOT_FOUND", "message": "shared file not found"}
        opener = Mock()
        opener.open.return_value = Reply(missing, 404)
        with patch.dict(os.environ, {"BCS_E2E_LOCAL_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(profile)
            absent, record = client.verify_shared_deleted("issued-secret")
        self.assertIsNone(absent)
        self.assertEqual(record["operation"], "file.shared_verify_deleted")
        self.assertTrue(record["response_envelope_request_id_absent"])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.headers["Cookie"], "private-cookie")
        self.assertNotIn("issued-secret", json.dumps(record))

    def test_preprod_file_deletion_verification_uses_station_json(self):
        profile = self.profile()
        profile["environment"]["tier"] = "preprod"
        profile["entrypoint"]["kind"] = "gateway"
        profile["entrypoint"]["origin"] = "https://gateway.example.test"
        profile["auth"] = {"header_env": {"Cookie": "BCS_E2E_TEST_COOKIE"}}
        missing = {"error": "FILE_NOT_FOUND", "message": "not found: file f-1"}
        opener = Mock(); opener.open.return_value = Reply(missing, 404)
        with patch.dict(os.environ, {"BCS_E2E_TEST_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(profile)
            absent, record = client.verify_deleted("s-1", "f-1")
        self.assertIsNone(absent)
        self.assertEqual(record["http_status"], 404)
        self.assertTrue(record["response_envelope_request_id_absent"])
        request = opener.open.call_args.args[0]
        self.assertIn("/sessions/s-1/files/f-1", request.full_url)
        self.assertEqual(request.headers["Cookie"], "private-cookie")

    def test_station_file_deletion_records_header_correlation(self):
        profile = self.profile()
        profile["environment"]["tier"] = "preprod"
        profile["entrypoint"]["kind"] = "gateway"
        profile["entrypoint"]["origin"] = "https://gateway.example.test"
        profile["auth"] = {"header_env": {"Cookie": "BCS_E2E_TEST_COOKIE"}}
        missing = {"error": "FILE_NOT_FOUND", "message": "not found: file f-1"}
        reply = Reply(missing, 404)
        reply.headers = {"x-request-id": "station-req-1"}
        opener = Mock(); opener.open.return_value = reply
        with patch.dict(os.environ, {"BCS_E2E_TEST_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(profile)
            absent, record = client.verify_deleted("s-1", "f-1")
        self.assertIsNone(absent)
        self.assertEqual(record["response_header_request_id"], "station-req-1")
        self.assertTrue(record["response_envelope_request_id_absent"])

    def test_station_file_deletion_drift_lands_correlation_before_raise(self):
        profile = self.profile()
        profile["environment"]["tier"] = "preprod"
        profile["entrypoint"]["kind"] = "gateway"
        profile["entrypoint"]["origin"] = "https://gateway.example.test"
        profile["auth"] = {"header_env": {"Cookie": "BCS_E2E_TEST_COOKIE"}}
        opener = Mock(); opener.open.return_value = Reply(
            {"error": "FILE_NOT_FOUND", "message": "drifted"}, 404)
        with patch.dict(os.environ, {"BCS_E2E_TEST_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(profile)
            with self.assertRaises(FileTransportError) as caught:
                client.verify_deleted("s-1", "f-1")
        record = caught.exception.request_records[0]
        self.assertTrue(record["response_envelope_request_id_absent"])
        self.assertEqual(record["http_status"], 404)

    def test_preprod_share_binding_uses_reviewed_cross_origin_for_metadata(self):
        profile = self.profile()
        profile["environment"]["tier"] = "preprod"
        profile["entrypoint"]["kind"] = "gateway"
        profile["entrypoint"]["origin"] = "https://gateway.example.test"
        profile["auth"] = {"header_env": {"Cookie": "BCS_E2E_TEST_COOKIE"}}
        profile["file_transfer"] = {
            "allowed_share_origins": ["https://share.example.test"],
            "allowed_storage_origins": ["https://storage.example.test"],
        }
        opener = Mock()
        login = {"actionType": "LOGIN", "buserviceErrorCode": "USER_NOT_LOGIN",
                 "decisionBy": "ACE"}
        opener.open.side_effect = [Reply(login, 200), Reply(b'{"file_id":"f-1"}')]
        with patch.dict(os.environ, {"BCS_E2E_TEST_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            client = RemoteFileClient(profile)
            client.register_share_target(
                "issued-secret",
                "https://share.example.test/sessions/shared-file/content?token=issued-secret")
            data, record = client.shared_metadata("issued-secret")
        self.assertEqual(data, {"file_id": "f-1"})
        request = opener.open.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).netloc, "share.example.test")
        self.assertEqual(urlsplit(request.full_url).path, "/sessions/shared-file")
        self.assertEqual(len(opener.open.call_args_list), 2)
        self.assertNotIn("Cookie", opener.open.call_args_list[0].args[0].headers)
        self.assertEqual(opener.open.call_args_list[1].args[0].headers["Cookie"], "private-cookie")
        self.assertNotIn("issued-secret", json.dumps(record))

    def test_redirect_download_reports_final_content_status(self):
        redirect = Reply(b"", 303)
        redirect.headers["Location"] = (
            "https://storage.example.test/object?signature=private")
        with self.client(redirect, Reply(b"payload", 200)) as (client, _):
            body, record = client.shared_download("issued-secret")
        self.assertEqual(body, b"payload")
        self.assertEqual(record["http_status"], 200)
        self.assertEqual([hop["http_status"] for hop in record["hops"]], [303, 200])
        self.assertNotIn("private", json.dumps(record))

    def test_shared_metadata_absence_uses_exact_legacy_not_found(self):
        missing = b'{"error":"NOT_FOUND","message":"shared file not found"}'
        with self.client(Reply(missing, 404)) as (client, opener):
            absent, record = client.shared_metadata_verify_deleted("issued-secret")
        self.assertIsNone(absent)
        self.assertEqual(record["http_status"], 404)
        self.assertEqual(record["operation"], "file.shared_metadata_verify_deleted")
        self.assertTrue(record["response_envelope_request_id_absent"])
        self.assertEqual(record["path"], "/sessions/shared-file?[REDACTED]")
        request = opener.open.call_args.args[0]
        self.assertNotIn("Cookie", request.headers)
        self.assertNotIn("Authorization", request.headers)
        self.assertTrue(request.full_url.endswith("?token=issued-secret"))
        self.assertNotIn("issued-secret", json.dumps(record))

    def test_shared_metadata_absence_rejects_drifted_not_found(self):
        for body in (b'{"error":"NOT_FOUND","message":"other"}',
                     b'{"error":"UNAUTHORIZED","message":"shared file not found"}',
                     b"<html>"):
            with self.subTest(body=body), self.client(Reply(body, 404)) as (client, opener):
                with self.assertRaises(FileTransportError) as caught:
                    client.shared_metadata_verify_deleted("issued-secret")
                self.assertNotIn("issued-secret", str(caught.exception))
                self.assertEqual(opener.open.call_count, 1)

    def test_file_not_found_requires_the_file_error_not_a_missing_parent(self):
        for error_code in ("session_file_not_found", "session_not_found", "group_not_found"):
            response = Reply({"code": 40400, "message": "not found", "request_id": "req-1",
                              "data": {"error_code": error_code}}, 404)
            with self.subTest(error=error_code), self.client(response) as (client, _):
                if error_code == "session_file_not_found":
                    self.assertIsNone(client.verify_deleted("s-1", "f-1")[0])
                else:
                    with self.assertRaises(FileTransportError):
                        client.verify_deleted("s-1", "f-1")
            self.assertTrue(response.closed)

    def test_denial_malformed_not_found_and_wrong_envelope_fail_closed(self):
        for response in (Reply(b"proxy not found", 404), Reply(envelope({}), 404),
                         Reply(b"denied-secret", 401), Reply(b"denied-secret", 403),
                         Reply(b"denied-secret", 302)):
            with self.subTest(code=response.code), self.client(response) as (client, opener):
                with self.assertRaises(FileTransportError) as caught:
                    client.verify_deleted("s-1", "f-1")
                self.assertNotIn("denied-secret", str(caught.exception))
                self.assertEqual(opener.open.call_count, 1)
            self.assertTrue(response.closed)
        for payload in ({"file_id": "f-1"}, {"code": 40000, "message": "denied", "data": {}, "request_id": "r"}):
            with self.client(Reply(payload)) as (client, _):
                with self.assertRaises(FileTransportError):
                    client.metadata("s-1", "f-1")

    def test_transport_itself_refuses_cross_origin_or_malformed_authenticated_targets(self):
        with self.client() as (client, opener):
            for target in ("https://unapproved.example.test/path", "//unapproved.example.test/path",
                           "http://user:secret@127.0.0.1:21000/path", "file:///tmp/data",
                           "http://127.0.0.1:invalid/path", "/path#secret", "/path\nsecret"):
                with self.subTest(target=target), self.assertRaises(FileTransportError):
                    client._request("GET", target, operation="file.test")
            opener.open.assert_not_called()

    def test_bounds_and_request_echo(self):
        response = Reply(b"abcd")
        with self.client(response) as (client, opener):
            client.max_bytes = 3
            with self.assertRaises(FileTransportError):
                client.download("s-1", "f-1")
        self.assertTrue(response.closed)
        with self.client() as (client, opener):
            def reply(request, timeout):
                result = Reply(b"bytes")
                result.headers["x-request-id"] = request.headers["X-request-id"]
                return result
            opener.open.side_effect = reply
            _, record = client.download("s-1", "f-1")
        self.assertTrue(record["request_id_echoed"])


if __name__ == "__main__":
    unittest.main()
