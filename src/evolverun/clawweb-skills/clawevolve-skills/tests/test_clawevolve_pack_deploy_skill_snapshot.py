import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

pack = load("snapshot_pack", "clawevolve-pack/scripts/pack.py")
deploy = load("snapshot_deploy", "clawevolve-deploy/scripts/deploy.py")


class SkillSnapshotTest(unittest.TestCase):
    def test_openversion_flat_skill_pack_restore_without_intermediate_directory(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CLAWWEB_VERSION": "openversion"}):
            base = Path(td); ws = base / "workspace"; staging = base / "package"
            skill = ws / "skills/sample/SKILL.md"
            skill.parent.mkdir(parents=True); skill.write_text("---\nname: sample\ndescription: sample\n---\nBody")
            shared = base / "shared"; shared.mkdir(); (shared / "private.txt").write_text("not packed")
            (ws / "skills/shared").symlink_to(shared)
            staging.mkdir(); rec = pack.handler_skill(ws, staging, object())
            self.assertFalse((staging / "skills-local").exists())
            self.assertFalse((staging / "skills/skills-local").exists())
            self.assertFalse((staging / "skills/shared").exists())
            self.assertEqual((staging / "skills/sample/SKILL.md").read_bytes(), skill.read_bytes())
            archive = base / "pack.zip"; pack._write_zip_with_symlinks(staging, archive)
            unpacked = base / "unpacked"; deploy._extract_zip_with_symlinks(archive, unpacked)
            package = unpacked / "package"
            target = base / "restored"; target.mkdir()
            manifest = {"_raw": pack.to_yaml({"layers": [rec]}), "layers": ["skill"]}
            deploy.laydown(package, target, set(), True, manifest)
            self.assertEqual((target / "skills/sample/SKILL.md").read_bytes(), skill.read_bytes())
            self.assertFalse((target / "skills-local").exists())
            self.assertFalse((target / "skills/skills-local").exists())
            self.assertEqual(deploy.verify_laydown(target, set(), manifest, staging=package), [])
            # Restore again after a user edit: the same flat snapshot is restored.
            (target / "skills/sample/SKILL.md").write_text("changed")
            deploy.laydown(package, target, set(), True, manifest)
            self.assertEqual((target / "skills/sample/SKILL.md").read_bytes(), skill.read_bytes())

    def test_mcp_is_preserved_as_raw_bytes_even_when_json_is_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); ws = base / "workspace"; staging = base / "package"
            source = ws / "config/mcporter.json"
            source.parent.mkdir(parents=True)
            raw = b'{"TOKEN":"one","TOKEN":"two"}\n{not-json}\x00'
            source.write_bytes(raw)
            staging.mkdir()

            result = pack.handler_mcp(ws, staging, object())

            self.assertFalse(result.get("skipped", False))
            self.assertEqual((staging / "mcp/mcporter.json").read_bytes(), raw)

    def test_pack_preserves_broken_absolute_links_and_empty_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); ws = base / "workspace"; staging = base / "package"
            (ws / "skills").mkdir(parents=True); (ws / "skills-local").mkdir()
            (ws / "skills/empty").mkdir()
            os.symlink("/does/not/exist", ws / "skills/mcporter")
            os.symlink("skills-local/missing", ws / "skills/broken")
            os.symlink("/shared/skills-repo", ws / "skills/skills-repo")
            (ws / "skills/skills-local/clawevolve-runtime").mkdir(parents=True)
            staging.mkdir()
            result = pack.handler_skill(ws, staging, object())
            self.assertNotIn("paths", result)
            self.assertEqual(result["snapshot"]["roots"], [
                {"path": "skills/", "present": True},
                {"path": "skills-local/", "present": True},
            ])
            self.assertEqual(result["snapshot"]["digestAlgorithm"], "personal-skill-snapshot-v1")
            self.assertFalse((staging / "skills/mcporter").exists())
            self.assertEqual(os.readlink(staging / "skills/broken"), "skills-local/missing")
            self.assertFalse((staging / "skills/skills-repo").exists())
            self.assertTrue((staging / "skills/empty").is_dir())
            self.assertFalse((staging / "skills/skills-local/clawevolve-runtime").exists())
            self.assertTrue(any("Release managed" in item for item in result["excluded"]))

    def test_zip_preserves_empty_directories_and_executable_bit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; unpacked = base / "unpacked"
            empty = staging / "skills-local/empty"
            empty.mkdir(parents=True)
            script = staging / "skills/run.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            script.chmod(0o755)
            archive = base / "snapshot.zip"
            pack._write_zip_with_symlinks(staging, archive)
            with zipfile.ZipFile(archive) as zf:
                self.assertIn("package/skills-local/empty/", zf.namelist())
            deploy._extract_zip_with_symlinks(archive, unpacked)
            self.assertTrue((unpacked / "package/skills-local/empty").is_dir())
            self.assertTrue((unpacked / "package/skills/run.sh").stat().st_mode & 0o100)

    def test_zip_defensively_excludes_python_runtime_cache(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"
            cache = staging / "skills-local/demo/__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.cpython-311.pyc").write_bytes(b"cache")
            (staging / "skills-local/demo/direct.pyc").write_bytes(b"cache")
            (staging / "skills-local/demo/module.py").write_text("keep", encoding="utf-8")
            archive = base / "snapshot.zip"

            pack._write_zip_with_symlinks(staging, archive)

            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any(name.endswith(".pyc") for name in names))
            self.assertIn("package/skills-local/demo/module.py", names)

    def test_zip_and_deploy_round_trip_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); ws = base / "workspace"; staging = base / "package"
            (ws / "skills").mkdir(parents=True); (ws / "skills-local").mkdir()
            (ws / "skills/foo").write_text("foo", encoding="utf-8")
            (ws / "skills-local/private").mkdir()
            (ws / "skills-local/private/data.txt").write_text("data", encoding="utf-8")
            os.symlink("../skills-local/private", ws / "skills/private")
            staging.mkdir(); rec = pack.handler_skill(ws, staging, object())
            raw = pack.to_yaml({"layers": [rec]})
            manifest = {"_raw": raw, "layers": ["skill"]}
            target = base / "target"; target.mkdir()
            deploy.laydown(staging, target, set(), True, manifest)
            self.assertEqual(os.readlink(target / "skills/private"), "../skills-local/private")
            self.assertEqual((target / "skills/foo").read_text(), "foo")
            self.assertEqual((target / "skills-local/private/data.txt").read_text(), "data")
            self.assertEqual(deploy.verify_laydown(target, set(), manifest, staging=staging), [])

    def test_no_follow_existing_external_parent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"; outside = base / "outside"
            (staging / "skills/foo").mkdir(parents=True); (staging / "skills/foo/bar").write_text("x")
            target.mkdir(); (target / "skills").mkdir(); outside.mkdir()
            (target / "skills/foo").symlink_to(outside, target_is_directory=True)
            deploy.laydown(staging, target, set(), False, {"_raw": "", "layers": []})
            self.assertTrue((target / "skills/foo").is_symlink())
            self.assertFalse((target / "skills/foo/bar").exists())
            self.assertFalse((outside / "bar").exists())

    def test_non_force_rejects_symlink_snapshot_root_without_following_it(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"; outside = base / "outside"
            (staging / "skills").mkdir(parents=True); (staging / "skills/value").write_text("new")
            target.mkdir(); outside.mkdir(); (outside / "value").write_text("outside")
            (target / "skills").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(RuntimeError):
                deploy.laydown(staging, target, set(), False, {"_raw": "", "layers": []})
            self.assertEqual((outside / "value").read_text(), "outside")

            deploy.laydown(staging, target, set(), True, {"_raw": "", "layers": []})
            self.assertFalse((target / "skills").is_symlink())
            self.assertEqual((target / "skills/value").read_text(), "new")
            self.assertEqual((outside / "value").read_text(), "outside")

    def test_non_force_restore_replaces_existing_bot_entries_and_keeps_release(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"
            (staging / "skills").mkdir(parents=True)
            (staging / "skills/new.txt").write_text("new")
            (target / "skills/old").mkdir(parents=True)
            (target / "skills/old/value.txt").write_text("old")
            release = target / "skills/clawevolve-runtime"
            release.mkdir(); (release / "state.txt").write_text("keep")

            deploy.laydown(staging, target, set(), False, {"_raw": "", "layers": ["skill"]})

            self.assertFalse((target / "skills/old").exists())
            self.assertEqual((target / "skills/new.txt").read_text(), "new")
            self.assertEqual((release / "state.txt").read_text(), "keep")

    def test_deploy_accepts_invalid_json_mcp_and_verifies_raw_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"
            (staging / "mcp").mkdir(parents=True)
            raw = b'{not-json}\n\x00'
            (staging / "mcp/mcporter.json").write_bytes(raw)
            target.mkdir()
            manifest = {
                "_raw": """  - name: mcp
    path: mcp/mcporter.json
""",
                "layers": ["mcp"],
            }

            deploy.laydown(staging, target, set(), False, manifest)

            self.assertEqual((target / "config/mcporter.json").read_bytes(), raw)
            self.assertEqual(deploy.verify_laydown(target, set(), manifest, staging=staging), [])

    def test_verify_ignores_nfs_handles_created_after_laydown(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"
            expected_dir = staging / "skills/skills-local/.clawevolve-deploy.backup.1/scripts"
            actual_dir = target / "skills/skills-local/.clawevolve-deploy.backup.1/scripts"
            expected_dir.mkdir(parents=True); actual_dir.mkdir(parents=True)
            (actual_dir / ".nfs0000000000000001").write_text("held")
            manifest = {"_raw": "", "layers": ["skill"], "schemaVersion": 2}

            self.assertEqual(deploy.verify_laydown(target, set(), manifest, staging=staging), [])

    def test_absent_root_is_removed_but_release_entry_survives(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"
            staging.mkdir(); target.mkdir(); (target / "skills").mkdir(); (target / "skills-local").mkdir()
            (target / "skills/old").write_text("old")
            (target / "skills/clawevolve-runtime").mkdir()
            (target / "skills/clawevolve-runtime/state").write_text("keep")
            (target / "skills-local/old").write_text("old")
            deploy.laydown(staging, target, set(), True, {"_raw": "", "layers": []})
            self.assertFalse((target / "skills/old").exists())
            self.assertTrue((target / "skills/clawevolve-runtime/state").is_file())
            self.assertFalse((target / "skills-local").exists())

    def test_absent_root_keeps_nested_release_parent_only(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"
            staging.mkdir(); (target / "skills/skills-local/clawevolve-pack").mkdir(parents=True)
            (target / "skills/skills-local/clawevolve-pack/SKILL.md").write_text("runtime")
            (target / "skills/skills-local/user-skill").mkdir()
            deploy.laydown(staging, target, set(), True, {"_raw": "", "layers": []})
            self.assertTrue((target / "skills/skills-local/clawevolve-pack/SKILL.md").is_file())
            self.assertFalse((target / "skills/skills-local/user-skill").exists())

    def test_old_pack_without_snapshot_metadata_uses_actual_roots(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"; target = base / "target"
            (staging / "skills/skills-local/legacy").mkdir(parents=True)
            (staging / "skills/skills-local/legacy/SKILL.md").write_text("legacy")
            target.mkdir()
            legacy_manifest = {"_raw": "", "layers": ["skill"], "schemaVersion": 2}
            deploy.laydown(staging, target, set(), True, legacy_manifest)
            self.assertTrue((target / "skills/skills-local/legacy/SKILL.md").is_file())
            self.assertFalse((target / "skills-local").exists())

    def test_schema_v3_rejects_tampered_skill_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); staging = base / "package"
            (staging / "skills").mkdir(parents=True)
            (staging / "skills/file.txt").write_text("tampered")
            manifest = {
                "schemaVersion": 3,
                "skillSha256": "0" * 64,
                "skillDigestAlgorithm": "personal-skill-snapshot-v1",
                "_raw": """  - name: skill
    snapshot:
      roots:
        - path: skills/
          present: true
        - path: skills-local/
          present: false
""",
            }
            with self.assertRaisesRegex(RuntimeError, "摘要不一致"):
                deploy._validate_skill_snapshot(staging, manifest)

    def test_early_schema_v3_without_digest_algorithm_remains_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            staging = Path(td) / "package"
            (staging / "skills").mkdir(parents=True)
            (staging / "skills/file.txt").write_text("historical")
            manifest = {
                "schemaVersion": 3,
                "skillSha256": "0" * 64,
                "skillDigestAlgorithm": None,
                "_raw": """  - name: skill
    snapshot:
      roots:
        - path: skills/
          present: true
        - path: skills-local/
          present: false
""",
            }
            deploy._validate_skill_snapshot(staging, manifest)

    def test_legacy_stale_root_presence_uses_archive_tree(self):
        with tempfile.TemporaryDirectory() as td:
            staging = Path(td) / "package"
            (staging / "skills").mkdir(parents=True)
            (staging / "skills/file.txt").write_text("historical")
            manifest = {
                "schemaVersion": 2,
                "skillDigestAlgorithm": None,
                "_raw": """  - name: skill
    snapshot:
      roots:
        - path: skills/
          present: false
        - path: skills-local/
          present: true
""",
            }

            self.assertEqual(
                deploy._skill_snapshot_root_states(staging, manifest),
                {"skills": True, "skills-local": False},
            )
            deploy._validate_skill_snapshot(staging, manifest)

if __name__ == "__main__":
    unittest.main()

class DeploymentRollbackTest(unittest.TestCase):
    def test_real_snapshot_restores_original_after_partial_laydown_failure(self):
        import subprocess
        import sys
        import tarfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); ws = base / 'workspace'; ws.mkdir()
            (ws / 'AGENTS.md').write_text('packed content')
            out = base / 'out'
            subprocess.run([sys.executable, str(ROOT / 'clawevolve-pack/scripts/pack.py'), '--workspace', str(ws), '--out-dir', str(out)], check=True, capture_output=True)
            image = next(out.glob('*.zip'))
            (ws / 'AGENTS.md').write_text('before deployment')
            original = deploy.laydown
            def fail_after_write(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('injected failure after real writes')
            with mock.patch.object(deploy, 'laydown', side_effect=fail_after_write):
                result = deploy.main(['--image', str(image), '--workspace', str(ws), '--skip-evolve-results', '--force-overwrite'])
            self.assertEqual(result, 2)
            self.assertEqual((ws / 'AGENTS.md').read_text(), 'before deployment')
            backup = next((ws / '.deploy-backups').glob('*.tgz'))
            with tarfile.open(backup) as archive:
                member = next(m for m in archive.getmembers() if m.name.endswith('AGENTS.md'))
                self.assertEqual(archive.extractfile(member).read(), b'before deployment')
