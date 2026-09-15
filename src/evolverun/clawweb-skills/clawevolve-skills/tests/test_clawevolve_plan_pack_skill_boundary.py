import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "clawevolve-plan/clawevolve_plan/pack_skill_core.py"


def load_module():
    spec = importlib.util.spec_from_file_location("plan_pack_skill_boundary", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pack = load_module()


class PlanPackSkillBoundaryTests(unittest.TestCase):
    def test_initial_pack_excludes_release_entries_but_keeps_personal_skills(self):
        with tempfile.TemporaryDirectory(prefix="plan-pack-private-root-") as td:
            root = Path(td)
            workspace = root / "workspace"
            staging = root / "staging"
            local = workspace / "skills/skills-local"
            (local / "personal-skill").mkdir(parents=True)
            (local / "personal-skill/SKILL.md").write_text("personal", encoding="utf-8")
            (local / "clawevolve-workflow").mkdir()
            (local / "clawevolve-workflow/SKILL.md").write_text("release", encoding="utf-8")
            (local / ".clawbench-base.backup.1").mkdir()
            (local / ".clawbench-base.backup.1/.clawevolve-version").write_text(
                "v1", encoding="utf-8"
            )
            staging.mkdir()

            result = pack.handler_skill(workspace, staging, object())

            self.assertTrue((staging / "skills/skills-local/personal-skill/SKILL.md").is_file())
            self.assertFalse((staging / "skills/skills-local/clawevolve-workflow").exists())
            self.assertFalse((staging / "skills/skills-local/.clawbench-base.backup.1").exists())
            self.assertEqual(result["skills_local_items"], ["personal-skill"])


if __name__ == "__main__":
    unittest.main()
