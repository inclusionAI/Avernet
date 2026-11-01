"""Exercise R25 discovery with real shell pipefail and a controlled Cargo CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "check-r25-conformance.sh"


class DiscoveryTest(unittest.TestCase):
    def discover(self, output, exit_code=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            listing = root / "listing"
            listing.write_text(output)
            cargo = root / "cargo"
            cargo.write_text('#!/bin/sh\ncat "$R25_TEST_LISTING"\nexit "$R25_TEST_EXIT"\n')
            cargo.chmod(0o755)
            # Load the actual gate functions, excluding its top-level suite.
            functions = SCRIPT.read_text().split("\ncheck_r25_1_trait_has_harness || fail=1")[0]
            return subprocess.run(
                ["bash", "-c", functions + "\ncheck_r25_3_conformance_discoverable\n"],
                env={**os.environ, "PATH": str(root) + ":" + os.environ["PATH"],
                     "R25_TEST_LISTING": str(listing), "R25_TEST_EXIT": str(exit_code)},
                text=True, capture_output=True, timeout=15,
            )

    def test_large_listing_with_early_conformance_match_is_discovered(self):
        result = self.discover("conformance_queue: test\n" + "ordinary_test: test\n" * 30_000)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS [R25.3]", result.stdout)

    def test_listing_without_conformance_entry_is_rejected(self):
        result = self.discover("ordinary_test: test\n" * 30_000)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not discover", result.stdout)

    def test_failed_cargo_cannot_pass_on_partial_listing(self):
        result = self.discover("conformance_queue: test\n", exit_code=101)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("discovery command failed", result.stdout)


if __name__ == "__main__":
    unittest.main()
