"""Large team admission happens before any instance mutation or registration."""
import subprocess
import unittest

from launcher_test_support import LauncherFixture


class TeamConfirmationTest(LauncherFixture):
    def populate_team(self, count):
        for number in range(1, count):
            (self.repo / f'engineering/worker-{number}.md').write_text(
                f'---\nname: Worker {number}\ndescription: Team worker\n---\nWork\n')
        return ['engineering/backend', *[f'engineering/worker-{n}' for n in range(1, count)]]

    def assert_no_instance_work(self):
        self.assertEqual(self.registrations, [])
        self.assertEqual(self.onboards, [])
        self.assertFalse(self.calls.exists())
        self.assertEqual(list(self.state.glob('*/instance.json')), [])

    def test_five_agents_need_no_confirmation(self):
        self.populate_team(5)
        proc = self.launch([], ['--team', 'engineering'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertNotIn('Continue launching', output)
        self.assertEqual(len(self.registrations), 5)

    def test_decline_or_default_no_cancels_before_instance_work(self):
        self.populate_team(6)
        for answer in ('n', ''):
            with self.subTest(answer=answer):
                proc, master = self.launch_interactive([], [answer], ['--team', 'engineering'])
                output = self.read_interactive_until(proc, master, marker=b'Launch cancelled')
                proc.wait(timeout=5)
                self.stop_interactive(proc, master)
                self.assertEqual(proc.returncode, 0, output)
                self.assertIn('6 agents', output)
                self.assertIn('[y/N]', output)
                self.assert_no_instance_work()

    def test_accept_counts_deduplicated_agents_and_continues(self):
        self.populate_team(6)
        proc, master = self.launch_interactive(['engineering/backend'], ['y'],
                                               ['--team', 'engineering', '--team', 'engineering'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(output.count('Continue launching all 6 agents?'), 1)
        self.assertEqual(len(self.registrations), 6)

    def test_total_includes_explicit_profiles_outside_the_team(self):
        self.populate_team(5)
        proc, master = self.launch_interactive(['design/reviewer'], ['n'], ['--team', 'engineering'])
        output = self.read_interactive_until(proc, master, marker=b'Launch cancelled')
        proc.wait(timeout=5)
        self.assertIn('6 agents', output)
        self.assert_no_instance_work()

    def test_noninteractive_cannot_bypass_with_other_confirmation_flags(self):
        self.populate_team(6)
        result = subprocess.run(self.command([], ['--team', 'engineering',
                                    '--overwrite-profile', '--reregister']),
                                env=self.env, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('6 agents', result.stdout + result.stderr)
        self.assertIn('interactive terminal', result.stderr)
        self.assert_no_instance_work()

    def test_only_explicit_profiles_do_not_trigger_team_confirmation(self):
        profiles = self.populate_team(6)
        proc = self.launch(profiles)
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertNotIn('Continue launching', output)
        self.assertEqual(len(self.registrations), 6)

    def test_large_team_decline_preserves_existing_profiles_and_sessions(self):
        profiles = self.populate_team(6)
        proc = self.launch(profiles)
        self.wait_ready(proc)
        self.stop_process(proc)
        files = [*self.state.glob('*/instance.json'), *self.state.glob('*/profile.md'),
                 *self.state.glob('*/workspace/SOUL.md'), *self.state.glob('*/.bcs/session.json')]
        before = {path: path.read_bytes() for path in files}
        calls = self.calls.read_bytes()
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text() + '\nChanged source.\n')
        proc, master = self.launch_interactive([], ['n'],
                                               ['--team', 'engineering', '--overwrite-profile', '--reregister'])
        output = self.read_interactive_until(proc, master, marker=b'Launch cancelled')
        proc.wait(timeout=5)
        self.assertNotIn('Re-register all existing BCS sessions', output)
        self.assertNotIn('Overwrite the local profile', output)
        self.assertEqual({path: path.read_bytes() for path in files}, before)
        self.assertEqual(self.calls.read_bytes(), calls)
        self.assertEqual(len(self.registrations), 6)


if __name__ == '__main__':
    unittest.main()
