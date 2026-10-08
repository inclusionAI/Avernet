import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pack = load("pack_skill_snapshot_boundary", "clawevolve-pack/scripts/pack.py")
deploy = load("deploy_skill_snapshot_boundary", "clawevolve-deploy/scripts/deploy.py")


class PackSkillBoundaryTest(unittest.TestCase):
    def test_snapshot_keeps_personal_links_and_excludes_public_links(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"; staging = base / "package"
            (workspace / "skills").mkdir(parents=True)
            (workspace / "skills-local/private").mkdir(parents=True)
            (workspace / "skills-local/private/SKILL.md").write_text("private")
            os.symlink("../skills-local/private", workspace / "skills/private")
            os.symlink("/shared/skills-repo/public", workspace / "skills/public")
            os.symlink("skills-local/missing", workspace / "skills/broken")
            staging.mkdir()

            pack.handler_skill(workspace, staging, object())

            self.assertEqual(os.readlink(staging / "skills/private"), "../skills-local/private")
            self.assertFalse((staging / "skills/public").exists())
            self.assertEqual(os.readlink(staging / "skills/broken"), "skills-local/missing")
            self.assertTrue((staging / "skills-local/private/SKILL.md").is_file())

    def test_release_managed_entries_are_excluded_at_all_skill_roots(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"; staging = base / "package"
            (workspace / "skills/skills-local/clawevolve-pack").mkdir(parents=True)
            (workspace / "skills/skills-local/.clawevolve-deploy.backup.1").mkdir(parents=True)
            (workspace / "skills-local/clawbench-base").mkdir(parents=True)
            os.symlink("skills-local/clawevolve-internal-only", workspace / "skills/clawevolve-internal-only")
            staging.mkdir()

            result = pack.handler_skill(workspace, staging, object())

            self.assertFalse((staging / "skills/skills-local/clawevolve-pack").exists())
            self.assertFalse((staging / "skills/skills-local/.clawevolve-deploy.backup.1").exists())
            self.assertFalse((staging / "skills-local/clawbench-base").exists())
            self.assertFalse((staging / "skills/clawevolve-internal-only").exists())
            self.assertEqual(len([x for x in result["excluded"] if "Release managed" in x]), 4)

    def test_private_release_runtime_root_is_not_part_of_pack_layers(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"; staging = base / "package"
            (workspace / "skills/skills-local/private-skill").mkdir(parents=True)
            (workspace / "skills/skills-local/private-skill/SKILL.md").write_text("private")
            (workspace / "clawevolve-skills/clawevolve-pack").mkdir(parents=True)
            (workspace / "clawevolve-skills/clawevolve-pack/SKILL.md").write_text("release")
            staging.mkdir()

            pack.handler_skill(workspace, staging, object())

            self.assertTrue((staging / "skills/skills-local/private-skill/SKILL.md").is_file())
            self.assertFalse((staging / "clawevolve-skills").exists())

    def test_pack_and_deploy_end_to_end_uses_schema_v3_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"; out = base / "out"
            (workspace / "skills").mkdir(parents=True)
            (workspace / "skills-local/write-bot-client").mkdir(parents=True)
            (workspace / "skills-local/write-bot-client/SKILL.md").write_text("client")
            (workspace / "SOUL.md").write_text("# Snapshot")
            os.symlink("../skills-local/write-bot-client", workspace / "skills/write-bot-client")

            self.assertEqual(pack.main([
                "--workspace", str(workspace), "--out-dir", str(out), "--bot-id", "snapshot-test",
            ]), 0)
            image = next(out.glob("*.zip"))
            staging, manifest = deploy.validate_image(image)
            target = base / "target"; target.mkdir()
            deploy.laydown(staging, target, set(), True, manifest)

            self.assertEqual(manifest["schemaVersion"], 3)
            self.assertEqual(os.readlink(target / "skills/write-bot-client"), "../skills-local/write-bot-client")
            self.assertTrue((target / "skills-local/write-bot-client/SKILL.md").is_file())
            self.assertEqual(deploy.verify_laydown(target, set(), manifest, staging=staging), [])

    def test_absolute_public_link_target_is_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"; staging = base / "package"
            (workspace / "skills").mkdir(parents=True); staging.mkdir()
            target = "/home/admin/.openclaw/workspace/skills/skills-repo/third-party/openclaw/mcporter"
            os.symlink(target, workspace / "skills/mcporter")
            pack.handler_skill(workspace, staging, object())
            self.assertFalse((staging / "skills/mcporter").exists())


if __name__ == "__main__":
    unittest.main()
