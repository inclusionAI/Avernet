import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from core.profile_loader import load_bench, validate_profile, profile_digest
from adapters.local.bcn import BcnAdapter
from core.grading import code_grade
from adapters.local.agents import LocalTeam
from adapters.local.services import LocalServices

BENCH = ROOT.parents[2] / 'bench-case/bcn-bench-case/task_00_connectivity'


class V1Tests(unittest.TestCase):
    def test_bundle(self):
        case, profile, manifest = load_bench(BENCH)
        self.assertEqual(len(manifest['bots']), 2)
        self.assertEqual(case['meta']['grading_type'], 'automated')
        self.assertEqual(manifest['entry_member'], 'manager')
        self.assertEqual(len(profile_digest(profile)), 64)

    def test_complete_profile_changes_digest(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(BENCH / 'profiles', Path(d) / 'profiles')
            p = Path(d) / 'profiles'
            before = profile_digest(p)
            (p / 'worker/custom.md').write_text('full resource')
            self.assertNotEqual(before, profile_digest(p))

    def test_duplicate_and_unsupported_runtime(self):
        for mutation in ('duplicate', 'runtime'):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'profiles'; shutil.copytree(BENCH / 'profiles', p)
                data = json.loads((p / 'bots.json').read_text())
                if mutation == 'duplicate':
                    data['bots'][1]['profile'] = 'manager'
                else:
                    data['bots'][1]['runtime']['unimplemented'] = True
                (p / 'bots.json').write_text(json.dumps(data))
                with self.assertRaises(ValueError): validate_profile(p)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'profiles'; shutil.copytree(BENCH / 'profiles', p)
            (p / 'worker/leak').symlink_to('/tmp')
            with self.assertRaises(ValueError): validate_profile(p)

    def test_manager_identity_and_single_goal(self):
        adapter = BcnAdapter('unused', 'http://localhost', {})
        with patch.object(adapter, 'call', return_value='ID: g\nSession: g:s\n') as call:
            adapter.create({'manager': 'm', 'worker': 'w'}, 'goal', 'run', 'manager')
            self.assertEqual(call.call_count, 1)
            self.assertIn('m', call.call_args.args)

    def evidence(self):
        return {'run': {'members': {'manager': 'm', 'worker': 'w'}, 'member_names': {'manager': 'M', 'worker': 'W'}, 'entry_member': 'manager'},
                'result': {'group_messages_complete': True},
                'session': {'status': 'completed', 'output': 'BCN_CONNECTIVITY_OK'},
                'messages': [
                    {'sender': 'M', 'role': 'tool_result', 'timestamp': 1, 'metadata': {'tool_name': 'bcs_assign_task', 'is_error': False,
                     'arguments': {'target_bot': 'W'}, 'result': {'ok': True, 'task_id': 't'}}},
                    {'sender': 'W', 'role': 'assistant', 'timestamp': 2, 'run_id': 't', 'content': 'BCN_ACK'},
                    {'sender': 'M', 'role': 'tool_result', 'timestamp': 3, 'metadata': {'tool_name': 'bcs_task_complete', 'is_error': False}}]}

    def test_connectivity_and_spoof(self):
        case, _, _ = load_bench(BENCH)
        with tempfile.TemporaryDirectory() as d:
            data = self.evidence()
            def grade():
                (Path(d) / 'bcn-evidence.json').write_text(json.dumps(data))
                return code_grade(case, d, 5, [])
            self.assertEqual(grade(), {'connectivity': 1})
            data['messages'][1]['sender'] = 'M'
            self.assertEqual(grade(), {'connectivity': 0})
            data['result']['group_messages_complete'] = False
            with self.assertRaises(ValueError): grade()

    def test_no_completion_is_not_pass(self):
        case, _, _ = load_bench(BENCH)
        with tempfile.TemporaryDirectory() as d:
            data = self.evidence(); data['session']['status'] = 'running'
            (Path(d) / 'bcn-evidence.json').write_text(json.dumps(data))
            self.assertEqual(code_grade(case, d, 5, []), {'connectivity': 0})

    def test_healthy_services_reused_and_not_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            services = LocalServices({}, d)
            specs = [('bcs', 21000, '/health')]
            with patch.object(services, 'specs', return_value=specs), patch('adapters.local.services.listening', return_value=True), patch('adapters.local.services.healthy', return_value=True):
                services.start(lambda _: None); services.stop()
                self.assertFalse(services.records[0]['owned'])

    def test_unhealthy_port_not_killed(self):
        with tempfile.TemporaryDirectory() as d:
            services = LocalServices({}, d)
            with patch.object(services, 'specs', return_value=[('bcs', 21000, '/health')]), patch('adapters.local.services.listening', return_value=True), patch('adapters.local.services.healthy', return_value=False):
                with self.assertRaises(RuntimeError): services.start(lambda _: None)

    def test_missing_services_print_explicit_commands_without_starting(self):
        with tempfile.TemporaryDirectory() as d:
            services = LocalServices({}, d)
            messages = []
            with patch('adapters.local.services.listening', return_value=False), patch('subprocess.run') as run:
                with self.assertRaises(RuntimeError):
                    services.start(messages.append)
                services.stop()
                run.assert_not_called()
            self.assertIn('./singlebox/singlebox.sh setup backend bcsfuse bcs', messages)
            self.assertIn('./singlebox/singlebox.sh start backend bcsfuse bcs', messages)
            self.assertTrue(all(not r['owned'] for r in services.records))

    def test_agent_finalization_preserves_timeout(self):
        from bench import main
        def finish(directory, report, assessment=None):
            args = ["finalize", "--run-dir", str(directory), "--report", str(report)]
            if assessment: args += ["--assessment", str(assessment)]
            main(args)
            return json.loads((Path(directory) / "result.json").read_text())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            case, _, _ = load_bench(BENCH)
            (p / 'evaluation-request.json').write_text(json.dumps({'case': case}))
            result = {'execution_status': 'timeout', 'grading_status': 'skipped', 'score': None}
            (p / 'result.json').write_text(json.dumps(result))
            (p / 'draft.md').write_text('# Timeout\nNo formal completion; evidence retained.')
            out = finish(p, p / 'draft.md')
            self.assertEqual(out['execution_status'], 'timeout')
            self.assertIsNone(out['score'])
            self.assertEqual(out['report_status'], 'completed')

    def test_agent_cannot_override_code_score(self):
        from bench import main
        def finish(directory, report, assessment=None):
            args = ["finalize", "--run-dir", str(directory), "--report", str(report)]
            if assessment: args += ["--assessment", str(assessment)]
            main(args)
            return json.loads((Path(directory) / "result.json").read_text())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            case, _, _ = load_bench(BENCH)
            (p / 'evaluation-request.json').write_text(json.dumps({'case': case}))
            result = {'execution_status': 'completed', 'grading_status': 'completed', 'score': 0}
            (p / 'result.json').write_text(json.dumps(result))
            (p / 'draft.md').write_text('# Report')
            (p / 'assessment.json').write_text(json.dumps({'scores': {'connectivity': 1}}))
            with self.assertRaises(ValueError): finish(p, p / 'draft.md', p / 'assessment.json')
            self.assertEqual(json.loads((p / 'result.json').read_text()), result)

    def test_full_profile_copy_excludes_personal_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); repo = p / 'repo'
            skill = repo / 'apps/bcs/crates/tools/bcs-cli/bcs-coordination'
            skill.mkdir(parents=True); (skill / 'SKILL.md').write_text('existing skill')
            config = {'repository': repo, 'port_start': 42001, 'entry_member': 'manager',
                      'plugin_path': repo / 'plugin', 'bcs_url': 'http://127.0.0.1:21000',
                      'model_data': {'models': {'providers': {}}, 'agents': {'defaults': {'model': 'x/y', 'workspace': '/personal'}},
                                     'channels': {'private': {'token': 'not-to-copy'}}}}
            with patch('adapters.local.agents.listening', return_value=False):
                team = LocalTeam(config, BENCH / 'profiles', p / 'run')
                profile, _ = team.prepare(team.manifest['bots'][0], 42001)
            data = json.loads((profile / 'openclaw.json').read_text())
            workspace = Path(data['agents']['defaults']['workspace'])
            self.assertEqual((workspace / 'TOOLS.md').read_bytes(), (BENCH / 'profiles/manager/TOOLS.md').read_bytes())
            self.assertNotIn('private', data['channels'])
            self.assertNotEqual(str(workspace), '/personal')

    def test_connect_then_onboard_before_grouping(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as d:
            config = {'repository': Path(d), 'port_start': 42001, 'bcs_url': 'http://127.0.0.1:21000',
                      'startup_timeout_seconds': 1, 'openclaw': 'openclaw'}
            with patch('adapters.local.agents.listening', return_value=False):
                team = LocalTeam(config, BENCH / 'profiles', Path(d) / 'run')
            client = MagicMock()
            with patch.object(team, 'prepare', return_value=(Path(d), {})), patch.object(team.processes, 'start'), patch('adapters.local.agents.wait_ready'), patch('adapters.local.agents.BcnAdapter', return_value=client):
                team.command('start')
            self.assertEqual([c.args[0] for c in client.call.call_args_list], ['onboard', 'visibility', 'onboard', 'visibility'])
