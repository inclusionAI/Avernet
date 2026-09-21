"""Distinguish a child-exit signal race from a genuine permission failure."""
import signal
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agency_runtime import terminate  # noqa: E402


class ProcessCleanupTest(unittest.TestCase):
    def test_reap_and_retry_if_child_exits_between_poll_and_signal(self):
        process = Mock(pid=12345)
        process.poll.side_effect = [None, 0]
        with patch('agency_runtime.os.killpg', side_effect=[
            PermissionError(), ProcessLookupError(),
        ]) as send:
            terminate(process)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args, (12345, signal.SIGTERM))

    def test_permission_error_for_live_child_is_not_swallowed(self):
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch('agency_runtime.os.killpg', side_effect=PermissionError()):
            with self.assertRaises(PermissionError):
                terminate(process)
