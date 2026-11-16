"""Ensure the live Group Card story detects stale/incomplete persisted Cards."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("group_card_story", ROOT / "e2e-test/group_card_story.py")
STORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORY)
FIXTURES = ROOT.parent / "crates/services/bcs-group-store/tests/fixtures"


class GroupCardStoryAssertions(unittest.TestCase):
    def setUp(self):
        self.card = json.loads((FIXTURES / "manager-worker-card-template.json").read_text())

    def check(self, card):
        STORY.assert_card(card, "group-mw-001", self.card["name"], "manager_worker", self.card["description"])

    def test_accepts_both_contract_fixtures(self):
        self.check(self.card)
        card = json.loads((FIXTURES / "state-machine-card-template.json").read_text())
        group_id = card["supportedInterfaces"][0]["url"].removeprefix("local://groups/")
        STORY.assert_card(card, group_id, card["name"], "state_machine", card["description"])

    def test_rejects_stale_name_and_description(self):
        for target in ("card", "skill"):
            for field in ("name", "description"):
                with self.subTest(target=target, field=field):
                    card = copy.deepcopy(self.card)
                    (card if target == "card" else card["skills"][0])[field] = "stale"
                    with self.assertRaises(AssertionError):
                        self.check(card)

    def test_rejects_wrong_group_interface_or_strategy(self):
        card = copy.deepcopy(self.card)
        card["supportedInterfaces"][0]["url"] = "local://groups/another-group"
        with self.assertRaises(AssertionError):
            self.check(card)
        card = copy.deepcopy(self.card)
        card["capabilities"]["extensions"][0]["params"]["groupKind"] = "state_machine"
        with self.assertRaises(AssertionError):
            self.check(card)

    def test_rejects_dynamic_snapshot_fields(self):
        for field in ("version", "revision", "providerName", "agents", "composition"):
            with self.subTest(field=field):
                card = {**self.card, field: "unexpected"}
                with self.assertRaises(AssertionError):
                    self.check(card)


if __name__ == "__main__":
    unittest.main()
