"""Finalization tests replace the removed direct-HTTP report path."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from core.evaluation import finalize, evaluation_request
from outputs.file import save_evaluation


class ReportTests(unittest.TestCase):
    def test_hybrid_uses_judge_total_and_preserves_original(self):
        request = {'case': {'meta': {'grading_type': 'hybrid', 'grading_weights': {'automated': .1, 'llm_judge': .9}}}}
        original = {'execution_status': 'completed', 'grading_status': 'awaiting_evaluation_agent',
                    'evidence_stable': True, 'evidence_complete': True, 'breakdown': {'automated': {'a': 1}}, 'score': None}
        result = finalize(request, original, '# Report', {'scores': {'quality': .5}, 'total': .5})
        self.assertAlmostEqual(result['score'], .55)
        self.assertIsNone(original['score'])
        self.assertEqual(original['grading_status'], 'awaiting_evaluation_agent')
        with self.assertRaises(ValueError): finalize(request, original, '# Report', {'scores': {'quality': 1}})
        original['evidence_stable'] = False
        with self.assertRaises(ValueError): finalize(request, original, '# Report', {'scores': {'quality': 1}})

    def test_failed_serialization_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'analysis-report.md').write_text('previous')
            (root / 'result.json').write_text('{"score":null}')
            with self.assertRaises(ValueError):
                save_evaluation(root, {'evaluation_agent_usage': float('nan')}, 'replacement')
            self.assertEqual((root / 'analysis-report.md').read_text(), 'previous')
            self.assertEqual((root / 'result.json').read_text(), '{"score":null}')

    def test_report_required_and_usage_separate(self):
        request = evaluation_request({'meta': {}, 'sections': {}})
        original = {'execution_status': 'timeout', 'grading_status': 'skipped', 'score': None, 'total_tokens': 12}
        with self.assertRaises(ValueError): finalize(request, original, '')
        final = finalize(request, original, '# Timeout', {'usage': {'tokens': 3}})
        self.assertEqual(final['total_tokens'], 12)
        self.assertEqual(final['evaluation_agent_usage'], {'tokens': 3})
        self.assertIsNone(final['score'])
