"""Core/runtime conformance and CLI boundaries; no live services or models."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from core.runner import run
from core.profile_loader import load_bench
from outputs.file import FileOutput
from adapters.local.runtime import LocalRuntime
from adapters.local.config import load_local_config
from bench import main

BENCH = ROOT.parents[2] / 'bench-case/bcn-bench-case/task_00_connectivity'


class RemoteLikeRuntime:
    """Contract double only: not a working Online/Simulator adapter."""
    def __init__(self):
        self.metadata = {'mode': 'contract-test', 'members': {'manager': 'm', 'worker': 'w'}}
        self.calls = []
    def prepare(self): self.calls.append('prepare')
    def submit(self, prompt, entry_member):
        self.calls.append('submit')
        return {'group_id': 'g', 'session_id': 'g:s'}
    def poll(self, timeout):
        self.calls.append('poll')
        return {'status': 'completed', 'output': 'answer', 'session': {'status': 'completed', 'output': 'answer'}}
    def quiesce(self, reason):
        self.calls.append('quiesce')
        return {'evidence_stable': True}  # Snapshot, no local process/port.
    def collect(self):
        self.calls.append('collect')
        return {'messages': [], 'session': {'status': 'completed', 'output': 'answer'},
                'summary': {'evidence_complete': True, 'evidence_origin': 'test-double', 'total_tokens': None}}
    def release(self):
        self.calls.append('release')
        return {'resources_released': True, 'reused_members_running': True}


class V3Tests(unittest.TestCase):
    def case(self):
        case, _, _ = load_bench(BENCH)
        case['code'] = "def grade(transcript, workspace): return {'delivery': 1}"
        return case

    def execute(self, runtime, case=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        output = FileOutput(tmp.name)
        result = run(case or self.case(), runtime, output, judge_timeout=3, poll_interval=0)
        return output, result

    def test_shared_core_without_local_processes(self):
        runtime = RemoteLikeRuntime()
        output, result = self.execute(runtime)
        self.assertEqual(result['score'], 1)
        self.assertNotIn('bots_stopped', result)
        self.assertTrue(result['cleanup']['reused_members_running'])
        self.assertIsNone(result['total_tokens'])
        self.assertFalse(result['usage_complete'])
        self.assertEqual(runtime.calls, ['prepare', 'submit', 'poll', 'quiesce', 'collect', 'release'])
        self.assertTrue((output.directory / 'evaluation-request.json').exists())

    def test_prepare_failure_releases_and_reports(self):
        runtime = RemoteLikeRuntime()
        with patch.object(runtime, 'prepare', side_effect=RuntimeError('private secret')):
            output, result = self.execute(runtime)
        self.assertEqual(result['execution_status'], 'environment_error')
        self.assertIsNone(result['score'])
        self.assertIn('release', runtime.calls)
        self.assertNotIn('submit', runtime.calls)
        self.assertNotIn('private secret', (output.directory / 'result.json').read_text())

    def test_uncertain_submission_not_retried(self):
        runtime = RemoteLikeRuntime()
        with patch.object(runtime, 'submit', side_effect=RuntimeError('uncertain')) as submit:
            _, result = self.execute(runtime)
        submit.assert_called_once()
        self.assertIsNone(result['score'])
        self.assertIn('release', runtime.calls)

    def test_timeout_and_cancel_have_handoff_no_grade(self):
        for error, status in [(TimeoutError(), 'failed'), (KeyboardInterrupt(), 'cancelled')]:
            runtime = RemoteLikeRuntime()
            with patch.object(runtime, 'poll', side_effect=error):
                output, result = self.execute(runtime)
            self.assertEqual(result['execution_status'], status)
            self.assertIsNone(result['score'])
            self.assertTrue((output.directory / 'evaluation-request.json').exists())
            self.assertIn('release', runtime.calls)

    def test_deadline_without_terminal_event(self):
        runtime = RemoteLikeRuntime(); case = self.case(); case['meta']['timeout_seconds'] = 0
        _, result = self.execute(runtime, case)
        self.assertEqual(result['execution_status'], 'timeout')
        self.assertNotIn('poll', runtime.calls)

    def test_partial_or_unstable_evidence_not_scored(self):
        for stage in ('collect', 'quiesce'):
            runtime = RemoteLikeRuntime()
            value = {'summary': {'evidence_complete': False}} if stage == 'collect' else {'evidence_stable': False}
            with patch.object(runtime, stage, return_value=value):
                _, result = self.execute(runtime)
            self.assertEqual(result['grading_status'], 'skipped')
            self.assertIsNone(result['score'])

    def test_collection_error_still_releases(self):
        runtime = RemoteLikeRuntime()
        with patch.object(runtime, 'collect', side_effect=RuntimeError()):
            _, result = self.execute(runtime)
        self.assertIn('release', runtime.calls)
        self.assertFalse(result['evidence_complete'])

    def test_release_failure_does_not_erase_stable_score(self):
        runtime = RemoteLikeRuntime()
        with patch.object(runtime, 'release', side_effect=RuntimeError()):
            _, result = self.execute(runtime)
        self.assertFalse(result['cleanup']['resources_released'])
        self.assertEqual(result['score'], 1)

    def test_hybrid_waits_for_host(self):
        case = self.case(); case['meta']['grading_type'] = 'hybrid'
        _, result = self.execute(RemoteLikeRuntime(), case)
        self.assertEqual(result['grading_status'], 'awaiting_evaluation_agent')
        self.assertIsNone(result['score'])

    def test_cli_check_both_bundles_no_runtime(self):
        with patch('bench.load_local_config') as config, patch('bench.LocalRuntime') as runtime:
            for name in ('task_00_connectivity', 'task_01_singlebox_feedback_plan'):
                self.assertEqual(main(['run', '--mode', 'local', '--bench-dir', str(BENCH.parent / name), '--check']), 0)
            config.assert_not_called(); runtime.assert_not_called()
        with self.assertRaises(SystemExit): main(['run', '--mode', 'online', '--bench-dir', str(BENCH)])

    def test_no_concrete_runtime_imports_in_core(self):
        for path in (ROOT / 'scripts/core').glob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names] if isinstance(node, ast.Import) else []
                self.assertFalse(any(m.split('.')[0] in {'adapters', 'outputs', 'urllib', 'requests', 'httpx'} for m in modules), path)
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(ast.unparse(node), 'os.environ', path)
        for old in ('run.sh', 'run-local.sh', 'finish_evaluation.py', 'benchmark.py', 'runtime.py', 'cli.py'):
            self.assertFalse((ROOT / 'scripts' / old).exists())

    def test_local_partial_start_cleans_owned_services(self):
        with tempfile.TemporaryDirectory() as d:
            output = FileOutput(d)
            with patch('adapters.local.runtime.LocalServices') as cls, patch('adapters.local.runtime.LocalTeam') as team:
                cls.return_value.start.side_effect = RuntimeError('start failed')
                runtime = LocalRuntime({}, BENCH / 'profiles', output)
                result = run(self.case(), runtime, output)
                cls.return_value.stop.assert_called_once(); team.assert_not_called()
                self.assertEqual(result['execution_status'], 'environment_error')

    def test_local_agent_partial_start_stops_agents(self):
        with tempfile.TemporaryDirectory() as d:
            output = FileOutput(d)
            with patch('adapters.local.runtime.LocalServices'), patch('adapters.local.runtime.LocalTeam') as team_cls:
                team = team_cls.return_value
                team.digest = 'hash'; team.mapping = {'manager': 'owned'}; team.display_names = {}; team.ports = []
                team.command.side_effect = [RuntimeError('failed start'), None]
                runtime = LocalRuntime({}, BENCH / 'profiles', output)
                result = run(self.case(), runtime, output)
                self.assertEqual([c.args[0] for c in team.command.call_args_list], ['start', 'stop'])
                self.assertEqual(result['execution_status'], 'environment_error')
                self.assertTrue(result['cleanup']['bots_stopped'])

    def test_runs_have_distinct_output(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = FileOutput(d), FileOutput(d)
            self.assertNotEqual(a.run_id, b.run_id)
            self.assertNotEqual(a.artifacts, b.artifacts)

    def test_local_model_configuration_and_node_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'apps/bcs/target/debug').mkdir(parents=True)
            (root / 'apps/bcs/target/debug/bcs-cli').touch()
            plugin = root / 'apps/bcs/crates/plugins/openclaw-channel-bcn/dist/esm'
            plugin.mkdir(parents=True); (plugin / 'index.js').touch()
            model = root / 'model.json'
            content = json.dumps({'agents': {'defaults': {'model': 'test/model'}},
                                  'models': {'providers': {'test': {'apiKey': '${TEST_KEY}'}}}})
            model.write_text(content)
            with patch('adapters.local.config.__file__', str(root / 'scripts/config.py')), patch('adapters.local.config.shutil.which', return_value='/bin/tool'), patch('adapters.local.config.subprocess.check_output', return_value='v22.22.1'):
                cfg = load_local_config(model, root / 'runs')
                self.assertEqual(cfg['model_id'], 'model')
                self.assertEqual(cfg['output_dir'], (root / 'runs').resolve())
                self.assertEqual(model.read_text(), content)
            with patch('adapters.local.config.__file__', str(root / 'scripts/config.py')), patch('adapters.local.config.shutil.which', return_value='/bin/tool'), patch('adapters.local.config.subprocess.check_output', return_value='v20.19.0'):
                with self.assertRaisesRegex(ValueError, 'Node.js'): load_local_config(model)

    def test_group_cleanup_attempted_even_when_bot_stop_fails(self):
        with tempfile.TemporaryDirectory() as d, patch('adapters.local.runtime.LocalServices'):
            runtime = LocalRuntime({}, BENCH / 'profiles', FileOutput(d))
            runtime.team = MagicMock(); runtime.client = MagicMock()
            runtime.gid = 'g'; runtime.sid = 'g:s'
            runtime.team.command.side_effect = RuntimeError('stop failed')
            boundary = runtime.quiesce('timeout')
            runtime.client.call.assert_called_once()
            self.assertFalse(boundary['evidence_stable'])
            self.assertTrue(boundary['group_terminated'])
            with self.assertRaises(RuntimeError): runtime.release()
            runtime.services.stop.assert_called_once()

    def test_uncertain_group_creation_never_claims_clean_release(self):
        with tempfile.TemporaryDirectory() as d, patch('adapters.local.runtime.LocalServices'):
            runtime = LocalRuntime({}, BENCH / 'profiles', FileOutput(d))
            runtime.metadata.update(members={'manager': 'm'}, submission_status='uncertain')
            runtime.client = MagicMock(); runtime.client.create.side_effect = RuntimeError('uncertain')
            with self.assertRaises(RuntimeError): runtime.submit('goal', 'manager')
            self.assertFalse(runtime.release()['resources_released'])
            runtime.client.create.assert_called_once()

    def test_unknown_usage_does_not_block_complete_logs(self):
        runtime = RemoteLikeRuntime()
        _, result = self.execute(runtime)
        self.assertTrue(result['evidence_complete'])
        self.assertFalse(result['usage_complete'])
        self.assertIsNone(result['total_tokens'])
        self.assertEqual(result['score'], 1)

    def test_poll_timeout_at_execution_deadline(self):
        runtime = RemoteLikeRuntime()
        with patch.object(runtime, 'poll', side_effect=TimeoutError()), patch('core.runner.time.monotonic', side_effect=[0, 0, 0, 601, 601]):
            _, result = self.execute(runtime)
        self.assertEqual(result['execution_status'], 'timeout')
        self.assertIsNone(result['score'])
