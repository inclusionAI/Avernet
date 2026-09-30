"""Exercise the E2E invocation recorder without starting a product stack."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class CliInvocationCoverageTest(unittest.TestCase):
    def test_records_chat_run_leaves_and_preserves_nested_aliases(self):
        common = Path(__file__).parent / "e2e-test" / "common.sh"
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "commands.log"
            subprocess.run(
                [
                    "bash", "-c", '''
source "$1"
get_bcs_cli_bin() { BCS_CLI_BIN_PATH=true; }
bcs_cli '' chat-run status --run-id coverage-probe
bcs_cli '' chat-run cancel --run-id coverage-probe
bcs_cli '' session file list --session coverage-probe
bcs_cli '' collaborate permission --session coverage-probe
''', "coverage-recorder", str(common),
                ],
                check=True,
                env={**os.environ, "BCS_CLI_COVERAGE_LOG": str(log)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(log.read_text().splitlines(), [
                "chat-run status", "chat-run cancel", "session file list", "collaboration permission",
            ])


if __name__ == "__main__":
    unittest.main()
