#!/usr/bin/env python3
"""Mock SSE provider for BCS downlink stream protocol e2e.

Responds to POST {webhook} with text/event-stream emitting protocol-format
frames: thinking deltas -> tool start/result -> chat delta -> chat final.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

FRAMES = [
    ("agent", {"runId": "engine-x", "seq": 1, "stream": "thinking", "delta": "Let me", "text": "Let me"}),
    ("agent", {"runId": "engine-x", "seq": 2, "stream": "tool", "phase": "start", "name": "read", "toolCallId": "t1", "args": {"path": "a"}}),
    ("agent", {"runId": "engine-x", "seq": 3, "stream": "tool", "phase": "result", "name": "read", "toolCallId": "t1", "isError": False, "result": {"content": [{"type": "text", "text": "hi"}]}}),
    ("chat", {"runId": "engine-x", "seq": 4, "state": "delta", "deltaText": "Done", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done"}]}}),
    ("chat", {"runId": "engine-x", "seq": 5, "state": "final", "stopReason": "end_turn", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done"}]}}),
]

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-BCN-Protocol-Version", "2.0")
        self.send_header("X-BCN-Run-Id", "engine-x")
        self.end_headers()
        for i, (event, data) in enumerate(FRAMES, start=1):
            frame = f"event: {event}\nid: {i}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(frame.encode())
            self.wfile.flush()

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 29999
    print(f"mock SSE provider on :{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
