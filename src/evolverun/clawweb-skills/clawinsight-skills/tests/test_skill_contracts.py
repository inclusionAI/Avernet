"""Project Skill examples conform to the existing deterministic execution contract."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.parents[1] / "clawweb-governance-batch"
sys.path.insert(0, str(RUNTIME))
from clawweb_batch.core import API_PREFIX, validate_analysis, validate_request


def json_example(relative_path: str) -> dict:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    if not match:
        raise AssertionError("missing JSON protocol example")
    return json.loads(match.group(1))


class SkillContractsTests(unittest.TestCase):
    def test_governance_example_is_accepted_by_program(self):
        value = json_example("clawweb-governance/references/analysis-contract.md")
        self.assertEqual(validate_analysis(value, {"existing_actions": []})["decision"], "WATCH")

    def test_governance_model_cannot_add_execute_permission(self):
        value = json_example("clawweb-governance/references/analysis-contract.md")
        value["autoExecute"] = True
        with self.assertRaises(ValueError):
            validate_analysis(value, {"existing_actions": []})

    def test_governance_cannot_invent_existing_item(self):
        value = json_example("clawweb-governance/references/analysis-contract.md")
        value.update(decision="DROP", existing_improvement_id=12345)
        with self.assertRaises(ValueError):
            validate_analysis(value, {"existing_actions": []})

    def test_verification_example_is_accepted_for_both_queues(self):
        value = json_example("clawweb-verification/references/verification-contract.md")
        for suffix in ("", "/open"):
            validate_request({"kind": "verify", "path": API_PREFIX + "/verification-results" + suffix,
                              "key": "example-format-only", "payload": value})

    def test_verification_cannot_turn_empty_evidence_into_success(self):
        value = json_example("clawweb-verification/references/verification-contract.md")
        value["outcome"] = "DISAPPEARED"
        with self.assertRaises(ValueError):
            validate_request({"kind": "verify", "path": API_PREFIX + "/verification-results",
                              "key": "example-format-only", "payload": value})

    def test_verification_cannot_bypass_state_machine(self):
        value = json_example("clawweb-verification/references/verification-contract.md")
        value["status"] = "RESOLVED"
        with self.assertRaises(ValueError):
            validate_request({"kind": "verify", "path": API_PREFIX + "/verification-results",
                              "key": "example-format-only", "payload": value})

    def test_markdown_references_resolve_inside_project_skill_bundle(self):
        for path in ROOT.rglob("*.md"):
            for dest in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                self.assertNotIn("://", dest, str(path))
                target = (path.parent / dest).resolve()
                self.assertTrue(target.is_relative_to(ROOT), str(target))
                self.assertTrue(target.is_file(), str(target))

    def test_skills_are_portable_and_do_not_include_deployment_values(self):
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8")
            for forbidden in ("/Users/", "jiangshen_skills", "/ossfs/node_", ".alipay.com", ".antfin.com"):
                self.assertNotIn(forbidden, text, str(path))


if __name__ == "__main__":
    unittest.main()
