"""Run the real Plan command; substitute only model and HTTP boundaries."""
import hashlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'platform'), str(ROOT / 'clawevolve-plan'), str(ROOT / 'clawevolve-plan/tests')]
from clawevolve_runtime import executor, runtime
from clawevolve_plan.cli import _parser
from clawevolve_plan.pipeline.runner import run_plan_command
from clawevolve_plan.integration.clawweb import ClawWebClient
from clawevolve_plan.bench.case_contract import _fallback_contract
from test_direct_goal import GOAL, TARGET, direct_payload


class NativePlanTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / 'candidate'
        target = self.workspace / TARGET
        target.parent.mkdir(parents=True)
        target.write_text('# Original target\n')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('SKILL.md', '# Business planning Skill')
        self.package = archive.getvalue()
        self.input = {'protocolVersion': 'clawevolve.stage-runtime/v1',
            'stage': {'key': 'plan', 'mode': 'replace'},
            'implementation': {'entrypoint': 'SKILL.md', 'executionContract': 'clawevolve.plan-business/v1',
                'packageSha256': hashlib.sha256(self.package).hexdigest(),
                'package': {'url': 'https://artifacts.example/plan.zip'}},
            'input': {'goal': GOAL}}
        self.calls = []
        self.reports = []
        self.templates = {}
        self.ask = False
        self.question = {'format': 'form', 'title': 'Scope', 'contents': [],
            'questions': [{'id': 'scope', 'type': 'short_text', 'title': 'Confirm scope', 'required': True}]}
        for patcher in [
            patch.object(runtime, 'SOURCE_WORKSPACE', self.root / 'runtime'),
            patch.object(runtime, '_http', self.platform_http),
            patch.object(executor, '_run_openclaw_agent', self.model),
            patch.object(ClawWebClient, '_open_with_retry', self.bench_http),
            patch.dict(os.environ, {'CLAWEVOLVE_TARGET_WORKSPACE': str(self.workspace), 'CLAWBENCH_OWNER_ID': 'owner'}),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self):
        return _parser().parse_args(['--task-id', 'EV-NATIVE', '--step-id', 'STEP-NATIVE',
            '--goal', GOAL, '--clawweb-url', 'http://localhost:1',
            '--workspace', str(self.workspace), '--evolve-results-dir', str(self.root / 'results')])

    def platform_http(self, method, url, *, body=None, **kwargs):
        if method == 'GET' and url.endswith('/input'):
            return json.dumps(self.input).encode()
        if url == 'https://artifacts.example/plan.zip':
            return self.package
        if method == 'POST' and url.endswith('/report'):
            self.reports.append(json.loads(body))
            return b'{"ok":true}'
        raise AssertionError((method, url))

    def report(self, task_id, step_id, **payload):
        self.reports.append(payload)
        return {'enabled': True, 'status': 'ok', 'response': {'ok': True}}

    def model(self, context, model):
        value = json.loads(Path(context['inputFile']).read_text())
        self.calls.append(value)
        if value['phase'] == 'direct_goal':
            result = direct_payload(self.workspace)
            second_case = copy.deepcopy(result['prospective_cases'][0])
            second_case.update(case_id='goal-case-002', query='请完成数据预处理任务 B')
            result['prospective_cases'].append(second_case)
            second_finding = copy.deepcopy(result['discovery']['case_findings'][0])
            second_finding['case_id'] = 'goal-case-002'
            result['discovery']['case_findings'].append(second_finding)
            result['discovery']['target_findings'][0]['related_case_ids'].append('goal-case-002')
        elif value['phase'] == 'case_contract':
            if self.ask and 'hitl' not in value:
                result = {'hitl': True, 'question': self.question}
                self.ask = False
            else:
                data = value['input']
                result = {'contracts': [_fallback_contract(data['case'], data['goal'], data['user_intent'])]}
        else:
            raise AssertionError(value['phase'])
        Path(context['resultFile']).write_text(json.dumps(result))
        return {'status': 'succeeded'}

    def bench_http(self, req, path, method, timeout=None):
        if path == '/api/bench/domains':
            value = json.loads(req.data)
            response = {'ownerUserId': 'owner', 'domainId': value['domainId'], 'status': 'active'}
        elif path.endswith('/uploads/scan'):
            message = BytesParser(policy=default).parsebytes(
                ('Content-Type: ' + req.get_header('Content-type') + '\r\n\r\n').encode() + req.data)
            raw = next(message.iter_parts()).get_payload(decode=True)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = [{'templateName': Path(name).stem, 'sourceHash': hashlib.sha256(archive.read(name)).hexdigest()[:16],
                            'imported': True} for name in archive.namelist() if not name.endswith('/')]
            self.templates[path.removesuffix('/uploads/scan')] = entries
            response = {'items': entries}
        elif path.endswith('/templates/batch-publish'):
            response = {'published': len(json.loads(req.data)['templates']), 'failed': 0}
        elif path.endswith('/templates?status=published'):
            response = {'items': self.templates[path.removesuffix('/templates?status=published')]}
        else:
            raise AssertionError((method, path))
        return 200, json.dumps(response)

    def test_custom_analysis_still_runs_native_render_publish_validate_and_report(self):
        result = run_plan_command(self.args(), step_reporter=self.report)
        self.assertEqual(result.exit_code, 0, result.payload)
        self.assertEqual(len(self.reports), 1)
        report = self.reports[0]
        self.assertEqual(report['status'], 'succeeded')
        output = report['output']['result']
        self.assertTrue(output['benchDomains']['trainBenchDomainId'])
        self.assertTrue(output['benchDomains']['testBenchDomainId'])
        self.assertEqual(len(self.templates), 2)
        files = list((self.root / 'results').rglob('objective.json'))
        self.assertEqual(len(files), 1)
        objective = json.loads(files[0].read_text())
        self.assertFalse(objective['document_generation']['model_used'])
        self.assertTrue(files[0].with_suffix('.md').is_file())
        self.assertEqual({call['phase'] for call in self.calls}, {'direct_goal', 'case_contract'})

    def test_real_handler_pauses_and_resumes_pending_call_before_publishing(self):
        self.ask = True
        first = run_plan_command(self.args(), step_reporter=self.report)
        self.assertEqual(first.payload['status'], 'waiting_for_input', first.payload)
        self.assertFalse(self.templates)
        pending = self.reports[-1]
        answer = {'answers': {'scope': {'value': 'keep original scope'}}}
        self.input['input']['human_input'] = {'history': [{
            'question': {**pending['output']['question'], 'business_resume': pending['progress']['business_resume']},
            'answer': answer,
        }]}
        resumed = run_plan_command(self.args(), step_reporter=self.report)
        self.assertEqual(resumed.exit_code, 0, resumed.payload)
        self.assertEqual(sum(call['phase'] == 'direct_goal' for call in self.calls), 1)
        resumed_calls = [call for call in self.calls if 'hitl' in call]
        self.assertEqual(len(resumed_calls), 1)
        self.assertEqual(resumed_calls[0]['hitl']['answer'], answer)
        self.assertEqual(self.reports[-1]['status'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
