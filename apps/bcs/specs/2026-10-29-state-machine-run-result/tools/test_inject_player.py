import unittest

from inject_player import select_candidate


class SelectionTest(unittest.TestCase):
    def test_selection_is_seeded_and_excludes_non_worker_or_inactive_nodes(self):
        nodes = [
            {"node_id": "speak_1", "status": "running", "assignee_bot_id": "a", "attempt": 0},
            {"node_id": "vote_2", "status": "running", "assignee_bot_id": "b", "attempt": 0},
            {"node_id": "speak_3", "status": "completed", "assignee_bot_id": "a", "attempt": 0},
            {"node_id": "collect", "status": "running", "assignee_bot_id": "a", "attempt": 0},
            {"node_id": "vote_start", "status": "running", "assignee_bot_id": "a", "attempt": 0},
            {"node_id": "speak_4", "status": "running", "assignee_bot_id": "manager", "attempt": 0},
        ]
        first, candidates = select_candidate(nodes, {"a", "b"}, 261001)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(first, select_candidate(list(reversed(nodes)), {"a", "b"}, 261001)[0])
        self.assertEqual({select_candidate(nodes, {"a", "b"}, seed)[0]["assignee_bot_id"]
                          for seed in range(20)}, {"a", "b"})

    def test_empty_selection_fails_before_injection(self):
        with self.assertRaises(ValueError):
            select_candidate([], {"a"}, 1)
        with self.assertRaises(ValueError):
            select_candidate([{"node_id": "vote_1", "status": "running", "assignee_bot_id": "a", "attempt": 0}], {"a"}, 1, minimum=2)


if __name__ == "__main__":
    unittest.main()
