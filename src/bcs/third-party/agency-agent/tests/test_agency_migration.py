"""Offline migration preserves credentials/content and rejects unsafe moves."""
import fcntl
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
from agency_migrate import apply_migration  # noqa: E402


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.old = self.root / 'third-party'
        self.older = self.root / 'old-bcs'
        self.target = self.root / 'agency-agent'
        self.port = 33000

    def instance(self, root, name, port=33000, engine='openclaw'):
        path = root / name
        (path / '.bcs').mkdir(parents=True)
        (path / 'workspace').mkdir()
        (path / 'plugins').mkdir()
        (path / 'agents/main/sessions').mkdir(parents=True)
        (path / 'instance.json').write_text(json.dumps({
            'engine': engine, 'profile_path': f'engineering/{name}.md',
            'source_sha256': 'profile-digest', 'port': port, 'source_root': str(root / 'agency-agent'),
        }))
        (path / '.bcs/session.json').write_text(json.dumps({
            'bot_uuid': name, 'token': 'test-token', 'bcs_url': 'ws://127.0.0.1:21000/ws/bot',
        }) + '\n')
        (path / 'openclaw.json').write_text(json.dumps({
            'gateway': {'port': port, 'auth': {'mode': 'token', 'token': 'test-token'}},
            'agents': {'defaults': {'workspace': str(path / 'workspace')},
                       'list': [{'id': 'main', 'agentDir': str(path / 'agents/main/agent')}]},
            'channels': {'bcs': {'botId': name}},
        }))
        (path / 'plugins/installs.json').write_text(json.dumps({
            'plugin': {'installPath': str(path / 'npm/node_modules/plugin')},
        }))
        (path / 'agents/main/sessions/sessions.json').write_text(json.dumps({
            'session': {'sessionFile': str(path / 'agents/main/sessions/chat.jsonl')},
        }))
        (path / 'agents/main/sessions/chat.jsonl').write_text('Historical text: ' + str(path))
        for filename in ('SOUL.md', 'AGENTS.md', 'IDENTITY.md', 'MEMORY.md'):
            (path / 'workspace' / filename).write_text('Preserve ' + filename)
        (path / 'profile.md').write_text('Original role snapshot')
        (path / 'workspace/link').symlink_to(path / 'workspace/SOUL.md')
        return path

    def invoke(self, *extra):
        script = SCRIPT_DIR / 'agency_migrate.py'
        self.assertTrue(script.is_file(), 'offline migration utility is missing')
        return subprocess.run([sys.executable, str(script), '--source-dir', str(self.old),
                               '--source-dir', str(self.older), '--state-dir', str(self.target), *extra],
                              capture_output=True, text=True, timeout=20, check=False)

    def test_commit_failure_rolls_back_all_published_destinations(self):
        first = self.instance(self.old, 'alpha')
        second = self.instance(self.older, 'beta', port=33020)
        contents = {p: (p / '.bcs/session.json').read_bytes() for p in (first, second)}
        real_rename = Path.rename

        def fail_second_backup(path, destination):
            if path == second:
                raise OSError('simulated archive failure')
            return real_rename(path, destination)

        with patch.object(Path, 'rename', fail_second_backup):
            with self.assertRaises(OSError):
                apply_migration([self.old, self.older], self.target, 'openclaw')
        for path, original in contents.items():
            self.assertEqual((path / '.bcs/session.json').read_bytes(), original)
        self.assertEqual(list(self.target.glob('openclaw/*/instance.json')), [])
        manifest = json.loads(next(self.target.glob('.migration-backups/*/manifest.json')).read_text())
        self.assertEqual(manifest['status'], 'rolled_back')

    def test_shared_checkout_is_copied_and_original_cache_retained(self):
        source = self.instance(self.old, 'alpha')
        cache = self.old / 'agency-agent'
        (cache / '.git').mkdir(parents=True)
        (cache / 'engineering').mkdir()
        (cache / 'engineering/alpha.md').write_text('Repository source')
        result = self.invoke('--apply')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.target / 'agency-agent/engineering/alpha.md').read_text(), 'Repository source')
        self.assertTrue((cache / '.git').exists())
        metadata = json.loads((self.target / 'openclaw' / source.name / 'instance.json').read_text())
        self.assertEqual(metadata['source_root'], str(self.target / 'agency-agent'))

    def test_preview_has_no_writes(self):
        source = self.instance(self.old, 'alpha')
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(source.exists())
        self.assertFalse(self.target.exists())
        self.assertIn('1 instance', result.stdout)
        self.assertNotIn('test-token', result.stdout + result.stderr)

    def test_migrate_both_roots_preserves_session_and_rebases_paths(self):
        source = self.instance(self.old, 'alpha')
        other = self.instance(self.older, 'beta')  # conflicting port
        foreign = self.instance(self.old, 'foreign', engine='codex')
        sessions = {p.name: (p / '.bcs/session.json').read_bytes() for p in (source, other)}
        result = self.invoke('--apply')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('test-token', result.stdout + result.stderr)
        self.assertTrue(foreign.exists())
        ports = []
        for original in (source, other):
            migrated = self.target / 'openclaw' / original.name
            self.assertFalse(original.exists())
            self.assertEqual((migrated / '.bcs/session.json').read_bytes(), sessions[original.name])
            self.assertEqual((migrated / '.bcs/session.json').stat().st_mode & 0o777, 0o600)
            cfg = json.loads((migrated / 'openclaw.json').read_text())
            self.assertEqual(cfg['agents']['defaults']['workspace'], str(migrated / 'workspace'))
            self.assertEqual(cfg['gateway']['auth']['token'], 'test-token')
            ports.append(cfg['gateway']['port'])
            self.assertEqual(json.loads((migrated / 'instance.json').read_text())['port'], ports[-1])
            receipt = json.loads((migrated / 'plugins/installs.json').read_text())
            self.assertEqual(receipt['plugin']['installPath'], str(migrated / 'npm/node_modules/plugin'))
            index = json.loads((migrated / 'agents/main/sessions/sessions.json').read_text())
            self.assertEqual(index['session']['sessionFile'], str(migrated / 'agents/main/sessions/chat.jsonl'))
            self.assertEqual((migrated / 'agents/main/sessions/chat.jsonl').read_text(), 'Historical text: ' + str(original))
            self.assertEqual((migrated / 'workspace/MEMORY.md').read_text(), 'Preserve MEMORY.md')
            self.assertEqual((migrated / 'workspace/link').resolve(), migrated / 'workspace/SOUL.md')
        self.assertEqual(ports, [33000, 33020])
        manifests = list(self.target.glob('.migration-backups/*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text())
        self.assertEqual(manifest['status'], 'complete')
        for item in manifest['instances']:
            self.assertEqual((Path(item['backup']) / '.bcs/session.json').read_bytes(), sessions[Path(item['source']).name])
        again = self.invoke('--apply')
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(len(list(self.target.glob('openclaw/*/instance.json'))), 2)

    def test_conflicting_destination_never_overwrites_either_identity(self):
        source = self.instance(self.old, 'alpha')
        dest = self.instance(self.target / 'openclaw', 'alpha')
        (dest / '.bcs/session.json').write_text('{"bot_uuid":"other identity"}')
        before = (source / '.bcs/session.json').read_bytes()
        result = self.invoke('--apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((source / '.bcs/session.json').read_bytes(), before)
        self.assertIn('other identity', (dest / '.bcs/session.json').read_text())

    def test_locked_source_or_target_is_refused(self):
        source = self.instance(self.old, 'alpha')
        engine = self.target / 'openclaw'
        engine.mkdir(parents=True)
        for directory in (self.old, engine):
            with (directory / '.launcher.lock').open('w') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self.invoke('--apply')
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(source.exists())
                self.assertFalse((engine / 'alpha').exists())

    def test_running_gateway_port_is_refused_without_modifying_source(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            source = self.instance(self.old, 'alpha', port=listener.getsockname()[1])
            result = self.invoke('--apply')
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(source.exists())

    def test_malformed_managed_metadata_leaves_original_intact(self):
        source = self.instance(self.old, 'alpha')
        (source / 'plugins/installs.json').write_text('malformed')
        result = self.invoke('--apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(source.exists())
        self.assertFalse((self.target / 'openclaw/alpha').exists())


if __name__ == '__main__':
    unittest.main()
