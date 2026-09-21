"""Standalone CLI, selection and persistent-directory contracts."""
import json
import shutil
import subprocess
import sys
import unittest

from launcher_test_support import LauncherFixture, SCRIPT_DIR


class AgencyCatalogTest(LauncherFixture):
    def test_bundle_runs_outside_avernet(self):
        self.assertTrue((SCRIPT_DIR / 'launch-agency.sh').is_file(),
                        'standalone third-party entry point is missing')
        bundle = self.root / 'standalone'
        shutil.copytree(SCRIPT_DIR, bundle, ignore=shutil.ignore_patterns('__pycache__'))
        command = self.command(['engineering/backend'])
        command = ['bash', str(bundle / 'launch-agency.sh'), *command[2:]]
        proc = subprocess.Popen(command, env=dict(self.env, AGENCY_PYTHON=sys.executable),
                                cwd=self.root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop_process, proc)
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(self.registrations), 1)

    def test_only_openclaw_engine_is_accepted_before_side_effects(self):
        for engine in ('codex', 'claude-code', 'unknown'):
            result = self.failed_run(['engineering/backend'], ['--engine', engine])
            self.assertIn('openclaw', result.stderr)
            self.assertFalse(self.calls.exists())
            self.assertFalse(self.state.exists())
            self.assertEqual(self.registrations, [])
        proc = self.launch(['engineering/backend'], ['--engine', 'openclaw'])
        self.wait_ready(proc)
        self.stop_process(proc)
        record = json.loads(next(self.state.glob('*/instance.json')).read_text())
        self.assertEqual(record['engine'], 'openclaw')

    def test_profile_and_team_overlap_reuses_the_same_directory_and_identity(self):
        second = self.repo / 'engineering/sre.md'
        second.write_text('---\nname: SRE Agent\ndescription: Reliable systems\n---\nSRE work\n')
        # README is documentation, not an Agent Profile.
        (self.repo / 'engineering/README.md').write_text('# Engineering team\n')
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        old_folder = next(self.state.glob('backend-*'))
        old_record = json.loads((old_folder / 'instance.json').read_text())
        old_session = json.loads((old_folder / '.bcs/session.json').read_text())
        (old_folder / 'workspace/MEMORY.md').write_text('Keep my memory')
        # Team plus overlapping explicit profile must register just one new Bot.
        proc = self.launch(['engineering/backend.md'], ['--team', 'engineering', '--team', 'engineering'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 2)
        self.assertEqual(len(list(self.state.glob('*/instance.json'))), 2)
        self.assertEqual(json.loads((old_folder / 'instance.json').read_text())['port'], old_record['port'])
        self.assertEqual(json.loads((old_folder / '.bcs/session.json').read_text()), old_session)
        self.assertEqual((old_folder / 'workspace/MEMORY.md').read_text(), 'Keep my memory')
        before_dirs = sorted(str(path) for path in self.state.glob('*/instance.json'))
        self.env.pop('BCS_REGISTER_TOKEN')
        proc = self.launch([], ['--team', 'engineering'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(sorted(str(path) for path in self.state.glob('*/instance.json')), before_dirs)
        self.assertEqual(len(self.registrations), 2)
        self.assertEqual(list(old_folder.glob('profile.previous.*.md')), [])
        self.assertEqual(list(old_folder.glob('.bcs/session.previous.*.json')), [])

    def test_multiple_teams_expand_in_command_order_and_sort_profiles(self):
        (self.repo / 'engineering/zeta.md').write_text(
            '---\nname: Zeta Agent\ndescription: Extra engineer\n---\nWork\n')
        proc = self.launch([], ['--team', 'design', '--team', 'engineering',
                               '--profile', 'design/reviewer'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual([record['bot-name'][0] for record in self.registrations],
                         ['代码审查员', 'Backend Architect', 'Zeta Agent'])

    def test_empty_or_invalid_selections_fail_before_install(self):
        (self.repo / 'empty').mkdir()
        bad_options = [[], ['--team', 'empty'], ['--team', 'missing'], ['--team', '../engineering'],
                       ['--profile', 'backend'], ['--profile', 'engineering/../engineering/backend'],
                       ['--profile', '/engineering/backend'], ['--profile', 'engineering//backend'],
                       ['--team', 'engineering/'], ['--profile', 'engineering/backend.txt']]
        for options in bad_options:
            result = subprocess.run(self.command([], options), env=self.env, capture_output=True,
                                    text=True, timeout=15, check=False)
            self.assertNotEqual(result.returncode, 0, options)
            self.assertFalse(self.calls.exists())
            self.assertEqual(self.registrations, [])

    def test_symlinked_team_or_member_cannot_escape_checkout(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'escape.md').write_text(
            '---\nname: Escaped Agent\ndescription: Unsafe location\n---\nWork\n')
        (self.repo / 'escape').symlink_to(outside, target_is_directory=True)
        self.failed_run([], ['--team', 'escape'])
        (self.repo / 'engineering/escape.md').symlink_to(outside / 'escape.md')
        self.failed_run([], ['--team', 'engineering'])
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())

    def test_nested_team_profiles_use_relative_identity(self):
        nested = self.repo / 'engineering/platform'
        nested.mkdir()
        (nested / 'backend.md').write_text(
            '---\nname: Platform Backend\ndescription: Platform work\n---\nWork\n')
        proc = self.launch([], ['--team', 'engineering'])
        self.wait_ready(proc)
        self.stop_process(proc)
        records = [json.loads(path.read_text()) for path in self.state.glob('*/instance.json')]
        self.assertEqual({record['profile_path'] for record in records},
                         {'engineering/backend.md', 'engineering/platform/backend.md'})
        proc = self.launch(['engineering/platform/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 2)

    def test_invalid_member_refuses_the_entire_team_before_side_effects(self):
        (self.repo / 'engineering/z-last.md').write_text('---\nname: [invalid\n---\nRole\n')
        self.failed_run([], ['--team', 'engineering'])
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.registrations, [])
        self.assertEqual(list(self.state.glob('*/instance.json')), [])

    def test_team_reuse_gets_one_final_bcs_question(self):
        proc = self.launch([], ['--team', 'engineering', '--team', 'design'])
        self.wait_ready(proc)
        self.stop_process(proc)
        proc, master = self.launch_interactive([], ['n'],
                                               ['--team', 'engineering', '--team', 'design'])
        output = self.read_interactive_until(proc, master)
        self.stop_interactive(proc, master)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(output.count('Re-register all existing BCS sessions as new Bots?'), 1)
        self.assertIn('Backend Architect: Bot ID', output)
        self.assertIn('代码审查员: Bot ID', output)
        self.assertEqual(len(self.registrations), 2)

    def test_retired_flags_are_not_silently_accepted(self):
        for flag in ('--yes', '--reregister-bcs'):
            result = self.failed_run(['engineering/backend'], [flag])
            self.assertIn('unrecognized', result.stderr)
            self.assertEqual(self.registrations, [])

    def test_legacy_state_without_engine_field_reuses_identity(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        record_path = next(self.state.glob('*/instance.json'))
        record = json.loads(record_path.read_text())
        record.pop('engine', None)
        record_path.write_text(json.dumps(record))
        self.env.pop('BCS_REGISTER_TOKEN')
        proc = self.launch(['engineering/backend.md'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 1)


if __name__ == '__main__':
    unittest.main()
