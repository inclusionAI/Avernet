"""Regression checks for verifying a completed Run inside a still-active Chat."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    "fixed_loop_story", Path(__file__).parent / "e2e-test" / "fixed_loop_story.py",
)
assert SPEC and SPEC.loader
story_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story_module)


class FixedLoopVerificationTests(unittest.TestCase):
    def story(self, change_history):
        story = story_module.Story(Mock())
        story.run_id, story.session_id = "sm-test", "group:session"
        story.run_path = "/state-machine-runs/sm-test"
        execution = {"definition_node_id": "review", "iteration": 2}
        nodes = []
        history = []
        for node_id in ("input-1", "review-1", "input-2", "review", "rewrite", "summary"):
            text = "final-editorial-output" if node_id == "summary" else node_id + "-result"
            node = {"node_id": node_id, "status": "completed", "artifact_text": text}
            if node_id == "review":
                node.update({"execution": execution, "outcome": "revise"})
            nodes.append(node)
            history.append({"id": "output-" + node_id, "content": text, "metadata": {
                "state_machine": {"run_id": story.run_id, "node_id": node_id,
                                  "event": "output", "execution": node.get("execution")},
            }})
        nodes.append({"node_id": "polish", "status": "skipped"})
        history.extend([
            {"id": "panel", "metadata": {"state_machine": {"run_id": story.run_id, "event": "panel"}}},
            {"id": "state-machine-result:sm-test", "content": "final-editorial-output"},
        ])
        reloaded = copy.deepcopy(history)
        change_history(reloaded)
        snapshots = iter([history, reloaded])
        graph = {"nodes": nodes, "edges": [{"source": "review", "target": "rewrite",
                                           "loop_route": {"logical_outcome": "exhausted"}}]}
        run = {"run": {"status": "completed", "output": "final-editorial-output"}, "nodes": nodes,
               "node_execution_metadata": {"review": execution}}

        def request(path):
            if path == story.run_path:
                return run
            if path.endswith("/messages"):
                return next(snapshots)
            return {"status": "running"}

        def cli(*args, **kwargs):
            if "--graph" in args:
                return graph
            if "--node" in args:
                return next(node for node in nodes if node["node_id"] == args[-1])
            return run

        story.request, story.cli = request, cli
        return story

    def test_allows_a_routed_chat_reply_after_workflow_completion(self):
        story = self.story(lambda history: history.append({"id": "bot-reply", "content": "follow-up"}))
        story.verify(False)

    def test_rejects_a_changed_workflow_output(self):
        story = self.story(lambda history: history[0].update(content="changed"))
        with self.assertRaisesRegex(AssertionError, "preserve workflow"):
            story.verify(False)

    def test_rejects_a_missing_workflow_record(self):
        story = self.story(lambda history: history.pop(0))
        with self.assertRaisesRegex(AssertionError, "preserve workflow"):
            story.verify(False)

    def test_rejects_a_changed_panel(self):
        story = self.story(lambda history: history[-2].update(content="changed panel"))
        with self.assertRaisesRegex(AssertionError, "preserve workflow"):
            story.verify(False)

    def test_rejects_a_duplicate_final_publication_with_a_new_id(self):
        story = self.story(lambda history: history.append({"id": "duplicate-result", "content": "final-editorial-output"}))
        with self.assertRaisesRegex(AssertionError, "exactly once"):
            story.verify(False)

    def test_rejects_a_duplicate_final_publication_with_the_same_id(self):
        story = self.story(lambda history: history.append(copy.deepcopy(history[-1])))
        with self.assertRaisesRegex(AssertionError, "preserve workflow"):
            story.verify(False)


if __name__ == "__main__":
    unittest.main()
