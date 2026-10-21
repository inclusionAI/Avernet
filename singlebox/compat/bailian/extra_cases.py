"""Live wire-level tool parsing and cancellation acceptance cases."""
import json
import secrets
import time
from urllib.parse import quote

from wire_probe import abort_acknowledged, tool_events, validate_tools


def tool_call_case(harness):
    marker = 'TOOL_' + secrets.token_hex(16)
    # Spaces, Unicode and quotes exercise real argument serialization/parsing.
    filename = 'probe 空格 "quoted".txt'
    path = harness.root / 'workspace' / filename
    path.write_text(marker + '\n')
    offset = len(harness.wire.frames)
    harness.chat('tool_call_format', 'Use a file-reading tool to read the file named '
                 + json.dumps(filename, ensure_ascii=False) + '. Return its complete content.', marker)
    row = harness.report['cases'][-1]
    row['status'] = 'FAIL'
    row['wire_evidence'] = validate_tools(harness.wire.frames[offset:], filename, marker)
    row['status'] = 'PASS'
    path.unlink()


def cancellation_case(harness):
    harness.stage = 'cancel_inflight'
    marker = 'LATE_' + secrets.token_hex(16)
    script = harness.root / 'workspace/ci_wait.py'
    script.write_text('import time\ntime.sleep(30)\nprint(' + repr(marker) + ')\n')
    offset = len(harness.wire.frames)
    accepted = harness.api('/bots/' + quote(harness.bot_id, safe='') + '/chat-async',
                           {'message': 'Run python3 ci_wait.py using your shell tool and return its output. '
                            'Wait for the command to finish; do not run it in the background.',
                            'timeout_ms': 180000, 'response_mode': 'after-last-tool-call'},
                           harness.caller['bot_token'])
    run_id = accepted['run_id']
    row = {'name': 'cancel_inflight', 'run_id': run_id, 'session_id': accepted['session_id'], 'status': 'FAIL'}
    harness.report['cases'].append(row)
    def active_tool():
        if harness.terminal(run_id):
            raise AssertionError('run_completed_before_cancellation')
        return next((event for event in tool_events(harness.wire.frames[offset:])
                     if event.get('phase') == 'start' and 'ci_wait.py' in json.dumps(event.get('args', {}))), None)
    event = harness.wait(active_tool, 90)
    harness.api('/chat/runs/' + quote(run_id, safe='') + '/cancel', {}, harness.caller['bot_token'])
    result = harness.wait(lambda: harness.terminal(run_id), 45)
    if result['state'] != 'cancelled':
        raise AssertionError('run_not_cancelled')
    harness.wait(lambda: abort_acknowledged(harness.wire.frames[offset:], event['runId']), 15)
    # Outlast the test command: late tool output must not arrive after cancel.
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        time.sleep(1)
        result = harness.terminal(run_id)
        if not result or result['state'] != 'cancelled' or marker in json.dumps(result):
            raise AssertionError('cancelled_run_changed_or_late_content')
        for tool in tool_events(harness.wire.frames[offset:]):
            if tool.get('toolCallId') == event.get('toolCallId') and marker in json.dumps(tool.get('result')):
                raise AssertionError('engine_continued_cancelled_tool')
    row.update(status='PASS', terminal_state='cancelled', downstream_abort=True, late_output_absent=True)
    harness.chat('post_cancel_recovery', 'Reply with exactly RECOVERED.', 'RECOVERED', accepted['session_id'])
