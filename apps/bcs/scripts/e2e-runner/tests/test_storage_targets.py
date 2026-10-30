"""Returned upload/download URLs are data, never an authority to leak credentials."""
from __future__ import annotations

import contextlib
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.file_transport import FileTransportError, RemoteFileClient
from bcs_e2e.profile import ConfigError, load, validate
from bcs_e2e.upload_targets import UploadTargetError, proxy_target
from test_file_transport import Reply, envelope


class StorageTargetTests(unittest.TestCase):
    def setUp(self):
        self.profile = load(ROOT / "profiles/preprod.example.json")
        self.profile["entrypoint"]["origin"] = "https://gateway.example.test"
        self.profile["file_transfer"] = {"allowed_storage_origins": ["https://storage.example.test"]}
        self.proxy = self.profile["entrypoint"]["origin"] + "/sessions/s-1/files/f-1/content"
        self.signed = "https://storage.example.test/private-signature-path?Signature=issued-secret&partNumber=1"

    @contextlib.contextmanager
    def client(self, *responses):
        opener = Mock()
        opener.open.side_effect = responses
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "private-cookie"}), \
             patch("bcs_e2e.file_transport.build_opener", return_value=opener):
            yield RemoteFileClient(self.profile), opener

    def target(self, url):
        return {"file_id": "f-1", "mode": "single", "method": "PUT", "upload_url": url}

    def prepare(self, client, size=7):
        return client.prepare("s-1", {"file_name": "x.txt", "size": size, "mime_type": "text/plain"})

    def test_signed_single_target_is_used_verbatim_without_product_auth_or_json_assumptions(self):
        with self.client(Reply(self.target(self.signed), 201), Reply(b"storage-xml-result", 200)) as (client, opener):
            self.prepare(client)
            uploaded, record = client.upload("s-1", "f-1", b"payload")
        request = opener.open.call_args_list[1].args[0]
        self.assertEqual(request.full_url, self.signed)
        self.assertEqual(request.data, b"payload")
        self.assertEqual(request.headers["Content-type"], "text/plain")
        self.assertEqual(request.headers["Content-length"], "7")
        self.assertNotIn("Cookie", request.headers)
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("X-request-id", request.headers)
        self.assertTrue(uploaded["upload_accepted"])
        self.assertNotIn("status", uploaded)  # No invented product Pending/Ready response.
        self.assertNotIn("issued-secret", json.dumps(record))
        self.assertNotIn("private-signature-path", json.dumps(record))
        self.assertIsNone(record["parts"][0]["submitted_request_id"])

    def test_tiny_multipart_proxy_and_direct_targets_preserve_slice_lengths_and_part_numbers(self):
        for proxy in (True, False):
            urls = [self.proxy + f"?part={n}" if proxy else self.signed.replace("partNumber=1", f"partNumber={n}")
                    for n in (1, 2, 3)]
            target = {"file_id": "f-1", "mode": "multipart", "method": "PUT", "part_size": 3,
                      "part_count": 3, "parts": [{"part_number": n, "upload_url": url} for n, url in enumerate(urls, 1)]}
            uploads = [Reply(envelope({"file_id": "f-1", "status": "pending"}), 202) if proxy else Reply(b"", 204)
                       for _ in urls]
            with self.subTest(proxy=proxy), self.client(Reply(target, 201), *uploads) as (client, opener):
                self.prepare(client)
                data, record = client.upload("s-1", "f-1", b"payload")
            requests = [call.args[0] for call in opener.open.call_args_list[1:]]
            self.assertEqual([r.full_url for r in requests], urls)
            self.assertEqual([r.data for r in requests], [b"pay", b"loa", b"d"])
            self.assertEqual([r.headers["Content-length"] for r in requests], ["3", "3", "1"])
            self.assertEqual(["Cookie" in r.headers for r in requests], [proxy] * 3)
            self.assertTrue(data["upload_accepted"])
            self.assertEqual(record["part_count"], 3)

    def test_all_parts_are_validated_before_first_put(self):
        target = {"file_id": "f-1", "mode": "multipart", "method": "PUT", "part_size": 4,
                  "part_count": 2, "parts": [{"part_number": 1, "upload_url": self.signed},
                                            {"part_number": 2, "upload_url": "https://unapproved.example.test/secret"}]}
        with self.client(Reply(target, 201)) as (client, opener):
            data, _ = self.prepare(client)
            self.assertEqual(data["file_id"], "f-1")  # ID remains available for ledger/cleanup.
            with self.assertRaises(FileTransportError):
                client.upload("s-1", "f-1", b"payload")
            self.assertEqual(opener.open.call_count, 1)

    def test_invalid_upload_shapes_and_scope_never_fall_back_to_fixed_proxy(self):
        targets = [{"file_id": "f-1"}, {**self.target(self.signed), "method": "POST"},
                   {**self.target(self.signed), "upload_url": "/relative-path"},
                   {"file_id": "f-1", "method": "PUT", "mode": "multipart", "part_size": 1,
                    "part_count": 99999, "parts": []}]
        for target in targets:
            with self.subTest(target=target), self.client(Reply(target, 201)) as (client, opener):
                self.prepare(client)
                with self.assertRaises((FileTransportError, UploadTargetError)):
                    client.upload("s-1", "f-1", b"payload")
                self.assertEqual(opener.open.call_count, 1)
        with self.client() as (client, opener):
            with self.assertRaises(FileTransportError):
                client.upload("another-session", "f-1", b"payload")
            opener.open.assert_not_called()

    def test_download_redirect_strips_auth_and_redacts_storage_paths(self):
        redirect = Reply(b"", 302)
        redirect.headers["Location"] = self.signed
        with self.client(redirect, Reply(b"payload")) as (client, opener):
            content, record = client.download("s-1", "f-1")
        first, second = [call.args[0] for call in opener.open.call_args_list]
        self.assertIn("Cookie", first.headers)
        self.assertNotIn("Cookie", second.headers)
        self.assertEqual(second.full_url, self.signed)
        self.assertEqual(content, b"payload")
        self.assertNotIn("issued-secret", json.dumps(record))
        self.assertNotIn("private-signature-path", json.dumps(record))
        self.assertEqual([hop["http_status"] for hop in record["hops"]], [302, 200])

    def test_redirect_allowlist_denial_and_hop_limit(self):
        for target in ("https://unknown.example.test/path", "http://storage.example.test/path",
                       "https://user:secret@storage.example.test/path"):
            redirect = Reply(b"", 302)
            redirect.headers["Location"] = target
            with self.subTest(target=target), self.client(redirect) as (client, opener):
                with self.assertRaises(FileTransportError):
                    client.download("s-1", "f-1")
                self.assertEqual(opener.open.call_count, 1)
        redirect = Reply(b"", 302)
        redirect.headers["Location"] = self.signed
        with self.client(*[redirect] * 4) as (client, opener):
            with self.assertRaisesRegex(FileTransportError, "hop limit"):
                client.download("s-1", "f-1")
            self.assertEqual(opener.open.call_count, 4)
        with self.client(Reply(b"private-error", 403)) as (client, opener):
            with self.assertRaises(FileTransportError):
                client.download("s-1", "f-1")
            self.assertEqual(opener.open.call_count, 1)

    def test_returned_share_target_must_match_token_and_is_not_rewritten(self):
        target = self.profile["entrypoint"]["origin"] + "/sessions/shared-file/content?token=issued-share"
        data = {"share_token": "issued-share", "share_url": target, "expires_at": 1}
        login = {"actionType": "LOGIN", "buserviceErrorCode": "USER_NOT_LOGIN", "decisionBy": "ACE"}
        with self.client(Reply(data, 201), Reply(login), Reply(b"payload")) as (client, opener):
            client.share("s-1", "f-1")
            client.shared_download("issued-share")
        request = opener.open.call_args_list[-1].args[0]
        self.assertEqual(request.full_url, target)
        self.assertEqual(request.headers["Cookie"], "private-cookie")
        for invalid in (target.replace("gateway", "other-gateway"), target.replace("issued-share", "different-token")):
            with self.client(Reply(envelope({**data, "share_url": invalid}, 201), 201)) as (client, opener):
                client.share("s-1", "f-1")
                with self.assertRaises(FileTransportError):
                    client.shared_download("issued-share")
                self.assertEqual(opener.open.call_count, 1)

    def test_proxy_scope_accepts_encoded_colon_but_rejects_separators_and_other_targets(self):
        expected = "/api/v1/collaboration/sessions/group%3Asession/files/f1/content"
        prefix = self.profile["entrypoint"]["origin"]
        for path in (expected, expected.replace("%3A", ":"), expected.replace("%3A", "%3a")):
            self.assertTrue(proxy_target(prefix + path, expected, None))
        for path in (expected.replace("%3A", "%2F"), expected.replace("%3A", "%253A"),
                     expected.replace("/f1/", "/f2/"), expected + "?token=foreign",
                     expected.replace("/files/", "/%2e%2e/files/"), expected + "?part=1"):
            self.assertFalse(proxy_target(prefix + path, expected, None))

    def test_exact_proxy_with_raw_colon_retains_auth_and_uses_returned_url(self):
        url = self.profile["entrypoint"]["origin"] + "/sessions/group:session/files/f-1/content"
        with self.client(Reply(self.target(url), 201),
                         Reply(envelope({"file_id": "f-1", "status": "pending"}), 202)) as (client, opener):
            client.prepare("group:session", {"file_name": "x", "size": 7, "mime_type": "text/plain"})
            client.upload("group:session", "f-1", b"payload")
            request = opener.open.call_args_list[1].args[0]
            self.assertEqual(request.full_url, url)
            self.assertIn("Cookie", request.headers)

    def test_profile_requires_exact_bounded_storage_origins(self):
        validate(self.profile)
        for origins in (["https://*.example.test"], ["https://storage.example.test/path"],
                        ["https://user:secret@storage.example.test"],
                        ["https://storage.example.test", "https://storage.example.test:443"],
                        ["http://storage.example.test"], "not-a-list"):
            profile = copy.deepcopy(self.profile)
            profile["file_transfer"]["allowed_storage_origins"] = origins
            with self.subTest(origins=origins), self.assertRaises(ConfigError):
                validate(profile)


if __name__ == "__main__":
    unittest.main()
