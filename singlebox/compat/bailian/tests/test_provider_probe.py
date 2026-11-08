import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from pathlib import Path
from threading import Thread
import unittest
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provider_probe import ProviderProbe


class ProbeTest(unittest.TestCase):
    def test_forwards_bytes_and_records_only_synthetic_evidence(self):
        marker = 'BCS_' + 'a' * 32
        received = []
        stream = b'data: {"choices":[{"delta":{"content":"BCS_aaaa"}}]}\n\ndata: [DONE]\n\n'
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(stream)))
                self.end_headers()
                self.wfile.write(stream)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        probe = ProviderProbe('http://127.0.0.1:' + str(server.server_port), marker)
        try:
            body = json.dumps({'messages': [{'role': 'tool', 'content': marker}],
                               'private': 'never-export-this'}).encode()
            with urlopen(Request(probe.start() + '/chat/completions', data=body,
                                 headers={'Authorization': 'Bearer private-test-value'})) as response:
                self.assertEqual(response.read(), stream)
            self.assertEqual(received, [body])
            self.assertTrue(probe.requests[0]['tool_results'][0]['contains_expected'])
            self.assertFalse(probe.requests[0]['assistant']['contains_expected'])
            self.assertNotIn('private', json.dumps(probe.requests))
        finally:
            probe.close()
            server.shutdown()
            server.server_close()
            thread.join()
