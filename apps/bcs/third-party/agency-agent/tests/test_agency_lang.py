"""Language selection isolates checkouts, instances, ports and identities."""
import json
import subprocess
import unittest

from launcher_test_support import LauncherFixture

ZH_ORIGIN = 'https://github.com/jnMetaCode/agency-agents-zh.git'


class AgencyLanguageTest(LauncherFixture):
    def test_default_language_is_english_without_flag(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        record = json.loads(next(self.state.glob('*/instance.json')).read_text())
        self.assertEqual(record['lang'], 'en')
        self.assertEqual(list(self.state_root.glob('openclaw-*')), [])
        self.assertEqual(len(self.registrations), 1)

    def test_zh_profiles_live_under_the_zh_engine_scope(self):
        proc = self.launch(['engineering/backend'], ['--lang', 'zh'])
        self.wait_ready(proc)
        self.stop_process(proc)
        zh_state = self.state_root / 'openclaw-zh'
        instance = next(zh_state.glob('*/'))
        self.assertTrue((instance / 'instance.json').is_file(),
                        f'no instance under {zh_state}')
        record = json.loads((instance / 'instance.json').read_text())
        self.assertEqual(record['lang'], 'zh')
        self.assertFalse(self.state.exists())
        self.assertEqual(len(self.registrations), 1)

    def test_same_profile_in_both_languages_is_isolated_and_port_offset(self):
        proc = self.launch(['engineering/backend'])
        self.wait_ready(proc)
        self.stop_process(proc)
        english_dir = next(self.state.glob('backend-*/'))
        proc = self.launch(['engineering/backend'], ['--lang', 'zh'])
        self.wait_ready(proc)
        self.stop_process(proc)
        zh_dir = next((self.state_root / 'openclaw-zh').glob('backend-*/'))
        self.assertNotEqual(zh_dir, english_dir)
        english_record = json.loads((english_dir / 'instance.json').read_text())
        zh_record = json.loads((zh_dir / 'instance.json').read_text())
        # Each language owns an independent Bot identity, session and port.
        self.assertEqual(len(self.registrations), 2)
        self.assertNotEqual(
            json.loads((english_dir / '.bcs/session.json').read_text())['bot_uuid'],
            json.loads((zh_dir / '.bcs/session.json').read_text())['bot_uuid'])
        self.assertNotEqual(zh_record['port'], english_record['port'])
        self.assertGreaterEqual(abs(zh_record['port'] - english_record['port']), 20)
        # Rerunning the zh selection still reuses its own instance and identity.
        self.env.pop('BCS_REGISTER_TOKEN')
        proc = self.launch(['engineering/backend'], ['--lang', 'zh'])
        self.wait_ready(proc)
        self.stop_process(proc)
        self.assertEqual(len(self.registrations), 2)
        self.assertEqual(json.loads((zh_dir / 'instance.json').read_text())['port'], zh_record['port'])
        self.assertEqual(len(list(self.state.glob('*/instance.json'))), 1)
        self.assertEqual(len(list((self.state_root / 'openclaw-zh').glob('*/instance.json'))), 1)

    def test_saved_language_mismatch_refuses_to_reuse_instance(self):
        proc = self.launch(['engineering/backend'], ['--lang', 'zh'])
        self.wait_ready(proc)
        self.stop_process(proc)
        record_path = next((self.state_root / 'openclaw-zh').glob('*/instance.json'))
        record = json.loads(record_path.read_text())
        record['lang'] = 'en'
        record_path.write_text(json.dumps(record))
        result = self.failed_run(['engineering/backend'], ['--lang', 'zh'])
        self.assertIn('saved profile language differs', result.stderr)
        self.assertEqual(len(self.registrations), 1)
        self.assertEqual(json.loads(record_path.read_text())['lang'], 'en')

    def test_unsupported_language_is_rejected_before_side_effects(self):
        result = self.failed_run(['engineering/backend'], ['--lang', 'fr'])
        self.assertIn('invalid choice', result.stderr)
        self.assertEqual(self.registrations, [])
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.state_root.exists())

    def test_default_zh_clone_uses_agency_agents_zh_under_zh_name(self):
        home = self.setup_defaults()
        self.env['FAKE_GIT_ORIGIN'] = ZH_ORIGIN
        omit = ('--agency-dir', '--model-config', '--state-dir')
        proc = self.launch(['engineering/backend'], ['--lang', 'zh'], omit=omit)
        self.wait_ready(proc)
        self.stop_process(proc)
        state_root = home / '.avernet/bcs/agency-agent'
        self.assertTrue((state_root / 'agency-agents-zh/engineering/backend.md').is_file())
        self.assertFalse((state_root / 'agency-agents').exists())
        self.assertTrue((state_root / 'openclaw-zh').is_dir())
        self.assertFalse((state_root / 'openclaw').exists())
        calls = [json.loads(line) for line in self.git_calls.read_text().splitlines()]
        clones = [call for call in calls if call['args'][0] == 'clone']
        self.assertEqual(len(clones), 1)
        self.assertIn(ZH_ORIGIN, clones[0]['args'])
        # Second run reuses the zh checkout and never touches the English one.
        proc = self.launch(['engineering/backend'], ['--lang', 'zh'], omit=omit)
        self.wait_ready(proc)
        self.stop_process(proc)
        calls = [json.loads(line) for line in self.git_calls.read_text().splitlines()]
        self.assertEqual(sum(1 for call in calls if call['args'][0] == 'clone'), 1)

    def test_wrong_origin_zh_cache_is_not_overwritten(self):
        self.setup_defaults()
        cache = self.state_root / 'agency-agents-zh'
        cache.mkdir(parents=True)
        (cache / '.git').mkdir()
        (cache / 'keep.txt').write_text('existing user data')
        # Fake remote reports the English origin for the zh cache: refuse to reuse.
        result = subprocess.run(self.command(['engineering/backend'], ['--lang', 'zh'],
                                             omit=('--agency-dir',)),
                                env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('not the expected repository', result.stderr)
        self.assertEqual((cache / 'keep.txt').read_text(), 'existing user data')
        self.assertEqual(self.registrations, [])


if __name__ == '__main__':
    unittest.main()
