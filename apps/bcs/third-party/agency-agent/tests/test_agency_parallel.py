"""Bounded startup concurrency, phase barriers, cancellation and terminal output."""
import json
import os
import signal
import time
import unittest

from launcher_test_support import LauncherFixture


class ParallelLaunchTest(LauncherFixture):
    def add_workers(self, count):
        for number in range(count):
            (self.repo / f'engineering/worker-{number}.md').write_text(
                f'---\nname: Worker {number}\ndescription: Parallel worker\n---\nWork\n')
        return [f'engineering/worker-{number}' for number in range(count)]

    def use_trace(self):
        self.trace = self.root / 'parallel-events.jsonl'
        self.env['FAKE_PARALLEL_TRACE'] = str(self.trace)
        self.env['FAKE_STAGE_DELAY'] = '0.35'

    def events(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def peak(self, stage):
        active = maximum = 0
        for event in sorted(self.events(), key=lambda event: event['at']):
            if event['stage'] == stage:
                active += 1 if event['event'] == 'begin' else -1
                maximum = max(maximum, active)
        self.assertEqual(active, 0, f'{stage} did not finish')
        return maximum

    def assert_phase_barrier(self):
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        # Registrations stay ordered/serial; each must observe all installs complete.
        for observation in self.registration_install_counts:
            self.assertEqual(observation, len(self.registrations))
        installs = [i for i, call in enumerate(calls) if call['args'][:2] == ['plugins', 'install']]
        starts = [i for i, call in enumerate(calls) if call['args'][:2] == ['gateway', 'run']]
        self.assertLess(max(installs), min(starts))

    def test_default_four_workers_overlap_install_and_connection_probes(self):
        self.use_trace()
        profiles = self.add_workers(6)
        proc = self.launch(profiles)
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertGreater(self.peak('install'), 1)
        self.assertLessEqual(self.peak('install'), 4)
        self.assertGreater(self.peak('probe'), 1)
        self.assertLessEqual(self.peak('probe'), 4)
        self.assertEqual([row['bot-name'][0] for row in self.registrations],
                         [f'Worker {number}' for number in range(6)])
        self.assert_phase_barrier()

    def test_parallel_one_restores_serial_install_and_probe(self):
        self.use_trace()
        proc = self.launch(extra=['--parallel', '1'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(self.peak('install'), 1)
        self.assertEqual(self.peak('probe'), 1)

    def test_custom_parallelism_caps_active_work(self):
        self.use_trace()
        proc = self.launch(self.add_workers(5), ['--parallel', '2'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(self.peak('install'), 2)
        self.assertEqual(self.peak('probe'), 2)

    def test_bad_parallelism_is_rejected_before_side_effects(self):
        for value in ('0', '-1', 'oops'):
            self.failed_run(extra=['--parallel', value])
            self.assertFalse(self.calls.exists())
            self.assertEqual(self.registrations, [])

    def wait_for_events(self, proc, stage, count):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and proc.poll() is None:
            if self.trace.exists():
                events = [event for event in self.events()
                          if event['stage'] == stage and event['event'] == 'begin']
                if len(events) >= count:
                    return events
            time.sleep(0.05)
        self.fail(f'{count} {stage} workers did not start')

    def assert_children_exited(self, events):
        for event in events:
            with self.assertRaises(ProcessLookupError, msg=f'child {event["pid"]} survived'):
                os.kill(event['pid'], 0)

    def test_interrupt_during_install_stops_workers_and_starts_no_queued_work(self):
        self.use_trace()
        self.env['FAKE_HANG_INSTALL'] = '1'
        proc = self.launch(self.add_workers(5), ['--parallel', '2'])
        events = self.wait_for_events(proc, 'install', 2)
        proc.send_signal(signal.SIGINT)
        output, _ = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.registrations, [])
        self.assert_children_exited(events)

    def test_install_failure_cancels_other_installs_without_registering(self):
        self.use_trace()
        self.env['FAKE_HANG_INSTALL'] = '1'
        self.env['FAKE_DELAYED_FAIL_INSTALL'] = 'worker-0'
        proc = self.launch(self.add_workers(5), ['--parallel', '2'])
        output, _ = proc.communicate(timeout=10)
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertNotIn(b'ALL CONNECTED', output)
        self.assertEqual(self.registrations, [])
        events = self.events()
        self.assertEqual(len(events), 2)
        self.assert_children_exited(events)

    def test_interrupt_during_readiness_reaps_probes_and_gateways(self):
        self.use_trace()
        self.env['FAKE_HANG_PROBE'] = '1'
        proc = self.launch(self.add_workers(5), ['--parallel', '2'])
        probes = self.wait_for_events(proc, 'probe', 2)
        gateways = [{'pid': int(path.read_text())} for path in self.state.glob('*/gateway.pid')]
        self.assertEqual(len(gateways), 2)
        proc.send_signal(signal.SIGINT)
        output, _ = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, output)
        self.assertNotIn(b'ALL CONNECTED', output)
        self.assert_children_exited(probes + gateways)
        self.assertEqual(len(list(self.state.glob('*/gateway.pid'))), 2,
                         'cancellation must not start the queued Gateways')

    def test_terminal_logs_are_colored_but_no_color_and_pipes_are_plain(self):
        self.env.pop('NO_COLOR', None)
        self.env['TERM'] = 'xterm-256color'
        proc, master = self.launch_interactive(['engineering/backend'], [])
        colored = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertIn('\x1b[34m', colored)
        self.assertIn('\x1b[32m', colored)
        self.env['NO_COLOR'] = '1'
        proc, master = self.launch_interactive(['engineering/backend'], ['n'])
        plain = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertNotIn('\x1b[', plain)
        self.env.pop('NO_COLOR')
        proc = self.launch(['engineering/backend'])
        plain = self.wait_ready(proc) + self.stop_process(proc)
        self.assertNotIn('\x1b[', plain)


if __name__ == '__main__':
    unittest.main()
