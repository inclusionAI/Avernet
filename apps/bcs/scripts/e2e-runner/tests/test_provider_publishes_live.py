"""Explicit opt-in only: frozen sheltered binaries; no build or preprod requests."""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.provider_publishes import dual_case_run as dual
from bcs_e2e.provider_publishes.contract import SERVER_SHA256, CLI_SHA256


@unittest.skipUnless(os.environ.get('BCS_E2E_S04_LIVE') == '1', 'explicit owned-local opt-in required')
class LiveTests(unittest.TestCase):
    def test_owned_local_98_live_receipts(self):
        base = Path.home() / '.codex/bcs-e2e'
        root = base / '20261031-w3a-provider-publishes-local'
        root.mkdir(mode=0o700, exist_ok=True)
        profile = root / 'entry-profile.json'
        if not profile.exists():
            profile.write_text(json.dumps(dual.profile_for('owned-local'), indent=2)+'\n')
        run_id = '20261031-w3a-provider-publishes'
        leaf = os.environ.get('BCS_E2E_S04_LEAF', 'attempt-1')
        self.assertRegex(leaf, r'^attempt-[1-9][0-9]*$')
        result = dual.dual_provider_publishes_case_run(profile_path=profile,
            binary=base/'20261031-wpe3-bcs-binary', binary_sha256=SERVER_SHA256,
            cli_binary=base/'20261031-wpe3-bcs-cli-binary', cli_binary_sha256=CLI_SHA256,
            state_dir=root/leaf, run_id=run_id, confirm_run_id=run_id, allow_write=True)
        self.assertEqual(result['status'], 'PASS', result['obligation_summary'])
        self.assertEqual(result['obligation_summary']['passed'], 98)
        self.assertEqual(result['local_owned_run']['shutdown']['status'], 'STOPPED')
        self.assertFalse(result['grants_release_credit'])
