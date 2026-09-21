"""Identity, profile and runtime-lock operations are engine-scoped."""
import fcntl
import unittest

from launcher_test_support import LauncherFixture


class EngineLayoutTest(LauncherFixture):
    def test_instance_is_under_engine_and_reused_without_cross_engine_changes(self):
        foreign = self.state_root / 'codex/backend-c425b14093'
        (foreign / '.bcs').mkdir(parents=True)
        (foreign / 'instance.json').write_text('not-an-openclaw-record')
        (foreign / '.bcs/session.json').write_text('{"bot_uuid":"foreign-bot"}')
        (foreign / 'profile.md').write_text('Codex role must stay untouched')
        before = {path: path.read_bytes() for path in foreign.rglob('*') if path.is_file()}
        proc = self.launch(['engineering/backend'])
        output = self.wait_ready(proc) + self.stop_process(proc)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(len(list(self.state.glob('*/instance.json'))), 1)
        self.assertEqual(list(self.state_root.glob('*/instance.json')), [])
        source = self.repo / 'engineering/backend.md'
        source.write_text(source.read_text() + '\nNew openclaw role\n')
        proc = self.launch(['engineering/backend'], ['--overwrite-profile', '--reregister'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 2)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertNotIn('foreign-bot', output)

    def test_flat_legacy_root_is_refused_instead_of_registering_a_duplicate(self):
        legacy = self.state_root / 'legacy-agent'
        legacy.mkdir(parents=True)
        (legacy / 'instance.json').write_text('{"engine":"openclaw"}')
        result = self.failed_run(['engineering/backend'])
        self.assertIn('migrate-layout.sh', result.stderr)
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())
        self.assertTrue((legacy / 'instance.json').exists())

    def test_other_engine_lock_does_not_block_openclaw(self):
        directory = self.state_root / 'codex'
        directory.mkdir(parents=True)
        with (directory / '.launcher.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = self.launch(['engineering/backend'])
            self.wait_ready(proc)
            self.stop_process(proc)
        self.assertTrue((self.state / '.launcher.lock').exists())
        self.assertEqual(len(self.registrations), 1)


if __name__ == '__main__':
    unittest.main()
