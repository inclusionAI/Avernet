from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.bench.case_contract import (  # noqa: E402
    _build_prompt,
    _extract_batch_contracts,
    _extract_json,
    _fallback_contract,
    _order_batch_contracts,
    _prompt_contract_schema,
    build_case_contracts,
    normalize_contract,
    validate_contract,
)
from clawevolve_plan.bench.split import assign_train_test_splits  # noqa: E402
from clawevolve_plan.bench.template_builder import (  # noqa: E402
    assign_case_template_ids,
    render_templates,
)


class CaseContractJsonParsingTests(unittest.TestCase):
    def test_extracts_canonical_array(self):
        payload = [{"case_id": "case-1"}, {"case_id": "case-2"}]

        self.assertEqual(_extract_json(json.dumps(payload)), payload)

    def test_extracts_canonical_contracts_envelope(self):
        payload = {"contracts": [{"case_id": "case-1"}]}

        self.assertEqual(_extract_json(json.dumps(payload)), payload)

    def test_extracts_all_ndjson_objects(self):
        response = '{"case_id":"case-1"}\n{"case_id":"case-2"}\n'

        self.assertEqual(
            _extract_json(response),
            [{"case_id": "case-1"}, {"case_id": "case-2"}],
        )

    def test_extracts_whitespace_delimited_json_objects_from_code_fence(self):
        response = '```json\n{"case_id":"case-1"}  {"case_id":"case-2"}\n```'

        self.assertEqual(
            _extract_json(response),
            [{"case_id": "case-1"}, {"case_id": "case-2"}],
        )

    def test_rejects_garbage_between_json_values(self):
        response = '{"case_id":"case-1"}\nnot-json\n{"case_id":"case-2"}'

        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            _extract_json(response)

    def test_single_contract_object_is_supported_only_for_one_case_batch(self):
        payload = {"case_id": "case-1"}

        self.assertEqual(
            _extract_batch_contracts(payload, expected_count=1, batch_number=1),
            [payload],
        )
        with self.assertRaisesRegex(ValueError, "no contracts"):
            _extract_batch_contracts(payload, expected_count=2, batch_number=1)


class CaseContractBatchValidationTests(unittest.TestCase):
    def test_orders_contracts_by_input_case_id(self):
        batch = [{"case_id": "case-1"}, {"case_id": "case-2"}]
        contracts = [{"case_id": "case-2"}, {"case_id": "case-1"}]

        ordered = _order_batch_contracts(contracts, batch=batch, batch_number=1)

        self.assertEqual([item["case_id"] for item in ordered], ["case-1", "case-2"])

    def test_rejects_duplicate_contract_case_id(self):
        batch = [{"case_id": "case-1"}, {"case_id": "case-2"}]
        contracts = [{"case_id": "case-1"}, {"case_id": "case-1"}]

        with self.assertRaisesRegex(ValueError, "duplicate contract case_id"):
            _order_batch_contracts(contracts, batch=batch, batch_number=1)

    def test_rejects_missing_and_unexpected_case_ids(self):
        batch = [{"case_id": "case-1"}, {"case_id": "case-2"}]
        contracts = [{"case_id": "case-1"}, {"case_id": "case-3"}]

        with self.assertRaisesRegex(
            ValueError, r"missing=\['case-2'\].*unexpected=\['case-3'\]"
        ):
            _order_batch_contracts(contracts, batch=batch, batch_number=1)

    def test_rejects_contract_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, "expected 2, got 1"):
            _extract_batch_contracts(
                [{"case_id": "case-1"}], expected_count=2, batch_number=1
            )


