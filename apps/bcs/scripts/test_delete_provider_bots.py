#!/usr/bin/env python3
"""Unit tests for delete_provider_bots.py."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import delete_provider_bots


class DeleteProviderBotsTest(unittest.TestCase):
    def test_default_bot_list_matches_cleanup_batch(self):
        self.assertEqual(delete_provider_bots.DEFAULT_BOT_IDS, [])

    def test_delete_url_keeps_owner_suffix_colon(self):
        url = delete_provider_bots.delete_url(
            "https://bcs.example.com/",
            "prv_example",
            "demo-provider-bot:11111111",
        )

        self.assertEqual(
            url,
            "https://bcs.example.com/providers/prv_example/bots/demo-provider-bot:11111111",
        )

    def test_authorization_header_accepts_raw_token(self):
        args = delete_provider_bots.parse_args(["--token", "bcs_pa_token"])

        self.assertEqual(delete_provider_bots.authorization_header(args), "Bearer bcs_pa_token")

    def test_authorization_header_preserves_bearer_prefix(self):
        args = delete_provider_bots.parse_args(["--authorization", "Bearer bcs_pa_token"])

        self.assertEqual(delete_provider_bots.authorization_header(args), "Bearer bcs_pa_token")

    def test_execute_is_required_before_requests_are_sent(self):
        output = io.StringIO()

        with patch.object(delete_provider_bots, "delete_bot") as delete_bot:
            with redirect_stdout(output):
                exit_code = delete_provider_bots.main(["--token", "bcs_pa_token", "--cookie", "a=b"])

        self.assertEqual(exit_code, 0)
        delete_bot.assert_not_called()
        self.assertIn("dry-run", output.getvalue())


if __name__ == "__main__":
    unittest.main()
