from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BAAS_JUSTFILE = REPO_ROOT / "src" / "baas" / "justfile"
GATEWAY_JUSTFILE = REPO_ROOT / "src" / "gateway" / "justfile"
LOCAL_TEST_BASE_LIB = REPO_ROOT / "scripts" / "lib" / "local_test_base.sh"


class LocalTestCoverageGateTest(unittest.TestCase):
    """The local `just test` recipes must align with GitHub CI by routing
    through ci_test.sh --base, while `just test-no-cov` keeps the fast feedback
    path that skips the gate. We parse justfile text rather than running `just`
    so the test stays hermetic."""

    def test_local_test_base_lib_exists(self) -> None:
        self.assertTrue(
            LOCAL_TEST_BASE_LIB.is_file(),
            f"expected shared resolver at {LOCAL_TEST_BASE_LIB}",
        )

    def test_baas_test_recipe_calls_ci_test_with_base(self) -> None:
        text = BAAS_JUSTFILE.read_text(encoding="utf-8")
        self.assertIn("source ../../scripts/lib/local_test_base.sh", text)
        self.assertIn("resolve_local_test_base", text)
        self.assertIn('./scripts/ci_test.sh --base "$base"', text)

    def test_gateway_test_recipe_calls_ci_test_with_base(self) -> None:
        text = GATEWAY_JUSTFILE.read_text(encoding="utf-8")
        self.assertIn("source ../../scripts/lib/local_test_base.sh", text)
        self.assertIn("resolve_local_test_base", text)
        self.assertIn('./scripts/ci_test.sh --base "$base"', text)

    def test_baas_test_no_cov_does_not_call_gate(self) -> None:
        text = BAAS_JUSTFILE.read_text(encoding="utf-8")
        # test-no-cov recipe must exist and must not pass --base to ci_test.sh.
        self.assertIn("test-no-cov", text)
        test_no_cov_block = self._recipe_block(text, "test-no-cov")
        self.assertNotIn("--base", test_no_cov_block)
        self.assertNotIn("resolve_local_test_base", test_no_cov_block)

    def test_gateway_test_no_cov_does_not_call_gate(self) -> None:
        text = GATEWAY_JUSTFILE.read_text(encoding="utf-8")
        self.assertIn("test-no-cov", text)
        test_no_cov_block = self._recipe_block(text, "test-no-cov")
        self.assertNotIn("--base", test_no_cov_block)
        self.assertNotIn("resolve_local_test_base", test_no_cov_block)

    def test_test_no_cov_keeps_run_ci_pipeline(self) -> None:
        for justfile in (BAAS_JUSTFILE, GATEWAY_JUSTFILE):
            text = justfile.read_text(encoding="utf-8")
            block = self._recipe_block(text, "test-no-cov")
            self.assertIn("run_ci_pipeline", block)

    @staticmethod
    def _recipe_block(text: str, name: str) -> str:
        """Return the body of a just recipe identified by its name.

        just recipes start with `name params:` at column 0 and continue through
        indented lines until the next non-indented non-blank line.
        """
        lines = text.splitlines()
        start = None
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped == "":
                continue
            if line[0].isspace():
                continue
            # Recipe header: name optionally followed by params, then `:`.
            head = stripped.split(":", 1)[0].strip()
            # Sanity: ensure we don't match e.g. `test-no-covx`.
            if head.split()[0] == name:
                start = index
                break
        if start is None:
            raise AssertionError(f"recipe '{name}' not found in justfile")
        body: list[str] = []
        for line in lines[start + 1:]:
            if line.strip() == "":
                body.append(line)
                continue
            if not line[0].isspace():
                break
            body.append(line)
        return "\n".join(body)


if __name__ == "__main__":
    unittest.main()