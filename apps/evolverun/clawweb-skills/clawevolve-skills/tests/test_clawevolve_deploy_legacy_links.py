import importlib.util
import os
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "clawevolve-deploy/scripts/deploy.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_deploy_legacy_snapshot", SCRIPT)
assert SPEC and SPEC.loader
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class DeployLegacySnapshotTest(unittest.TestCase):
    def test_old_private_metadata_is_ignored_and_actual_tree_is_restored(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); staging = base / "package"; workspace = base / "workspace"
            (staging / "skills/skills-local/private").mkdir(parents=True)
            (staging / "skills/skills-local/private/SKILL.md").write_text("private")
            os.symlink("skills-local/missing", staging / "skills/broken")
            os.symlink("/shared/platform-skill", staging / "skills/shared")
            workspace.mkdir()
            old_manifest = {
                "schemaVersion": 2,
                "_raw": "",
                "layers": ["skill"],
                "privateSkillItems": [{"name": "private", "layout": "nested"}],
            }

            deploy.laydown(staging, workspace, set(), True, old_manifest)

            self.assertTrue((workspace / "skills/skills-local/private/SKILL.md").is_file())
            self.assertEqual(os.readlink(workspace / "skills/broken"), "skills-local/missing")
            self.assertFalse((workspace / "skills/shared").exists())

    def test_backup_clear_and_rollback_cover_both_roots(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"
            (workspace / "skills").mkdir(parents=True)
            (workspace / "skills-local/private").mkdir(parents=True)
            (workspace / "skills-local/private/SKILL.md").write_text("private")
            os.symlink("../skills-local/private", workspace / "skills/private")

            backup = deploy.backup_workspace(workspace, set(), base / "backup")
            deploy._clear_skills_entries(workspace / "skills")
            self.assertFalse((workspace / "skills/private").exists())
            self.assertFalse((workspace / "skills-local/private").exists())

            deploy.rollback(workspace, backup, set())
            self.assertEqual(os.readlink(workspace / "skills/private"), "../skills-local/private")
            self.assertTrue((workspace / "skills-local/private/SKILL.md").is_file())

    def test_rollback_preserves_current_release_and_restores_bot_entries(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"
            release = workspace / "skills/skills-local/clawevolve-deploy"
            release.mkdir(parents=True)
            (release / "state.txt").write_text("before-backup")
            (workspace / "skills/user").mkdir(parents=True)
            (workspace / "skills/user/value.txt").write_text("before")

            backup = deploy.backup_workspace(workspace, set(), base / "backup")
            (release / "state.txt").write_text("current-release")
            (workspace / "skills/user/value.txt").write_text("partial")
            deploy.rollback(workspace, backup, set())

            self.assertEqual((release / "state.txt").read_text(), "current-release")
            self.assertEqual((workspace / "skills/user/value.txt").read_text(), "before")

    def test_release_entries_survive_clear_and_stale_pack_entries_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"; staging = base / "package"
            runtime = workspace / "skills/skills-local/clawevolve-deploy"
            runtime.mkdir(parents=True)
            (runtime / "scripts").mkdir(); (runtime / "scripts/deploy.sh").write_text("current")
            os.symlink("skills-local/clawevolve-deploy", workspace / "skills/clawevolve-deploy")
            stale = staging / "skills/skills-local/clawevolve-deploy"
            stale.mkdir(parents=True); (stale / "scripts").mkdir(); (stale / "scripts/deploy.sh").write_text("stale")
            (staging / "skills/user").mkdir(parents=True)
            (staging / "skills/user/value").write_text("new")

            deploy.laydown(staging, workspace, set(), True, {"_raw": "", "layers": ["skill"]})

            self.assertEqual((runtime / "scripts/deploy.sh").read_text(), "current")
            self.assertTrue((workspace / "skills/clawevolve-deploy").is_symlink())
            self.assertEqual((workspace / "skills/user/value").read_text(), "new")

    def test_public_and_mount_links_are_preserved_environment_entries(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"
            (workspace / "skills").mkdir(parents=True)
            os.symlink("/shared/repo", workspace / "skills/skills-repo")
            os.symlink("/shared/public", workspace / "skills/public")
            removed = deploy._clear_skills_entries(workspace / "skills")
            self.assertEqual(removed, [])
            self.assertTrue((workspace / "skills/public").is_symlink())
            self.assertTrue((workspace / "skills/skills-repo").is_symlink())

    def test_transaction_backup_excludes_public_and_release_sync_entries(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); workspace = base / "workspace"
            (workspace / "skills").mkdir(parents=True)
            (workspace / "skills-local/private").mkdir(parents=True)
            (workspace / "skills-local/private/SKILL.md").write_text("private")
            os.symlink("../skills-local/private", workspace / "skills/private")
            os.symlink("/shared/skills-repo/public", workspace / "skills/public")
            os.symlink("/shared/skills-pool/skills-repo", workspace / "skills/skills-repo")
            (workspace / "skills/skills-local/.clawevolve-deploy.backup.1").mkdir(parents=True)

            backup = deploy.backup_workspace(workspace, set(), base / "backup")
            with tarfile.open(backup, "r:gz") as archive:
                names = set(archive.getnames())

            self.assertIn("skills/private", names)
            self.assertIn("skills-local/private/SKILL.md", names)
            self.assertNotIn("skills/public", names)
            self.assertNotIn("skills/skills-repo", names)
            self.assertFalse(any("clawevolve-deploy.backup" in name for name in names))

    def test_broken_links_are_valid_when_they_match_the_package(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root); staging = base / "package"; workspace = base / "workspace"
            (staging / "skills").mkdir(parents=True)
            os.symlink("skills-local/missing", staging / "skills/missing")
            workspace.mkdir()
            manifest = {"_raw": "", "layers": ["skill"]}
            deploy.laydown(staging, workspace, set(), True, manifest)
            self.assertEqual(deploy.verify_laydown(workspace, set(), manifest, staging=staging), [])


if __name__ == "__main__":
    unittest.main()
