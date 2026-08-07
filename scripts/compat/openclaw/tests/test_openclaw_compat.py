from __future__ import annotations

import json
from argparse import Namespace
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_DIR = REPO_ROOT / "src/bcs/crates/plugins/openclaw-channel-bcn"
sys.path.insert(0, str(TOOL_DIR))

from discover_versions import discover, select_versions  # noqa: E402
from report import write_reports  # noqa: E402
from run_matrix import selected_versions  # noqa: E402
from run_one import status_from_phases  # noqa: E402


def wait_for_json(path: Path, process: subprocess.Popen[str], timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            raise AssertionError(f"process exited early with {process.returncode}")
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


class DiscoverVersionsTest(unittest.TestCase):
    def test_filters_beta_but_keeps_official_repair_suffixes(self) -> None:
        versions = [
            "2026.7.1-2",
            "2026.3.22",
            "2026.3.28-beta.1",
            "2026.3.28",
            "2026.4.1",
            "2026.7.1-beta.6",
            "2026.7.1",
            "2026.7.1-1",
            "2026.7.1-2",
            "2026.7.2-beta.1",
        ]
        self.assertEqual(
            select_versions(versions, floor="2026.3.28", latest="2026.7.1-2"),
            ["2026.3.28", "2026.4.1", "2026.7.1", "2026.7.1-1", "2026.7.1-2"],
        )

    def test_rejects_explicit_beta_versions(self) -> None:
        with self.assertRaisesRegex(ValueError, "only exact non-beta"):
            selected_versions(Namespace(version=["2026.7.2-beta.1"]), {"versions": []})

    def test_reads_floor_from_plugin_api_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package.json"
            versions = root / "versions.json"
            tags = root / "tags.json"
            package.write_text(
                json.dumps(
                    {
                        "name": "compat-plugin",
                        "version": "1.0.0",
                        "peerDependencies": {"openclaw": ">=2026.1.1"},
                        "openclaw": {"compat": {"pluginApi": ">=2026.3.28"}},
                    }
                ),
                encoding="utf-8",
            )
            versions.write_text(json.dumps(["2026.3.28", "2026.4.1-beta.1", "2026.4.1"]), encoding="utf-8")
            tags.write_text(json.dumps({"latest": "2026.4.1", "beta": "2026.4.2-beta.1"}), encoding="utf-8")
            payload = discover(package_file=package, versions_file=versions, dist_tags_file=tags)
            self.assertEqual(payload["floor"], "2026.3.28")
            self.assertEqual(payload["versions"], ["2026.3.28", "2026.4.1"])

    def test_rejects_an_unstable_latest_dist_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package.json"
            versions = root / "versions.json"
            tags = root / "tags.json"
            package.write_text(
                json.dumps({"openclaw": {"compat": {"pluginApi": ">=2026.3.28"}}}),
                encoding="utf-8",
            )
            versions.write_text(json.dumps(["2026.3.28", "2026.4.1-beta.1"]), encoding="utf-8")
            tags.write_text(json.dumps({"latest": "2026.4.1-beta.1"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a stable release"):
                discover(package_file=package, versions_file=versions, dist_tags_file=tags)


class ResultStatusTest(unittest.TestCase):
    def test_reports_llm_pipeline_failure_separately(self) -> None:
        self.assertEqual(
            status_from_phases(
                {
                    "install": {"ok": True},
                    "sdk_imports": {"ok": True},
                    "typecheck": {"ok": True},
                    "runtime": {"ok": False, "llm_request_count": 0},
                }
            ),
            "FAIL_LLM_PIPELINE",
        )


class ReportTest(unittest.TestCase):
    def test_writes_all_report_formats_and_detects_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            reports = root / "reports"
            discovery = root / "discovery.json"
            for version, status in (("2026.3.28", "PASS"), ("2026.4.1", "FAIL_RUNTIME")):
                target = results / version
                target.mkdir(parents=True)
                (target / "result.json").write_text(
                    json.dumps(
                        {
                            "openclaw_version": version,
                            "status": status,
                            "duration_seconds": 1,
                            "phases": {
                                "install": {"ok": True},
                                "sdk_imports": {"ok": True},
                                "typecheck": {"ok": True},
                                "runtime": {"ok": status == "PASS", "error": "boom"},
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            stale = results / "2026.2.1"
            stale.mkdir(parents=True)
            (stale / "result.json").write_text(
                json.dumps({"openclaw_version": "2026.2.1", "status": "PASS", "phases": {}}),
                encoding="utf-8",
            )
            discovery.write_text(
                json.dumps(
                    {
                        "floor": "2026.3.28",
                        "latest": "2026.4.1",
                        "versions": ["2026.3.28", "2026.4.1"],
                    }
                ),
                encoding="utf-8",
            )
            summary = write_reports(results_dir=results, output_dir=reports, discovery_file=discovery)
            self.assertFalse(summary["compatible"])
            self.assertEqual(summary["tested_count"], 2)
            self.assertEqual(summary["status_counts"], {"FAIL_RUNTIME": 1, "PASS": 1})
            for artifact in ("summary.json", "summary.md", "junit.xml", "report.html"):
                self.assertTrue((reports / artifact).is_file(), artifact)

    def test_setup_failure_is_reported_in_all_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            discovery = root / "discovery.json"
            discovery.write_text(
                json.dumps({"floor": None, "latest": None, "versions": []}),
                encoding="utf-8",
            )
            summary = write_reports(
                results_dir=root / "results",
                output_dir=root / "reports",
                discovery_file=discovery,
                setup_error="npm metadata unavailable",
            )
            self.assertFalse(summary["compatible"])
            self.assertIn("npm metadata unavailable", (root / "reports/summary.md").read_text(encoding="utf-8"))
            self.assertIn("SETUP_ERROR", (root / "reports/junit.xml").read_text(encoding="utf-8"))
            self.assertIn("npm metadata unavailable", (root / "reports/report.html").read_text(encoding="utf-8"))


class MockLlmTest(unittest.TestCase):
    def test_serves_streaming_openai_compatible_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "ready.json"
            requests = root / "requests.jsonl"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(TOOL_DIR / "mock_llm.py"),
                    "--ready-file",
                    str(ready),
                    "--requests-file",
                    str(requests),
                    "--response-text",
                    "COMPAT_TEST_REPLY",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                info = wait_for_json(ready, process)
                request = urllib.request.Request(
                    f"{info['base_url']}/v1/chat/completions",
                    data=json.dumps({"model": "compat-model", "messages": [], "stream": True}).encode(),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read().decode()
                self.assertIn("COMPAT_TEST_REPLY", body)
                self.assertIn("data: [DONE]", body)
                self.assertEqual(len(requests.read_text(encoding="utf-8").splitlines()), 1)
            finally:
                process.terminate()
                process.wait(timeout=5)


class MockBcsTest(unittest.TestCase):
    def test_accepts_connect_and_validates_one_final_reply(self) -> None:
        if not (PLUGIN_DIR / "node_modules/ws").is_dir():
            self.skipTest("plugin node_modules/ws is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "ready.json"
            result = root / "result.json"
            frames = root / "frames.jsonl"
            server = subprocess.Popen(
                [
                    "node",
                    str(PLUGIN_DIR / "test/compat/mock_bcs.mjs"),
                    "--ready-file",
                    str(ready),
                    "--result-file",
                    str(result),
                    "--frames-file",
                    str(frames),
                    "--timeout-ms",
                    "5000",
                ],
                cwd=PLUGIN_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                text=True,
            )
            client: subprocess.Popen[str] | None = None
            try:
                info = wait_for_json(ready, server)
                script = r"""
const WebSocket = require('ws');
const ws = new WebSocket(process.argv[1]);
ws.on('open', () => ws.send(JSON.stringify({type:'req',id:'connect-1',method:'bot.connect',params:{bot_id:'openclaw-compat-bot'}})));
ws.on('message', raw => {
  const frame = JSON.parse(raw.toString());
  if (frame.type === 'req' && frame.method === 'chat.send') {
    ws.send(JSON.stringify({type:'res',id:frame.id,ok:true,payload:{run_id:'compat-run-1'}}));
    ws.send(JSON.stringify({type:'event',event:'agent',payload:{run_id:'compat-run-1'},seq:1}));
    ws.send(JSON.stringify({type:'event',event:'chat.event',payload:{run_id:'compat-run-1',state:'final',message:{content:[{type:'text',text:'OPENCLAW_COMPAT_OK'}]}},seq:2}));
  }
});
"""
                client = subprocess.Popen(
                    ["node", "-e", script, info["ws_url"]],
                    cwd=PLUGIN_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                payload = wait_for_json(result, server)
                self.assertTrue(payload["ok"], payload)
                self.assertEqual(payload["observations"]["chatFinals"], 1)
            finally:
                if client and client.poll() is None:
                    client.terminate()
                    client.wait(timeout=5)
                if server.poll() is None:
                    server.terminate()
                    server.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
