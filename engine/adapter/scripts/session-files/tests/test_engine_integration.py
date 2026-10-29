"""Real CLI -> TCP -> Engine router/service tests; run with Backend's venv."""

import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "session-file-publish/scripts/teamclaw-session-file"
DEPENDENCIES = ("uvicorn", "httpx", "fastapi", "fastapi_injector", "injector", "engine")


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in DEPENDENCIES),
                     "Requires Backend .venv and PYTHONPATH=engine/adapter/src")
class EngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        import httpx
        import uvicorn
        from engine.community.api.session_files.router import router
        from engine.community.core.session_files.export_service import (
            SessionFileExportService,
        )
        from engine.community.core.session_files.service import SessionFileService
        from fastapi import FastAPI
        from fastapi_injector import attach_injector
        from injector import Injector, InstanceProvider

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        service = SessionFileService(workspace_root_provider=lambda: self.root)

        class NoExternalExport:
            def __getattr__(self, name):
                raise AssertionError("Small inline files must not call external export")

        exporter = SessionFileExportService(session_file_service=service, export_client=NoExternalExport())
        injector = Injector()
        injector.binder.bind(SessionFileService, to=InstanceProvider(service))
        injector.binder.bind(SessionFileExportService, to=InstanceProvider(exporter))
        app = FastAPI()
        attach_injector(app, injector)
        app.include_router(router)
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        self.url = "http://127.0.0.1:" + str(listener.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="error", access_log=False,
            loop="asyncio", http="h11", ws="none", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [listener]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.server.started, "Real Engine HTTP server did not start")
        self.http = httpx.Client(base_url=self.url, trust_env=False, timeout=5)
        self.addCleanup(self.http.close)
        self.session = "agent:bot:chat:exact/a ?&=中文"

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.assertFalse(self.thread.is_alive(), "Engine HTTP server did not stop")

    def publish(self, path):
        return subprocess.run(
            [sys.executable, str(CLI), "publish", path, "--session-key", self.session,
             "--engine-url", self.url],
            cwd=self.root.parent, capture_output=True, text=True, timeout=10, check=False)

    def test_register_list_content_delete_source_retained(self):
        source = self.root / "reports/报告.txt"
        source.parent.mkdir()
        source.write_bytes(b"completed generated file")
        result = self.publish("reports/报告.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertTrue(response["ok"])
        self.assertEqual(response["code"], "published")
        view = response["file"]
        self.assertEqual(view["availability"], "ready")
        self.assertEqual(view["display_name"], "报告.txt")
        self.assertEqual(view["size_bytes"], source.stat().st_size)
        self.assertIsInstance(view["uploaded_at"], str)
        params = {"sessionKey": self.session}
        listed = self.http.get("/api/session-files", params=params)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), {"files": [view]})
        self.assertEqual(self.http.get("/api/session-files", params={"sessionKey": "other"}).json(), {"files": []})
        resource = "/api/session-files/" + view["resource_id"]
        self.assertEqual(self.http.get(resource + "/content", params={"sessionKey": "other"}).status_code, 404)
        repeated = self.publish("reports/报告.txt")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["file"]["resource_id"], view["resource_id"])
        source.write_bytes(b"changed original")
        content = self.http.get(resource + "/content", params=params)
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content, b"completed generated file")
        self.assertEqual(self.http.delete(resource, params={"sessionKey": "other"}).status_code, 404)
        deleted = self.http.delete(resource, params=params)
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])
        self.assertEqual(source.read_bytes(), b"changed original")
        self.assertEqual(self.http.get("/api/session-files", params=params).json(), {"files": []})
        self.assertEqual(self.http.get(resource + "/content", params=params).status_code, 404)
        self.assertNotIn("completed generated file", result.stdout + result.stderr)

    def test_engine_rejects_missing_directory_and_symlink_via_cli(self):
        (self.root / "directory").mkdir()
        (self.root / "source.txt").write_text("private content")
        (self.root / "link.txt").symlink_to(self.root / "source.txt")
        for path in ("missing.txt", "directory", "link.txt"):
            with self.subTest(path=path):
                result = self.publish(path)
                self.assertEqual(result.returncode, 4, result.stderr)
                direct = self.http.post("/api/session-files", params={"sessionKey": self.session},
                                        json={"workspace_relative_path": path})
                self.assertIn(direct.status_code, (400, 503))
                self.assertEqual(json.loads(result.stdout), {
                    "ok": False, "code": "engine_http_error", "http_status": direct.status_code})
                self.assertNotIn("private content", result.stdout + result.stderr)
        self.assertEqual(self.http.get("/api/session-files", params={"sessionKey": self.session}).json(), {"files": []})


if __name__ == "__main__":
    unittest.main()
