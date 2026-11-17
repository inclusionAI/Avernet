import copy
import unittest
from agent_evaluation.synthesis import candidate_gates as gates


def candidate():
    criterion = dict(criterion="Answer the question", max_score=100,
                     full_credit="Correct", partial_credit="Partly correct", zero_credit="Incorrect")
    return dict(id="fixture-1", question="What does the fixture state?", tags=["fixture-rule"],
                sources=[dict(type="fixture", name="Fixture", reference="fixture://one",
                              observed_at="2026-01-01", evidence="The fixture states a rule.")],
                answer="A rule", answer_criteria=[criterion],
                process_criteria=[dict(criterion, evidence="Observable input event")],
                difficulty={key: dict(score=0, reason="Fixture only") for key in gates.DIFFICULTY})


class CandidateContractTests(unittest.TestCase):
    def test_complete_meta(self):
        self.assertEqual(gates.shape_errors(candidate(), {"fixture-rule"}), [])

    def test_actor_input_does_not_leak_answer_or_rubric(self):
        self.assertEqual(set(gates.actor_payload(candidate())), {"id", "question"})

    def test_independent_totals(self):
        value = candidate(); value["process_criteria"][0]["max_score"] = 50
        self.assertIn("PROCESS_CRITERIA_SUM", gates.shape_errors(value, value["tags"]))

    def test_boolean_is_not_score(self):
        value = candidate(); value["answer_criteria"][0]["max_score"] = True
        self.assertIn("ANSWER_CRITERIA_FIELDS", gates.shape_errors(value, value["tags"]))

    def test_missing_baseline_is_not_novelty(self):
        value = candidate()
        result = gates.novelty(value, [], False, [], {}, False, "0" * 64)
        self.assertEqual(result["reason"], "BASELINE_UNAVAILABLE")

    def test_live_mode_does_not_accept_fixture_label(self):
        value = candidate(); policy = "0" * 64
        labels = {gates.fingerprint(value): dict(decision="novel", reason="fixture", policy_sha256=policy)}
        self.assertEqual(gates.novelty(value, [], True, [], labels, False, policy)["decision"], "review")

    def test_duplicate_question_normalization(self):
        value = candidate(); previous = copy.deepcopy(value); previous["id"] = "old"
        previous["question"] = "  " + value["question"].upper()
        self.assertEqual(gates.novelty(value, [previous], True, [], {}, False, "0" * 64)["reason"], "EXACT_DUPLICATE")
