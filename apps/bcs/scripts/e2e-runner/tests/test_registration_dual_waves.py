"""Registration-surface guard for the dual-environment case runner waves.

Every dual case runner landed by a migration wave must be registered in
CASE_COMMANDS, carry its parser, and be wired into the CLI dispatch; this
guard pins the wave registry so an unregistered wrapper cannot silently
reappear.
"""
from __future__ import annotations

import unittest

from bcs_e2e.case_command_registration import CASE_COMMANDS


class DualWaveRegistrationTests(unittest.TestCase):
    def test_wave_commands_are_registered(self):
        self.assertIn("dual-structured-density-run", CASE_COMMANDS)
        self.assertIn("dual-fixed-loop-run", CASE_COMMANDS)
        self.assertIn("dual-nested-fixed-loop-run", CASE_COMMANDS)
        self.assertIn("dual-result-queue-run", CASE_COMMANDS)
        self.assertIn("dual-draft-group-run", CASE_COMMANDS)
        self.assertIn("dual-workspace-field-run", CASE_COMMANDS)
        self.assertIn("dual-delivery-policy-density-run", CASE_COMMANDS)
        self.assertIn("dual-service-policy-density-run", CASE_COMMANDS)

    def test_cli_builds_the_registered_parsers(self):
        from bcs_e2e.cli import main

        for command in ("dual-structured-density-run", "dual-fixed-loop-run",
                        "dual-nested-fixed-loop-run", "dual-result-queue-run",
                        "dual-draft-group-run", "dual-workspace-field-run",
                        "dual-delivery-policy-density-run",
                        "dual-service-policy-density-run"):
            with self.subTest(command=command):
                with self.assertRaises(SystemExit) as caught:
                    main([command, "--help"])
                self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
