"""Focused offline tests for the structured assertion-density projection."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.structured_density.contract import (
    DENSITY_ASSERTION_COUNT,
    DENSITY_PARENT_COUNT,
    NEW_ASSERTION_COUNT,
    parsed_assertions,
    structured_density_plan,
)
from bcs_e2e.structured_density.profile import validate_density_profile
from bcs_e2e.structured_density.run import run_structured_density
from bcs_e2e.structured_density.transport import Observation, ReplayTransport
from bcs_e2e.run_state import RunStateError


def profile(*, tier: str = "local", kind: str = "singlebox") -> dict:
    local = tier == "local"
    return {
        "environment": {
            "tier": tier,
            "id": "structured-density-test",
            "expected_bcs_env": "local" if local else "pre",
            "lifecycle": "local-provision" if local else "remote-attach",
            "deployment": "singlebox" if local else "managed",
            "provider": "local" if local else "internal",
        },
        "entrypoint": {
            "kind": kind,
            "origin": "http://127.0.0.1:0" if local else "https://bcs-pre.alipay.com",
            "api_face": "public-v1",
            "manifest_face": "internal-v1" if local else "legacy",
        },
        "auth": {"header_env": {
            "Cookie": "BCS_E2E_HUMAN_COOKIE",
            "Authorization": "BCS_E2E_DRIVER_TOKEN",
        }},
    }


def observations() -> dict[str, Observation]:
    common = {
        "templates.list": Observation(200, {"templates": [{"id": "single-bot-guided-answer"}]}),
        "templates.detail": Observation(200, {"id": "single-bot-guided-answer", "lang": "zh-CN", "yaml": "name: E2E Guided Answer"}),
        "group.create": Observation(200, {"id": "group-structured-density-1"}),
        "group.read": Observation(200, {"group_strategy": "state_machine"}),
        "definition.read": Observation(200, {"default_definition": {"id": "definition-structured-density-1", "version": 7}, "yaml_source": "original"}),
        "definition.patch": Observation(200, {"default_definition": {"id": "definition-structured-density-1", "version": 8}, "body": "Draft and verify a concise release recommendation."}),
        "definition.upgrade": Observation(404, {"error": "definition-structured-density-1 @107 not found"}),
        "run.start": Observation(202, {"run": {"run_id": "run-structured-density-1", "group_id": "group-structured-density-1"}, "nodes": [{"node_id": "answer"}]}),
        "run.read": Observation(200, {"run": {"run_id": "run-structured-density-1", "status": "running"}}),
        "run.graph": Observation(200, {"run": {"run_id": "run-structured-density-1"}, "nodes": [{"node_id": "answer"}]}),
        "run.node": Observation(200, {"node": {"node_id": "answer", "run_id": "run-structured-density-1"}}),
        "run.pending-human": Observation(200, []),
        "run.human-response": Observation(400, {"error": {"message": "not a human_input node"}}),
        "run.cancel": Observation(200, {"run": {"status": "aborted"}}),
        "run.terminal": Observation(200, {"run": {"status": "aborted"}}),
        "run.rerun": Observation(409, {"error": {"message": "must be failed before rerun"}}),
        "group.delete": Observation(200, {"id": "group-structured-density-1"}),
    }
    return {row.assertion_id: common[row.operation] for row in parsed_assertions()}


class StructuredDensityTests(unittest.TestCase):
    def test_plan_has_17_parents_and_43_source_anchored_assertions(self):
        plan = structured_density_plan()
        self.assertEqual(plan["parent_obligation_count"], DENSITY_PARENT_COUNT)
        self.assertEqual(plan["parsed_assertion_count"], DENSITY_ASSERTION_COUNT)
        self.assertEqual(plan["new_field_assertion_count"], NEW_ASSERTION_COUNT)
        self.assertEqual(plan["delta"], 26)
        self.assertEqual(len(plan["assertions"]), 43)
        self.assertEqual(sum(row["density"] for row in plan["assertions"]), 26)
        self.assertEqual(plan["unlocated_count"], 0)
        self.assertEqual(len(plan["unlocated_source_probes"]), 3)
        self.assertEqual({row["family"] for row in plan["assertions"]}, {"Group", "Join", "Leave", "Message"})

    def test_owned_local_fake_transport_projects_every_assertion(self):
        transport = ReplayTransport(observations())
        report = run_structured_density(profile=profile(), transport=transport)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["executor"], "owned-local")
        self.assertEqual(report["case_scope"]["expected_assertion_count"], 43)
        self.assertEqual(report["case_scope"]["new_field_assertion_count"], 26)
        self.assertEqual(report["execution"]["completed_count"], 43)
        self.assertEqual(len(transport.calls), 43)
        self.assertTrue(all(row["status"] == "PASS" for row in report["assertions"]))
        self.assertTrue(all(row["parent_id"].startswith("STRUCTURED.") for row in report["assertions"]))
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])

    def test_preprod_bcs_direct_is_honestly_blocked_without_transport(self):
        report = run_structured_density(
            profile=profile(tier="preprod", kind="bcs-direct"),
        )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["executor"], "preprod-bcs-direct-public-v1")
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(len(report["assertions"]), 43)
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in report["assertions"]))
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])

    def test_profiles_reject_wrong_leg_and_secret_values(self):
        with self.assertRaises(RunStateError):
            validate_density_profile(profile(tier="preprod", kind="singlebox"))
        bad = profile()
        bad["auth"]["header_env"]["Cookie"] = "cookie-private-value"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)

    def test_failure_does_not_charge_later_assertions_as_pass(self):
        fake = observations()
        fake["STRUCTURED.F05.field"] = Observation(200, {"group_strategy": "chat"})
        report = run_structured_density(profile=profile(), transport=ReplayTransport(fake))
        self.assertEqual(report["status"], "FAIL")
        by_id = {row["id"]: row for row in report["assertions"]}
        self.assertEqual(by_id["STRUCTURED.F05.field"]["status"], "FAIL")
        self.assertEqual(by_id["STRUCTURED.F06.field"]["status"], "NOT_RUN")


class StructuredDensityProfileAdmissionTests(unittest.TestCase):
    def test_local_leg_requires_the_owned_local_singlebox_lifecycle(self):
        bad = profile()
        bad["environment"]["lifecycle"] = "remote-attach"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = profile()
        bad["environment"]["deployment"] = "managed"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)

    def test_preprod_structured_leg_requires_the_station_direct_legacy_face(self):
        with self.assertRaises(RunStateError):
            validate_density_profile(profile(tier="preprod", kind="singlebox"))
        gateway = profile(tier="preprod", kind="gateway")
        gateway["entrypoint"]["manifest_face"] = "legacy"
        with self.assertRaises(RunStateError):
            validate_density_profile(gateway)
        bad = profile(tier="preprod", kind="bcs-direct")
        bad["entrypoint"]["manifest_face"] = "internal-v1"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)

    def test_structured_leg_requires_both_credential_references(self):
        bad = profile()
        bad["auth"]["header_env"].pop("Authorization")
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)

    def test_templates_leg_admits_local_singlebox_and_preprod_gateway_with_browser_origin(self):
        local = profile()
        local["entrypoint"]["manifest_face"] = "internal-v1"
        from bcs_e2e.structured_density.profile import validate_density_templates_profile
        validate_density_templates_profile(local)
        preprod = {
            "environment": {"tier": "preprod", "id": "preprod", "expected_bcs_env": "pre",
                            "lifecycle": "remote-attach", "deployment": "managed", "provider": "internal"},
            "entrypoint": {"kind": "gateway", "origin": "https://teamclawgw-pre.alipay.com",
                           "api_face": "public-v1", "manifest_face": "internal-v1"},
            "auth": {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE"}},
            "browser_origin": "https://teamclaw-pre.alipay.com",
        }
        validate_density_templates_profile(preprod)

    def test_templates_leg_rejects_missing_cookie_or_browser_origin_or_secrets(self):
        from bcs_e2e.structured_density.profile import validate_density_templates_profile
        cookie_only_local = profile()
        cookie_only_local["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
        validate_density_templates_profile(cookie_only_local)
        preprod = {
            "environment": {"tier": "preprod", "id": "preprod", "expected_bcs_env": "pre",
                            "lifecycle": "remote-attach", "deployment": "managed", "provider": "internal"},
            "entrypoint": {"kind": "gateway", "origin": "https://teamclawgw-pre.alipay.com",
                           "api_face": "public-v1", "manifest_face": "internal-v1"},
            "auth": {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE"}},
        }
        with self.assertRaises(RunStateError):
            validate_density_templates_profile(preprod)
        preprod["browser_origin"] = "https://teamclaw-pre.alipay.com"
        preprod["auth"]["header_env"]["Authorization"] = "BCS_E2E_DRIVER_TOKEN"
        validate_density_templates_profile(preprod)
        preprod["secrets"] = {"note": "inline-value-forbidden"}
        with self.assertRaises(RunStateError):
            validate_density_templates_profile(preprod)


if __name__ == "__main__":
    unittest.main()
