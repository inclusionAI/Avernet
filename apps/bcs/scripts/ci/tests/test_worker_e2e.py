"""Regressions for the isolated Gateway–Worker coverage story."""
import importlib.util
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


BCS = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    'worker_runtime_story', BCS / 'scripts/e2e-test/worker_runtime_story.py')
STORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORY)


def valid_report():
    return {
        'status': 'passed', 'store': 'memory_mock', 'durable_store': False, 'archive': 'sqlite',
        'checks': [
            'gateway_http_retry_conflict_once', 'gateway_http_concurrent_first_request',
            'gateway_http_sse_get_task_consistency', 'document_projection_atomic_patch_and_unknown',
            'finish_archive_restore_and_late_cancel', 'append_archive_restore_and_late_cancel',
            'cancel_independent_of_runner', 'quarantine_and_control_authorization',
            'blocked_and_control_authorization', 'memory_store_worker_takeover',
            'graceful_handoff_preserves_pending_task', 'human_reply_dispatch_input_once',
            'holder_input_acceptance_and_cancel_unknown', 'live_follow_snapshot_trim_gap_restore',
            'sending_unknown_late_evidence_without_rerun',
        ],
    }


class WorkerCoverageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scripts = self.root / 'scripts'
        self.stories = self.scripts / 'e2e-test'
        self.stories.mkdir(parents=True)
        self.wrapper = self.scripts / 'worker_e2e_coverage.sh'
        shutil.copy(BCS / 'scripts/worker_e2e_coverage.sh', self.wrapper)
        ci = self.scripts / 'ci'
        ci.mkdir()
        shutil.copy(BCS / 'scripts/ci/validate_worker_e2e.py', ci)
        self.runtime = self.root / 'target/cov-e2e/llvm-cov-target'
        (self.runtime / 'debug').mkdir(parents=True)
        self.report = self.runtime.parent / 'worker_e2e.json'

    def run_wrapper(self):
        return subprocess.run(['bash', str(self.wrapper), '--run-only'],
                              capture_output=True, text=True, timeout=10)

    def test_failed_profile_cleanup_invalidates_previous_success(self):
        # A directory matching the profile glob forces unlink to fail deterministically.
        (self.runtime / 'worker-stale.profraw').mkdir()
        self.report.write_text('{"status":"passed"}')
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.report.read_text())['status'], 'failed')

    def test_wrapper_requires_fresh_complete_evidence_despite_story_success(self):
        binary = self.runtime / 'debug/bcs-worker-e2e'
        binary.touch(mode=0o755)
        # The empty executable only satisfies the wrapper's permission check.
        # The fake story exits zero regardless of the evidence it writes.
        story = self.stories / 'worker_runtime_story.py'
        story.write_text('from pathlib import Path\nimport sys\n'
                         'report = Path(sys.argv[sys.argv.index("--report") + 1])\n'
                         'evidence = Path(__file__).with_suffix(".json").read_text()\n'
                         'if evidence:\n    report.write_text(evidence)\n'
                         'else:\n    report.unlink()\n')
        expected = valid_report()
        cases = [('complete', expected, True), ('missing', None, False),
                 ('malformed', 'not-json', False), ('failed', {**expected, 'status': 'failed'}, False),
                 ('wrong store', {**expected, 'store': 'persistent'}, False),
                 ('durable', {**expected, 'durable_store': True}, False),
                 ('wrong archive', {**expected, 'archive': 'mock'}, False),
                 ('not an object', [expected], False),
                 ('empty checks', {**expected, 'checks': []}, False)]
        for scenario in expected['checks']:
            cases.append((scenario, {**expected, 'checks': [
                c for c in expected['checks'] if c != scenario]}, False))
        for label, report, succeeds in cases:
            with self.subTest(label=label):
                self.report.write_text(json.dumps(expected))  # Simulate a previous success.
                story.with_suffix('.json').write_text(
                    '' if report is None else report if isinstance(report, str) else json.dumps(report))
                result = self.run_wrapper()
                self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
                if succeeds:
                    self.assertIn('graceful_handoff_preserves_pending_task', result.stdout)

    def test_story_failure_keeps_its_status_and_surfaces_worker_log(self):
        (self.runtime / 'debug/bcs-worker-e2e').touch(mode=0o755)
        story = self.stories / 'worker_runtime_story.py'
        story.write_text('from pathlib import Path\nimport sys\n'
                         'Path(sys.argv[-1]).with_suffix(".log").write_text("worker startup failed\\n")\n'
                         'sys.exit(7)\n')
        self.report.with_suffix('.log').write_text('stale worker log\n')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('worker startup failed', result.stderr)
        self.assertNotIn('stale worker log', result.stderr)
        self.assertEqual(json.loads(self.report.read_text())['status'], 'failed')

    def test_failed_readiness_reaps_owned_process(self):
        child = self.root / 'child'
        for readiness, output in [('malformed', "print('not-json', flush=True)"),
                                  ('wrong', "print('{}', flush=True)"), ('silent', 'pass')]:
            with self.subTest(readiness=readiness):
                child.write_text(f'#!{sys.executable}\nimport time\n{output}\ntime.sleep(300)\n')
                child.chmod(0o755)
                children = []
                popen = subprocess.Popen

                def start(*args, **kwargs):
                    process = popen(*args, **kwargs)
                    children.append(process)
                    return process

                def bounded_read(host):
                    item = host.replies.get(timeout=0.25)
                    if isinstance(item, Exception):
                        raise item
                    return item

                with patch.object(STORY.subprocess, 'Popen', start), patch.object(STORY.Host, 'read', bounded_read):
                    with (self.root / 'child.log').open('w') as log:
                        with self.assertRaises((ValueError, AssertionError, queue.Empty)):
                            STORY.Host(child, self.root / 'archive.db', log)
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)
