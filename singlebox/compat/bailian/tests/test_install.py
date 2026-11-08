import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install import install_openclaw_profile


class InstallTest(unittest.TestCase):
    def test_install_owns_initial_config_and_provider_settings_are_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'openclaw.json'
            original = {'models': {'mode': 'replace'}}
            path.write_text(json.dumps(original))
            def install():
                self.assertFalse(path.exists())
                path.write_text(json.dumps({'plugins': {'entries': {'bcn': {'enabled': True}}}}))
            install_openclaw_profile(path, install)
            result = json.loads(path.read_text())
            self.assertEqual(result['models'], original['models'])
            self.assertTrue(result['plugins']['entries']['bcn']['enabled'])

    def test_failure_propagates_and_restores_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'openclaw.json'
            path.write_text('{"models":{}}')
            def fail():
                raise RuntimeError('install_failed')
            with self.assertRaisesRegex(RuntimeError, 'install_failed'):
                install_openclaw_profile(path, fail)
            self.assertEqual(json.loads(path.read_text()), {'models': {}})
