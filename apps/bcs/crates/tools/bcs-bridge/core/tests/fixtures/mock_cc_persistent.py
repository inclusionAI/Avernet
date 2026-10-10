#!/usr/bin/env python3
"""Local stream-json peer for durable Bridge session integration tests."""

import json
import select
import sys
import time
import uuid
from pathlib import Path


def emit(value):
    print(json.dumps(value), flush=True)


args = sys.argv[1:]
assert args[args.index("--input-format") + 1] == "stream-json"
assert args[args.index("--output-format") + 1] == "stream-json"
resume = args[args.index("--resume") + 1] if "--resume" in args else None
session_id = str(uuid.UUID(resume)) if resume else str(uuid.uuid4())
message = json.loads(sys.stdin.readline())
assert message["type"] == "user"
assert message["message"]["role"] == "user"
prompt = "\n".join(part["text"] for part in message["message"]["content"])
# Only synthetic test arguments and prompt content are recorded; no environment.
with Path("calls.jsonl").open("a") as calls:
    calls.write(json.dumps({"argv": args, "session_id": session_id,
                           "resume": resume, "prompt": prompt}) + "\n")

emit({"type": "system", "subtype": "init", "session_id": session_id})
emit({"type": "stream_event", "event": {
    "type": "content_block_delta", "delta": {
        "type": "text_delta", "text": "__engine_initialized__"}}})

if "WAIT_AFTER_INIT" in prompt:
    deadline = time.monotonic() + 10
    while not Path("release-engine").exists():
        if time.monotonic() >= deadline:
            sys.exit("test did not release the engine")
        # A killed Bridge closes stdin. Exit instead of leaving an orphan peer.
        readable, _, _ = select.select([sys.stdin], [], [], 0.02)
        if readable and not sys.stdin.readline():
            sys.exit(0)

failed = "FAIL_AFTER_INIT" in prompt
emit({"type": "result", "subtype": "success", "is_error": failed,
      "result": "synthetic engine failure" if failed else prompt,
      "session_id": session_id})
