"""Drive the distributed Direct Gateway, Worker and Connector over loopback A2A."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import urllib.request


class Agent(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        method = request['method']
        reply_id = request['id']

        def result(value):
            return json.dumps({'jsonrpc': '2.0', 'id': reply_id, 'result': value}).encode()

        if method == 'CancelTask':
            remote = request['params']['id']
            task = self.server.tasks[remote]
            task['cancel'].set()
            data = result({'id': remote, 'contextId': task['context'],
                           'status': {'state': 'TASK_STATE_CANCELED'}})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
            return

        if method != 'SendStreamingMessage':
            self.send_error(400, f'Unexpected agent method: {method}')
            return
        msg = request['params']['message']
        text = msg['parts'][0]['text']
        context = msg['contextId']
        remote = msg.get('taskId', 'remote-' + text)
        task = self.server.tasks.setdefault(remote, {'context': context, 'cancel': threading.Event()})
        self.server.calls.append((text, context, remote))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()

        def event(value):
            self.wfile.write(b'data: ' + result(value) + b'\n\n')
            self.wfile.flush()

        event({'task': {'id': remote, 'contextId': context, 'status': {'state': 'TASK_STATE_WORKING'}}})
        if text in ('question', 'stop'):
            event({'statusUpdate': {'taskId': remote, 'contextId': context,
                   'status': {'state': 'TASK_STATE_INPUT_REQUIRED', 'message': {
                       'messageId': 'prompt', 'role': 'ROLE_AGENT', 'parts': [{'text': 'Continue?'}]}}}})
            task['cancel'].wait(45)
            return
        if text == 'yes':
            task['cancel'].set()
        for fragment, append in [('draft', False), (text, False), ('!', True)]:
            event({'artifactUpdate': {'taskId': remote, 'contextId': context, 'append': append,
                   'artifact': {'artifactId': 'answer', 'parts': [{'text': fragment}]}}})
        event({'statusUpdate': {'taskId': remote, 'contextId': context,
                                'status': {'state': 'TASK_STATE_COMPLETED'}}})


def run(host, checks):
    agent = ThreadingHTTPServer(('127.0.0.1', 0), Agent)
    agent.tasks, agent.calls = {}, []
    thread = threading.Thread(target=agent.serve_forever, daemon=True)
    thread.start()
    try:
        url = host.call('direct_listen', agent_url=f'http://127.0.0.1:{agent.server_port}/rpc')

        def rpc(method, params, target_url=url):
            data = json.dumps({'jsonrpc': '2.0', 'id': 'story', 'method': method, 'params': params}).encode()
            with urllib.request.urlopen(urllib.request.Request(target_url, data=data, headers={
                    'Content-Type': 'application/json', 'A2A-Version': '1.0'}), timeout=30) as response:
                return json.load(response)

        def send(text, sid=None, tid=None, target_url=url):
            message = {'messageId': text, 'role': 'ROLE_USER', 'parts': [{'text': text}]}
            if sid:
                message['contextId'] = sid
            if tid:
                message['taskId'] = tid
            return rpc('SendMessage', {'message': message, 'configuration': {'returnImmediately': True}},
                       target_url)

        def wait(tid, state):
            def inspect():
                nonlocal current
                current = rpc('GetTask', {'id': tid})['result']
                assert current['status']['state'] != 'TASK_STATE_FAILED', current
                return current['status']['state'] == state
            current = None
            host.until(inspect, f'{tid} never reached {state}')
            return current

        missing = url.rsplit('/', 1)[0] + '/missing-agent'
        assert 'error' in send('denied', target_url=missing)
        assert not agent.calls
        first = send('hello')['result']['task']
        sid = first['contextId']
        completed = wait(first['id'], 'TASK_STATE_COMPLETED')
        assert completed['artifacts'][0]['parts'] == [{'text': 'hello!'}], completed
        tid = send('question', sid)['result']['task']['id']
        waiting = wait(tid, 'TASK_STATE_INPUT_REQUIRED')
        assert waiting['status']['message']['parts'][0]['text'] == 'Continue?', waiting
        assert send('yes', sid, tid)['result']['task']['id'] == tid
        assert wait(tid, 'TASK_STATE_COMPLETED')['artifacts'][0]['parts'] == [{'text': 'yes!'}]
        assert [row[2] for row in agent.calls] == ['remote-hello', 'remote-question', 'remote-question']
        checks.append('direct_admission_stream_and_reply')

        tid = send('stop', sid)['result']['task']['id']
        wait(tid, 'TASK_STATE_INPUT_REQUIRED')
        with ThreadPoolExecutor(max_workers=1) as pool:
            canceled = pool.submit(rpc, 'CancelTask', {'id': tid})
            host.until(canceled.done, 'Gateway did not confirm local cancellation')
            assert canceled.result()['result']['status']['state'] == 'TASK_STATE_CANCELED'
        next_tid = send('again', sid)['result']['task']['id']
        assert wait(next_tid, 'TASK_STATE_COMPLETED')['artifacts'][0]['parts'] == [{'text': 'again!'}]
        assert agent.tasks['remote-stop']['cancel'].is_set()
        assert all(context == f'{sid}:0' for _, context, _ in agent.calls), agent.calls
        checks.append('direct_confirmed_cancel_preserves_context')
    finally:
        for task in list(agent.tasks.values()):
            task['cancel'].set()
        agent.shutdown()
        agent.server_close()
        thread.join()
