"""Real loopback A2A requests through the memory-only Worker companion."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time
import urllib.request


def run(h, checks):
    url = h.call('gateway_listen')

    def rpc(method, params):
        request = urllib.request.Request(
            url, data=json.dumps({'jsonrpc': '2.0', 'id': 'gateway-rpc',
                                  'method': method, 'params': params}).encode(),
            headers={'Content-Type': 'application/json', 'A2A-Version': '1.0'})
        return urllib.request.urlopen(request, timeout=10)

    def result(method, params):
        with rpc(method, params) as response:
            return json.load(response)

    # Select one of this companion's explicitly configured shards without
    # creating abandoned tasks on an unowned shard.
    def message_id(label):
        for index in range(100):
            candidate = f'gateway-{label}-{index}'
            fields = ['sid', 'singlebox', 'bot:gateway-story', 'gateway-story', candidate]
            digest = hashlib.sha256(b''.join(len(x.encode()).to_bytes(8, 'big') + x.encode()
                                             for x in fields)).digest()
            if int.from_bytes(digest[:8], 'big') % 2 == 1:
                return candidate
        raise AssertionError('unable to select test shard')

    def params(identity, size=70000):
        return {'message': {'messageId': identity, 'role': 'ROLE_USER',
                            'parts': [{'data': {'mode': 'append', 'bytes': size}}]},
                'configuration': {'returnImmediately': True}}

    def current(tid):
        return result('GetTask', {'id': tid})['result']

    identity = message_id('retry')
    request = params(identity)
    before = h.call('tick')['runner_calls']
    # Admit, then discard the HTTP response body before retrying.
    response = rpc('SendMessage', request)
    response.close()
    time.sleep(0.01)  # distinct wall-clock initialization on a naive retry
    with ThreadPoolExecutor(max_workers=2) as pool:
        copies = list(pool.map(lambda _: result('SendMessage', request), range(2)))
    tid = copies[0]['result']['task']['id']
    assert copies[1]['result']['task']['id'] == tid
    assert 'error' in result('SendMessage', params(identity, 70001))
    h.until(lambda: current(tid)['status']['state'] == 'TASK_STATE_COMPLETED',
            'Gateway-created task failed to finish')
    task = current(tid)
    text = task['artifacts'][0]['parts'][0]['text']
    assert text == 'x' * 70000
    assert result('SendMessage', request)['result']['task']['id'] == tid
    assert h.call('tick')['runner_calls'] == before + 1
    checks.append('gateway_http_retry_conflict_once')

    # Fresh concurrent first requests must also converge to the same task.
    race = params(message_id('race'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        copies = list(pool.map(lambda _: result('SendMessage', race), range(2)))
    race_tid = copies[0]['result']['task']['id']
    assert copies[1]['result']['task']['id'] == race_tid
    h.until(lambda: current(race_tid)['status']['state'] == 'TASK_STATE_COMPLETED',
            'concurrent Gateway task failed to finish')
    assert h.call('tick')['runner_calls'] == before + 2
    checks.append('gateway_http_concurrent_first_request')

    # Read real SSE concurrently with Worker ticks; fold delivered append frames
    # and compare their stable identity/full text with GetTask.
    stream_request = params(message_id('stream'))
    stream_request.pop('configuration')

    def read_stream():
        frames = []
        with rpc('SendStreamingMessage', stream_request) as response:
            for line in response:
                if line.startswith(b'data:'):
                    frames.append(json.loads(line[5:].strip()))
        return frames

    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(read_stream)
        deadline = time.monotonic() + 30
        while not reading.done() and time.monotonic() < deadline:
            h.call('tick', count=5)
        frames = reading.result(timeout=10)
    stream_tid = frames[0]['result']['task']['id']
    full, artifact_id = '', None
    for frame in frames:
        assert 'error' not in frame, frame
        update = frame['result'].get('artifactUpdate')
        if update:
            artifact = update['artifact']
            if artifact_id is not None:
                assert artifact_id == artifact['artifactId']
            artifact_id = artifact['artifactId']
            if not update.get('append', False):
                full = ''
            full += artifact['parts'][0]['text']
    task = current(stream_tid)
    assert full == 'x' * 70000
    assert task['artifacts'][0]['artifactId'] == artifact_id
    assert task['artifacts'][0]['parts'][0]['text'] == full
    assert h.call('tick')['runner_calls'] == before + 3
    checks.append('gateway_http_sse_get_task_consistency')
