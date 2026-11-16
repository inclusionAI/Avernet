"""Convert archived messages to the AgentBench message-list contract."""
from copy import deepcopy
from datetime import datetime


def timestamp_key(event):
    value = event.get('timestamp', 0)
    if type(value) in (int, float):
        return value / 1000  # BCS timestamps are epoch milliseconds.
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    return 0


def normalize_content(content):
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}]
    if not isinstance(content, list):
        raise ValueError('Message content must be text or a block list')
    blocks = deepcopy(content)
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError('Message block must be an object')
        if block.get('type') in ('toolCall', 'tool_use'):
            block['type'] = 'toolCall'
            if 'params' not in block:
                block['params'] = block.get('arguments', block.get('input', {}))
    return blocks


def build_transcript(messages, session_id):
    transcript = []
    for raw in sorted(messages, key=timestamp_key):
        # An adapter may supply original OpenClaw message events or BCS messages.
        event = deepcopy(raw)
        if event.get('type') == 'message' and isinstance(event.get('message'), dict):
            message = event['message']
        else:
            message = {'role': event.get('role', 'system'), 'content': event.get('content', '')}
            event = {key: value for key, value in event.items() if key not in ('role', 'content')}
            event.update(type='message', message=message)
        if message.get('role') == 'tool_result':
            message['role'] = 'toolResult'
        message['content'] = normalize_content(message.get('content', ''))
        event['session_id'] = session_id
        transcript.append(event)
    return transcript
