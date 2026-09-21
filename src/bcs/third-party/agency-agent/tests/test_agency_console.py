"""Console rendering is colored only on terminals, and writes remain whole lines."""
import io
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agency_console import report  # noqa: E402


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ConsoleTest(unittest.TestCase):
    def test_status_colors_are_applied_to_the_correct_stream(self):
        output, errors = Terminal(), Terminal()
        with patch.dict(os.environ, {'TERM': 'xterm'}, clear=True), redirect_stdout(output), redirect_stderr(errors):
            report('preparing')
            report('connected', 'success')
            report('pending', 'warning')
            report('failed', 'error', error=True)
        self.assertEqual(output.getvalue(), '\x1b[34mpreparing\x1b[0m\n'
                         '\x1b[32mconnected\x1b[0m\n\x1b[33mpending\x1b[0m\n')
        self.assertEqual(errors.getvalue(), '\x1b[31mfailed\x1b[0m\n')

    def test_no_color_or_dumb_terminal_disables_ansi(self):
        for environment in ({'NO_COLOR': ''}, {'TERM': 'dumb'}):
            with self.subTest(environment=environment):
                output = Terminal()
                with patch.dict(os.environ, environment, clear=True), redirect_stdout(output):
                    report('connected', 'success')
                self.assertEqual(output.getvalue(), 'connected\n')

    def test_parallel_messages_remain_complete_plain_lines_when_piped(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(lambda index: report(f'[Agent {index}] CONNECTED', 'success'), range(40)))
        self.assertEqual(set(output.getvalue().splitlines()),
                         {f'[Agent {index}] CONNECTED' for index in range(40)})
        self.assertEqual(len(output.getvalue().splitlines()), 40)
