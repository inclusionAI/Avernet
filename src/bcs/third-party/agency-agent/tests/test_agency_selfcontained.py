"""Self-contained entry behavior and command documentation consistency."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]


class SelfContainedLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        shutil.copy(SCRIPT_DIR / 'launch-agency.sh', self.bundle / 'launch-agency.sh')
        (self.bundle / 'launch-agency.sh').chmod(0o755)

    def test_direct_entry_downloads_fixed_helpers_into_private_cache(self):
        calls = self.root / 'curl-calls.jsonl'
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        shutil.copy(SCRIPT_DIR / 'tests' / 'fake_git.py', bin_dir / 'git')
        (bin_dir / 'git').chmod(0o755)
        shutil.copy(SCRIPT_DIR / 'tests' / 'fake_openclaw.py', bin_dir / 'openclaw')
        (bin_dir / 'openclaw').chmod(0o755)
        cache = self.root / 'home' / '.avernet' / 'bcs' / 'agency-agent' / '.bundle'
        cache.mkdir(parents=True)
        for name in ('agency_launcher.py', 'agency_console.py', 'agency_parallel.py',
                     'agency_profiles.py', 'agency_runtime.py'):
            (cache / name).write_text('# fixed helper')
        curl = bin_dir / 'curl'
        curl.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$AGENCY_CURL_CALLS"\nexit 0\n')
        curl.chmod(0o755)
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                   AGENCY_CURL_CALLS=str(calls), HOME=str(self.root / 'home'),
                   BCS_REGISTER_TOKEN='test-token')
        env.pop('AGENCY_PYTHON', None)
        result = subprocess.run(['bash', str(self.bundle / 'launch-agency.sh'), '--help'],
                                env=env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('download', result.stderr + result.stdout)
        self.assertTrue(calls.read_text().startswith('-fsSL https://github.com/inclusionAI/Avernet/archive/refs/heads/dev.tar.gz'))

    def test_readme_documents_actual_download_command(self):
        content = (SCRIPT_DIR / 'README.zh-CN.md').read_text()
        self.assertIn('curl -fsSL', content)
        self.assertIn('launch-agency.sh', content)
        self.assertIn('~/.avernet/bcs/agency-agent/.bundle', content)


if __name__ == '__main__':
    unittest.main()
