import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import core, executor, runtime


class BusinessCoreTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.input = self.root / 'input.json'
        self.input.write_text('{}')
        skill = self.root / 'SKILL.md'
        skill.write_text('Original business instructions')
        self.context = {'inputFile': str(self.input), 'resultFile': str(self.root / 'result.json'),
                        'implementationSkill': str(skill)}
        self.outputs = []
        self.calls = []
        self.form = {'format': 'form', 'title': 'Confirm', 'contents': [],
                     'questions': [{'id': 'choice', 'type': 'short_text', 'title': 'Scope', 'required': True}]}
        self.args = argparse.Namespace(task_id='task', step_id='step', clawweb_url='http://localhost:1')
        patcher = patch.object(executor, '_run_openclaw_agent', self.agent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self, context, model):
        self.calls.append(json.loads(Path(context['inputFile']).read_text()))
        Path(context['resultFile']).write_text(json.dumps(self.outputs.pop(0)))
        return {'status': 'succeeded'}

    def test_resumes_only_pending_business_call_and_preserves_complete_answers(self):
        self.outputs = [{'analysis': 'first call'}, {'hitl': True, 'question': self.form}, {'contracts': []}]
        session = core.BusinessCore(self.context)
        self.assertEqual(session('discovery', {'source': 'actual'}, {}), {'analysis': 'first call'})
        with self.assertRaises(core.CoreWaiting) as caught:
            session('case_contract', {'case': 'a'}, {})
        waiting = caught.exception
        with patch.object(runtime, '_http', return_value=b'{"ok":true}') as http:
            session.report_waiting(self.args, waiting)
            body = json.loads(http.call_args.kwargs['body'])
            self.assertEqual(body['progress'], waiting.progress)
        self.input.write_text(json.dumps({'human_input': {'history': [{
            'question': {**self.form, 'business_resume': waiting.progress['business_resume']},
            'answer': {'answers': {'choice': {'value': 'original answer', 'comment': 'keep this'}}},
        }]}}))
        resumed = core.BusinessCore(self.context)
        self.assertEqual(resumed('discovery', {'source': 'actual'}, {}), {'analysis': 'first call'})
        self.assertEqual(resumed('case_contract', {'case': 'a'}, {}), {'contracts': []})
        self.assertEqual(len(self.calls), 3)
        delivered = self.calls[-1]['hitl']
        self.assertNotIn('business_resume', delivered['question'])
        self.assertEqual(delivered['answer']['answers']['choice']['comment'], 'keep this')
        self.assertEqual(delivered['history'][-1]['answer'], delivered['answer'])

    def test_does_not_repeat_waiting_call_without_new_answer(self):
        self.outputs = [{'hitl': True, 'question': self.form}]
        session = core.BusinessCore(self.context)
        with self.assertRaises(core.CoreWaiting) as caught:
            session('analysis', {}, {})
        with patch.object(runtime, '_http', return_value=b'{"ok":true}') as http:
            session.report_waiting(self.args, caught.exception)
            with self.assertRaises(core.CoreWaiting) as resumed:
                core.BusinessCore(self.context)('analysis', {}, {})
            session.report_waiting(self.args, resumed.exception)
            self.assertEqual(http.call_count, 1)
        self.assertEqual(len(self.calls), 1)

    def test_uncertain_report_blocks_model_reexecution(self):
        self.outputs = [{'hitl': True, 'question': self.form}]
        session = core.BusinessCore(self.context)
        with self.assertRaises(core.CoreWaiting) as caught:
            session('analysis', {}, {})
        with patch.object(runtime, '_http', side_effect=TimeoutError('lost reply')):
            with self.assertRaises(TimeoutError):
                session.report_waiting(self.args, caught.exception)
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'uncertain outcome'):
            core.BusinessCore(self.context)('analysis', {}, {})
        self.assertEqual(len(self.calls), 1)

    def test_new_loop_step_executes_again_with_its_own_feedback(self):
        self.outputs = [{'analysis': 'round one'}, {'analysis': 'round two'}]
        core.BusinessCore(self.context)('analysis', {}, {})
        next_dir = self.root / 'next-step'
        next_dir.mkdir()
        next_input = next_dir / 'input.json'
        loop = {'round': 2, 'previous_result': {'summary': 'prior'}, 'user_feedback': {'text': 'adjust'}}
        next_input.write_text(json.dumps({'loop': loop}))
        context = {**self.context, 'inputFile': str(next_input), 'resultFile': str(next_dir / 'result.json')}
        self.assertEqual(core.BusinessCore(context)('analysis', {}, {}), {'analysis': 'round two'})
        self.assertEqual(self.calls[-1]['loop'], loop)


if __name__ == '__main__':
    unittest.main()
