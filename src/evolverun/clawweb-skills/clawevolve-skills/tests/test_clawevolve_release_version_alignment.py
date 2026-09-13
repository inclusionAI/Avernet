from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionAlignmentTest(unittest.TestCase):
    def test_packaging_aligns_skill_versions_with_release_sequence(self):
        source = (ROOT / "scripts/package_clawevolve_skills.sh").read_text(encoding="utf-8")
        self.assertIn('RELEASE_DATE="${BASH_REMATCH[1]}"', source)
        self.assertIn('RELEASE_SEQUENCE="${BASH_REMATCH[2]}"', source)
        self.assertIn("version_for_release", source)
        self.assertIn('"$RELEASE_DATE" "$RELEASE_SEQUENCE"', source)
        self.assertNotIn("bump_version", source)
