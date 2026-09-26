"""Prepare real candidate files from a frozen ZIP; mock only HTTP I/O."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import candidate, runtime


def skill_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class CandidatePrepareTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.source = self.home / '.openclaw/workspace'
        self.candidates = self.home / '.openclaw/clawevolve_workspaces'
        self.workspace = self.candidates / 'EV-TEST/workspace'
        self.relative_skill = 'skills/skills-local/test-skill'
        self.target = self.workspace / self.relative_skill
        self.marker = self.workspace / '.clawevolve-candidate.json'
        self.live_skill = self.source / self.relative_skill
        self.live_skill.mkdir(parents=True)
        (self.live_skill / 'SKILL.md').write_text('Live Skill changed after task creation')
        (self.live_skill / 'live-only.txt').write_text('Not part of the frozen package')
        (self.source / 'skills/test-skill').symlink_to(self.live_skill)
        self.pool = self.source / 'skills-pool/skills-repo/unrelated/node_modules'
        self.pool.mkdir(parents=True)
        (self.pool / '.temporary-file').write_text('Unrelated installation in progress')
        (self.source / 'AGENTS.md').write_text('Unrelated Bot context')
        self.files = {
            'SKILL.md': '# Frozen Skill\n',
            'scripts/run.py': 'print("frozen")\n',
            'references/input.md': '# Reference\n',
            'assets/data.bin': b'\x00\x01\xff',
            'node_modules/dependency/index.js': 'export default 1;\n',
        }
        self.package = skill_zip(self.files)
        self.logical = '/home/admin/.openclaw/clawevolve_workspaces/EV-TEST/workspace'
        self.payload = {
            'protocolVersion': 'clawevolve.skill-candidate/v1', 'action': 'prepare',
            'sourceWorkspace': '/home/admin/.openclaw/workspace',
            'candidateWorkspace': self.logical,
            'targetSkill': {
                'path': f'{self.logical}/{self.relative_skill}',
                'baselineSha256': hashlib.sha256(self.package).hexdigest(),
                'package': {'method': 'GET', 'url': 'https://storage.example/baseline.zip'},
            },
        }
        self.args = SimpleNamespace(task_id='EV-TEST', step_id='STEP-PREPARE',
                                    clawweb_url='http://localhost:1')
        self.reports = []
        self.downloads = 0
        self.uploads = []
        for patcher in (
            patch.object(runtime, 'RUNTIME_LAYOUT_HOME', self.home),
            patch.object(runtime, 'SOURCE_WORKSPACE', self.source),
            patch.object(runtime, 'CANDIDATE_ROOT', self.candidates),
            patch.object(runtime, '_http', self.http),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def http(self, method, url, *, body=None, **kwargs):
        if method == 'GET' and url.endswith('/input'):
            return json.dumps(self.payload).encode()
        if method == 'GET' and url == self.payload['targetSkill']['package']['url']:
            self.downloads += 1
            if isinstance(self.package, Exception):
                raise self.package
            return self.package
        if method == 'PUT' and url == self.payload.get('candidatePackage', {}).get('url'):
            self.uploads.append(body)
            return b''
        self.assertEqual((method, url), ('POST', runtime._step_url(self.args, 'report')))
        self.reports.append(json.loads(body))
        return b'{"ok":true}'

    def test_prepares_only_complete_frozen_skill_and_runtime_links(self):
        result = runtime._prepare(self.args)
        actual = {str(path.relative_to(self.target)): path.read_bytes()
                  for path in self.target.rglob('*') if path.is_file()}
        self.assertEqual(actual, {name: value.encode() if isinstance(value, str) else value
                                  for name, value in self.files.items()})
        self.assertEqual({path.name for path in self.workspace.iterdir()},
                         {'skills', '.clawevolve-candidate.json', 'clawevolve_results'})
        self.assertEqual((self.workspace / 'skills/test-skill').resolve(), self.target)
        self.assertEqual((self.workspace / 'clawevolve_results').resolve(),
                         self.source / 'clawevolve_results')
        (self.workspace / 'skills/test-skill/SKILL.md').write_text('Candidate edit')
        self.assertEqual((self.target / 'SKILL.md').read_text(), 'Candidate edit')
        self.assertEqual((self.live_skill / 'SKILL.md').read_text(),
                         'Live Skill changed after task creation')
        self.assertEqual(result['output'], {'prepared': True, 'workspace': self.logical,
                         'targetSkillPath': f'{self.logical}/{self.relative_skill}'})
        self.assertEqual(self.reports[0]['status'], 'succeeded')

    def test_live_workspace_is_never_enumerated(self):
        scandir = os.scandir

        def guarded_scandir(path):
            if not isinstance(path, int) and Path(path).is_relative_to(self.source):
                raise FileNotFoundError('Live workspace entry changed during enumeration')
            return scandir(path)

        with patch.object(os, 'scandir', guarded_scandir):
            runtime._prepare(self.args)
        self.assertEqual((self.target / 'SKILL.md').read_text(), self.files['SKILL.md'])
        self.assertEqual(self.downloads, 1)

    def test_retry_preserves_candidate_edits_and_does_not_download_again(self):
        runtime._prepare(self.args)
        (self.target / 'SKILL.md').write_text('Round 2 edits')
        marker = self.marker.read_bytes()
        runtime._prepare(self.args)
        self.assertEqual((self.target / 'SKILL.md').read_text(), 'Round 2 edits')
        self.assertEqual(self.marker.read_bytes(), marker)
        self.assertEqual(self.downloads, 1)
        self.assertEqual(self.reports[0], self.reports[1])

    def test_prepared_skill_can_be_edited_validated_and_finalized(self):
        runtime._prepare(self.args)
        business = runtime._stage_business_input({'target_skill': {
            'workspace': self.logical, 'path': f'{self.logical}/{self.relative_skill}',
        }}, self.args.task_id)
        changes = candidate.CandidateChanges(business['target_skill']['workspace'],
            business['target_skill']['path'], self.home / 'round-snapshot.json')
        (self.target / 'SKILL.md').write_text('# Hardened Skill\n')
        self.assertEqual(changes.validate({'changed': True})['changed_files'], ['SKILL.md'])
        self.payload['action'] = 'finalize'
        self.payload['candidatePackage'] = {'method': 'PUT',
            'url': 'https://storage.example/candidate.zip', 'ref': 'candidate.zip'}
        result = runtime._finalize(self.args)
        self.assertEqual(len(self.uploads), 1)
        with zipfile.ZipFile(io.BytesIO(self.uploads[0])) as archive:
            self.assertEqual(set(archive.namelist()), set(self.files))
            self.assertEqual(archive.read('SKILL.md'), b'# Hardened Skill\n')
            self.assertEqual(archive.read('assets/data.bin'), self.files['assets/data.bin'])
        self.assertEqual(result['output']['artifact']['sha256'],
                         hashlib.sha256(self.uploads[0]).hexdigest())

    def test_incomplete_prepare_is_rebuilt_without_touching_other_tasks(self):
        self.target.mkdir(parents=True)
        (self.target / 'partial.txt').write_text('Interrupted prepare')
        other = self.candidates / 'EV-OTHER/workspace/keep.txt'
        other.parent.mkdir(parents=True)
        other.write_text('Other task')
        runtime._prepare(self.args)
        self.assertFalse((self.target / 'partial.txt').exists())
        self.assertEqual((self.target / 'SKILL.md').read_text(), self.files['SKILL.md'])
        self.assertEqual(other.read_text(), 'Other task')

    def test_download_failure_is_not_reported_as_prepared_and_can_retry(self):
        package = self.package
        self.package = OSError('Download unavailable')
        with self.assertRaisesRegex(OSError, 'Download unavailable'):
            runtime._prepare(self.args)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.reports)
        self.package = package
        runtime._prepare(self.args)
        self.assertTrue(self.marker.is_file())
        self.assertEqual(self.reports[0]['status'], 'succeeded')

    def test_checksum_mismatch_does_not_publish_candidate(self):
        self.package = skill_zip({'SKILL.md': 'Unexpected version'})
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'checksum mismatch'):
            runtime._prepare(self.args)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.target.exists())
        self.assertFalse(self.reports)

    def test_unsafe_zip_does_not_publish_candidate(self):
        self.package = skill_zip({'../escaped.md': 'Unsafe package'})
        self.payload['targetSkill']['baselineSha256'] = hashlib.sha256(self.package).hexdigest()
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'unsafe ZIP path'):
            runtime._prepare(self.args)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.target.exists())
        self.assertFalse(self.reports)

    def test_existing_candidate_rejects_different_baseline(self):
        runtime._prepare(self.args)
        self.payload['targetSkill']['baselineSha256'] = '0' * 64
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'does not match frozen task input'):
            runtime._prepare(self.args)
        self.assertEqual(self.downloads, 1)
        self.assertEqual(len(self.reports), 1)


if __name__ == '__main__':
    unittest.main()
