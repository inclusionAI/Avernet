import importlib.util
from pathlib import Path
import unittest
from unittest import mock


MODULE_PATH = (
    Path(__file__).parents[1]
    / "clawevolve-workflow/scripts/handlers/lib_http_retry.py"
)
SPEC = importlib.util.spec_from_file_location("lib_http_retry", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class HttpRetryTest(unittest.TestCase):
    def test_uses_five_attempts_and_three_second_delays(self):
        operation = mock.Mock(side_effect=OSError("temporary failure"))
        with mock.patch.object(MODULE.time, "sleep") as sleep:
            with self.assertRaises(OSError):
                MODULE.retry_http(operation)

        self.assertEqual(operation.call_count, 5)
        self.assertEqual(sleep.call_args_list, [mock.call(3)] * 4)

    def test_returns_after_a_successful_retry(self):
        operation = mock.Mock(side_effect=[OSError("temporary failure"), "ok"])
        with mock.patch.object(MODULE.time, "sleep") as sleep:
            self.assertEqual(MODULE.retry_http(operation), "ok")

        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(3)


if __name__ == "__main__":
    unittest.main()
