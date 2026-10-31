"""package hygiene, line budget, credential-free guard Split module of the agent-network density charter."""
from __future__ import annotations

from agent_network_density_test_support import *  # noqa: E402,F401,F403
from agent_network_density_test_support import (  # noqa: E402
    _assert_all, _disk_lines, _disk_text, _BEARER, _guard_violations)



class AgentNetworkDensityHygieneTests(unittest.TestCase):
    def test_package_file_set_line_budget_and_guard_replica(self):
        self.assertEqual({path.name for path in PACKAGE_DIR.glob("*.py")},
                         {"__init__.py", "obligations.py", "replay.py",
                          "registration_proposal.py"})
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            self.assertLessEqual(
                len(path.read_text(encoding="utf-8").splitlines()), 1000,
                path.name)
            self.assertEqual(_guard_violations(path), [], path.name)
        self.assertLessEqual(
            len(Path(__file__).read_text(encoding="utf-8").splitlines()),
            1000)
        self.assertEqual(_guard_violations(Path(__file__)), [])

    def test_no_transport_process_env_or_credential_value_literals(self):
        text = "\n".join(path.read_text(encoding="utf-8")
                         for path in sorted(PACKAGE_DIR.glob("*.py")))
        for forbidden in ("urllib", "socket", "subprocess", "os.getenv",
                          "os.environ", "http.client"):
            self.assertNotIn(forbidden, text, f"forbidden literal {forbidden!r}")
        for pattern in (_BEARER + r"\s", "Cookie:" + r"\s", "-----BEGIN"):
            self.assertIsNone(re.search(pattern, text), pattern)
        uuid_like = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            self.assertIsNone(
                uuid_like.search(path.read_text(encoding="utf-8")), path.name)
        self.assertEqual((HUMAN_COOKIE_ENV in text,
                          DRIVER_TOKEN_ENV in text,
                          HUMAN_COOKIE_ENV + "=" in text,
                          DRIVER_TOKEN_ENV + "=" in text),
                         (True, True, False, False))

    def test_every_quoted_anchor_matches_disk_bytes(self):
        for row in obligations():
            self.assertTrue(row.quote)
            filename = Path(row.source_file).name
            lines = _disk_lines(filename)
            self.assertEqual(row.quote,
                             "\n".join(lines[row.line_start - 1:row.line_end]),
                             row.obligation_id)
            self.assertIn(row.quote, _disk_text(filename))
        for leaf_id, quote in AN_OBL.EXPECTED_LEAF_QUOTES.items():
            filename = "e2e.sh" if leaf_id == "AN.F03" else "stories.sh"
            self.assertIn(quote, _disk_text(filename))

    def test_module_shadow_trap_and_public_exports(self):
        import bcs_e2e.agent_network_density as package

        self.assertTrue(callable(package.obligations))
        self.assertIs(package.obligations, obligations)
        self.assertIs(AN_OBL.obligations, obligations)
        self.assertNotIsInstance(AN_OBL, type(obligations))
        self.assertEqual(len(getattr(package, "__all__")), 21)
        _assert_all(self, [
            "DENSITY_CONTRACT", "DENSITY_OBLIGATION_COUNT",
            "PARSED_OBLIGATION_COUNT", "PARENT", "PARENT_OBLIGATION_COUNT",
            "PROPOSAL_TOKEN", "RESIDUAL_COUNT_GAP", "STATUS_PASS",
            "STATUS_NOT_RUN", "STATUS_FAIL", "density_plan", "obligations",
            "project", "mock_observations", "registration_proposal",
            "describe_only_mainline_fragment", "HUMAN_COOKIE_ENV",
            "DRIVER_TOKEN_ENV", "EXECUTOR_ROW_INDEX"], package.__all__)
        self.assertEqual(AN_OBL.PARSED_SNAPSHOT[0],
                         (1, "PASS", "platform health returns 200",
                          "stories.sh", 269))
        self.assertEqual((AN_OBL.EXECUTOR_ROW_INDEX, AN_OBL.SITE_PREFIX),
                         (1, "AN"))


