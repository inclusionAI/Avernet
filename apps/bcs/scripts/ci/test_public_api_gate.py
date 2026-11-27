"""Regression tests for public API gate prerequisites and cleanup (no Cargo build)."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "apps/bcs/scripts/ci/check-public-api.sh"


class PublicApiGateTests(unittest.TestCase):
    def test_linked_worktree_cleanup(self):
        script = SCRIPT.read_text()
        cleanup = script[script.index("cleanup() {"):script.index("trap cleanup EXIT")]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            (checkout / ".git").write_text("gitdir: fixture\n")
            subprocess.run(["bash", "-c", 'set -eu; git() { return 0; }; '
                            'BCS_ROOT="$1"; BASE_WORKTREE="$1/checkout"; '
                            + cleanup + '\ncleanup', "bash", directory], check=True)
            self.assertFalse(checkout.exists())

    def test_ci_self_installs_pinned_nightly(self):
        # The repo toolchain invariant (test_bcs_rust_toolchain) forbids any
        # toolchain install but the pinned stable in workflow text, so the
        # gate self-installs its pinned nightly when running under CI.
        workflow = (ROOT / ".github/workflows/architecture-checks.yml").read_text()
        self.assertNotIn("nightly", workflow)
        self.assertIn("cargo-public-api --version 0.52.0", workflow)
        script = SCRIPT.read_text()
        self.assertIn('rustup toolchain install "$NIGHTLY_TOOLCHAIN" --profile minimal', script)
        self.assertIn('rustup component add rust-src --toolchain "$NIGHTLY_TOOLCHAIN"', script)

    def test_ci_prerequisites_are_mandatory(self):
        self.assertIn('PREREQUISITE_EXIT=1', SCRIPT.read_text())

    def test_old_persistence_shapes_are_checked(self):
        self.assertIn("--test persistence_shape", SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
