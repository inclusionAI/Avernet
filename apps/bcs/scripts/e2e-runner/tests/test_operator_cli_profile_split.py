"""Split charter for the operator-cli profile validation plane.

The 1,000-line source-file policy split moved the frozen review tables,
the field validators and the dual-tier Profile validator out of
``bcs_e2e/operator_cli_runner.py`` into ``bcs_e2e/operator_cli_profile.py``.
This charter pins the three invariants that make the split load-bearing:

1. both modules stay inside the repository 1,000-line CI budget;
2. the moved names are defined exactly once, in the plane, and are
   re-exported through ``operator_cli_runner`` (existing importers and
   the frozen review tables keep resolving);
3. validator behavior is identical through both import paths.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e import operator_cli_profile as plane  # noqa: E402
from bcs_e2e import operator_cli_runner as runner  # noqa: E402
from bcs_e2e.profile import ConfigError  # noqa: E402

MOVED_CONSTANTS = (
    "OPERATOR_CLI_PROFILE_CONTRACT",
    "OPERATOR_CLI_RELATIONSHIPS",
    "OPERATOR_CLI_ROUTE_BINDINGS",
    "OPERATOR_CLI_STATION_BINDINGS",
    "OPERATOR_CLI_ACTOR_ROLES",
    "OPERATOR_CLI_VISIBILITY_LEVELS",
)
MOVED_FUNCTIONS = ("_env_ref", "_actor", "_route_binding",
                   "validate_operator_cli_profile")


def _top_level_definitions(module_path: Path) -> set:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


class OperatorCliProfileSplitTests(unittest.TestCase):
    def test_both_modules_stay_within_the_1000_line_budget(self):
        for path in (ROOT / "bcs_e2e/operator_cli_runner.py",
                     ROOT / "bcs_e2e/operator_cli_profile.py"):
            self.assertLessEqual(
                len(path.read_text(encoding="utf-8").splitlines()),
                1000, path.name)

    def test_moved_symbols_are_defined_only_in_the_plane(self):
        runner_defs = _top_level_definitions(
            ROOT / "bcs_e2e/operator_cli_runner.py")
        plane_defs = _top_level_definitions(
            ROOT / "bcs_e2e/operator_cli_profile.py")
        self.assertFalse(set(MOVED_CONSTANTS) & runner_defs)
        self.assertFalse(set(MOVED_FUNCTIONS) & runner_defs)
        self.assertTrue(set(MOVED_CONSTANTS) <= plane_defs)
        self.assertTrue(set(MOVED_FUNCTIONS) <= plane_defs)

    def test_runner_reexports_the_public_plane_objects(self):
        public = MOVED_CONSTANTS + ("validate_operator_cli_profile",)
        for name in public:
            self.assertIs(getattr(runner, name), getattr(plane, name), name)

    def test_private_field_validators_live_only_in_the_plane(self):
        for name in ("_env_ref", "_actor", "_route_binding"):
            self.assertTrue(callable(getattr(plane, name)))
            self.assertFalse(hasattr(runner, name))

    def test_validator_behavior_is_identical_through_both_paths(self):
        with self.assertRaises(ConfigError):
            runner.validate_operator_cli_profile([])
        with self.assertRaises(ConfigError):
            plane.validate_operator_cli_profile([])
        from test_operator_cli_runner import profile_value
        raw = profile_value()
        self.assertEqual(runner.validate_operator_cli_profile(raw),
                         plane.validate_operator_cli_profile(raw))
