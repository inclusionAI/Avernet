from __future__ import annotations

# Test module adjusts sys.path before importing project modules.
# ruff: noqa: E402

import copy
import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.input.contract import (  # noqa: E402
    PlanSourceError,
    canonical_json,
    digest_json,
)
from clawevolve_plan.input.context import build_planning_context  # noqa: E402
from clawevolve_plan.input.resolver import (
    PlanSourceResolution,
    fetch_step_input,
    resolve_plan_source,
)  # noqa: E402
from clawevolve_plan.pipeline.fresh import _load_and_validate_inputs  # noqa: E402
from clawevolve_plan.pipeline.inputs import _validate_discovery_inputs  # noqa: E402
from clawevolve_plan.pipeline.runner import run_plan_command  # noqa: E402
from clawevolve_plan.discovery.agent import DiscoveryAgentError  # noqa: E402
from clawevolve_plan.discovery.prompt import (  # noqa: E402
    MAX_DISCOVERY_PROMPT_BYTES,
    build_discovery_prompt,
)
from scripts.resolve_plan_source import (
    _local_test_base_url,
    main as resolve_source_main,
)  # noqa: E402


def source_fixture() -> dict:
    return {
        "schema_version": "plan-source/v2",
        "generated_at": "2026-08-11T09:00:00Z",
        "source": {
            "type": "insight_improvement",
            "id": "improvement:123",
            "producer": "clawweb-insight-plan-source-adapter",
            "adapter_version": "insight-to-plan-source/v1",
            "owner_user_id": "specialist-1",
            "bot_owner_user_id": "owner-1",
            "bot_id": "bot-1",
            "version": "4",
            "frozen_at": "2026-08-11T09:00:00Z",
        },
        "problem": {
            "title": "工具失败后没有降级",
            "user_guidance": "保留可执行降级路径",
        },
        "cases": [
            {
                "case_id": "insight_123_abc_0",
                "case_type": "bad",
                "ordinal": 0,
                "session_id": "session-1",
                "task_index": 0,
                "query": "查询昨天的日志",
                "evidence": {
                    "schema_version": "session-evidence/v1",
                    "message_range": [0, 2],
                    "source_task": {"task_index": 0, "is_complete": 0},
                    "messages": [
                        {
                            "message_index": 0,
                            "role": "user",
                            "content": "查询昨天的日志",
                            "raw": {"trace": "keep"},
                        }
                    ],
                    "payload_ref": "oss://bucket/session-1.json",
                    "payload_etag": "etag-1",
                    "payload_version_id": "v1",
                },
                "analysis": {
                    "failure_class": "TOOL_FAILURE",
                    "evolution_failure_mode": "tool_execution_failure",
                    "judge_summary": "工具失败后直接结束",
                },
            }
        ],
        "analysis": {
            "case_distribution": {"total": 1, "bad": 1, "score": 0.1},
            "root_cause_clusters": [],
        },
        "planning_hints": {},
        "extensions": {"insight": {"batch_id": "batch-1"}},
    }


def descriptor(source: dict) -> dict:
    return {
            "protocolVersion": "1.2",
        "inputs": {
            "planSource": {
                "descriptorVersion": "plan-source-descriptor/v2",
                "sourceType": "insight_improvement",
                "schemaVersion": "plan-source/v2",
                "digest": digest_json(source),
                "delivery": {"type": "inline", "content": source},
            }
        },
    }


