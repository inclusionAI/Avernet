from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.bench.case_contract import _fallback_contract  # noqa: E402
from clawevolve_plan.bench.template_builder import (  # noqa: E402
    _checks_markdown,
    render_templates,
)
from clawevolve_plan.integration.clawweb import (  # noqa: E402
    ClawWebClient,
    ClawWebResponseError,
    post_step_report,
)
from clawevolve_plan.pipeline.bench_flow import (  # noqa: E402
    prepare_bench_artifacts,
)
from clawevolve_plan.pipeline.existing import _existing_plan_result  # noqa: E402
from clawevolve_plan.pipeline.generation import (  # noqa: E402
    begin_generation,
    complete_generation,
)
from clawevolve_plan.pipeline.invocation import (  # noqa: E402
    build_invocation_identity,
)
from clawevolve_plan.pipeline.runner import _handle_existing_plan  # noqa: E402
from clawevolve_plan.pipeline.upload import (  # noqa: E402
    skipped_dual_domain_upload_result,
)
from clawevolve_plan.spec.renderer import (  # noqa: E402
    render_goal_markdown,
    render_markdown,
)


def _identity(fingerprint: str = "fingerprint-a") -> dict[str, str]:
    return {
        "schema_version": "clawevolve.plan-invocation.v1",
        "fingerprint": fingerprint,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _complete_upload(manifest: dict) -> dict:
    domains = {}
    for split in ("train", "test"):
        package = manifest["split_packages"][split]
        domains[split] = {
            "enabled": True,
            "status": "published",
            "domain_id": f"domain-{split}",
            "owner_user_id": "owner-1",
            "base_url": "https://clawweb.example",
            "published": True,
            "verified": True,
            "zip_sha256": package["zip_sha256"],
            "expected_template_names": package["template_names"],
        }
    return {
        "schema_version": "clawevolve.clawweb-dual-domain-upload.v1",
        "enabled": True,
        "required": True,
        "status": "published",
        "published": True,
        "verified": True,
        "domains": domains,
        "train_domain_id": "domain-train",
        "test_domain_id": "domain-test",
    }


def _cached_plan(output: Path, *, identity: dict | None = None) -> dict:
    plan = {
        "bot_id": "bot-1",
        "cases": [
            {
                "case_id": "case-1",
                "source_session_id": "session-1",
                "query": "/run-one",
                "case_type": "bad",
            },
            {
                "case_id": "case-2",
                "source_session_id": "session-2",
                "query": "/run-two",
                "case_type": "bad",
            },
        ],
    }
    _, _, _, manifest = render_templates(plan, output)
    _write_json(output / "clawbench_manifest.json", manifest)

    objective: dict = {}
    spec: dict = {}
    (output / "objective.md").write_text(
        render_goal_markdown(objective), encoding="utf-8"
    )
    (output / "spec-v0.md").write_text(render_markdown(spec), encoding="utf-8")
    _write_json(output / "objective.json", objective)
    _write_json(output / "spec-v0.json", spec)

    manifest_by_case = {item["case_id"]: item for item in manifest["templates"]}
    for case in plan["cases"]:
        item = manifest_by_case[case["case_id"]]
        case["template_id"] = item["id"]
        case["split"] = item["split"]
    contracts = [
        _fallback_contract(case, "improve", {"intent_text": "improve"})
        for case in plan["cases"]
    ]
    _write_json(output / "case_contracts.json", {"contracts": contracts})
    _write_json(
        output / "case_contract_audit.json",
        {
            "schema_version": "clawevolve.case-contract.v1",
            "status": "validated",
            "contract_count": len(contracts),
            "batches": [
                {
                    "batch": 1,
                    "status": "validated",
                    "case_ids": [item["case_id"] for item in contracts],
                }
            ],
        },
    )
    _write_json(
        output / "input_manifest.json",
        {"items": [], "invocation_identity": identity or {}},
    )
    _write_json(
        output / "clawweb_upload_result.json",
        skipped_dual_domain_upload_result(manifest=manifest),
    )
    if identity is not None:
        complete_generation(output, invocation_identity=identity)
    return manifest


def _execute_checks(checks: list[dict], transcript: list, workspace: Path) -> dict:
    namespace: dict = {}
    exec(_checks_markdown(checks), namespace)
    return namespace["grade"](transcript, str(workspace))


class InvocationAndGenerationGuardTests(unittest.TestCase):
    def test_same_task_with_changed_goal_has_different_cache_identity(self):
        common = {
            "plan_source_path": "",
            "run_dir": "/missing",
            "evolve_results_dir": "",
            "discovery_notes": "",
            "target": [],
            "clawweb_url": "https://clawweb.example",
            "skip_clawweb_report": False,
        }
        first = build_invocation_identity(
            argparse.Namespace(goal="成功率达到80%", **common), task_id="EV-1"
        )
        second = build_invocation_identity(
            argparse.Namespace(goal="成功率达到90%", **common), task_id="EV-1"
        )
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_changed_diagnose_source_digest_has_different_cache_identity(self):
        with tempfile.TemporaryDirectory(prefix="plan-source-digest-") as td:
            run_dir = Path(td)
            source = run_dir / "plan-source.json"
            source.write_text('{"version": 1}', encoding="utf-8")
            args = argparse.Namespace(
                plan_source_path="",
                run_dir=str(run_dir),
                evolve_results_dir="",
                discovery_notes="",
                target=[],
                clawweb_url="",
                skip_clawweb_report=True,
                goal="",
            )
            first = build_invocation_identity(args, task_id="EV-1")
            source.write_text('{"version": 2}', encoding="utf-8")
            second = build_invocation_identity(args, task_id="EV-1")
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_running_generation_is_never_reused(self):
        with tempfile.TemporaryDirectory(prefix="plan-running-state-") as td:
            output = Path(td)
            identity = _identity()
            _cached_plan(output, identity=identity)
            begin_generation(output, invocation_identity=identity)
            self.assertIsNone(
                _existing_plan_result(output, expected_identity=identity)
            )

    def test_fresh_generation_removes_old_derived_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="plan-generation-clean-") as td:
            output = Path(td)
            for name in (
                "objective.md",
                "spec-v0.json",
                "clawbench_train_dataset.zip",
                "clawweb_upload_result.json",
            ):
                (output / name).write_text("stale", encoding="utf-8")
            (output / "templates").mkdir()
            (output / "templates" / "old.md").write_text("stale", encoding="utf-8")
            (output / "clawevolve-plan.log").write_text("keep", encoding="utf-8")

            begin_generation(output, invocation_identity=_identity())

            self.assertFalse((output / "objective.md").exists())
            self.assertFalse((output / "templates").exists())
            self.assertTrue((output / "clawevolve-plan.log").is_file())
            state = json.loads(
                (output / "plan_generation_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "running")


class BenchPipelineGuardTests(unittest.TestCase):
    def test_single_source_group_continues_with_train_only_split(self):
        plan = {
            "cases": [
                {
                    "case_id": "case-1",
                    "source_session_id": "same-session",
                    "query": "one",
                },
                {
                    "case_id": "case-2",
                    "source_session_id": "same-session",
                    "query": "two",
                },
            ]
        }
        args = argparse.Namespace(
            goal="improve",
            task_id="EV-1",
            skip_clawweb_report=True,
            overwrite=False,
        )
        def build_contracts(**kwargs):
            cases = kwargs["cases"]
            self.assertEqual({case["split"] for case in cases}, {"train"})
            return {
                "contracts": [
                    {"case_id": case["case_id"], "template_id": case["template_id"]}
                    for case in cases
                ]
            }

        with (
            tempfile.TemporaryDirectory(prefix="plan-single-source-") as td,
            patch(
                "clawevolve_plan.pipeline.bench_flow.build_case_contracts",
                side_effect=build_contracts,
            ) as contract_agent,
            patch(
                "clawevolve_plan.pipeline.bench_flow._render_templates",
                return_value=(
                    Path(td) / "templates",
                    Path(td) / "dataset.zip",
                    ["task-1", "task-2"],
                    {"split_packages": {"train": {}, "test": {}}},
                ),
            ),
            patch(
                "clawevolve_plan.pipeline.bench_flow._ensure_clawweb_domains",
                return_value={"enabled": False, "status": "skipped", "domains": {}},
            ),
            patch("clawevolve_plan.pipeline.bench_flow._record_bench_artifacts"),
        ):
            prepare_bench_artifacts(args, plan, Path(td), task_id="EV-1")
        contract_agent.assert_called_once()

    def test_cached_split_zip_tampering_blocks_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-zip-") as td:
            output = Path(td)
            _cached_plan(output)
            with (output / "clawbench_train_dataset.zip").open("ab") as handle:
                handle.write(b"tampered")
            self.assertIsNone(_existing_plan_result(output))

    def test_contract_schema_tampering_blocks_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-contract-schema-") as td:
            output = Path(td)
            _cached_plan(output)
            path = output / "case_contracts.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["contracts"][0]["task_contract"].pop("required_outcomes")
            _write_json(path, payload)
            self.assertIsNone(_existing_plan_result(output))

    def test_contract_manifest_identity_drift_blocks_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-contract-") as td:
            output = Path(td)
            _cached_plan(output)
            payload = json.loads(
                (output / "case_contracts.json").read_text(encoding="utf-8")
            )
            payload["contracts"][0]["template_id"] = "wrong-template"
            _write_json(output / "case_contracts.json", payload)
            self.assertIsNone(_existing_plan_result(output))


    def test_missing_template_digest_blocks_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-digest-") as td:
            output = Path(td)
            manifest = _cached_plan(output)
            manifest["templates"][0].pop("content_sha256", None)
            _write_json(output / "templates" / "manifest.json", manifest)
            _write_json(output / "clawbench_manifest.json", manifest)
            self.assertIsNone(_existing_plan_result(output))

    def test_duplicate_split_audit_item_blocks_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-split-audit-") as td:
            output = Path(td)
            _cached_plan(output)
            audit_path = output / "bench_split.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["items"][1] = dict(audit["items"][0])
            _write_json(audit_path, audit)
            self.assertIsNone(_existing_plan_result(output))

    def test_split_package_relative_paths_drift_blocks_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-package-") as td:
            output = Path(td)
            manifest = _cached_plan(output)
            manifest["split_packages"]["train"]["relative_paths"] = [
                "opt/not-the-template.md"
            ]
            _write_json(output / "templates" / "manifest.json", manifest)
            _write_json(output / "clawbench_manifest.json", manifest)
            self.assertIsNone(_existing_plan_result(output))

    def test_aggregate_zip_digest_drift_blocks_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-aggregate-") as td:
            output = Path(td)
            manifest = _cached_plan(output)
            manifest["aggregate_zip_sha256"] = "0" * 64
            _write_json(output / "templates" / "manifest.json", manifest)
            _write_json(output / "clawbench_manifest.json", manifest)
            self.assertIsNone(_existing_plan_result(output))

    def test_online_cache_rejects_fallback_contract_audit(self):
        with tempfile.TemporaryDirectory(prefix="plan-cache-fallback-") as td:
            output = Path(td)
            identity = _identity()
            identity["skip_clawweb_report"] = False
            _cached_plan(output, identity=identity)
            audit_path = output / "case_contract_audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["status"] = "fallback"
            audit["batches"][0]["status"] = "fallback"
            _write_json(audit_path, audit)
            self.assertIsNone(
                _existing_plan_result(output, expected_identity=identity)
            )

    def test_domain_repair_updates_json_and_markdown_together(self):
        with tempfile.TemporaryDirectory(prefix="plan-domain-repair-") as td:
            output = Path(td)
            identity = _identity()
            manifest = _cached_plan(output, identity=identity)
            args = argparse.Namespace(skip_clawweb_report=False)
            with patch(
                "clawevolve_plan.pipeline.runner.upload_bench_domains",
                return_value=_complete_upload(manifest),
            ):
                result = _handle_existing_plan(
                    args=args,
                    task_id="EV-1",
                    step_id="STEP-1",
                    output_dir=output,
                    step_reporter=lambda *a, **k: {"status": "ok"},
                    invocation_identity=identity,
                )

            self.assertEqual(result["status"], "already_exists")
            for name in ("objective.md", "spec-v0.md"):
                text = (output / name).read_text(encoding="utf-8")
                self.assertIn("domain-train", text)
                self.assertIn("domain-test", text)
            objective = json.loads(
                (output / "objective.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                objective["clawweb_domains"]["domains"]["test"]["domain_id"],
                "domain-test",
            )


class AutomatedCheckGuardTests(unittest.TestCase):
    def test_checks_use_real_transcript_and_response_evidence(self):
        transcript = [
            {
                "type": "message",
                "message": {"role": "assistant", "content": "verified result"},
            }
        ]
        with tempfile.TemporaryDirectory(prefix="plan-auto-check-") as td:
            scores = _execute_checks(
                [
                    {"type": "transcript_present"},
                    {"type": "response_contains", "text": "verified"},
                    {"type": "response_not_contains", "text": "fabricated"},
                ],
                transcript,
                Path(td),
            )
        self.assertEqual(list(scores.values()), [1.0, 1.0, 1.0])
        with tempfile.TemporaryDirectory(prefix="plan-auto-empty-") as td:
            empty_scores = _execute_checks(
                [{"type": "transcript_present"}], [], Path(td)
            )
        self.assertEqual(list(empty_scores.values()), [0.0])

    def test_file_check_rejects_workspace_escape(self):
        with tempfile.TemporaryDirectory(prefix="plan-auto-workspace-") as td:
            workspace = Path(td) / "workspace"
            workspace.mkdir()
            outside = Path(td) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            scores = _execute_checks(
                [{"type": "file_contains", "path": "../secret.txt", "text": "secret"}],
                [],
                workspace,
            )
        self.assertEqual(list(scores.values()), [0.0])


class ClawWebResponseGuardTests(unittest.TestCase):
    def test_successful_html_response_is_reported_as_contract_error(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit=-1):
                return b"<!doctype html><html>login</html>"

        client = ClawWebClient(base_url="https://clawweb.example")
        client.opener.open = Mock(return_value=Response())
        with self.assertRaisesRegex(ClawWebResponseError, "HTML instead of JSON"):
            client.get_domain("owner", "domain")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_step_report_html_contract_error_is_not_retried(self):
        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit=-1):
                return b"<!doctype html><html>login</html>"

        opener = Mock()
        opener.open.return_value = Response()
        with patch(
            "clawevolve_plan.integration.clawweb._report_opener",
            return_value=opener,
        ), patch("clawevolve_plan.integration.clawweb.time.sleep") as sleep:
            result = post_step_report(
                "EV-1", "STEP-1", status="succeeded", summary="done"
            )
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["error_category"], "response_contract_error")
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_domain_endpoint_rejects_empty_json_object(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit=-1):
                return b"{}"

        client = ClawWebClient(base_url="https://clawweb.example")
        client.opener.open = Mock(return_value=Response())
        with self.assertRaisesRegex(
            ClawWebResponseError, "non-empty object"
        ):
            client.get_domain("owner", "domain")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_domain_endpoint_rejects_unrecognized_object(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit=-1):
                return b'{"ok":true}'

        client = ClawWebClient(base_url="https://clawweb.example")
        client.opener.open = Mock(return_value=Response())
        with self.assertRaisesRegex(
            ClawWebResponseError, "recognizable domain metadata"
        ):
            client.get_domain("owner", "domain")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_json_array_response_remains_supported(self):
        raw = '[{"templateName":"task-1"}]'
        parsed = ClawWebClient._decode_json_response("GET", "/templates", 200, raw)
        self.assertIsInstance(parsed, list)


if __name__ == "__main__":
    unittest.main()
