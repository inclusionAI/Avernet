import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACK = load_module("pack_noise", ROOT / "clawevolve-pack/scripts/pack.py")
DEPLOY = load_module("deploy_noise", ROOT / "clawevolve-deploy/scripts/deploy.py")


class PackNoiseTest(unittest.TestCase):
    def test_pack_excludes_nested_python_runtime_cache_only(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            skill = base / "workspace/skills/skills-local/demo"
            cache = skill / "nested/__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.cpython-311.pyc").write_bytes(b"cache")
            (skill / "nested/direct.pyc").write_bytes(b"cache")
            (skill / "nested/module.py").write_text("print('keep')", encoding="utf-8")
            (skill / "nested/cache.txt").write_text("keep", encoding="utf-8")
            (skill / "SKILL.md").write_text("valid", encoding="utf-8")

            PACK.handler_skill(base / "workspace", base / "staging", object())

            packed = base / "staging/skills/skills-local/demo/nested"
            self.assertFalse((packed / "__pycache__").exists())
            self.assertFalse((packed / "direct.pyc").exists())
            self.assertTrue((packed / "module.py").is_file())
            self.assertTrue((packed / "cache.txt").is_file())

    def test_pack_preserves_macos_metadata_from_skills(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            skills = base / "workspace/skills"
            (skills / "__MACOSX").mkdir(parents=True)
            (skills / "__MACOSX/._skill").write_text("metadata")
            (skills / "valid-skill").mkdir()
            (skills / "valid-skill/SKILL.md").write_text("valid")

            result = PACK.handler_skill(base / "workspace", base / "staging", object())

            self.assertEqual(
                (base / "staging/skills/__MACOSX/._skill").read_text(),
                "metadata",
            )
            self.assertTrue((base / "staging/skills/valid-skill/SKILL.md").is_file())
            self.assertEqual(result["skills"], [])
            self.assertFalse(any("__MACOSX" in item for item in result["excluded"]))

    def test_pack_preserves_nested_git_but_excludes_nfs_handles(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            skill = base / "workspace/skills/skills-local/data-preprocessing"
            (skill / ".git/objects/aa").mkdir(parents=True)
            (skill / ".git/objects/aa/.nfs123").write_text("held object")
            (skill / "runtime/.nfs456").parent.mkdir(parents=True)
            (skill / "runtime/.nfs456").write_text("held runtime file")
            (skill / "runtime/keep.txt").write_text("keep")
            (skill / "SKILL.md").write_text("valid")
            os.symlink("skills-local/data-preprocessing", base / "workspace/skills/data-preprocessing")

            PACK.handler_skill(base / "workspace", base / "staging", object())

            packed = base / "staging/skills/skills-local/data-preprocessing"
            self.assertTrue((packed / ".git/objects/aa").is_dir())
            self.assertFalse((packed / ".git/objects/aa/.nfs123").exists())
            self.assertFalse((packed / "runtime/.nfs456").exists())
            self.assertEqual((packed / "runtime/keep.txt").read_text(), "keep")

    def test_pack_preserves_top_level_entity_skill_with_git_directory(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            skill = base / "workspace/skills/direct-skill"
            (skill / ".git/objects").mkdir(parents=True)
            (skill / ".git/objects/object").write_text("git object")
            (skill / "SKILL.md").write_text("valid")

            PACK.handler_skill(base / "workspace", base / "staging", object())

            packed = base / "staging/skills/direct-skill"
            self.assertTrue((packed / "SKILL.md").is_file())
            self.assertEqual((packed / ".git/objects/object").read_text(), "git object")

    def test_pack_preserves_generic_directory(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            noise = base / "workspace/skills/download-cache"
            noise.mkdir(parents=True)
            (noise / "payload.txt").write_text("not a skill")

            result = PACK.handler_skill(base / "workspace", base / "staging", object())

            self.assertEqual(
                (base / "staging/skills/download-cache/payload.txt").read_text(),
                "not a skill",
            )
            self.assertEqual(result["skills"], [])

    def test_deploy_preserves_git_and_filters_nfs_handles(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            staging = base / "staging"
            skill = staging / "skills/skills-local/data-preprocessing"
            (skill / ".git/objects/aa").mkdir(parents=True)
            (skill / ".git/objects/aa/.nfs123").write_text("legacy contamination")
            (skill / "runtime").mkdir()
            (skill / "runtime/.nfs456").write_text("legacy handle")
            (skill / "runtime/keep.txt").write_text("keep")
            (skill / "SKILL.md").write_text("valid")
            os.symlink("skills-local/data-preprocessing", staging / "skills/data-preprocessing")
            workspace = base / "workspace"

            manifest = {"privateSkillItems": [{
                "name": "data-preprocessing",
                "layout": "nested",
                "linkTarget": "skills-local/data-preprocessing",
            }]}
            laid = DEPLOY.laydown(staging, workspace, set(), False, manifest)

            deployed = workspace / "skills/skills-local/data-preprocessing"
            self.assertIn("skills-local", laid["skills_top"])
            self.assertTrue((deployed / ".git/objects/aa").is_dir())
            self.assertFalse((deployed / ".git/objects/aa/.nfs123").exists())
            self.assertFalse((deployed / "runtime/.nfs456").exists())
            self.assertEqual((deployed / "runtime/keep.txt").read_text(), "keep")

    def test_deploy_preserves_macos_metadata_in_legacy_pack(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            staging = base / "staging"
            (staging / "skills/__MACOSX").mkdir(parents=True)
            (staging / "skills/__MACOSX/._skill").write_text("metadata")
            (staging / "skills/valid-skill").mkdir()
            (staging / "skills/valid-skill/SKILL.md").write_text("valid")
            workspace = base / "workspace"

            laid = DEPLOY.laydown(staging, workspace, set(), False, {"privateSkillItems": []})
            issues = DEPLOY.verify_laydown(workspace, set(), {"_raw": ""}, None)

            self.assertEqual(laid["skills_top"], ["__MACOSX", "valid-skill"])
            self.assertEqual((workspace / "skills/__MACOSX/._skill").read_text(), "metadata")
            self.assertTrue((workspace / "skills/valid-skill/SKILL.md").is_file())
            self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