class PlanSourceResolverTests(unittest.TestCase):
    def test_case_level_failure_mode_is_required_for_every_historical_source(self):
        historical_plan = {
            "root_cause_clusters": [],
            "cases": [{"evolution_failure_mode": "case_only_failure"}],
        }
        notes = "inspected `skills/example/SKILL.md` for the selected case"

        with self.assertRaisesRegex(ValueError, "case_only_failure"):
            _validate_discovery_inputs(
                notes, ["skills/example/SKILL.md"], historical_plan
            )

        insight_plan = build_planning_context(
            source_fixture(), "/tmp/source.json"
        )
        with self.assertRaisesRegex(ValueError, "tool_execution_failure"):
            _validate_discovery_inputs(notes, ["skills/example/SKILL.md"], insight_plan)

    def test_unknown_insight_failure_mode_allows_discovery_refinement(self):
        source = source_fixture()
        source["cases"][0]["analysis"]["evolution_failure_mode"] = (
            "unknown_failure_mode"
        )
        insight_plan = build_planning_context(source, "/tmp/source.json")
        notes = (
            "inspected `skills/example/SKILL.md`; discovery refined the source placeholder "
            "to tool_parameter_error"
        )

        _validate_discovery_inputs(notes, ["skills/example/SKILL.md"], insight_plan)

    def test_ordinary_plan_failure_preserves_the_legacy_string_report(self):
        reports: list[dict] = []

        def reporter(task_id, step_id, **payload):
            reports.append({"task_id": task_id, "step_id": step_id, **payload})
            return {"status": "ok"}

        with tempfile.TemporaryDirectory(prefix="plan-legacy-failure-") as td:
            args = argparse.Namespace(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                run_dir="",
                goal="write a plan from this explicit goal",
                bot_id="bot-1",
                discovery_notes="notes",
                target=["skills/example/SKILL.md"],
                overwrite=True,
                skip_clawweb_report=False,
            )
            with patch(
                "clawevolve_plan.pipeline.runner.run_fresh_plan",
                side_effect=RuntimeError("legacy failure"),
            ):
                result = run_plan_command(args, step_reporter=reporter)

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(reports[-1]["status"], "failed")
        self.assertEqual(reports[-1]["error"], "RuntimeError: legacy failure")
        self.assertNotIn("error_detail", result.payload)

    def test_plan_source_failure_reports_structured_source_error_only(self):
        reports: list[dict] = []

        def reporter(task_id, step_id, **payload):
            reports.append({"task_id": task_id, "step_id": step_id, **payload})
            return {"status": "ok"}

        def failed_resolver(**_kwargs):
            raise PlanSourceError(
                "PLAN_SOURCE_DIGEST_MISMATCH",
                "frozen source changed",
                stage="digest_validation",
            )

        with tempfile.TemporaryDirectory(prefix="plan-source-failure-") as td:
            args = argparse.Namespace(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                run_dir="",
                goal="",
                discovery_notes="notes",
                target=["skills/example/SKILL.md"],
                overwrite=True,
                skip_clawweb_report=False,
            )
            result = run_plan_command(
                args,
                step_reporter=reporter,
                plan_source_resolver=failed_resolver,
            )

        expected = {
            "code": "PLAN_SOURCE_DIGEST_MISMATCH",
            "message": "frozen source changed",
            "stage": "digest_validation",
            "retryable": False,
        }
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(reports[-1]["status"], "failed")
        self.assertEqual(reports[-1]["error"], expected)
        self.assertEqual(result.payload["error_detail"], expected)

    def test_helper_base_url_override_is_loopback_only(self):
        self.assertEqual(
            _local_test_base_url("http://127.0.0.1:8080"), "http://127.0.0.1:8080"
        )
        with self.assertRaisesRegex(PlanSourceError, "仅允许本地回环地址"):
            _local_test_base_url("https://example.com")

    def test_step_input_uses_the_unified_endpoint_without_source_credentials(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            descriptor(source_fixture())
        ).encode("utf-8")
        opener = MagicMock()
        opener.open.return_value = response

        with patch(
            "clawevolve_plan.input.resolver.urllib.request.build_opener",
            return_value=opener,
        ):
            result = fetch_step_input("EV-1", "STEP-1")

        request = opener.open.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertTrue(
            request.full_url.endswith(
                "/api/evolve/internal/tasks/EV-1/steps/STEP-1/input"
            )
        )
        self.assertEqual(
            result["inputs"]["planSource"]["sourceType"], "insight_improvement"
        )

    def test_canonical_json_digest_matches_the_clawweb_vector(self):
        vector = {
            "z": 0.1,
            "a": [1, True, None],
            "汉": "值",
            "\ue000": "private",
            "😀": "astral",
        }
        self.assertEqual(
            canonical_json(vector),
            '{"a":[1e0,true,null],"z":1.0000000000000001e-1,"汉":"值","":"private","😀":"astral"}',
        )
        self.assertEqual(
            digest_json(vector),
            "sha256:0a9ed1f37826efcb7d2a734fa72a0b93b1b8af4dd7129aac0b73de7a7a4a014d",
        )

    def test_resolver_local_only_mode_never_fetches_missing_source(self):
        with tempfile.TemporaryDirectory(prefix="plan-source-local-only-") as td:
            with self.assertRaises(PlanSourceError) as raised:
                resolve_plan_source(
                    task_id="EV-LOCAL",
                    step_id="STEP-LOCAL",
                    evolve_results_dir=td,
                    step_input_fetcher=lambda *_args: (_ for _ in ()).throw(
                        AssertionError("local-only mode must not fetch Step Input")
                    ),
                    allow_network=False,
                )

        self.assertEqual(raised.exception.code, "PLAN_SOURCE_NETWORK_DISABLED")
        self.assertFalse(raised.exception.retryable)

    def test_resolver_atomically_writes_then_reuses_the_same_source(self):
        source = source_fixture()
        fetch_count = 0

        def first_fetch(_task_id, _step_id):
            nonlocal fetch_count
            fetch_count += 1
            return descriptor(source)

        def unexpected_fetch(_task_id, _step_id):
            raise AssertionError("local retry must not fetch Step Input")

        with tempfile.TemporaryDirectory(prefix="plan-source-") as td:
            result = resolve_plan_source(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                step_input_fetcher=first_fetch,
            )
            self.assertEqual(result.status, "written")
            self.assertEqual(result.digest, digest_json(source))
            self.assertEqual(
                result.source_path,
                (Path(td) / "EV-1" / "plan" / "input" / "source.json").resolve(),
            )
            self.assertEqual(result.source, source)
            self.assertTrue(result.descriptor_path.is_file())

            reused = resolve_plan_source(
                task_id="EV-1",
                step_id="STEP-2",
                evolve_results_dir=td,
                step_input_fetcher=unexpected_fetch,
            )
            self.assertEqual(reused.status, "reused")
            self.assertEqual(
                reused.source_path.read_text(encoding="utf-8"),
                result.source_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(fetch_count, 1)

    def test_resolver_snapshots_a_local_diagnose_source_without_network(self):
        source = source_fixture()
        source["source"].update(
            {
                "type": "diagnose",
                "id": "diagnose:EV-LOCAL",
                "producer": "clawevolve-diagnose",
            }
        )
        with tempfile.TemporaryDirectory(prefix="plan-source-diagnose-") as td:
            producer_path = Path(td) / "diagnose" / "plan-source.json"
            producer_path.parent.mkdir(parents=True)
            producer_path.write_text(
                json.dumps(source, ensure_ascii=False), encoding="utf-8"
            )

            result = resolve_plan_source(
                task_id="EV-LOCAL",
                step_id="STEP-LOCAL",
                evolve_results_dir=td,
                local_source_path=str(producer_path),
                step_input_fetcher=lambda *_args: (_ for _ in ()).throw(
                    AssertionError("Diagnose Plan Source must not fetch Step Input")
                ),
            )

            self.assertEqual(result.status, "written")
            self.assertEqual(result.source["source"]["type"], "diagnose")
            self.assertEqual(result.source_path.name, "source.json")
            self.assertEqual(
                json.loads(result.source_path.read_text(encoding="utf-8")), source
            )

            source["problem"]["title"] = "Diagnose producer changed"
            producer_path.write_text(
                json.dumps(source, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaises(PlanSourceError) as raised:
                resolve_plan_source(
                    task_id="EV-LOCAL",
                    step_id="STEP-LOCAL-2",
                    evolve_results_dir=td,
                    local_source_path=str(producer_path),
                    step_input_fetcher=lambda *_args: (_ for _ in ()).throw(
                        AssertionError("changed Diagnose Source must fail before network")
                    ),
                )
            self.assertEqual(raised.exception.code, "PLAN_SOURCE_DIGEST_MISMATCH")

    def test_resolver_archives_verified_v1_insight_snapshot_and_rebuilds_v2(self):
        legacy = source_fixture()
        legacy["schema_version"] = "plan-source/v1"
        current = source_fixture()
        with tempfile.TemporaryDirectory(prefix="plan-source-v1-migration-") as td:
            input_dir = Path(td) / "EV-1" / "plan" / "input"
            input_dir.mkdir(parents=True)
            source_path = input_dir / "source.json"
            descriptor_path = input_dir / "source-descriptor.json"
            source_path.write_text(json.dumps(legacy), encoding="utf-8")
            descriptor_path.write_text(
                json.dumps(
                    {
                        "descriptorVersion": "plan-source-descriptor/v1",
                        "sourceType": "insight_improvement",
                        "schemaVersion": "plan-source/v1",
                        "digest": digest_json(legacy),
                        "delivery": {"type": "inline"},
                    }
                ),
                encoding="utf-8",
            )
            calls = 0

            def fetcher(task_id: str, step_id: str) -> dict:
                nonlocal calls
                calls += 1
                return descriptor(current)

            resolution = resolve_plan_source(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                step_input_fetcher=fetcher,
            )

            suffix = digest_json(legacy).removeprefix("sha256:")[:12]
            self.assertEqual(calls, 1)
            self.assertEqual(resolution.source["schema_version"], "plan-source/v2")
            self.assertTrue(
                (input_dir / f"source.plan-source-v1.{suffix}.json").is_file()
            )
            self.assertTrue(
                (
                    input_dir
                    / f"source-descriptor.plan-source-v1.{suffix}.json"
                ).is_file()
            )

    def test_resolver_rejects_tampered_v1_snapshot_without_fetching(self):
        legacy = source_fixture()
        legacy["schema_version"] = "plan-source/v1"
        with tempfile.TemporaryDirectory(prefix="plan-source-v1-tampered-") as td:
            input_dir = Path(td) / "EV-1" / "plan" / "input"
            input_dir.mkdir(parents=True)
            (input_dir / "source.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )
            (input_dir / "source-descriptor.json").write_text(
                json.dumps(
                    {
                        "descriptorVersion": "plan-source-descriptor/v1",
                        "sourceType": "insight_improvement",
                        "schemaVersion": "plan-source/v1",
                        "digest": "sha256:" + "0" * 64,
                        "delivery": {"type": "inline"},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PlanSourceError, "无法安全迁移"):
                resolve_plan_source(
                    task_id="EV-1",
                    step_id="STEP-1",
                    evolve_results_dir=td,
                    step_input_fetcher=lambda *_: self.fail("must not fetch"),
                )

    def test_resolver_refuses_to_overwrite_a_modified_local_source(self):
        source = source_fixture()
        with tempfile.TemporaryDirectory(prefix="plan-source-") as td:
            first = resolve_plan_source(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                step_input_fetcher=lambda _task_id, _step_id: descriptor(source),
            )
            tampered = first.source_path.read_text(encoding="utf-8").replace(
                "工具失败后没有降级", "tampered"
            )
            first.source_path.write_text(tampered, encoding="utf-8")

            with self.assertRaises(PlanSourceError) as raised:
                resolve_plan_source(
                    task_id="EV-1",
                    step_id="STEP-2",
                    evolve_results_dir=td,
                    step_input_fetcher=lambda _task_id, _step_id: (_ for _ in ()).throw(
                        AssertionError("tampered local Source must fail before network")
                    ),
                )
            self.assertEqual(raised.exception.code, "PLAN_SOURCE_DIGEST_MISMATCH")
            self.assertIn("tampered", first.source_path.read_text(encoding="utf-8"))

    def test_resolver_refuses_to_repair_a_tampered_local_descriptor(self):
        source = source_fixture()
        with tempfile.TemporaryDirectory(prefix="plan-source-") as td:
            first = resolve_plan_source(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                step_input_fetcher=lambda _task_id, _step_id: descriptor(source),
            )
            local_descriptor = json.loads(
                first.descriptor_path.read_text(encoding="utf-8")
            )
            local_descriptor["schemaVersion"] = "plan-source/v3"
            first.descriptor_path.write_text(
                json.dumps(local_descriptor), encoding="utf-8"
            )

            with self.assertRaises(PlanSourceError) as raised:
                resolve_plan_source(
                    task_id="EV-1",
                    step_id="STEP-2",
                    evolve_results_dir=td,
                    step_input_fetcher=lambda _task_id, _step_id: (_ for _ in ()).throw(
                        AssertionError(
                            "tampered local descriptor must fail before network"
                        )
                    ),
                )
            self.assertEqual(raised.exception.code, "PLAN_SOURCE_DIGEST_MISMATCH")
            self.assertIn(
                "plan-source/v3", first.descriptor_path.read_text(encoding="utf-8")
            )

    def test_resolver_rejects_descriptor_digest_or_schema_mismatch(self):
        source = source_fixture()
        bad_digest = descriptor(source)
        bad_digest["inputs"]["planSource"]["digest"] = "sha256:" + "0" * 64
        bad_schema = copy.deepcopy(source)
        bad_schema["schema_version"] = "plan-source/v3"

        with tempfile.TemporaryDirectory(prefix="plan-source-") as td:
            with self.assertRaises(PlanSourceError) as digest_error:
                resolve_plan_source(
                    task_id="EV-1",
                    step_id="STEP-1",
                    evolve_results_dir=td,
                    step_input_fetcher=lambda _task_id, _step_id: bad_digest,
                )
            self.assertEqual(digest_error.exception.code, "PLAN_SOURCE_DIGEST_MISMATCH")

            with self.assertRaises(PlanSourceError) as schema_error:
                resolve_plan_source(
                    task_id="EV-2",
                    step_id="STEP-1",
                    evolve_results_dir=td,
                    step_input_fetcher=lambda _task_id, _step_id: descriptor(
                        bad_schema
                    ),
                )
            self.assertEqual(schema_error.exception.code, "PLAN_SOURCE_SCHEMA_INVALID")

            wrong_type = descriptor(source)
            wrong_type["inputs"]["planSource"]["sourceType"] = "diagnose"
            with self.assertRaises(PlanSourceError) as source_type_error:
                resolve_plan_source(
                    task_id="EV-3",
                    step_id="STEP-1",
                    evolve_results_dir=td,
                    step_input_fetcher=lambda _task_id, _step_id: wrong_type,
                )
            self.assertEqual(
                source_type_error.exception.code, "PLAN_SOURCE_SCHEMA_INVALID"
            )

    def test_phase_zero_helper_then_runner_only_fetches_step_input_once(self):
        source = source_fixture()
        calls: list[tuple[str, str]] = []

        def first_network_read(task_id, step_id, *, base_url=None):
            calls.append((task_id, step_id))
            return descriptor(source)

        with tempfile.TemporaryDirectory(prefix="plan-source-phase-zero-") as td:
            with (
                patch(
                    "clawevolve_plan.input.resolver.fetch_step_input",
                    side_effect=first_network_read,
                ),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = resolve_source_main(
                    [
                        "--task-id",
                        "EV-1",
                        "--step-id",
                        "STEP-1",
                        "--evolve-results-dir",
                        td,
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(calls, [("EV-1", "STEP-1")])

            args = argparse.Namespace(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                run_dir="",
                goal="",
                discovery_notes="notes",
                target=["skills/example/SKILL.md"],
                overwrite=True,
                skip_clawweb_report=False,
            )

            def reporter(task_id, step_id, **payload):
                return {"task_id": task_id, "step_id": step_id, **payload}

            with (
                patch(
                    "clawevolve_plan.input.resolver.fetch_step_input",
                    side_effect=AssertionError(
                        "runner must reuse Phase 0 local Source"
                    ),
                ),
                patch(
                    "clawevolve_plan.pipeline.runner.run_fresh_plan",
                    return_value={"status": "ok"},
                ),
            ):
                result = run_plan_command(args, step_reporter=reporter)

            self.assertEqual(result.exit_code, 0, result.payload)
            self.assertEqual(calls, [("EV-1", "STEP-1")])

    def test_planning_context_compacts_external_evidence_and_keeps_full_source(self):
        source = source_fixture()
        normalized = build_planning_context(source, "/tmp/source.json")

        self.assertEqual(normalized["schema_version"], "plan-source/v2")
        self.assertEqual(normalized["bot_id"], "bot-1")
        self.assertEqual(
            normalized["default_optimization_goal"]["goal_text"],
            "工具失败后没有降级；保留可执行降级路径",
        )
        self.assertEqual(normalized["root_cause_clusters"], [])
        evidence = normalized["cases"][0]["evidence"]
        self.assertEqual(evidence["message_count"], 1)
        self.assertEqual(evidence["message_samples"][0]["content"], "查询昨天的日志")
        self.assertNotIn("messages", evidence)
        self.assertNotIn("plan_source", normalized)
        self.assertEqual(normalized["plan_source_ref"]["case_count"], 1)
        self.assertEqual(source["cases"][0]["evidence"]["messages"][0]["raw"], {"trace": "keep"})
        self.assertEqual(
            normalized["cases"][0]["evolution_failure_mode"], "tool_execution_failure"
        )
        self.assertEqual(normalized["cases"][0]["root_cause_summary"], "")

    def test_planning_context_preserves_diagnose_semantics_without_legacy_schema(self):
        source = source_fixture()
        source["source"].update(
            {
                "type": "diagnose",
                "producer": "clawevolve-diagnose",
                "adapter_version": None,
            }
        )
        source["extensions"] = {
            "diagnose": {
                "artifacts": {
                    "analysis_report_md": "/tmp/report.md",
                    "diagnose_result_json": "/tmp/result.json",
                    "diagnose_cases_dir": "/tmp/cases",
                },
                "selection_report": {"status": "ok"},
                "clawweb_domain": {"status": "deferred_to_plan"},
            }
        }

        normalized = build_planning_context(source, "/tmp/source.json")

        self.assertEqual(normalized["input_mode"], "diagnose")
        self.assertEqual(normalized["analysis_report"], "/tmp/report.md")
        self.assertEqual(normalized["diagnose_result"], "/tmp/result.json")
        self.assertEqual(normalized["diagnose_cases_dir"], "/tmp/cases")
        self.assertEqual(normalized["selection_report"], {"status": "ok"})
        self.assertEqual(
            normalized["clawweb_domain"], {"status": "deferred_to_plan"}
        )
        self.assertNotIn("clawevolve-plan-input.v1", json.dumps(normalized))

    def test_planning_context_uses_execution_target_and_preserves_cross_bot_context(
        self,
    ):
        source = source_fixture()
        source["source"]["adapter_version"] = "insight-to-plan-source/v2"
        source["planning_hints"] = {
            "target_context": {
                "relationship": "cross_bot",
                "applicability_required": True,
                "source_bot": {
                    "owner_user_id": "owner-1",
                    "bot_id": "bot-1",
                },
                "execution_target": {
                    "owner_user_id": "specialist-1",
                    "bot_id": "target-bot-1",
                },
            }
        }

        normalized = build_planning_context(source, "/tmp/source.json")

        self.assertEqual(normalized["bot_id"], "target-bot-1")
        self.assertEqual(normalized["source_bot_id"], "bot-1")
        self.assertEqual(
            normalized["agent_context"]["target_context"],
            source["planning_hints"]["target_context"],
        )

    def test_planning_context_rejects_an_unconfirmed_cross_bot_context(self):
        source = source_fixture()
        source["planning_hints"] = {
            "target_context": {
                "relationship": "cross_bot",
                "applicability_required": False,
                "source_bot": {"owner_user_id": "owner-1", "bot_id": "bot-1"},
                "execution_target": {
                    "owner_user_id": "specialist-1",
                    "bot_id": "target-bot-1",
                },
            }
        }

        with self.assertRaisesRegex(ValueError, "applicability flag is inconsistent"):
            build_planning_context(source, "/tmp/source.json")

    def test_plan_pipeline_loads_the_resolved_source_through_its_input_interface(self):
        source = source_fixture()
        with tempfile.TemporaryDirectory(prefix="plan-source-pipeline-") as td:
            root = Path(td)
            resolved_source = root / "resolved" / "source.json"
            resolved_source.parent.mkdir(parents=True)
            resolved_source.write_text(
                json.dumps(source, ensure_ascii=False), encoding="utf-8"
            )
            notes = root / "discovery.md"
            notes.write_text(
                "# Discovery Notes\n\n## Inspected Targets\n"
                "- `skills/example/SKILL.md`: inspected for tool_execution_failure\n",
                encoding="utf-8",
            )
            input_dir = root / "run" / "plan" / "input"
            output_dir = root / "run" / "plan" / "output"
            args = argparse.Namespace(
                run_dir="",
                task_id="EV-1",
                evolve_results_dir=str(root),
                discovery_notes=str(notes),
                target=["skills/example/SKILL.md"],
                plan_source_path=str(resolved_source),
            )

            plan, manifest, _, _, plan_path = _load_and_validate_inputs(
                args, input_dir, output_dir
            )
            self.assertEqual(plan["schema_version"], "plan-source/v2")
            self.assertEqual(plan["cases"][0]["evidence"]["message_count"], 1)
            self.assertNotIn("messages", plan["cases"][0]["evidence"])
            self.assertNotIn("plan_source", plan)
            self.assertEqual(plan["plan_source_ref"]["case_count"], 1)
            self.assertEqual(plan["root_cause_clusters"], [])
            self.assertEqual(Path(plan_path), input_dir / "source.json")
            self.assertEqual(manifest["items"][0]["label"], "plan_source")

    def test_large_external_evidence_never_crosses_the_discovery_argv_boundary(self):
        source = source_fixture()
        marker = "FULL_EVIDENCE_MUST_STAY_IN_SOURCE_FILE"
        source["cases"][0]["evidence"]["messages"] = [
            {
                "message_index": index,
                "role": "assistant" if index % 2 else "user",
                "content": marker + ("x" * 10_000),
            }
            for index in range(50)
        ]
        context = build_planning_context(source, "/tmp/source.json")
        prompt = build_discovery_prompt(
            source_path=Path("/tmp/source.json"),
            workspace_root=Path("/tmp/workspace"),
            output_path=Path("/tmp/discovery.json"),
            source_schema="plan-source/v2",
            source_size_bytes=len(json.dumps(source, ensure_ascii=False).encode("utf-8")),
            case_count=len(source["cases"]),
            cluster_count=0,
            input_mode="insight_improvement",
        )

        self.assertLessEqual(
            len(prompt.encode("utf-8")), MAX_DISCOVERY_PROMPT_BYTES
        )
        self.assertNotIn(marker, prompt)
        self.assertIn(json.dumps(str(Path("/tmp/source.json").resolve())), prompt)
        self.assertEqual(len(context["cases"][0]["evidence"]["message_samples"]), 3)
        self.assertEqual(len(source["cases"][0]["evidence"]["messages"]), 50)

    def test_plan_command_resolves_source_before_entering_the_existing_pipeline(self):
        source = source_fixture()
        with tempfile.TemporaryDirectory(prefix="plan-source-command-") as td:
            source_path = Path(td) / "EV-1" / "plan" / "input" / "source.json"
            descriptor_path = source_path.with_name("source-descriptor.json")
            calls: list[dict] = []

            def fake_resolver(**kwargs):
                calls.append(kwargs)
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text(json.dumps(source), encoding="utf-8")
                return PlanSourceResolution(
                    "written", digest_json(source), source, source_path, descriptor_path
                )

            args = argparse.Namespace(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                run_dir="",
                goal="",
                discovery_notes="notes",
                target=["skills/example/SKILL.md"],
                overwrite=True,
                skip_clawweb_report=False,
            )
            reports: list[dict] = []

            def reporter(task_id, step_id, **payload):
                reports.append({"task_id": task_id, "step_id": step_id, **payload})
                return {"status": "ok"}

            with patch(
                "clawevolve_plan.pipeline.runner.run_fresh_plan",
                return_value={"status": "ok"},
            ) as fresh:
                result = run_plan_command(
                    args,
                    step_reporter=reporter,
                    plan_source_resolver=fake_resolver,
                )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(args.plan_source_path, str(source_path))
            self.assertEqual(
                calls,
                [
                    {
                        "task_id": "EV-1",
                        "step_id": "STEP-1",
                        "evolve_results_dir": td,
                        "local_source_path": "",
                        "allow_network": True,
                    }
                ],
            )
            self.assertTrue(fresh.called)
            self.assertEqual(reports, [])

    def test_full_plan_flow_generates_existing_outputs_from_source_without_remote_io(
        self,
    ):
        source = source_fixture()
        second_case = json.loads(json.dumps(source["cases"][0]))
        second_case.update(
            {
                "case_id": "insight_123_def_1",
                "ordinal": 1,
                "session_id": "session-2",
                "query": "查询今天的日志",
            }
        )
        second_case["evidence"]["payload_ref"] = "oss://bucket/session-2.json"
        second_case["evidence"]["payload_etag"] = "etag-2"
        second_case["evidence"]["messages"][0]["content"] = "查询今天的日志"
        source["cases"].append(second_case)
        source["analysis"]["case_distribution"].update({"total": 2, "bad": 2})
        with tempfile.TemporaryDirectory(prefix="plan-source-full-") as td:
            root = Path(td)
            source_path = root / "EV-1" / "plan" / "input" / "source.json"
            descriptor_path = source_path.with_name("source-descriptor.json")
            notes = root / "discovery.md"
            notes.write_text(
                "# Discovery Notes\n\n"
                "## Inspected Targets\n"
                "- `skills/example/SKILL.md`: inspected for tool_execution_failure\n\n"
                "## Findings\n"
                "- `tool_execution_failure`: missing fallback\n\n"
                "## Allowed Update Target Candidates\n"
                "- `skills/example/SKILL.md`: add fallback guidance\n\n"
                "## Forbidden Boundary Check\n"
                "- Judge/scorer/templates/ClawWeb domain/production config/secrets/global runtime were not modified.\n",
                encoding="utf-8",
            )

            def fake_resolver(**_kwargs):
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text(
                    json.dumps(source, ensure_ascii=False), encoding="utf-8"
                )
                descriptor_path.write_text("{}", encoding="utf-8")
                return PlanSourceResolution(
                    "written", digest_json(source), source, source_path, descriptor_path
                )

            args = argparse.Namespace(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                run_dir="",
                goal="",
                discovery_notes=str(notes),
                target=["skills/example/SKILL.md"],
                overwrite=True,
                skip_clawweb_report=True,
            )
            with (
                patch(
                    "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
                    side_effect=DiscoveryAgentError("offline test"),
                ),
                patch(
                    "clawevolve_plan.pipeline.upload.upload_templates"
                ) as upload_templates,
                patch(
                    "clawevolve_plan.integration.artifact_publish._run_pack_skill"
                ) as run_pack_skill,
            ):
                result = run_plan_command(args, plan_source_resolver=fake_resolver)

            upload_templates.assert_not_called()
            run_pack_skill.assert_not_called()
            self.assertEqual(result.exit_code, 0, result.payload)
            self.assertEqual(result.payload["status"], "ok")
            self.assertTrue(
                (root / "EV-1" / "plan" / "output" / "objective.md").is_file()
            )
            self.assertTrue(
                (root / "EV-1" / "plan" / "output" / "spec-v0.md").is_file()
            )
            upload_result = json.loads(
                (
                    root / "EV-1" / "plan" / "output" / "clawweb_upload_result.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(upload_result["status"], "skipped")
            self.assertEqual(upload_result["reason"], "--skip-clawweb-report")
            self.assertFalse(upload_result["enabled"])
            self.assertEqual(set(upload_result["domains"]), {"train", "test"})
            oss_result = json.loads(
                (
                    root / "EV-1" / "plan" / "output" / "oss_upload_result.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(oss_result["status"], "skipped")
            manifest = json.loads(
                (root / "EV-1" / "plan" / "output" / "input_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["items"][0]["label"], "plan_source")


if __name__ == "__main__":
    unittest.main()
