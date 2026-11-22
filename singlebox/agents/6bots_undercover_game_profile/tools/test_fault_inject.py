import unittest

from fault_inject import recovery_outcome


class RecoveryOutcomeTest(unittest.TestCase):
    def test_running_reopened_run_is_not_reported_as_recovered(self) -> None:
        self.assertIsNone(recovery_outcome("running", None, 3.4, 60.0))

    def test_completed_delivered_reopened_run_is_recovered(self) -> None:
        self.assertEqual(recovery_outcome("completed", "delivered", 3.4, 60.0), "recovered")

    def test_failed_reopened_run_is_not_reported_as_recovered(self) -> None:
        self.assertEqual(recovery_outcome("failed", None, 3.4, 60.0), "failed")


if __name__ == "__main__":
    unittest.main()
