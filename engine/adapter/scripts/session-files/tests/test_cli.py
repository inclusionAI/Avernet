"""Standalone CLI tests; no Engine imports or third-party dependencies."""

import concurrent.futures
import contextlib
import http.client
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "session-file-publish/scripts/teamclaw-session-file"
VIEW = {"resource_id": "sr_1", "display_name": "报告.txt", "size_bytes": 12,
        "availability": "ready", "uploaded_at": None}


class FakeEngine(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.calls.append((self.path, dict(self.headers),
                                  self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(self.server.status)
        self.send_header("Location", "http://remote.invalid/SECRET")
        self.end_headers()
        self.wfile.write(self.server.body)

    def do_GET(self):
        self.server.get_calls.append((self.path, dict(self.headers)))
        self.send_response(self.server.get_status)
        self.send_header("Location", "http://remote.invalid/SECRET")
        self.end_headers()
        self.wfile.write(self.server.get_body)

    def log_message(self, *args):
        pass


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = importlib.machinery.SourceFileLoader("session_file_cli", str(CLI))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.cli = importlib.util.module_from_spec(spec)
        with patch.object(sys, "dont_write_bytecode", True):
            loader.exec_module(cls.cli)

    def setUp(self):
        clean_env = dict(os.environ)
        clean_env.pop("HITL_SESSION_KEY", None)
        environment = patch.dict(os.environ, clean_env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = self.cli.main(list(args))
        return status, json.loads(out.getvalue()), err.getvalue()

    @contextlib.contextmanager
    def server(self, status=200, body=None, get_status=200, get_body=None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
        server.status, server.body, server.calls = status, body or json.dumps(VIEW).encode(), []
        server.get_status, server.get_calls = get_status, []
        server.get_body = json.dumps({"files": [VIEW]}).encode() if get_body is None else get_body
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, "http://127.0.0.1:" + str(server.server_port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_actual_executable_exact_session_and_projection_no_credentials(self):
        with self.server(body=json.dumps(dict(VIEW, token="SECRET")).encode()) as (server, url):
            env = dict(os.environ, HTTP_PROXY="http://remote.invalid", ALL_PROXY="http://remote.invalid")
            result = subprocess.run([str(CLI), "publish", "输出/report.txt", "--session-key",
                                     "agent:bot:chat:a/b ?&=中文", "--engine-url", url],
                                    capture_output=True, text=True, env=env, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "code": "published", "file": VIEW})
        path, headers, body = server.calls[0]
        self.assertEqual(urlsplit(path).path, "/api/session-files")
        self.assertEqual(parse_qs(urlsplit(path).query), {"sessionKey": ["agent:bot:chat:a/b ?&=中文"]})
        self.assertEqual(json.loads(body), {"workspace_relative_path": "输出/report.txt"})
        self.assertEqual(server.get_calls[0][0], path)
        self.assertEqual(len(server.calls), 1)
        self.assertEqual(len(server.get_calls), 1)
        self.assertFalse({k.lower() for k in server.get_calls[0][1]} & {"authorization", "cookie"})
        self.assertFalse({k.lower() for k in headers} & {"authorization", "cookie"})
        self.assertNotIn("SECRET", result.stdout + result.stderr)
        diagnostics = [json.loads(line) for line in result.stderr.splitlines()]
        self.assertEqual([item["event"] for item in diagnostics], ["request", "success", "verification_request", "verification_success", "published"])
        for item in diagnostics:
            self.assertEqual(item["system"], "engine")
            self.assertEqual(item["direction"], "out")
            self.assertEqual(item["operation"], "publish")
            self.assertEqual(item["method"], "GET" if item["event"].startswith("verification_") else "POST")
            self.assertEqual(item["path"], "/api/session-files")
            self.assertEqual(item["sessionKey"], "agent:bot:chat:a/b ?&=中文")
            self.assertEqual(item["workspace_relative_path"], "输出/report.txt")
            self.assertNotIn("session_key_hash", item)
            self.assertGreaterEqual(item["elapsed_ms"], 0)
        self.assertEqual(diagnostics[-1]["http_status"], 200)
        for key, value in VIEW.items():
            self.assertEqual(diagnostics[-1][key], value)

    def test_invalid_arguments_do_not_connect_or_echo(self):
        cases = [[], ["publish", "/SECRET", "--session-key", "s"],
                 ["publish", "a/../SECRET", "--session-key", "s"],
                 ["publish", "C:\\SECRET", "--session-key", "s"],
                 ["publish", "a", "--session-key", "s", "--SECRET"]]
        with patch.object(http.client.HTTPConnection, "connect", side_effect=AssertionError):
            for args in cases:
                with self.subTest(args=args):
                    status, data, err = self.invoke(*args)
                    self.assertEqual(status, 2)
                    self.assertEqual(data["code"], "invalid_arguments")
                    self.assertNotIn("SECRET", json.dumps(data) + err)

    def test_reject_remote_and_ambiguous_urls(self):
        for url in ["https://127.0.0.1", "http://example.com", "http://10.0.0.1",
                    "http://localhost.evil", "http://127.1", "http://2130706433",
                    "http://user:SECRET@127.0.0.1", "http://127.0.0.1/api",
                    "http://127.0.0.1?SECRET", "http://127.0.0.1#SECRET",
                    "http://127.0.0.1:0", "http://[::1%25lo0]", "\nhttp://127.0.0.1"]:
            with self.subTest(url=url), patch.object(http.client.HTTPConnection, "connect", side_effect=AssertionError):
                status, data, err = self.invoke("publish", "a", "--session-key", "s", "--engine-url", url)
                self.assertEqual((status, data["code"]), (2, "invalid_engine_url"))
                self.assertNotIn("SECRET", json.dumps(data) + err)

    def test_loopback_forms(self):
        for url, host in [("http://localhost:20003/", "127.0.0.1"),
                          ("http://127.0.0.2:1234", "127.0.0.2"), ("http://[::1]:1234", "::1")]:
            self.assertEqual(self.cli.endpoint(url)[0], host)

    def test_http_failures_and_redirects_are_sanitized(self):
        for status, code in [(301, "redirect_rejected"), (302, "redirect_rejected"),
                             (303, "redirect_rejected"), (307, "redirect_rejected"),
                             (308, "redirect_rejected"), (400, "engine_http_error"),
                             (401, "engine_http_error"), (404, "engine_http_error"),
                             (500, "engine_http_error")]:
            with self.subTest(status=status), self.server(status, b"SECRET file bytes") as (server, url):
                result, data, err = self.invoke("publish", "a", "--session-key", "s", "--engine-url", url)
                self.assertEqual((result, data["code"], data["http_status"]), (4, code, status))
                self.assertEqual(len(server.calls), 1)
                self.assertNotIn("SECRET", json.dumps(data) + err)
                diagnostic = json.loads(err.splitlines()[-1])
                self.assertEqual(diagnostic["sessionKey"], "s")
                self.assertEqual(diagnostic["workspace_relative_path"], "a")
                self.assertEqual(diagnostic["http_status"], status)
                self.assertEqual(diagnostic["code"], code)
                self.assertGreaterEqual(diagnostic["elapsed_ms"], 0)

    def test_invalid_responses(self):
        for body in [b"SECRET", b"[]", b"{}", b"null", b"x" * 65537,
                     json.dumps(dict(VIEW, size_bytes=True)).encode(),
                     json.dumps(dict(VIEW, availability="missing")).encode(),
                     json.dumps(dict(VIEW, creator_type="admin")).encode(),
                     json.dumps(dict(VIEW, creator_name={"SECRET": "value"})).encode(),
                     json.dumps(dict(VIEW, created_at={"SECRET": "value"})).encode()]:
            with self.subTest(body=body[:20]), self.server(body=body) as (_, url):
                status, data, err = self.invoke("publish", "a", "--session-key", "s", "--engine-url", url)
                self.assertEqual((status, data["code"]), (5, "invalid_response"))
                self.assertNotIn("SECRET", json.dumps(data) + err)

    def test_creation_metadata_is_allowlisted_and_old_engine_stays_compatible(self):
        self.assertEqual(self.cli.view_from_response(json.dumps(VIEW).encode()), VIEW)
        for creator in ["bot", "user", None]:
            metadata = dict(creator_type=creator, creator_name="报告助手" if creator == "bot" else "运海", created_at="2026-10-30T00:00:00Z")
            result = self.cli.view_from_response(json.dumps(dict(VIEW, **metadata, secret="SECRET")).encode())
            self.assertEqual(result, dict(VIEW, **metadata))
            self.assertNotIn("SECRET", json.dumps(result))

    def test_transport_failures_are_sanitized(self):
        for exc, code in [(TimeoutError("SECRET"), "engine_timeout"),
                          (OSError("SECRET"), "engine_unreachable"),
                          (http.client.BadStatusLine("SECRET"), "engine_unreachable")]:
            with patch.object(http.client.HTTPConnection, "request", side_effect=exc):
                status, data, err = self.invoke("publish", "a", "--session-key", "s")
                self.assertEqual((status, data["code"]), (3, code))
                self.assertNotIn("SECRET", json.dumps(data) + err)

    def test_main_reads_authoritative_environment_every_call(self):
        with self.server() as (server, url):
            for key in ("agent:a/中文 ?&=", "opaque:second"):
                with patch.dict(os.environ, {"HITL_SESSION_KEY": key}):
                    status, data, _ = self.invoke("publish", "a", "--engine-url", url)
                    self.assertEqual((status, data["code"]), (0, "published"))
                    status, data, _ = self.invoke("publish", "a", "--session-key", key, "--engine-url", url)
                    self.assertEqual((status, data["code"]), (0, "published"))
            keys = [parse_qs(urlsplit(call[0]).query)["sessionKey"][0] for call in server.calls]
            self.assertEqual(keys, ["agent:a/中文 ?&=", "agent:a/中文 ?&=", "opaque:second", "opaque:second"])
            self.assertEqual([call[0] for call in server.calls], [call[0] for call in server.get_calls])

    def test_invalid_session_context_never_connects_or_echoes(self):
        cases = [(None, None, "missing_session_key"), ("trusted", "other-SECRET", "session_key_mismatch")]
        for invalid in ("", "current", "CURRENT", "Latest", "default", "DeFaUlT", " s", "s ", "s\nSECRET", "s\x7fSECRET", "s\x85SECRET", "s\x9fSECRET"):
            cases.extend([(invalid, "manual", "invalid_session_key"),
                          (None, invalid, "invalid_session_key"),
                          ("trusted", invalid, "invalid_session_key")])
        with patch.object(http.client.HTTPConnection, "connect", side_effect=AssertionError("must not connect")):
            for runtime, explicit, code in cases:
                with self.subTest(runtime=runtime, explicit=explicit):
                    env = {} if runtime is None else {"HITL_SESSION_KEY": runtime}
                    args = ["publish", "a"]
                    if explicit is not None:
                        args += ["--session-key", explicit]
                    with patch.dict(os.environ, env, clear=True):
                        status, data, err = self.invoke(*args)
                    self.assertEqual((status, data["code"]), (2, code))
                    self.assertNotIn("SECRET", json.dumps(data) + err)

    def test_parallel_subprocess_environments_do_not_share_sessions(self):
        keys = ["agent:first/中文 ?&=", "agent:second/中文 ?&="]
        with self.server() as (server, url):
            def run(key):
                return subprocess.run([sys.executable, str(CLI), "publish", "a", "--engine-url", url],
                                      env=dict(os.environ, HITL_SESSION_KEY=key), capture_output=True,
                                      text=True, timeout=10, check=False)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, keys))
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["code"], "published")
            for calls in (server.calls, server.get_calls):
                self.assertCountEqual([parse_qs(urlsplit(call[0]).query)["sessionKey"][0] for call in calls], keys)
            for key, result in zip(keys, results):
                events = [json.loads(line) for line in result.stderr.splitlines()]
                self.assertTrue(all(event["sessionKey"] == key for event in events))

    def test_get_must_confirm_exact_resource_ready_without_retry(self):
        cases = [(200, {"files": []}, "registration_not_confirmed"),
                 (200, {"files": [dict(VIEW, resource_id="other")]}, "registration_not_confirmed"),
                 (200, {"files": [dict(VIEW, availability="missing")]}, "registration_not_confirmed"),
                 (200, {"files": [dict(VIEW, availability="changed")]}, "registration_not_confirmed"),
                 (200, {"files": [VIEW, VIEW]}, "verification_failed"),
                 (200, {}, "verification_failed"), (200, {"files": {}}, "verification_failed"),
                 (200, {"files": [None]}, "verification_failed"),
                 (200, {"files": [dict(VIEW, size_bytes=True)]}, "verification_failed"),
                 (302, {"SECRET": "value"}, "verification_failed"),
                 (404, {"SECRET": "value"}, "verification_failed"),
                 (500, {"SECRET": "value"}, "verification_failed")]
        cases += [(200, body, "verification_failed") for body in (b"SECRET", b"x" * 65537)]
        for http_status, body, code in cases:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            with self.subTest(status=http_status, body=raw[:60]), self.server(get_status=http_status, get_body=raw) as (server, url):
                status, data, err = self.invoke("publish", "a", "--session-key", "s", "--engine-url", url)
                self.assertEqual((status, data["code"]), (6, code))
                self.assertFalse(data["ok"])
                self.assertTrue(data["registration_may_have_succeeded"])
                self.assertNotIn("SECRET", json.dumps(data) + err)
                self.assertEqual((len(server.calls), len(server.get_calls)), (1, 1))
                events = [json.loads(line) for line in err.splitlines()]
                self.assertIn("success", [event["event"] for event in events])
                self.assertNotIn("published", [event["event"] for event in events])
                self.assertEqual(events[-2]["event"], "verification_failure")
                self.assertEqual(events[-2]["method"], "GET")
                self.assertEqual(events[-2]["http_status"], http_status)

    def test_get_confirmed_view_is_projected_without_extra_secrets(self):
        refreshed = dict(VIEW, size_bytes=25, creator_type="bot", creator_name="助手")
        body = json.dumps({"files": [dict(VIEW, resource_id="other", availability="missing"),
                                      dict(refreshed, signed_url="SECRET", credentials={"token": "SECRET"})]}).encode()
        with self.server(get_body=body) as (server, url):
            status, data, err = self.invoke("publish", "a", "--session-key", "s", "--engine-url", url)
        self.assertEqual((status, data), (0, {"ok": True, "code": "published", "file": refreshed}))
        self.assertNotIn("SECRET", json.dumps(data) + err)
        self.assertEqual((len(server.calls), len(server.get_calls)), (1, 1))

    def test_get_transport_failure_is_uncertain_and_not_retried(self):
        original = http.client.HTTPConnection.request
        for exc in (TimeoutError("SECRET"), OSError("SECRET"), http.client.BadStatusLine("SECRET")):
            methods = []
            def request(connection, method, *args, **kwargs):
                methods.append(method)
                if method == "GET":
                    raise exc
                return original(connection, method, *args, **kwargs)
            with self.subTest(exc=type(exc)), self.server() as (_, url):
                with patch.object(http.client.HTTPConnection, "request", request):
                    status, data, err = self.invoke("publish", "a", "--session-key", "s", "--engine-url", url)
                self.assertEqual((status, data["code"]), (6, "verification_failed"))
                self.assertTrue(data["registration_may_have_succeeded"])
                self.assertEqual(methods, ["POST", "GET"])
                self.assertNotIn("SECRET", json.dumps(data) + err)

    def test_independent_tar_install_in_workspace_with_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "publisher.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(ROOT / "session-file-publish", arcname="session-file-publish")
                bundle.add(ROOT / "README.md", arcname="README.md")
            skills = root / "actual workspace/skills"
            skills.mkdir(parents=True)
            with tarfile.open(archive) as bundle:
                # Only extract this test's locally constructed archive.
                bundle.extractall(skills, filter="data")
            self.assertTrue((skills / "README.md").is_file())
            installed = skills / "session-file-publish/scripts/teamclaw-session-file"
            self.assertTrue((installed.parents[1] / "SKILL.md").is_file())
            installed.chmod(0o644)
            with self.server() as (server, url):
                result = subprocess.run([sys.executable, str(installed), "publish", "report.txt",
                                         "--session-key", "exact:current", "--engine-url", url],
                                        cwd=root, capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["file"], VIEW)
            self.assertEqual(len(server.calls), 1)


if __name__ == "__main__":
    unittest.main()
