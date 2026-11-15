"""Conformance with the AgentBench Case/Judge contract; no shared-service changes."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from core.case_loader import load_case
from core.profile_loader import load_bench
from core.runner import run
from core.evaluation import finalize, evaluation_request
from core.grading import code_grade
from core.transcript import build_transcript
from adapters.local.evidence import archived_tool_calls
from outputs.file import FileOutput
from test_v3 import RemoteLikeRuntime

BENCH = ROOT.parents[2] / 'bench-case/bcn-bench-case/task_00_connectivity'


class V4Tests(unittest.TestCase):
    def read_case(self, kind, body='', **meta):
        source = {'id': 'original_template', 'grading_type': kind, 'timeout_seconds': 120,
                  'workspace_files': [], **meta}
        text = '---\n' + yaml.safe_dump(source) + '---\n\n## Prompt\nDo the task.\n\n' + body
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'task.md'; p.write_text(text)
            return load_case(p)

    def test_original_template_three_types_and_default_weights(self):
        code = '## Automated Checks\n```python\ndef grade(transcript, workspace_path): return {"ok": 1}\n```\n'
        rubric = '## LLM Judge Rubric\nQuality (40%), completeness (60%).\n'
        self.assertEqual(self.read_case('automated', code)['code'].splitlines()[0], 'def grade(transcript, workspace_path): return {"ok": 1}')
        self.assertEqual(self.read_case('llm_judge', rubric)['code'], '')
        self.assertEqual(self.read_case('hybrid', code + rubric)['meta']['grading_weights'], {'automated': .5, 'llm_judge': .5})
        case = self.read_case('hybrid', code + rubric, grading_weights={'automated': 2, 'llm_judge': 8})
        self.assertEqual(case['meta']['grading_weights'], {'automated': .2, 'llm_judge': .8})
        self.assertNotIn('bcn', case['meta'])
        self.assertNotIn('llm_judge_dimensions', case['meta'])

    def test_criteria_fallback_and_invalid_inputs(self):
        case = self.read_case('llm_judge', '## Grading Criteria\n- [ ] Meets the goal.\n')
        self.assertEqual(case['code'], '')
        for kind, body, extra in [
            ('automated', '', {}), ('llm_judge', '', {}),
            ('llm_judge', '## Grading Criteria\nquality', {'workspace_files': [{'path': 'input.txt', 'content': 'x'}]}),
            ('llm_judge', '## Grading Criteria\nquality', {'bcn': {'entry_member': 'ceo'}}),
            ('hybrid', '', {'grading_weights': {'automated': 0, 'llm_judge': 0}}),
        ]:
            with self.subTest(kind=kind, extra=extra), self.assertRaises(ValueError):
                self.read_case(kind, body, **extra)

    def test_entry_member_only_from_profile(self):
        case, _, manifest = load_bench(BENCH)
        self.assertEqual(case['entry_member'], manifest['entry_member'])
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(BENCH, Path(d) / 'bundle')
            p = Path(d) / 'bundle/profiles/bots.json'
            data = json.loads(p.read_text()); data['entry_member'] = 'absent'
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'entry_member'):
                load_bench(p.parent.parent)

    def test_list_transcript_and_real_tool_arguments(self):
        source = [
            {'sender': 'w', 'timestamp': 2000, 'role': 'assistant', 'content': 'reply'},
            {'sender': 'm', 'timestamp': '1970-01-01T00:00:01Z', 'type': 'message',
             'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'name': 'dispatch', 'arguments': {'target': 'w'}}]}},
            {'sender': 'm', 'timestamp': 3000, 'role': 'tool_result', 'metadata': {'tool_name': 'finish'}, 'content': 'ok'},
        ]
        before = copy.deepcopy(source)
        transcript = build_transcript(source, 'g:s')
        self.assertEqual(source, before)
        self.assertEqual([m['sender'] for m in transcript], ['m', 'w', 'm'])
        self.assertTrue(all(m['type'] == 'message' and m['session_id'] == 'g:s' for m in transcript))
        case = {'code': '''def grade(transcript: list, workspace_path: str) -> dict:
    assert isinstance(transcript, list)
    calls = [b for e in transcript for b in e['message']['content'] if b.get('type') == 'toolCall']
    return {'real_call': float(calls[0]['params']['target'] == 'w')}
'''}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(code_grade(case, d, 3, transcript), {'real_call': 1})
        # Tool-result metadata alone must never fabricate an assistant tool call.
        self.assertEqual(transcript[-1]['message']['role'], 'toolResult')

    def test_archived_calls_exact_files_and_no_duplicate_text(self):
        event = {'id': 'a', 'type': 'message', 'message': {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'already in group'}, {'type': 'toolCall', 'name': 'assign', 'arguments': {'target': 'w'}}]}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'exact.jsonl'; p.write_text((json.dumps(event) + '\n') * 2)
            events = archived_tool_calls([p], 'm', 'g:s')
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]['message']['content']), 1)
        self.assertEqual(events[0]['sender'], 'm')

    def test_llm_only_handoff_and_nonuniform_total(self):
        case, _, _ = load_bench(BENCH)
        case['meta']['grading_type'] = 'llm_judge'; case['code'] = ''
        with tempfile.TemporaryDirectory() as d, patch('core.runner.code_grade') as grade:
            output = FileOutput(d)
            original = run(case, RemoteLikeRuntime(), output, poll_interval=0)
            grade.assert_not_called()
            self.assertEqual(original['grading_status'], 'awaiting_evaluation_agent')
            self.assertEqual(json.loads((output.artifacts / 'transcript.json').read_text()), [])
            final = finalize(evaluation_request(case), original, '# Evaluation', {
                'scores': {'quality': 1, 'completeness': 0}, 'total': .8, 'notes': '80/20 rubric'})
            self.assertEqual(final['score'], .8)  # Not the .5 arithmetic mean.
            self.assertNotIn('automated', final['breakdown'])
            self.assertIsNone(original['score'])

    def test_model_missing_invalid_total_preserves_waiting_result(self):
        request = {'case': {'meta': {'grading_type': 'hybrid', 'grading_weights': {'automated': .1, 'llm_judge': .9}}}}
        original = {'execution_status': 'completed', 'grading_status': 'awaiting_evaluation_agent', 'score': None,
                    'evidence_stable': True, 'evidence_complete': True, 'breakdown': {'automated': {'a': 1}}}
        for total in (None, True, float('nan'), float('inf'), -1, 1.1):
            with self.subTest(total=total), self.assertRaises(ValueError):
                finalize(request, original, '# Report', {'scores': {'quality': .5}, 'total': total})
        self.assertEqual(original['grading_status'], 'awaiting_evaluation_agent')
        result = finalize(request, original, '# Report', {'scores': {'a': 1, 'b': 0}, 'total': .8})
        self.assertAlmostEqual(result['score'], .82)
        self.assertEqual(result['breakdown']['automated'], {'a': 1})

    def test_connectivity_rejects_unrelated_worker_run(self):
        from test_v1 import V1Tests
        case, _, _ = load_bench(BENCH)
        evidence = V1Tests().evidence()
        with tempfile.TemporaryDirectory() as d:
            evidence['messages'][1]['run_id'] = 'another-task'
            (Path(d) / 'bcn-evidence.json').write_text(json.dumps(evidence))
            self.assertEqual(code_grade(case, d, 3, []), {'connectivity': 0})


if __name__ == '__main__':
    unittest.main()
