"""Diagnostic-propagation guards for the five CLI family sub-harnesses.

Preprod r8 blocked the channel-cli / friend-cli / collaboration-team /
direct-chat / operator-cli families with ``error_detail=null``: the bounded
``capture()`` dropped stderr (product CLI errors go to stderr) and each
family runner/executor discarded the failure text.  These guards pin the
lens and prove the scrubbed bounded-tail helpers offline.  No network, no
binary, no credential and no live leg is involved.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.bounded_process import capture, scrub_failure_text, stderr_failure_detail
from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.collaboration_team_contract import (
    ADAPTER_METHODS as COLLAB_ADAPTER_METHODS,
    execute_collaboration_team_case,
)
from bcs_e2e.direct_chat_cli_case_run import (
    DualDirectChatAdapter,
    execute_direct_chat_case,
)
from bcs_e2e.local_friend_cli_accept_contract import (
    execute_friend_cli_accept_case,
)
from bcs_e2e.remote_channel_cli_adapter import (
    CHANNEL_CLI_ADAPTER_METHODS,
    CHANNEL_CLI_EFFECT,
    RemoteChannelCliAdapter,
    execute_channel_cli_case,
)

RUNNER_SOURCES = (
    "channel_cli_runner.py",
    "friend_cli_runner.py",
    "collaboration_team_runner.py",
    "direct_chat_cli_case_leaf.py",
    "operator_cli_runner.py",
)

# (runner module, fragments embedded in its nonzero projection; adjacent
# string literals in source are split, so fragments are matched separately)
NONZERO_PROJECTIONS = (
    ("channel_cli_runner.py", ("channel product CLI returned nonzero",)),
    ("friend_cli_runner.py", ("friendship product CLI returned nonzero",)),
    ("collaboration_team_runner.py",
     ("collaboration-team product CLI returned nonzero",)),
    ("direct_chat_cli_case_leaf.py",
     ("direct chat product CLI returned ", "nonzero: ", "| stderr: ")),
    ("operator_cli_runner.py", ("operator product CLI returned nonzero",)),
)

EXECUTOR_SOURCES = (
    "remote_channel_cli_adapter.py",
    "local_friend_cli_accept_contract.py",
    "collaboration_team_contract.py",
    "direct_chat_cli_case_run.py",
)

ALREADY_LENSED_EXECUTORS = (
    "remote_operator_cli_adapter.py",
    "custom_collaboration_cli_case_run.py",
)

REPORT_SOURCES = (
    "channel_cli_case_run.py",
    "friend_cli_case_run.py",
    "operator_cli_case_run.py",
    "direct_chat_cli_case_run.py",
    "collaboration_team_case_run.py",
    "custom_collaboration_cli_case_run.py",
)

SECRET = b"owned-local-token-PLACEHOLDER-0000"
BEARER_TEXT = "Bearer sample.jwt.value"


def source_text(name: str) -> str:
    return (ROOT / "bcs_e2e" / name).read_text(encoding="utf-8")


class SourceGuards(unittest.TestCase):
    def test_every_family_runner_collects_stderr(self):
        for name in RUNNER_SOURCES:
            with self.subTest(file=name):
                self.assertIn("collect_stderr=True", source_text(name))

    def test_nonzero_projection_carries_bounded_stderr_tail(self):
        for name, markers in NONZERO_PROJECTIONS:
            with self.subTest(file=name):
                normalized = " ".join(source_text(name).split())
                for marker in markers:
                    self.assertIn(marker, normalized)
                self.assertIn("stderr_failure_detail", source_text(name))

    def test_channel_nonzero_raise_exempts_the_disabled_bridge_probe(self):
        text = source_text("channel_cli_runner.py")
        self.assertIn("operation != CHANNEL_CLI_BIND_OPERATION", text)
        # The rc!=0 bind probe remains a guarded expectation, not a failure.
        self.assertIn(
            "channel bind under the disabled bridge must fail nonzero", text)

    def test_direct_nonzero_scrubs_stdout_before_journaling(self):
        text = source_text("direct_chat_cli_case_leaf.py")
        self.assertIn("scrub_failure_text(", text)
        self.assertIn("secrets)[:500]", text)
        self.assertIn("stderr_failure_detail(result, secrets)", text)

    def test_bounded_helpers_are_defined_in_bounded_process(self):
        text = source_text("bounded_process.py")
        self.assertIn("def scrub_failure_text(", text)
        self.assertIn("def stderr_failure_detail(", text)

    def test_family_executors_record_the_first_blocked_detail(self):
        for name in EXECUTOR_SOURCES:
            with self.subTest(file=name):
                text = source_text(name)
                self.assertIn("record_error_detail", text)
                self.assertIn("error_detail=", text)

    def test_already_lensed_executors_keep_the_lens(self):
        for name in ALREADY_LENSED_EXECUTORS:
            with self.subTest(file=name):
                text = source_text(name)
                self.assertIn("record_error_detail", text)
                self.assertIn("error_detail=", text)

    def test_family_reports_journal_execution_error_detail(self):
        for name in REPORT_SOURCES:
            with self.subTest(file=name):
                text = source_text(name)
                self.assertIn('"error_detail": execution.error_detail', text)

    def test_custom_cli_envelope_stays_public_failure_class_only(self):
        # Frozen contract (test_blocked_receipt_projects_public_failure_class_without_secret):
        # the public envelope carries the failure class marker only; the leaf
        # layer already owns scrubbed stderr detail (custom_collaboration_cli_leaf).
        text = source_text("custom_collaboration_cli_case_run.py")
        self.assertNotIn("detail: {record_error_detail(error)", text)
        self.assertIn("public_failure_class(error)", text)


class BoundedStderrHelpers(unittest.TestCase):
    def _result(self, *, returncode=1, stdout=b"", stderr=b""):
        return subprocess.CompletedProcess(
            ["bcs-cli"], returncode, stdout, stderr)

    def test_stderr_tail_is_single_line_bounded_and_scrubbed(self):
        noise = " ".join(f"chunk-{index}" for index in range(240))
        stderr = (b"panic stack\n  at frame\ncookie=" + SECRET + b" central\n"
                  + noise.encode() + b"\nAuthorization: " + BEARER_TEXT.encode())
        detail = stderr_failure_detail(self._result(stderr=stderr), (SECRET,))
        self.assertNotIn("\n", detail)
        self.assertNotIn("\t", detail)
        self.assertLessEqual(len(detail), 400)
        self.assertNotIn(SECRET.decode(), detail)
        self.assertIn("[REDACTED]", detail)
        self.assertNotIn("sample.jwt.value", detail)
        self.assertIn("Bearer [REDACTED]", detail)
        self.assertIn(noise[-160:], detail)

    def test_default_limit_is_400(self):
        detail = stderr_failure_detail(self._result(stderr=b"z" * 1200), ())
        self.assertEqual(len(detail), 400)
        self.assertTrue(detail.startswith("z"))

    def test_limit_override_bounds_the_tail(self):
        detail = stderr_failure_detail(
            self._result(stderr=b"q" * 300), (), limit=10)
        self.assertEqual(detail, "q" * 10)

    def test_missing_or_none_stderr_is_tolerated(self):
        missing = SimpleNamespace(returncode=3, stdout=b"")
        self.assertEqual(stderr_failure_detail(missing, ()), "")
        none_stderr = SimpleNamespace(returncode=3, stdout=b"", stderr=None)
        self.assertEqual(stderr_failure_detail(none_stderr, ()), "")

    def test_scrub_failure_text_redacts_secret_bytes_and_bearer(self):
        self.assertEqual(
            scrub_failure_text("cookie=abc; next=1", (b"abc",)),
            "cookie=[REDACTED]; next=1")
        self.assertEqual(
            scrub_failure_text("Authorization: Bearer abc.def.ghi", ()),
            "Authorization: Bearer [REDACTED]")

    def test_empty_secret_entries_are_skipped(self):
        self.assertEqual(
            scrub_failure_text("plain text", (b"", b"")),
            "plain text")


class BoundedCaptureIntegration(unittest.TestCase):
    def test_collect_stderr_true_populates_the_stderr_lens(self):
        code = "import sys; sys.stderr.write('boom-diag'); sys.exit(3)"
        result = capture([sys.executable, "-c", code],
                         timeout_seconds=30, max_output_bytes=1 << 20,
                         collect_stderr=True)
        self.assertEqual(result.returncode, 3)
        self.assertIn(b"boom-diag", result.stderr)
        self.assertEqual(stderr_failure_detail(result, ()), "boom-diag")

    def test_collect_stderr_false_keeps_the_lens_empty(self):
        code = "import sys; sys.stderr.write('hidden-diag'); sys.exit(3)"
        result = capture([sys.executable, "-c", code],
                         timeout_seconds=30, max_output_bytes=1 << 20)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stderr, b"")


class ChannelExecutorLens(unittest.TestCase):
    def test_blocked_execution_carries_detail_on_execution_and_step(self):
        class BoomChannelAdapter(RemoteChannelCliAdapter):
            def __init__(self):
                pass

            def _credential_kind(self):
                return "human_cookie"

            def bind_disabled_bridge(self):
                raise RuntimeError("boom-channel")

            def _unreached(self):
                raise AssertionError("post-failure obligation must stay NOT_RUN")

            list_bindings_before = _unreached
            create_cli_group = _unreached
            create_cli_session = _unreached
            read_empty_conversations = _unreached
            unbind_missing_binding = _unreached
            list_bindings_after = _unreached
            delete_cli_session = _unreached
            delete_cli_group = _unreached

        execution = execute_channel_cli_case(BoomChannelAdapter())
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RuntimeError")
        self.assertEqual(execution.error_detail, "boom-channel")
        self.assertFalse(execution.ready_to_execute)
        first = execution.steps[0]
        self.assertEqual(first.status, "NOT_RUN")
        self.assertEqual(first.error_type, "RuntimeError")
        self.assertEqual(first.error_detail, "boom-channel")
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["error_detail"], "boom-channel")
        self.assertEqual(snapshot["steps"][0]["error_detail"], "boom-channel")

    def test_pass_execution_carries_no_detail(self):
        class PassChannelAdapter(RemoteChannelCliAdapter):
            def __init__(self):
                pass

            def _credential_kind(self):
                return "human_cookie"

            def _call(self):
                return AdapterCall(data={}, evidence={
                    "transport": "PRODUCT_CLI",
                    "credential_kind": "human_cookie",
                    "fixture_effect": CHANNEL_CLI_EFFECT,
                    "remote_validation": "NOT_RUN",
                    "grants_release_credit": False})

            bind_disabled_bridge = _call
            list_bindings_before = _call
            create_cli_group = _call
            create_cli_session = _call
            read_empty_conversations = _call
            unbind_missing_binding = _call
            list_bindings_after = _call
            delete_cli_session = _call
            delete_cli_group = _call

        execution = execute_channel_cli_case(PassChannelAdapter())
        self.assertEqual(execution.status, "PASS")
        self.assertIsNone(execution.error_detail)
        self.assertEqual(
            execution.completed_count, len(CHANNEL_CLI_ADAPTER_METHODS))


class CollaborationTeamExecutorLens(unittest.TestCase):
    def _adapter(self, boom: bool):
        def boom_first(self):
            raise RuntimeError("boom-collab")

        def unreached(self):
            raise AssertionError("post-failure obligation must stay NOT_RUN")

        def passed(self):
            return AdapterCall(data={}, evidence={
                "transport": "PRODUCT_CLI",
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False})

        attributes = {}
        for index, name in enumerate(COLLAB_ADAPTER_METHODS):
            if index == 0:
                attributes[name] = boom_first if boom else passed
            else:
                attributes[name] = unreached if boom else passed
        return type("StubCollaborationAdapter", (), attributes)()

    def test_blocked_execution_carries_detail_on_execution_and_step(self):
        execution = execute_collaboration_team_case(self._adapter(True))
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RuntimeError")
        self.assertEqual(execution.error_detail, "boom-collab")
        self.assertEqual(len(execution.steps), len(COLLAB_ADAPTER_METHODS))
        failed, *remaining = execution.steps
        self.assertEqual(failed.status, "NOT_RUN")
        self.assertEqual(failed.error_detail, "boom-collab")
        for step in remaining:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertIsNone(step.error_type)
            self.assertIsNone(step.error_detail)

    def test_pass_execution_carries_no_detail(self):
        execution = execute_collaboration_team_case(self._adapter(False))
        self.assertEqual(execution.status, "PASS")
        self.assertIsNone(execution.error_detail)
        self.assertEqual(execution.completed_count, len(COLLAB_ADAPTER_METHODS))


class FriendExecutorLens(unittest.TestCase):
    def test_blocked_execution_carries_detail_on_execution_and_step(self):
        class BoomFriendAdapter:
            def read_initial_member_requests(self):
                raise RuntimeError("boom-friend")

            def request_driver_friendship(self):
                raise AssertionError("post-failure obligation must stay NOT_RUN")

            def select_exact_member_request(self):
                raise AssertionError("post-failure obligation must stay NOT_RUN")

            def accept_member_friend_request(self):
                raise AssertionError("post-failure obligation must stay NOT_RUN")

            def read_final_driver_friend_list(self):
                raise AssertionError("post-failure obligation must stay NOT_RUN")

        execution = execute_friend_cli_accept_case(BoomFriendAdapter())
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RuntimeError")
        self.assertEqual(execution.error_detail, "boom-friend")
        step = execution.steps[0]
        self.assertEqual(step.status, "NOT_RUN")
        self.assertEqual(step.error_detail, "boom-friend")
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["error_detail"], "boom-friend")
        self.assertEqual(snapshot["steps"][0]["error_detail"], "boom-friend")


class DirectChatExecutorLens(unittest.TestCase):
    def test_blocked_execution_carries_detail_on_execution_and_steps(self):
        class BoomDirectChatAdapter(DualDirectChatAdapter):
            def __init__(self):
                pass

            def submit_ping_run(self):
                raise RuntimeError("boom-direct")

        execution = execute_direct_chat_case(BoomDirectChatAdapter())
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RuntimeError")
        self.assertEqual(execution.error_detail, "boom-direct")
        self.assertEqual(len(execution.steps), 5)
        for step in execution.steps:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertEqual(step.error_detail, "boom-direct")
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["error_detail"], "boom-direct")
        self.assertEqual(snapshot["steps"][0]["error_detail"], "boom-direct")


if __name__ == "__main__":
    unittest.main()
