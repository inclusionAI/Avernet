from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.discovery.service import _resolve_workspace_root  # noqa: E402
from clawevolve_plan.discovery.schema import target_guard_problems  # noqa: E402


class DiscoveryWorkspaceResolutionTests(unittest.TestCase):
    def test_private_release_root_cannot_be_a_discovery_target(self):
        with tempfile.TemporaryDirectory(prefix="plan-private-release-") as td:
            workspace = Path(td) / "workspace"
            target = workspace / "clawevolve-skills" / "clawevolve-plan" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("name: clawevolve-plan\n", encoding="utf-8")

            problems = target_guard_problems(
                "clawevolve-skills/clawevolve-plan/SKILL.md",
                workspace_root=workspace,
            )

        self.assertTrue(problems)
        self.assertTrue(any("forbidden" in problem.lower() for problem in problems))

    def test_resolves_workspace_from_plan_input_dir_when_baas_cwd_is_unrelated(self):
        with tempfile.TemporaryDirectory(prefix="plan-baas-workspace-") as td:
            root = Path(td)
            workspace = root / ".openclaw" / "workspace"
            input_dir = workspace / "clawevolve_results" / "EV-1" / "plan" / "input"
            (workspace / "skills").mkdir(parents=True)
            input_dir.mkdir(parents=True)
            unrelated_cwd = root / "baas-launcher"
            unrelated_cwd.mkdir()

            with patch.dict(
                os.environ,
                {"CLAWEVOLVE_INVOCATION_CWD": str(unrelated_cwd)},
                clear=False,
            ), patch(
                "clawevolve_plan.discovery.service.os.getcwd",
                return_value=str(unrelated_cwd),
            ):
                resolved = _resolve_workspace_root({}, workspace_hint=input_dir)

        self.assertEqual(resolved, workspace.resolve())

    def test_resolves_openclaw_workspace_when_invoked_from_installed_plan_skill(self):
        with tempfile.TemporaryDirectory(prefix="plan-openclaw-workspace-") as td:
            workspace = Path(td) / ".openclaw" / "workspace"
            (workspace / "skills").mkdir(parents=True)
            plan_skill = workspace / "clawevolve-skills" / "clawevolve-plan"
            (plan_skill / "clawevolve_plan").mkdir(parents=True)
            (plan_skill / "SKILL.md").write_text("name: clawevolve-plan\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"CLAWEVOLVE_INVOCATION_CWD": str(plan_skill)},
                clear=False,
            ), patch("clawevolve_plan.discovery.service.os.getcwd", return_value=str(plan_skill)):
                resolved = _resolve_workspace_root({})

        self.assertEqual(resolved, workspace.resolve())

    def test_explicit_target_workspace_still_wins_over_installed_skill_fallback(self):
        with tempfile.TemporaryDirectory(prefix="plan-explicit-workspace-") as td:
            root = Path(td)
            explicit_workspace = root / "target-workspace"
            explicit_workspace.mkdir()
            installed_workspace = root / ".openclaw" / "workspace"
            plan_skill = installed_workspace / "skills" / "clawevolve-plan"
            (plan_skill / "clawevolve_plan").mkdir(parents=True)
            (plan_skill / "SKILL.md").write_text("name: clawevolve-plan\n", encoding="utf-8")
            plan = {"agent_context": {"workspace_root": str(explicit_workspace)}}

            with patch.dict(
                os.environ,
                {"CLAWEVOLVE_INVOCATION_CWD": str(plan_skill)},
                clear=False,
            ), patch("clawevolve_plan.discovery.service.os.getcwd", return_value=str(plan_skill)):
                resolved = _resolve_workspace_root(plan)

        self.assertEqual(resolved, explicit_workspace.resolve())

    def test_explicit_target_workspace_wins_over_plan_input_dir_hint(self):
        with tempfile.TemporaryDirectory(prefix="plan-explicit-hint-") as td:
            root = Path(td)
            explicit_workspace = root / "target-workspace"
            explicit_workspace.mkdir()
            inferred_workspace = root / ".openclaw" / "workspace"
            input_dir = inferred_workspace / "clawevolve_results" / "EV-1" / "plan" / "input"
            (inferred_workspace / "skills").mkdir(parents=True)
            input_dir.mkdir(parents=True)
            plan = {"agent_context": {"workspace_root": str(explicit_workspace)}}

            resolved = _resolve_workspace_root(plan, workspace_hint=input_dir)

        self.assertEqual(resolved, explicit_workspace.resolve())


if __name__ == "__main__":
    unittest.main()
