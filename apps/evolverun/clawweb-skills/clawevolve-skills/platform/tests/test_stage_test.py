"""Exercise real native fixture rendering; mock only the CW HTTP boundary."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import runtime, stage_test


class BenchPublicationTest(unittest.TestCase):
    def test_published_source_hash_matches_bench_api_contract(self):
        stage_test._plan_imports()
        calls = []
        client = SimpleNamespace(
            create_domain=lambda *args: None,
            upload_zip=lambda *args: calls.append('upload'),
            batch_publish=lambda *args: {'body': {'published': 1, 'failed': 0}},
            list_published_templates=lambda *args: {'body': [
                {'templateName': 'case', 'sourceHash': 'a' * 16}]},
        )
        result = stage_test._publish(client, 'owner', 'domain', {'zip_path': '/tmp/test.zip'},
            [{'id': 'case', 'content_sha256': 'a' * 64}])
        self.assertEqual(result, {'case': 'a' * 16})
        self.assertEqual(calls, ['upload'])
        with self.assertRaisesRegex(ValueError, 'do not match'):
            stage_test._publish(client, 'owner', 'domain', {'zip_path': '/tmp/test.zip'},
                [{'id': 'case', 'content_sha256': 'b' * 64}])

    def test_fixture_failure_is_reported_before_native_watchdog(self):
        args = SimpleNamespace(task_id='EV-TEST', step_id='STEP-OPT', clawweb_url='http://localhost:1')
        with patch.object(runtime, '_step_input', return_value={}), \
                patch.object(stage_test, 'prepare_optimize_test', side_effect=ValueError('invalid fixture')), \
                patch.object(runtime, '_fail') as fail:
            with self.assertRaisesRegex(ValueError, 'invalid fixture'):
                runtime._begin_core(args)
        fail.assert_called_once_with(args)
        self.assertIn('invalid fixture', args.message)


class OptimizeFixtureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / '.openclaw/clawevolve_workspaces/EV-TEST/workspace'
        self.skill = self.workspace / 'skills/skills-local/stage-test-text-summary'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('# Test Skill\n')
        self.source = self.root / '.openclaw/workspace'
        self.source.mkdir(parents=True)
        self.args = SimpleNamespace(task_id='EV-TEST', step_id='STEP-OPT', clawweb_url='http://localhost:1')
        logical = '/home/admin/.openclaw/clawevolve_workspaces/EV-TEST/workspace'
        self.payload = {'protocolVersion': '1.0', 'task': {'taskId': 'EV-TEST', 'taskType': 'stage_test',
            'config': {'stageTest': {'stage': 'optimize', 'flow': 'skill_evolution', 'inputFixture': 'optimize-v1'},
                'trainBenchDomainId': 'stage_test_EV-TEST_train', 'testBenchDomainId': 'stage_test_EV-TEST_test',
                'targetSkill': {'name': 'stage-test-text-summary', 'baseline': {'sha256': 'a'*64},
                    'candidate': {'prepared': {'workspacePath': logical,
                        'skillPath': logical + '/skills/skills-local/stage-test-text-summary', 'baselineSha256': 'a'*64}}}}},
            'step': {'stepId': 'STEP-OPT', 'stepType': 'optimize'}, 'target': {'userId': 'owner', 'botId': 'bot'},
            'inputs': {'diagnoses': [{'taskId': 'EV-TEST', 'plan': {'stepId': 'STEP-FIXTURE', 'output': {}}}]}}
        self.reports = []
        self.published = []
        stage_test._plan_imports()
        for patcher in [patch.object(runtime, 'RUNTIME_LAYOUT_HOME', self.root),
                        patch.object(runtime, 'SOURCE_WORKSPACE', self.source),
                        patch.object(runtime, '_http', self.http),
                        patch.object(stage_test, '_publish', self.publish),
                        patch('clawevolve_plan.integration.clawweb.ClawWebClient')]:
            patcher.start(); self.addCleanup(patcher.stop)

    def http(self, method, url, *, body=None, **kwargs):
        if method == 'GET':
            return json.dumps(self.payload).encode()
        self.assertEqual(kwargs.get('headers', {}).get('Content-Type'), 'application/json')
        self.assertIn('/EV-TEST/steps/STEP-FIXTURE/report', url)
        self.reports.append(json.loads(body))
        return b'{"ok":true}'

    def publish(self, client, owner, domain, package, items):
        self.assertTrue(Path(package['zip_path']).is_file())
        self.assertEqual(owner, 'owner')
        self.assertEqual(len(items), 2)
        self.published.append(domain)
        return {item['id']: item['content_sha256'] for item in items}

    def test_native_files_bench_resources_and_retry_preserve_candidate(self):
        stage_test.prepare_optimize_test(self.args, self.payload)
        root = self.source / 'clawevolve_results/EV-TEST/optimize/input'
        self.assertEqual(len(self.published), 2)
        self.assertEqual(self.reports[0]['output']['benchCases']['trainCount'], 2)
        objective = json.loads((root / 'objective.json').read_text())
        self.assertEqual(objective['primary_metric']['target'], 1.0)
        self.assertTrue((root / 'spec-v0.md').is_file())
        (self.skill / 'SKILL.md').write_text('Actual candidate edits')
        stage_test.prepare_optimize_test(self.args, self.payload)
        self.assertEqual(len(self.published), 2)
        self.assertEqual((self.skill / 'SKILL.md').read_text(), 'Actual candidate edits')
        self.assertEqual(self.reports[0], self.reports[1])
        (root / 'objective.md').write_text('Tampered')
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            stage_test.prepare_optimize_test(self.args, self.payload)

    def test_full_tasks_and_historical_tests_have_no_effect(self):
        for kind in ['full', 'optimize', 'diagnose']:
            payload = copy.deepcopy(self.payload)
            payload['task']['taskType'] = kind
            stage_test.prepare_optimize_test(self.args, payload)
        payload = copy.deepcopy(self.payload)
        del payload['task']['config']['stageTest']['inputFixture']
        stage_test.prepare_optimize_test(self.args, payload)
        self.assertFalse(self.reports)
        self.assertFalse((self.source / 'clawevolve_results').exists())

    def test_rejects_wrong_candidate_domains_identity_before_writes(self):
        for mutate in [lambda p: p['task'].update(taskId='OTHER'),
                       lambda p: p['task']['config'].update(trainBenchDomainId='shared-domain'),
                       lambda p: p['task']['config']['targetSkill']['candidate']['prepared'].update(workspacePath='/tmp/other'),
                       lambda p: p['inputs']['diagnoses'][0].update(taskId='OTHER')]:
            payload = copy.deepcopy(self.payload); mutate(payload)
            with self.assertRaises(ValueError):
                stage_test.prepare_optimize_test(self.args, payload)
        self.assertFalse(self.reports)
        self.assertFalse(self.published)

    def test_replacement_uses_native_metadata_and_receives_constructed_result(self):
        payload = {'protocolVersion': 'clawevolve.stage-runtime/v1',
            'task': {'taskId': 'EV-TEST', 'taskType': 'stage_test'},
            'stage': {'key': 'optimize', 'mode': 'replace'}, 'input': {}}
        stage_test.prepare_optimize_test(self.args, payload)
        self.assertEqual(payload['input']['plan_result'], self.reports[0]['output'])

    def test_failed_publication_does_not_report_prepared_or_write_success_marker(self):
        with patch.object(stage_test, '_publish', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                stage_test.prepare_optimize_test(self.args, self.payload)
        self.assertFalse(self.reports)
        self.assertFalse(list(self.source.rglob('stage-test-fixture.json')))


if __name__ == '__main__':
    unittest.main()
