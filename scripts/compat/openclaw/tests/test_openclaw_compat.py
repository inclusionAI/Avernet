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
from run_matrix import clear_selected_results, selected_versions  # noqa: E402
from run_one import (  # noqa: E402
    apply_skipped_phase_status,
    resolve_runtime_sdk,
    stage_runtime_plugin,
    status_from_phases,
)


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
    def test_skipped_runtime_is_incomplete(self) -> None:
        self.assertEqual(
            apply_skipped_phase_status("PASS_WITH_WARNINGS", ["runtime"]),
            "INCOMPLETE_SKIPPED_RUNTIME",
        )

    def test_skipped_optional_typecheck_does_not_degrade_status(self) -> None:
        self.assertEqual(
            apply_skipped_phase_status("PASS", ["typecheck"]),
            "PASS",
        )

    def test_reports_source_type_drift_as_a_non_blocking_warning(self) -> None:
        self.assertEqual(
            status_from_phases(
                {
                    "install": {"ok": True},
                    "sdk_imports": {"ok": True},
                    "typecheck": {"ok": False},
                    "runtime": {"ok": True, "llm_request_count": 1},
                }
            ),
            "PASS_WITH_WARNINGS",
        )

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


class MatrixSetupArtifactTest(unittest.TestCase):
    def test_selected_version_cleanup_removes_all_previous_run_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / "results"
            selected = results / "2026.4.21"
            selected.mkdir(parents=True)
            for filename in (
                "result.json",
                "mock-llm-ready.json",
                "mock-llm-requests.jsonl",
                "mock-bcs-ready.json",
                "mock-bcs-result.json",
                "mock-bcs-frames.jsonl",
                "runner.log",
            ):
                (selected / filename).write_text("stale\n", encoding="utf-8")

            unselected = results / "2026.3.28"
            unselected.mkdir()
            (unselected / "result.json").write_text("preserved\n", encoding="utf-8")

            clear_selected_results(results, ["2026.4.21"])

            self.assertFalse(selected.exists())
            self.assertTrue((unselected / "result.json").is_file())

    def test_discovery_failure_does_not_reuse_previous_metadata_or_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            stale_result = output / "results/2026.3.28/result.json"
            stale_result.parent.mkdir(parents=True)
            stale_result.write_text(
                json.dumps({"openclaw_version": "2026.3.28", "status": "PASS", "phases": {}}),
                encoding="utf-8",
            )
            (output / "discovery.json").write_text(
                json.dumps({"floor": "2026.3.28", "latest": "2026.3.28", "versions": ["2026.3.28"]}),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_DIR / "run_matrix.py"),
                    "--plugin-dir",
                    str(PLUGIN_DIR),
                    "--output-dir",
                    str(output),
                    "--versions-file",
                    str(root / "missing-versions.json"),
                    "--dist-tags-file",
                    str(root / "missing-tags.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            discovery = json.loads((output / "discovery.json").read_text(encoding="utf-8"))
            summary = json.loads((output / "reports/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(discovery["versions"], [])
            self.assertEqual(summary["tested_count"], 0)
            self.assertIsNotNone(summary["setup_error"])

    def test_plugin_build_failure_does_not_report_stale_selected_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin = root / "plugin"
            tools = plugin / "node_modules/.bin"
            tools.mkdir(parents=True)
            for tool in ("tsc", "tshy"):
                (tools / tool).write_text("", encoding="utf-8")
            (plugin / "package.json").write_text(
                json.dumps(
                    {
                        "name": "failing-compat-plugin",
                        "version": "1.0.0",
                        "openclaw": {"compat": {"pluginApi": ">=2026.3.28"}},
                        "scripts": {"build": "node -e \"process.exit(7)\""},
                    }
                ),
                encoding="utf-8",
            )
            versions = root / "versions.json"
            tags = root / "tags.json"
            versions.write_text(json.dumps(["2026.3.28"]), encoding="utf-8")
            tags.write_text(json.dumps({"latest": "2026.3.28"}), encoding="utf-8")

            output = root / "output"
            stale_result = output / "results/2026.3.28/result.json"
            stale_result.parent.mkdir(parents=True)
            stale_result.write_text(
                json.dumps({"openclaw_version": "2026.3.28", "status": "PASS", "phases": {}}),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_DIR / "run_matrix.py"),
                    "--plugin-dir",
                    str(plugin),
                    "--output-dir",
                    str(output),
                    "--versions-file",
                    str(versions),
                    "--dist-tags-file",
                    str(tags),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            summary = json.loads((output / "reports/summary.json").read_text(encoding="utf-8"))
            self.assertFalse(stale_result.exists())
            self.assertEqual(summary["tested_count"], 0)
            self.assertEqual(summary["missing_versions"], ["2026.3.28"])
            self.assertIsNotNone(summary["setup_error"])


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


class RuntimePluginIsolationTest(unittest.TestCase):
    def test_runtime_sdk_resolves_from_the_selected_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source-plugin"
            install = root / "host"
            (source / "dist/esm").mkdir(parents=True)
            (source / "dist/esm/index.js").write_text("export default {};\n", encoding="utf-8")
            (source / "dist/node_modules").mkdir()
            (source / "dist/node_modules/self").symlink_to(source, target_is_directory=True)
            (source / "package.json").write_text(
                json.dumps({"name": "compat-plugin", "type": "module"}),
                encoding="utf-8",
            )
            (source / "openclaw.plugin.json").write_text("{}\n", encoding="utf-8")
            (source / "node_modules/openclaw").mkdir(parents=True)
            (source / "node_modules/openclaw/package.json").write_text(
                json.dumps({"name": "openclaw", "version": "0.0.0-stub"}),
                encoding="utf-8",
            )

            selected_openclaw = install / "node_modules/openclaw"
            selected_openclaw.mkdir(parents=True)
            (selected_openclaw / "package.json").write_text(
                json.dumps(
                    {
                        "name": "openclaw",
                        "version": "2026.7.1-2",
                        "exports": {"./plugin-sdk/core": "./core.js"},
                    }
                ),
                encoding="utf-8",
            )
            (selected_openclaw / "core.js").write_text("export {};\n", encoding="utf-8")

            runtime_plugin = stage_runtime_plugin(source, install)
            resolved = resolve_runtime_sdk(runtime_plugin, install, root / "logs")

            self.assertFalse((runtime_plugin / "node_modules").exists())
            self.assertFalse((runtime_plugin / "dist/node_modules").exists())
            self.assertEqual(Path(resolved), (selected_openclaw / "core.js").resolve())


class MockBcsTest(unittest.TestCase):
    def run_scenario(self, *, include_delta: bool) -> dict:
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
const includeDelta = process.argv[2] === 'true';
ws.on('open', () => ws.send(JSON.stringify({type:'req',id:'connect-1',method:'bot.connect',params:{bot_id:'openclaw-compat-bot'}})));
ws.on('message', raw => {
  const frame = JSON.parse(raw.toString());
  if (frame.type === 'req' && frame.method === 'chat.send') {
    ws.send(JSON.stringify({type:'res',id:frame.id,ok:true,payload:{run_id:'compat-run-1'}}));
    ws.send(JSON.stringify({type:'event',event:'agent',payload:{run_id:'compat-run-1'},seq:1}));
    if (includeDelta) {
      ws.send(JSON.stringify({type:'event',event:'chat.event',payload:{run_id:'compat-run-1',state:'delta',message:{content:[{type:'text',text:'OPENCLAW_COMPAT_OK'}]}},seq:2}));
    }
    ws.send(JSON.stringify({type:'event',event:'chat.event',payload:{run_id:'compat-run-1',state:'final',message:{content:[{type:'text',text:'OPENCLAW_COMPAT_OK'}]}},seq:3}));
  }
});
"""
                client = subprocess.Popen(
                    ["node", "-e", script, info["ws_url"], str(include_delta).lower()],
                    cwd=PLUGIN_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                payload = wait_for_json(result, server)
                return payload
            finally:
                if client and client.poll() is None:
                    client.terminate()
                    client.wait(timeout=5)
                if server.poll() is None:
                    server.terminate()
                    server.wait(timeout=5)

    def test_accepts_ack_delta_and_one_final_reply(self) -> None:
        payload = self.run_scenario(include_delta=True)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["observations"]["chatDeltas"], 1)
        self.assertEqual(payload["observations"]["chatFinals"], 1)

    def test_rejects_final_without_delta(self) -> None:
        payload = self.run_scenario(include_delta=False)
        self.assertFalse(payload["ok"], payload)
        self.assertIn("without a preceding delta", payload["reason"])


if __name__ == "__main__":
    unittest.main()
