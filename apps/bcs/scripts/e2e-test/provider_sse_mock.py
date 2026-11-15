"""Scripted HTTP wire failures for the live Provider SSE story."""
import json
import time
from http.server import BaseHTTPRequestHandler


def write_reply(handler: BaseHTTPRequestHandler, run_id: str, reply: dict[str, str]) -> None:
    mode, text = reply["mode"], reply["text"]
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream; charset=utf-8")
    handler.send_header("connection", "close")
    if mode == "read_error":
        # Promise a byte the server never sends: reqwest must report a body
        # read error, as distinct from a valid stream ending before terminal.
        handler.send_header("content-length", "1")
    handler.end_headers()
    handler.close_connection = True

    def frame(seq: int, state: str, *, lag_ms: int = 0) -> None:
        event = {"runId": run_id, "seq": seq, "state": state,
                 "ts": int(time.time() * 1000) - lag_ms, "content": text}
        handler.wfile.write(("event: chat\ndata: " + json.dumps(event) + "\n\n").encode())
        handler.wfile.flush()

    if mode in ("eof", "read_error"):
        return
    if mode == "lag_recovery":
        # Timestamps trigger real lag edges without a long wall-clock wait.
        # Two pairs also verify only the first pair is business-key tagged.
        for seq in range(1, 5):
            frame(seq, "delta", lag_ms=6000 if seq % 2 else 0)
        frame(5, "final")
        return
    frame(1, "delta")
    frame(2, "final")
