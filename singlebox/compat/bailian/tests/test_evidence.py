import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evidence import openclaw_evidence


class EvidenceTest(unittest.TestCase):
    def test_distinguishes_tool_result_from_shortened_assistant_and_cli(self):
        marker = 'BCS_' + 'a' * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'openclaw/agents/main/sessions/test.jsonl'
            path.parent.mkdir(parents=True)
            messages = [
                {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'private'},
                 {'type': 'toolCall', 'name': 'read', 'arguments': {'secret': 'private'}}]},
                {'role': 'toolResult', 'content': [{'type': 'text', 'text': marker}]},
                {'role': 'assistant', 'content': [{'type': 'text', 'text': marker[:10]}]},
            ]
            path.write_text('\n'.join(json.dumps({'message': m}) for m in messages))
            result = openclaw_evidence(root, marker, marker[:10])
            self.assertTrue(result['native_messages'][1]['contains_expected'])
            self.assertFalse(result['native_messages'][2]['contains_expected'])
            self.assertFalse(result['cli_final']['contains_expected'])
            self.assertNotIn('private', json.dumps(result))