class CaseContractIntegrationTests(unittest.TestCase):
    def test_build_generates_five_contracts_independently_and_preserves_order(self):
        cases = [
            {
                "case_id": f"case-{index}",
                "query": f"任务{index}",
                "case_type": "bad",
            }
            for index in range(1, 6)
        ]
        runs = [
            SimpleNamespace(
                status="success",
                elapsed_seconds=0.1,
                response_text=json.dumps(
                    {
                        "contracts": [
                            _fallback_contract(
                                case, "完成任务", {"intent_text": "完成任务"}
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                stdout_text="",
                diagnostics={},
            )
            for case in cases
        ]

        with tempfile.TemporaryDirectory(prefix="plan-contract-") as td, patch(
            "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
            side_effect=runs,
        ) as agent_mock:
            result = build_case_contracts(
                plan={"input_mode": "diagnose"},
                cases=cases,
                goal_text="完成任务",
                user_intent={"intent_text": "完成任务"},
                discovery_notes="",
                output_dir=Path(td),
                task_id="EV-CONTRACT-1",
            )

        self.assertEqual(
            [contract["case_id"] for contract in result["contracts"]],
            [f"case-{index}" for index in range(1, 6)],
        )
        self.assertEqual(agent_mock.call_count, 5)
        self.assertEqual(len(result["batches"]), 5)
        self.assertTrue(
            all(batch["status"] == "validated" for batch in result["batches"])
        )
        self.assertEqual(
            [batch["case_ids"] for batch in result["batches"]],
            [[f"case-{index}"] for index in range(1, 6)],
        )


    def test_online_mode_rejects_transport_fallback(self):
        case = {"case_id": "case-1", "query": "完成任务"}
        with tempfile.TemporaryDirectory(prefix="plan-contract-online-") as td, patch(
            "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
            side_effect=__import__(
                "clawevolve_plan.discovery.agent", fromlist=["DiscoveryAgentError"]
            ).DiscoveryAgentError("gateway unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "required for an online Plan"):
                build_case_contracts(
                    plan={"input_mode": "diagnose"},
                    cases=[case],
                    goal_text="完成任务",
                    user_intent={"intent_text": "完成任务"},
                    discovery_notes="",
                    output_dir=Path(td),
                    task_id="EV-CONTRACT-ONLINE",
                    allow_fallback=False,
                )
            audit = json.loads(
                (Path(td) / "case_contract_audit.json").read_text(encoding="utf-8")
            )
        self.assertEqual(audit["status"], "failed")
        self.assertEqual(audit["batches"][0]["status"], "failed")

    def test_prompt_requires_one_contracts_envelope_and_forbids_ndjson(self):
        prompt = _build_prompt(
            {"input_mode": "diagnose"},
            [{"case_id": "case-1"}],
            "完成任务",
            None,
            "",
        )

        self.assertIn("exactly one top-level JSON object", prompt)
        self.assertIn('{"contracts": [{...}]}', prompt)
        self.assertIn("exactly one canonical", prompt)
        self.assertIn("Do not return NDJSON", prompt)
        self.assertIn("return automated_checks as an empty list", prompt)

    def test_prompt_rejects_more_than_one_case(self):
        with self.assertRaisesRegex(ValueError, "exactly one case"):
            _build_prompt(
                {"input_mode": "diagnose"},
                [{"case_id": "case-1"}, {"case_id": "case-2"}],
                "完成任务",
                None,
                "",
            )

    def test_prompt_declares_every_validator_required_contract_field(self):
        prompt = _build_prompt(
            {"input_mode": "direct_goal"},
            [{"case_id": "case-1"}],
            "完成任务",
            None,
            "",
        )
        schema = _prompt_contract_schema()["contracts"][0]

        for field in (
            "schema_version",
            "case_id",
            "task_contract",
            "grading_strategy",
            "automated_checks",
            "replayability",
            "provenance",
        ):
            self.assertIn(field, schema)
            self.assertIn(f'"{field}"', prompt)
        for field in (
            "user_intent",
            "required_outcomes",
            "acceptable_approaches",
            "required_actions",
            "required_evidence",
            "completion_signals",
            "acceptable_failure_handling",
            "forbidden_behaviors",
        ):
            self.assertIn(field, schema["task_contract"])
            self.assertIn(f'"{field}"', prompt)
        for field in (
            "name",
            "weight",
            "description",
            "score_1",
            "score_075",
            "score_05",
            "score_025",
            "score_0",
        ):
            self.assertIn(field, schema["grading_strategy"]["criteria"][0])
            self.assertIn(f'"{field}"', prompt)
        self.assertIn("sum to exactly 100", prompt)
        self.assertEqual(schema["automated_checks"], [])

    def test_invalid_optional_check_is_dropped_without_contract_regeneration(self):
        case = {
            "case_id": "goal-case-004",
            "template_id": "task_goal_case_004",
            "case_type": "prospective",
            "split": "train",
            "query": "最新的 AI 大模型有哪些",
        }
        contract = _fallback_contract(case, "优化搜索", {"intent_text": "优化搜索"})
        contract["grading_strategy"]["grading_type"] = "hybrid"
        contract["automated_checks"] = [{"type": "response_contains"}]
        run = SimpleNamespace(
            status="success",
            elapsed_seconds=0.1,
            response_text=json.dumps({"contracts": [contract]}, ensure_ascii=False),
            stdout_text="",
            diagnostics={},
        )

        with tempfile.TemporaryDirectory(prefix="plan-contract-optional-check-") as td, patch(
            "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
            return_value=run,
        ) as agent_mock:
            result = build_case_contracts(
                plan={"input_mode": "direct_goal"},
                cases=[case],
                goal_text="优化搜索",
                user_intent={"intent_text": "优化搜索"},
                discovery_notes="",
                output_dir=Path(td),
                task_id="EV-OPTIONAL-CHECK",
            )
            audit = json.loads(
                (Path(td) / "case_contract_audit.json").read_text(encoding="utf-8")
            )

        agent_mock.assert_called_once()
        normalized = result["contracts"][0]
        self.assertEqual(normalized["automated_checks"], [])
        self.assertEqual(
            normalized["grading_strategy"]["grading_type"], "llm_judge"
        )
        self.assertNotIn("warnings", audit)
        self.assertEqual(
            [warning["code"] for warning in audit["batches"][0]["warnings"]],
            [
                "automated_check_dropped",
                "automated_checks_downgraded_to_llm_judge",
            ],
        )


class CaseContractNormalizationTests(unittest.TestCase):
    def test_normalizes_production_legacy_flat_contract_to_canonical_v1(self):
        case = {
            "case_id": "goal-case-001",
            "case_type": "prospective",
            "query": "统计 bot 每天新建的 session 数量",
            "expected_behavior": "按日期返回准确统计",
            "success_criteria": ["每天的统计值准确"],
            "scoring_hints": ["重点检查日期边界"],
            "forbidden_behavior": ["不得编造 session"],
            "split": "train",
        }
        legacy = {
            "case_id": "goal-case-001",
            "goal": "创建 session 统计 skill",
            "query": case["query"],
            "expected_behavior": case["expected_behavior"],
            "success_criteria": case["success_criteria"],
            "scoring_hints": case["scoring_hints"],
            "forbidden_behavior": case["forbidden_behavior"],
            "replayable": True,
            "source_session_id": "",
        }

        contract = normalize_contract(
            legacy,
            case,
            goal="创建 session 统计 skill",
            intent={"intent_text": "创建 session 统计 skill"},
        )
        validate_contract(contract, case)

        self.assertEqual(contract["schema_version"], "clawevolve.case-contract.v1")
        self.assertEqual(contract["task_contract"]["user_intent"], case["query"])
        self.assertEqual(
            contract["task_contract"]["required_outcomes"],
            case["success_criteria"],
        )
        self.assertIn(
            "不得编造 session", contract["task_contract"]["forbidden_behaviors"]
        )
        self.assertEqual(
            sum(
                item["weight"]
                for item in contract["grading_strategy"]["criteria"]
            ),
            100,
        )
        self.assertTrue(contract["replayability"]["replayable"])
        self.assertEqual(
            contract["provenance"]["normalization"], "legacy_flat_to_v1"
        )
        self.assertEqual(contract["automated_checks"], [])
        self.assertEqual(contract["grading_strategy"]["grading_type"], "llm_judge")

    def test_does_not_silently_repair_malformed_canonical_contract(self):
        case = {"case_id": "case-1", "query": "完成任务"}
        malformed = {
            "case_id": "case-1",
            "task_contract": {"user_intent": "完成任务"},
            "grading_strategy": {"criteria": []},
        }

        contract = normalize_contract(malformed, case)

        with self.assertRaisesRegex(ValueError, "unsupported case contract schema"):
            validate_contract(contract, case)

    def test_rejects_unrecognized_flat_object_instead_of_inventing_contract(self):
        case = {"case_id": "case-1", "query": "完成任务"}
        unrecognized = {"case_id": "case-1", "unrelated": "value"}

        contract = normalize_contract(unrecognized, case)

        with self.assertRaisesRegex(ValueError, "unsupported case contract schema"):
            validate_contract(contract, case)

    def test_build_accepts_five_production_legacy_flat_contracts(self):
        cases = [
            {
                "case_id": f"goal-case-{index:03d}",
                "case_type": "prospective",
                "query": f"执行验证任务 {index}",
                "expected_behavior": f"正确完成任务 {index}",
                "success_criteria": [f"任务 {index} 完整完成"],
                "scoring_hints": ["检查准确性"],
                "forbidden_behavior": ["不得编造结果"],
                "split": "train",
            }
            for index in range(1, 6)
        ]
        cases = assign_case_template_ids(assign_train_test_splits(cases))
        runs = [
            SimpleNamespace(
                status="success",
                elapsed_seconds=0.1,
                response_text=json.dumps(
                    {
                        "contracts": [
                            {
                                "case_id": case["case_id"],
                                "goal": "完成五个验证任务",
                                "query": case["query"],
                                "expected_behavior": case["expected_behavior"],
                                "success_criteria": case["success_criteria"],
                                "scoring_hints": case["scoring_hints"],
                                "forbidden_behavior": case["forbidden_behavior"],
                                "replayable": True,
                                "source_session_id": "",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                stdout_text="",
                diagnostics={},
            )
            for case in cases
        ]

        with tempfile.TemporaryDirectory(prefix="plan-contract-legacy-") as td, patch(
            "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
            side_effect=runs,
        ) as agent_mock:
            result = build_case_contracts(
                plan={"input_mode": "direct_goal"},
                cases=cases,
                goal_text="完成五个验证任务",
                user_intent={"intent_text": "完成五个验证任务"},
                discovery_notes="",
                output_dir=Path(td),
                task_id="EV-CONTRACT-LEGACY",
            )

            contracts_by_id = {
                contract["case_id"]: contract for contract in result["contracts"]
            }
            templates_dir, zip_path, names, manifest = render_templates(
                {"bot_id": "bot-1", "cases": cases},
                Path(td),
                "完成五个验证任务",
                {"intent_text": "完成五个验证任务"},
                contracts_by_id,
            )
            zip_exists = zip_path.exists()
            rendered_template_count = len(
                list(templates_dir.glob("**/task_*.md"))
            )

        self.assertEqual(len(result["contracts"]), 5)
        self.assertEqual(agent_mock.call_count, 5)
        self.assertEqual(len(result["batches"]), 5)
        self.assertTrue(
            all(
                batch["normalization_modes"] == ["legacy_flat"]
                for batch in result["batches"]
            )
        )
        for case, contract in zip(cases, result["contracts"]):
            validate_contract(contract, case)
        self.assertEqual(len(names), 5)
        self.assertEqual(manifest["template_count"], 5)
        self.assertTrue(zip_exists)
        self.assertEqual(rendered_template_count, 5)

    def test_invalid_canonical_response_gets_schema_correction(self):
        case = {
            "case_id": "case-1",
            "query": "完成任务",
            "case_type": "bad",
            "split": "train",
        }
        invalid_run = SimpleNamespace(
            status="success",
            elapsed_seconds=0.1,
            response_text=json.dumps(
                {
                    "contracts": [
                        {
                            "case_id": "case-1",
                            "task_contract": {"user_intent": "完成任务"},
                            "grading_strategy": {"criteria": []},
                        }
                    ]
                }
            ),
            stdout_text="",
            diagnostics={},
        )
        valid_contract = _fallback_contract(
            case, "完成任务", {"intent_text": "完成任务"}
        )
        corrected_run = SimpleNamespace(
            status="success",
            elapsed_seconds=0.1,
            response_text=json.dumps({"contracts": [valid_contract]}),
            stdout_text="",
            diagnostics={},
        )

        with tempfile.TemporaryDirectory(prefix="plan-contract-correction-") as td, patch(
            "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
            side_effect=[invalid_run, corrected_run],
        ) as agent_mock:
            result = build_case_contracts(
                plan={"input_mode": "diagnose"},
                cases=[case],
                goal_text="完成任务",
                user_intent={"intent_text": "完成任务"},
                discovery_notes="",
                output_dir=Path(td),
                task_id="EV-CONTRACT-CORRECTION",
            )
            audit = json.loads(
                (Path(td) / "case_contract_audit.json").read_text(encoding="utf-8")
            )

        self.assertEqual(agent_mock.call_count, 2)
        self.assertEqual(len(result["contracts"]), 1)
        self.assertEqual(
            result["batches"][0]["attempts"],
            [
                {
                    "attempt": 1,
                    "status": "schema_rejected",
                    "error": "contract 'case-1' is invalid: unsupported case contract schema for case-1",
                    "response_chars": len(invalid_run.response_text),
                },
                {
                    "attempt": 2,
                    "status": "validated",
                    "response_chars": len(corrected_run.response_text),
                },
            ],
        )
        correction_prompt = agent_mock.call_args_list[1].kwargs["message"]
        self.assertIn("CORRECTION_ATTEMPT: 1/2", correction_prompt)
        self.assertNotIn("discovery_notes", correction_prompt)
        self.assertEqual(audit["status"], "validated")

    def test_two_schema_corrections_exhausted_fails_closed_and_writes_audit(self):
        case = {"case_id": "case-1", "query": "完成任务"}
        invalid_run = SimpleNamespace(
            status="success",
            elapsed_seconds=0.1,
            response_text=json.dumps(
                {
                    "contracts": [
                        {
                            "case_id": "case-1",
                            "task_contract": {"user_intent": "完成任务"},
                            "grading_strategy": {"criteria": []},
                        }
                    ]
                }
            ),
            stdout_text="",
            diagnostics={},
        )

        with tempfile.TemporaryDirectory(prefix="plan-contract-failed-audit-") as td, patch(
            "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
            side_effect=[invalid_run, invalid_run, invalid_run],
        ) as agent_mock:
            with self.assertRaisesRegex(
                ValueError, "remained invalid after 2 schema corrections"
            ):
                build_case_contracts(
                    plan={"input_mode": "diagnose"},
                    cases=[case],
                    goal_text="完成任务",
                    user_intent={"intent_text": "完成任务"},
                    discovery_notes="",
                    output_dir=Path(td),
                    task_id="EV-CONTRACT-FAILED",
                )
            audit = json.loads(
                (Path(td) / "case_contract_audit.json").read_text(encoding="utf-8")
            )

        self.assertEqual(agent_mock.call_count, 3)
        self.assertEqual(audit["status"], "failed")
        self.assertEqual(
            [attempt["status"] for attempt in audit["batches"][0]["attempts"]],
            ["schema_rejected", "schema_rejected", "schema_rejected"],
        )

    def test_valid_case_is_not_regenerated_when_later_case_needs_correction(self):
        cases = [
            {"case_id": "case-1", "query": "任务一"},
            {"case_id": "case-2", "query": "任务二"},
        ]
        valid_runs = [
            SimpleNamespace(
                status="success",
                elapsed_seconds=0.1,
                response_text=json.dumps(
                    {
                        "contracts": [
                            _fallback_contract(
                                case, "完成任务", {"intent_text": "完成任务"}
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                stdout_text="",
                diagnostics={},
            )
            for case in cases
        ]
        invalid_second_run = SimpleNamespace(
            status="success",
            elapsed_seconds=0.1,
            response_text=json.dumps(
                {
                    "contracts": [
                        {
                            "case_id": "case-2",
                            "task_contract": {"user_intent": "任务二"},
                            "grading_strategy": {"criteria": []},
                        }
                    ]
                }
            ),
            stdout_text="",
            diagnostics={},
        )

        with tempfile.TemporaryDirectory(prefix="plan-contract-isolation-") as td, patch(
            "clawevolve_plan.bench.case_contract.run_openclaw_agent_message",
            side_effect=[valid_runs[0], invalid_second_run, valid_runs[1]],
        ) as agent_mock:
            result = build_case_contracts(
                plan={"input_mode": "diagnose"},
                cases=cases,
                goal_text="完成任务",
                user_intent={"intent_text": "完成任务"},
                discovery_notes="",
                output_dir=Path(td),
                task_id="EV-CONTRACT-ISOLATION",
            )

        self.assertEqual(agent_mock.call_count, 3)
        self.assertEqual(
            [contract["case_id"] for contract in result["contracts"]],
            ["case-1", "case-2"],
        )
        self.assertEqual(
            [attempt["status"] for attempt in result["batches"][0]["attempts"]],
            ["validated"],
        )
        self.assertEqual(
            [attempt["status"] for attempt in result["batches"][1]["attempts"]],
            ["schema_rejected", "validated"],
        )


class CaseContractStrictValidationTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "case_id": "case-1",
            "template_id": "task_case_1",
            "case_type": "bad",
            "split": "train",
            "source_session_id": "session-1",
            "query": "完成任务",
        }
        self.contract = _fallback_contract(
            self.case, "完成任务", {"intent_text": "完成任务"}
        )

    def test_evidence_fallback_uses_llm_judge_without_automated_checks(self):
        self.assertEqual(self.contract["automated_checks"], [])
        self.assertEqual(
            self.contract["grading_strategy"]["grading_type"], "llm_judge"
        )

    def test_rejects_source_session_id_mismatch(self):
        contract = copy.deepcopy(self.contract)
        contract["source_session_id"] = "invented-session"

        with self.assertRaisesRegex(ValueError, "source_session_id mismatch"):
            validate_contract(contract, self.case)

    def test_rejects_boolean_and_non_finite_grading_weights(self):
        for invalid_weight in (True, math.nan, math.inf):
            contract = copy.deepcopy(self.contract)
            contract["grading_strategy"]["criteria"][0]["weight"] = invalid_weight
            with self.subTest(weight=invalid_weight), self.assertRaisesRegex(
                ValueError, "grading weight"
            ):
                validate_contract(contract, self.case)

    def test_rejects_replayable_contract_without_real_query(self):
        case = dict(self.case)
        case.pop("query")
        contract = _fallback_contract(
            case, "完成任务", {"intent_text": "完成任务"}
        )
        contract["replayability"]["replayable"] = True

        with self.assertRaisesRegex(ValueError, "requires a query"):
            validate_contract(contract, case)

    def test_prospective_contract_never_inherits_historical_session_id(self):
        case = {
            **self.case,
            "case_type": "prospective",
            "source_session_id": "must-not-leak",
        }
        contract = _fallback_contract(
            case, "完成任务", {"intent_text": "完成任务"}
        )

        validate_contract(contract, case)
        self.assertEqual(contract["source_session_id"], "")




if __name__ == "__main__":
    unittest.main()
