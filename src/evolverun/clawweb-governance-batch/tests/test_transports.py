"""Real loopback POST tests: the final mutation contract, without an external write."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawweb_batch.adapters.http import ClawWebHTTP, JsonAnalyst, JsonHTTP
from clawweb_batch.adapters.nas import NASReader
from clawweb_batch.core import API_PREFIX, instant, make_candidate
from test_batch import SOURCE, bundle, proposal, session


class HTTPContractTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        events = self.events
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                events.append((self.path, body, self.headers.get("Idempotency-Key")))
                if self.path == API_PREFIX + "/actions":
                    result = {"improvementId": 91, "version": 1, "status": "PENDING_ADMIN", "adminReviewStatus": "PENDING"}
                elif self.path == API_PREFIX + "/verification-results":
                    result = {"improvement": {"improvementId": body["improvementId"], "version": body["version"] + 1,
                                              "status": "IN_PROGRESS", "verificationStatus": "STILL_PRESENT"}}
                else:
                    self.send_error(404); return
                data = json.dumps(result).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            def do_GET(self):
                self.send_response(302); self.send_header("Location", "/must-not-follow"); self.end_headers()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def test_create_and_verify_final_http_serialization_and_receipt(self):
        client = ClawWebHTTP(self.origin, JsonHTTP(5), True)
        b = bundle(); request = make_candidate(proposal(b), b, [])
        created = client.write(request["path"], request["payload"], request["key"])
        self.assertEqual(created["status"], "PENDING_ADMIN")
        payload = {"improvementId": 91, "version": 3, "outcome": "STILL_PRESENT", "newSessionCount": 2,
                   "lastRecurrenceAt": "2026-09-28T12:00:00+08:00"}
        verified = client.write(API_PREFIX + "/verification-results", payload, "verify-91-3")
        self.assertEqual(verified["version"], 4)
        self.assertEqual(self.events[0][2], request["key"])
        self.assertEqual(self.events[1][1], payload)
        self.assertEqual(len(self.events), 2)

    def test_dry_run_never_sends_even_to_mock_backend(self):
        with self.assertRaises(PermissionError):
            ClawWebHTTP(self.origin, JsonHTTP(5), False).write(API_PREFIX + "/actions", {}, "x")
        self.assertEqual(self.events, [])

    def test_redirect_not_followed(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP_302"):
            JsonHTTP(5).request(self.origin + "/redirect")


class AnalystContractTests(unittest.TestCase):
    def test_no_tools_and_truncated_response_cannot_pass(self):
        calls = []
        class HTTP:
            def request(self, *args, **kwargs):
                calls.append(kwargs)
                return {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        analyst = JsonAnalyst({"base_url": "https://example.invalid/v1", "model": "configured-model", "api_key": "test"}, HTTP(), 12000)
        with self.assertRaisesRegex(ValueError, "truncated"):
            analyst.review({"tasks": []})
        self.assertNotIn("tools", calls[0]["body"])
        self.assertEqual(calls[0]["body"]["max_tokens"], 12000)


class CurrentNASContractTests(unittest.TestCase):
    def test_current_exact_source_gap_and_malformed_log_fail_closed(self):
        b = bundle(); source = b["signatures"][0]["signature"]["source"]
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); target = base / "prod_staff_u_openclaw_b/.openclaw/agents/main/sessions"
            target.mkdir(parents=True)
            path = target / "new-session.jsonl"
            messages = json.loads(session()["messages"])
            stamp = "2026-09-29T02:00:00Z"
            path.write_text("\n".join(json.dumps({"type": "message", "timestamp": stamp, "message": m}) for m in messages))
            ts = instant(stamp).timestamp(); os.utime(path, (ts, ts))
            reader = NASReader((base,))
            result = reader.scan_since(source, "u", "b", "2026-09-29T00:00:00Z", 100000)
            self.assertTrue(result["complete"], result["warnings"])
            self.assertEqual(len(result["tasks"]), 1)
            self.assertTrue(result["tasks"][0]["signature_ids"])
            bad = target / "partial.jsonl"; bad.write_text('{"type":'); os.utime(bad, (ts, ts))
            self.assertFalse(reader.scan_since(source, "u", "b", "2026-09-29T00:00:00Z", 100000)["complete"])

    def test_desktop_current_state_not_inferred_from_server_nas(self):
        with tempfile.TemporaryDirectory() as d:
            result = NASReader((Path(d),)).scan_since("aidesktop/agents/main", "u", "b", "2026-09-29T00:00:00Z", 100000)
            self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
