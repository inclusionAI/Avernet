"""Exercise the native Diagnose command, substituting only HTTP and the model."""
import io
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'platform'), str(ROOT / 'clawevolve-diagnose')]
from clawevolve_runtime import executor, runtime
from clawevolve_diagnose.cli import build_parser
from clawevolve_diagnose.run.command import run_diagnose_command


class NativeDiagnoseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = self.root / 'session.jsonl'
        self.session.write_text('\n'.join(json.dumps(value) for value in [
            {'type': 'session', 'id': 'source-session', 'timestamp': '2026-09-21T08:00:00Z'},
            {'type': 'message', 'message': {'role': 'user', 'content': 'Read the project README and summarize it.'}},
            {'type': 'message', 'message': {'role': 'assistant', 'content': 'I cannot access any files.'}},
        ]))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('SKILL.md', '# Analyze the supplied session')
        self.package = archive.getvalue()
        self.input = {'protocolVersion': 'clawevolve.stage-runtime/v1',
            'stage': {'key': 'diagnose', 'mode': 'replace'},
            'implementation': {'entrypoint': 'SKILL.md',
                'packageSha256': hashlib.sha256(self.package).hexdigest(),
                'package': {'url': 'https://artifacts.example/diagnose.zip'}},
            'input': {'goal': 'Analyze tool use'}}
        self.calls, self.reports = [], []
        self.ask = False
        for patcher in [patch.object(runtime, 'SOURCE_WORKSPACE', self.root / 'runtime'),
                        patch.object(runtime, '_http', self.http),
                        patch.object(executor, '_run_openclaw_agent', self.model),
                        patch.dict(os.environ, {'OPENAI_API_KEY': ''})]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self):
        return build_parser().parse_args(['--task-id', 'EV-NATIVE-D', '--step-id', 'STEP-NATIVE-D',
            '--clawweb-url', 'http://localhost:1', '--intent', 'Analyze tool use',
            '--debug-session-path', str(self.session), '--openclaw-home', str(self.root / '.openclaw'),
            '--output-dir', str(self.root / 'output')])

    def http(self, method, url, *, body=None, **kwargs):
        if url.endswith('/input'):
            return json.dumps(self.input).encode()
        if url == 'https://artifacts.example/diagnose.zip':
            return self.package
        if method == 'POST' and url.endswith('/report'):
            self.reports.append(json.loads(body))
            return b'{"ok":true}'
        raise AssertionError((method, url))

    def report(self, task_id, step_id, **payload):
        self.reports.append(payload)
        return {'status': 'posted', 'reported_status': payload['status']}

    def model(self, context, model):
        value = json.loads(Path(context['inputFile']).read_text())
        self.calls.append(value)
        self.assertEqual(value['phase'], 'session_analysis')
        self.assertEqual(Path(value['input']['session']['path']), self.session)
        if self.ask:
            self.ask = False
            result = {'hitl': True, 'question': {'format': 'form', 'title': 'Scope', 'contents': [],
                'questions': [{'id': 'scope', 'type': 'short_text', 'title': 'Which tool?', 'required': True}]}}
        else:
            result = {'diagnoses': [{'case_type': 'bad', 'symptom_class': 'tool_use',
                'root_cause_class': 'instruction_gap', 'common_problem_key': 'file_access',
                'evolution_failure_mode': 'tool_failure', 'query': 'Read the project README and summarize it.',
                'root_cause_summary': 'The assistant did not use the available file tool.',
                'confidence': 0.9, 'quality_score': 0.9,
                'evidence': [{'kind': 'assistant', 'text': 'I cannot access any files.'}],
                'intent_match': {'is_match': True, 'confidence': 0.9}}]}
        Path(context['resultFile']).write_text(json.dumps(result))
        return {'status': 'succeeded'}

    def test_native_command_retains_acquisition_selection_and_artifacts(self):
        result = run_diagnose_command(self.args(), step_reporter=self.report)
        self.assertEqual(result.exit_code, 0, result.payload)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([report['status'] for report in self.reports], ['succeeded'])
        output = self.reports[0]['output']['result']
        self.assertIn('cases', output)
        sources = list((self.root / 'output').rglob('plan-source.json'))
        self.assertTrue(sources)
        self.assertEqual(json.loads(sources[0].read_text())['schema_version'], 'plan-source/v2')
        self.assertNotIn('plan_source', self.reports[0])

    def test_hitl_exits_without_failure_and_resumes_the_native_pipeline(self):
        self.ask = True
        first = run_diagnose_command(self.args(), step_reporter=self.report)
        self.assertEqual(first.payload['status'], 'waiting_for_input', first.payload)
        pending = self.reports[-1]
        answer = {'answers': {'scope': {'value': 'file tool'}}}
        self.input['input']['human_input'] = {'history': [{
            'question': {**pending['output']['question'], 'business_resume': pending['progress']['business_resume']},
            'answer': answer}]}
        resumed = run_diagnose_command(self.args(), step_reporter=self.report)
        self.assertEqual(resumed.exit_code, 0, resumed.payload)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[-1]['hitl']['answer'], answer)
        self.assertEqual([report['status'] for report in self.reports], ['succeeded', 'succeeded'])


if __name__ == '__main__':
    unittest.main()
