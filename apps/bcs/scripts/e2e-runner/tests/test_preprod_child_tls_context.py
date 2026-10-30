"""Fail-closed TLS guards for the script-mode dual preprod children.

r8 regression evidence: the service-policy and draft-delivery children and
the authority admission probe used bare ``HTTPSConnection`` / ``build_opener``
without an explicit SSL context. Framework Python on macOS resolves OpenSSL
default verify paths to an empty CA store, so every preprod leg died with
``SSLCertVerificationError`` and the case receipts reported fake BLOCKED
transport verdicts. All three transport points must share the fail-closed
``tls_context()`` (system store -> certifi -> system bundle; verification
never disabled).
"""
from __future__ import annotations

import ssl
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.transport import tls_context

SERVICE_POLICY_STORY = ROOT / "bcs_e2e" / "service_policy" / "story.py"
DRAFT_DELIVERY_STORY = ROOT / "bcs_e2e" / "draft_delivery" / "story.py"
AUTHORITY_DUAL = ROOT / "bcs_e2e" / "authority" / "dual_case_run.py"


class TlsContextFailClosedTests(unittest.TestCase):
    def test_context_verifies_chain_and_hostname(self) -> None:
        context = tls_context()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


class ChildStoryBootstrapTests(unittest.TestCase):
    def test_service_policy_story_reuses_shared_tls_context(self) -> None:
        source = SERVICE_POLICY_STORY.read_text(encoding="utf-8")
        self.assertIn(
            "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))", source)
        self.assertIn("from bcs_e2e.transport import tls_context", source)
        self.assertIn("context=tls_context())", source)

    def test_draft_delivery_story_reuses_shared_tls_context(self) -> None:
        source = DRAFT_DELIVERY_STORY.read_text(encoding="utf-8")
        self.assertIn(
            "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))", source)
        self.assertIn("from bcs_e2e.transport import tls_context", source)
        self.assertIn("context=tls_context())", source)
        self.assertNotIn(
            "http.client.HTTPSConnection if self.base.scheme",
            source,
            "conditional constructor cannot carry an SSL context",
        )

    def test_script_children_compile(self) -> None:
        for path in (SERVICE_POLICY_STORY, DRAFT_DELIVERY_STORY, AUTHORITY_DUAL):
            subprocess.run(
                [sys.executable, "-m", "py_compile", str(path)], check=True)


class AuthorityProbeOpenerTests(unittest.TestCase):
    def test_probe_opener_pins_tls_context(self) -> None:
        source = AUTHORITY_DUAL.read_text(encoding="utf-8")
        self.assertIn("HTTPSHandler(context=tls_context())", source)
        self.assertIn(
            "from urllib.request import HTTPSHandler, ProxyHandler, build_opener", source)
        self.assertIn("from ..transport import NoRedirect, tls_context", source)


if __name__ == "__main__":
    unittest.main()
