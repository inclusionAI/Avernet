import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clawevolve_runtime import executor, runtime


class ContentFilesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_markdown_is_read_verbatim_and_questions_are_not_split_or_rewritten(self):
        markdown = '# 原始材料\n\n包含 "引号"、\\、```json 和中文。\n'
        (self.root / '材料.md').write_text(markdown)
        questions = [{'id': f'q{i}', 'type': 'short_text', 'title': f'问题 {i}', 'required': False} for i in range(12)]
        value = {'hitl': True, 'question': {'format': 'form', 'title': '原始业务问题',
            'contents': [{'id': 'original', 'title': '材料', 'format': 'markdown', 'contentFile': '材料.md'}],
            'questions': questions}}
        collected = executor.collect_content_files(value, self.root)
        self.assertEqual(collected['question']['contents'][0]['content'], markdown)
        self.assertEqual(collected['question']['questions'], questions)
        self.assertIn('contentFile', value['question']['contents'][0])
        self.assertEqual(runtime._normalize_result(collected), collected)

    def test_summary_file_preserves_the_business_result_and_feedback_request(self):
        (self.root / 'summary.md').write_text('# 本轮原始总结\n')
        result = {'hitl': False, 'result': {'summaryFile': 'summary.md', 'changed': False},
                  'loop': {'action': 'request_feedback', 'prompt': '继续？', 'accepts': {'text': True, 'files': []}}}
        collected = executor.collect_content_files(result, self.root)
        self.assertEqual(collected['result'], {'summary': '# 本轮原始总结\n', 'changed': False})
        self.assertEqual(collected['loop'], result['loop'])

    def test_text_confirmation_file_reaches_normal_contract_verbatim(self):
        markdown = '## 执行前确认\n\n避免将"两个事项都没有编号/时间戳"当成同一事项。\n\n请确认是否执行。\n'
        (self.root / 'confirmation.md').write_text(markdown, encoding='utf-8')
        (self.root / 'SKILL.md').write_text('# Business')
        (self.root / 'input.json').write_text('{}')
        def agent(context, model):
            Path(context['resultFile']).write_text(
                '{"hitl":true,"question":{"format":"text","tag":"execution_confirmation",'
                '"contentFile":"confirmation.md"}}')
            return {'status': 'succeeded'}
        value = executor.execute_stage_skill({
            'implementationSkill': str(self.root / 'SKILL.md'),
            'inputFile': str(self.root / 'input.json'),
            'resultFile': str(self.root / 'result.json'),
        }, agent_runner=agent)
        self.assertEqual(runtime._normalize_result(value), {'hitl': True, 'question': {
            'format': 'text', 'tag': 'execution_confirmation', 'content': markdown}})

    def test_text_confirmation_uses_the_same_file_guards(self):
        (self.root / 'large.md').write_bytes(b'x' * (1024 * 1024 + 1))
        (self.root / 'linked.md').symlink_to(self.root.parent / 'outside.md')
        for fields in (
            {'contentFile': '../outside.md'}, {'contentFile': 'linked.md'},
            {'contentFile': 'missing.md'}, {'contentFile': 'large.md'},
            {'contentFile': 'confirmation.md', 'content': 'conflicting inline'},
        ):
            with self.subTest(fields=fields), self.assertRaises(executor.CoreDispatchError):
                executor.collect_content_files({'hitl': True, 'question': {
                    'format': 'text', 'tag': 'confirm', **fields}}, self.root)

    def test_does_not_read_outside_the_result_directory_or_follow_an_escaping_link(self):
        directory = self.root / 'result'
        directory.mkdir()
        outside = self.root / 'outside.md'
        outside.write_text('outside')
        (directory / 'linked.md').symlink_to(outside)
        for reference in ('../outside.md', 'linked.md'):
            with self.subTest(reference=reference), self.assertRaises(executor.CoreDispatchError):
                executor.collect_content_files({'summaryFile': reference}, directory)

    def test_conflicting_inline_and_file_content_fails_instead_of_guessing(self):
        with self.assertRaisesRegex(executor.CoreDispatchError, 'cannot both'):
            executor.collect_content_files({'summary': 'one', 'summaryFile': 'two.md'}, self.root)

    def test_malformed_json_is_not_repaired_into_a_different_business_question(self):
        path = self.root / 'result.json'
        path.write_text('{"title":"A1. "同一交付物"如何判定"}')
        with self.assertRaises(runtime.RuntimeFailure):
            runtime._read_json(path, strict=True)


if __name__ == '__main__':
    unittest.main()
