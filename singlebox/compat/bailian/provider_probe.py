"""Loopback forwarding probe for synthetic CI cases; exports no HTTP payloads."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evidence import marker_evidence


class ProviderProbe:
    def __init__(self, base_url, marker):
        self.requests = []
        self.marker = marker
        probe = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass  # HTTP paths/headers must never enter logs.

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                row = {'tool_results': [], 'assistant': {}}
                probe.requests.append(row)
                try:
                    payload = json.loads(body)
                    for message in payload.get('messages', []):
                        if message.get('role') == 'tool':
                            content = message.get('content', '')
                            row['tool_results'].append(marker_evidence(
                                content if isinstance(content, str) else json.dumps(content), marker))
                    headers = {key: self.headers[key] for key in ('Authorization', 'Content-Type')
                               if key in self.headers}
                    request = Request(base_url.rstrip('/') + self.path, data=body, headers=headers)
                    try:
                        response = urlopen(request, timeout=180)
                    except HTTPError as error:
                        response = error
                    with response:
                        row['http_status'] = response.status
                        self.send_response(response.status)
                        self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                        self.send_header('Connection', 'close')
                        self.send_header('Transfer-Encoding', 'chunked')
                        self.end_headers()
                        def forward(data):
                            if data:
                                self.wfile.write(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
                                self.wfile.flush()
                        assistant = ''
                        tool_calls = 0
                        for line in response:
                            forward(line)
                            if line.strip() == b'data: [DONE]':
                                forward(response.readline())
                                break
                            if not line.startswith(b'data:'):
                                continue
                            try:
                                event = json.loads(line[5:])
                            except (ValueError, UnicodeError):
                                continue
                            for choice in event.get('choices', []):
                                delta = choice.get('delta', {})
                                assistant += delta.get('content') or ''
                                tool_calls += len(delta.get('tool_calls') or [])
                        self.wfile.write(b'0\r\n\r\n')
                        self.wfile.flush()
                        row['assistant'] = marker_evidence(assistant, marker)
                        row['tool_call_deltas'] = tool_calls
                        self.close_connection = True
                except Exception as error:
                    row['error_type'] = type(error).__name__
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
