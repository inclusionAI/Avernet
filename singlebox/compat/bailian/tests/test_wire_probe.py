import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wire_probe import abort_acknowledged, validate_tools


class WireEvidenceTest(unittest.TestCase):
    def frames(self, args=None, call_id='call-1', session='session-1'):
        def frame(phase, **fields):
            return ('engine', {'event': 'agent', 'payload': {
                'stream': 'tool', 'runId': 'run-1', 'sessionId': 'session-1',
                'phase': phase, 'toolCallId': 'call-1', **fields}})
        return [frame('start', name='Read', args=args if args is not None else {'path': 'a "引号".txt'}),
                frame('result', toolCallId=call_id, sessionId=session, result='TOKEN', isError=False)]

    def test_unicode_quotes_and_json_argument_string(self):
        for args in ({'path': 'a "引号".txt'}, json.dumps({'path': 'a "引号".txt'}),
                     {'command': r'cat "a \"引号\".txt"'}):
            self.assertTrue(validate_tools(self.frames(args), 'a "引号".txt', 'TOKEN')['arguments_object'])

    def test_rejects_malformed_json_unpaired_id_wrong_session_and_missing_events(self):
        for frames in [self.frames('{broken'), self.frames(call_id='wrong'),
                       self.frames(session='wrong'), []]:
            with self.assertRaises((AssertionError, ValueError)):
                validate_tools(frames, 'a "引号".txt', 'TOKEN')

    def test_abort_requires_matching_request_response_and_target_run(self):
        request = ('bcs', {'type': 'req', 'id': 'abort', 'method': 'chat.abort', 'params': {'run_id': 'run'}})
        response = ('engine', {'type': 'res', 'id': 'abort', 'ok': True, 'payload': {'aborted_run_ids': ['run']}})
        self.assertTrue(abort_acknowledged([request, response], 'run'))
        self.assertFalse(abort_acknowledged([response], 'run'))
        self.assertFalse(abort_acknowledged([request, response], 'unrelated'))
        response[1]['payload']['aborted_run_ids'] = []
        self.assertFalse(abort_acknowledged([request, response], 'run'))
