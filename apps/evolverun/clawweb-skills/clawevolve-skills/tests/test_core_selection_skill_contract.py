from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CoreSelectionSkillContractTest(unittest.TestCase):
    def test_hardening_does_not_delegate_core_selection_to_the_agent(self) -> None:
        source = (ROOT / "clawevolve-hardening" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("begin-core", source)
        self.assertNotIn("selected: true", source)
        self.assertNotIn("selected: false", source)

    def test_hardening_skill_only_invokes_its_native_handler(self) -> None:
        source = (ROOT / "clawevolve-hardening" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/run.py", source)

    def test_platform_runtime_is_code_and_native_skills_do_not_select_the_core(self) -> None:
        self.assertFalse((ROOT / "clawevolve-stage" / "SKILL.md").exists())
        self.assertTrue((ROOT / "platform" / "clawevolve_runtime" / "runner.py").is_file())
        for name in ("clawevolve-diagnose", "clawevolve-plan", "clawevolve-workflow"):
            source = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("begin-core", source)
            self.assertNotIn("selected: true", source)
            self.assertNotIn("selected: false", source)


if __name__ == "__main__":
    unittest.main()
