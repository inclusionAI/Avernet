from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STORIES = ROOT / 'apps/bcs/scripts/e2e-test'


class BcsCliCancellationStoryTest(unittest.TestCase):
    def run_story(self, waiting: object, cancelled: object, *, wait_exit: int = 0,
                  cancel_exit: int = 0) -> tuple[int, int, int, int, list[list[str]]]:
        script = r'''
set -euo pipefail
source "$STORIES/common.sh"
source "$STORIES/stories.sh"
source "$STORIES/cli-stories.sh"
source "$STORIES/cli_error_paths_story.sh"
BOT_ENG_UUID=engine-under-test
calls=0
bcs_cli() {
    calls=$((calls + 1))
    printf 'CALL'; printf '\t%s' "$@"; printf '\n'
    [[ "${BCS_CLI_FORCE_JSON:-}" == 1 ]] || return 99
    BCS_CLI_STDERR='stub command error'
    if [[ "$calls" == 1 ]]; then
        BCS_CLI_STDOUT="$WAIT_JSON"
        BCS_CLI_EXIT="$WAIT_EXIT"
    else
        BCS_CLI_STDOUT="$CANCEL_JSON"
        BCS_CLI_EXIT="$CANCEL_EXIT"
    fi
    return "$BCS_CLI_EXIT"
}
rc=0
_cli_story_wait_and_cancel_chat || rc=$?
printf 'RESULT\t%s\t%s\t%s\t%s\n' "$rc" "$TESTS_PASSED" "$TESTS_FAILED" "$TESTS_TOTAL"
'''
        result = subprocess.run(
            ['bash', '-c', script], cwd=ROOT, text=True, capture_output=True, timeout=10,
            env={**os.environ, 'STORIES': str(STORIES),
                 # Strings allow malformed JSON at the CLI output boundary.
                 'WAIT_JSON': waiting if isinstance(waiting, str) else json.dumps(waiting),
                 'CANCEL_JSON': cancelled if isinstance(cancelled, str) else json.dumps(cancelled),
                 'WAIT_EXIT': str(wait_exit), 'CANCEL_EXIT': str(cancel_exit)},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        summary = next(line for line in lines if line.startswith('RESULT\t'))
        rc, passed, failed, total = map(int, summary.split('\t')[1:])
        self.assertEqual(passed + failed, total, result.stdout)
        calls = [line.split('\t')[1:] for line in lines if line.startswith('CALL\t')]
        self.assertGreaterEqual(len(calls), 1)
        self.assertEqual(calls[0][:5], ['PM', 'chat', '--bot-uuid', 'engine-under-test', '--message'])
        self.assertRegex(calls[0][5], r'^terminal wait check \d+$')
        self.assertEqual(calls[0][6:], ['--wait-until', 'running', '--timeout-ms', '15000'])
        if len(calls) > 1:
            self.assertEqual(calls[1], ['PM', 'chat-run', 'cancel', '--run-id', 'run-under-test'])
        return rc, passed, failed, total, calls

    def wait_result(self, state: str = 'running') -> dict[str, object]:
        return {'run_id': 'run-under-test', 'state': state}

    def cancel_result(self, state: str = 'cancelled', cancelled: object = True) -> dict[str, object]:
        return {'run_id': 'run-under-test', 'state': state, 'cancelled': cancelled}

    def assert_outcome(self, waiting: object, cancelled: object, *, valid: bool) -> None:
        rc, passed, failed, total, calls = self.run_story(waiting, cancelled)
        self.assertEqual((passed, failed, total), (4, 0, 4) if valid else (3, 1, 4))
        self.assertEqual(rc == 0, valid)
        self.assertEqual(len(calls), 2)

    def test_running_run_is_cancelled(self) -> None:
        self.assert_outcome(self.wait_result(), self.cancel_result(), valid=True)

    def test_already_completed_run_is_a_noop(self) -> None:
        self.assert_outcome(self.wait_result('completed'),
                            self.cancel_result('completed', False), valid=True)

    def test_run_completing_between_wait_and_cancel_is_a_noop(self) -> None:
        self.assert_outcome(self.wait_result(), self.cancel_result('completed', False), valid=True)

    def test_completed_wait_cannot_become_cancelled(self) -> None:
        self.assert_outcome(self.wait_result('completed'), self.cancel_result(), valid=False)

    def test_cancel_rejects_other_states(self) -> None:
        for state in ['failed', 'running', 'submitted', 'pending', 'unknown', 'timed_out', '']:
            with self.subTest(state=state):
                self.assert_outcome(self.wait_result(), self.cancel_result(state), valid=False)

    def test_cancel_requires_same_identity(self) -> None:
        for run_id in ['other-run', '', None, 1, True, [], {}]:
            with self.subTest(run_id=run_id):
                self.assert_outcome(self.wait_result(),
                                    {**self.cancel_result(), 'run_id': run_id}, valid=False)

    def test_cancel_requires_boolean_flag_consistent_with_state(self) -> None:
        for state, correct_flag in [('cancelled', True), ('completed', False)]:
            for flag in [not correct_flag, None, 'true', 'false', 0, 1, [], {}]:
                with self.subTest(state=state, flag=flag):
                    self.assert_outcome(self.wait_result(),
                                        self.cancel_result(state, flag), valid=False)

    def test_cancel_requires_all_contract_fields(self) -> None:
        for key in ['run_id', 'state', 'cancelled']:
            with self.subTest(key=key):
                cancelled = self.cancel_result()
                del cancelled[key]
                self.assert_outcome(self.wait_result(), cancelled, valid=False)

    def test_cancel_rejects_non_object_json(self) -> None:
        for value in [None, [], '"cancelled"', True, 1, '{']:
            with self.subTest(value=value):
                self.assert_outcome(self.wait_result(), value, valid=False)

    def test_wait_rejects_other_states_before_cancel(self) -> None:
        for state in ['failed', 'cancelled', 'pending', 'submitted', 'unknown', 'timed_out', '']:
            with self.subTest(state=state):
                rc, passed, failed, total, calls = self.run_story(
                    self.wait_result(state), self.cancel_result())
                self.assertEqual((passed, failed, total), (1, 1, 2))
                self.assertNotEqual(rc, 0)
                self.assertEqual(len(calls), 1)

    def test_wait_requires_nonempty_string_identity(self) -> None:
        for run_id in ['', None, 1, True, [], {}]:
            with self.subTest(run_id=run_id):
                rc, passed, failed, total, calls = self.run_story(
                    {**self.wait_result(), 'run_id': run_id}, self.cancel_result())
                self.assertEqual((passed, failed, total), (1, 1, 2))
                self.assertNotEqual(rc, 0)
                self.assertEqual(len(calls), 1)

    def test_wait_rejects_missing_fields_or_invalid_json(self) -> None:
        for waiting in [{}, {'run_id': 'run-under-test'}, {'state': 'running'},
                        None, [], '"running"', True, 1, '{']:
            with self.subTest(waiting=waiting):
                rc, passed, failed, total, calls = self.run_story(waiting, self.cancel_result())
                self.assertEqual((passed, failed, total), (1, 1, 2))
                self.assertNotEqual(rc, 0)
                self.assertEqual(len(calls), 1)

    def test_wait_command_failure_is_counted_and_stops_cancel(self) -> None:
        rc, passed, failed, total, calls = self.run_story(
            self.wait_result(), self.cancel_result(), wait_exit=17)
        self.assertEqual((passed, failed, total), (0, 1, 1))
        self.assertNotEqual(rc, 0)
        self.assertEqual(len(calls), 1)

    def test_cancel_command_failure_is_counted_even_with_valid_payload(self) -> None:
        rc, passed, failed, total, calls = self.run_story(
            self.wait_result(), self.cancel_result(), cancel_exit=23)
        self.assertEqual((passed, failed, total), (2, 1, 3))
        self.assertNotEqual(rc, 0)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
