"""Native Tune/Review calls and watchdog pause; only model/HTTP are replaced."""
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

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'platform'), str(ROOT / 'clawevolve-workflow/scripts/handlers')]
from clawevolve_runtime import executor, runtime
from clawevolve_runtime.core import CoreWaiting
import clawevolve_optimize_run as handler


class NativeOptimizeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / 'workspace'
        self.target = self.workspace / 'skills/example/SKILL.md'
        self.target.parent.mkdir(parents=True)
        self.target.write_text('# Example\n')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('SKILL.md', '# Improve the supplied target and explain the result')
        self.package = archive.getvalue()
        self.input = {'protocolVersion': 'clawevolve.stage-runtime/v1',
            'stage': {'key': 'optimize', 'mode': 'replace'},
            'implementation': {'entrypoint': 'SKILL.md',
                'packageSha256': hashlib.sha256(self.package).hexdigest(),
                'package': {'url': 'https://artifacts.example/optimize.zip'}}, 'input': {'round': 1}}
        self.calls, self.reports = [], []
        self.ask = False
        for patcher in [patch.object(runtime, 'SOURCE_WORKSPACE', self.root / 'runtime'),
                        patch.object(runtime, '_http', self.http),
                        patch.object(executor, '_run_openclaw_agent', self.model),
                        patch.dict(os.environ, {'CLAWWEB_VERSION': 'openversion',
                            'OPENCLAW_WORKSPACE': str(self.workspace)})]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.paths = handler.resolve_paths(self.args())
        for key in ('input_dir', 'tune_dir', 'spec_dir', 'accept_dir', 'optimize_input_dir', 'rollback_dir'):
            self.paths[key].mkdir(parents=True, exist_ok=True)
        self.spec = {'schema_version': 'evolution.spec.v1', 'spec_version': 'v0',
            'objective_contract': {'objective_summary': ['Clarify file access']},
            'accepted_baseline_snapshot': {}, 'experiment_questions': [], 'scope_contract': {}}
        (self.paths['input_dir'] / 'spec-v0.json').write_text(json.dumps(self.spec))
        (self.paths['optimize_input_dir'] / 'objective.md').write_text('Clarify file access')
        snapshot = self.paths['rollback_dir'] / 'snapshot.zip'
        with zipfile.ZipFile(snapshot, 'w') as package:
            package.write(self.target, 'skills/example/SKILL.md')
        self.state_path = self.paths['round_dir'] / 'round_state.json'
        self.state_path.write_text(json.dumps({'task_id': 'EV-NATIVE-O', 'step_id': 'STEP-NATIVE-O',
            'rollback_snapshot': {'status': 'ready', 'path': str(snapshot),
                'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}}))
        self.acceptance = {'accepted': False, 'decision': 'not_improved',
            'bench_decision': 'not_improved', 'promotion_status': 'not_started'}
        (self.paths['accept_dir'] / 'acceptance_report.json').write_text(json.dumps(self.acceptance))

    def args(self):
        return SimpleNamespace(task_id='EV-NATIVE-O', step_id='STEP-NATIVE-O', round=1,
            workspace=str(self.workspace), skill_base_dir=str(ROOT), clawweb_url='http://localhost:1',
            clawweb_url_camel='', skip_clawweb=False, model='', optimizer_model='', tune_model='',
            review_model='', resume=True, force=False)

    def http(self, method, url, *, body=None, **kwargs):
        if url.endswith('/input'):
            return json.dumps(self.input).encode()
        if url == 'https://artifacts.example/optimize.zip':
            return self.package
        if method == 'POST' and url.endswith('/report'):
            self.reports.append(json.loads(body))
            return b'{"ok":true}'
        raise AssertionError((method, url))

    def model(self, context, model):
        value = json.loads(Path(context['inputFile']).read_text())
        self.calls.append(value)
        if self.ask:
            self.ask = False
            result = {'hitl': True, 'question': {'format': 'form', 'title': 'Scope', 'contents': [],
                'questions': [{'id': 'scope', 'type': 'short_text', 'title': 'Which files?', 'required': True}]}}
        else:
            files = value['input']['output_files']
            if value['phase'] == 'tune':
                self.target.write_text('# Example\nUse the file tool to read the README.\n')
                content = {'tune_report.md': 'Clarified file access.',
                    'changed_files.txt': 'skills/example/SKILL.md\n',
                    'diff.patch': '--- a/skills/example/SKILL.md\n+++ b/skills/example/SKILL.md\n@@ -1 +1,2 @@\n # Example\n+Use the file tool to read the README.\n',
                    'change_manifest.json': json.dumps({'schema_version': 'evolution.change_manifest.v2',
                        'changed_files': ['skills/example/SKILL.md']})}
            else:
                self.assertEqual(value['input']['acceptance']['bench_decision'], 'not_improved')
                content = {'review_decision.json': json.dumps({'schema_version': 'evolution.review_decision.v2',
                    'round_id': 1, 'acceptance_decision': 'not_improved', 'summary': 'The measured result did not improve.',
                    'confidence': 'medium', 'hypotheses': [], 'direction_decisions': []})}
            for name, location in files.items():
                Path(location).write_text(content[name])
            result = {'artifacts': files}
        Path(context['resultFile']).write_text(json.dumps(result))
        return {'status': 'succeeded'}

    def test_native_tune_change_summary_and_review_renderer_still_run(self):
        args = self.args()
        handler.select_business_core(args)
        handler.action_ensure_tune(args)
        self.assertEqual(json.loads(self.state_path.read_text())['candidate_mutation_state'], 'applied')
        handler.action_ensure_review(args)
        rendered = json.loads((self.paths['spec_dir'] / 'spec-v1.json').read_text())
        self.assertEqual(rendered['created_by'], 'clawevolve-review-renderer')
        acceptance = json.loads((self.paths['accept_dir'] / 'acceptance_report.json').read_text())
        for field, expected in self.acceptance.items():
            self.assertEqual(acceptance[field], expected)
        self.assertEqual([call['phase'] for call in self.calls], ['tune', 'review'])

    def test_watchdog_pauses_without_retry_fallback_or_restore_on_answer(self):
        args = self.args()
        handler.select_business_core(args)
        self.ask = True
        with self.assertRaises(CoreWaiting) as raised:
            handler._call_action_for_round_watchdog(args, 'ensure-tune', handler.action_ensure_tune)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.reports, [])
        args._business_core.report_waiting(args, raised.exception)
        pending = self.reports[-1]
        self.input['input']['human_input'] = {'history': [{
            'question': {**pending['output']['question'], 'business_resume': pending['progress']['business_resume']},
            'answer': {'answers': {'scope': {'value': 'example Skill only'}}}}]}
        resumed = self.args()
        handler.select_business_core(resumed)
        result = handler._call_action_for_round_watchdog(resumed, 'ensure-tune', handler.action_ensure_tune)
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.paths['round_dir'] / 'watchdog_startup_restore.json').exists())
        self.assertEqual(handler._step_policy(resumed, 'ensure-tune')['fallback'], None)


if __name__ == '__main__':
    unittest.main()
