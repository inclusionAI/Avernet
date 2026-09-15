from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionAlignmentTest(unittest.TestCase):
    def test_individual_skill_version_files_are_removed(self):
        self.assertEqual(list(ROOT.glob("*/version")), [])
        self.assertFalse((ROOT / "scripts/package-clawbench.sh").exists())

    def test_packaging_uses_the_bundle_release_for_every_skill(self):
        source = (ROOT / "scripts/package_clawevolve_skills.sh").read_text(encoding="utf-8")
        self.assertIn("legacy per-Skill version file is not allowed", source)
        self.assertIn('printf \'%s\\n\' "$RELEASE_VERSION" > "$STAGING_DIR/skills/$name/.clawevolve-version"', source)
        self.assertIn('printf \'%s\\t%s\\t%s\\n\' "$name" "$RELEASE_VERSION" "$digest"', source)
        self.assertNotIn("version_for_release", source)
        self.assertNotIn("version bumped:", source)

    def test_runtime_update_decision_uses_only_the_bundle_release(self):
        source = (ROOT / "scripts/clawevolve_async_runner.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$installed_release" == "$release_version" ]]', source)
        self.assertIn('version_is_newer "$installed_release" "$release_version" "clawevolve"', source)
        self.assertIn('"$manifest_skill_version" =~ ^[A-Za-z0-9._-]{1,128}$', source)
        self.assertNotIn('"$manifest_skill_version" == "$release_version"', source)
        self.assertNotIn('installed_version=""', source)
        self.assertNotIn('version_is_newer "$installed_version" "$packaged_version"', source)
