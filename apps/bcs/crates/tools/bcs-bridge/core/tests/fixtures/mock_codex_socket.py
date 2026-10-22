#!/usr/bin/env python3
"""Owned Unix WebSocket transport for the existing persistent protocol fixture.

Uses only the Python standard library. Stdout is deliberately not an RPC
transport; tests can fill it before readiness to verify the parent's drain.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import socket
import struct
import subprocess
import sys
import time

args = sys.argv[1:]
assert "--listen" in args, args
endpoint = args[args.index("--listen") + 1]
assert endpoint.startswith("unix:///"), endpoint
path = endpoint.removeprefix("unix://")
log_path = args[0] if args[0] not in ("--codex", "app-server") else "socket-rpc.jsonl"
grandchild = None
stubborn_descendant = bool(os.environ.get("MOCK_SOCKET_STUBBORN_DESCENDANT"))
if stubborn_descendant:
    grandchild = subprocess.Popen([sys.executable, "-c",
        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(300)"],
        stdout=subprocess.PIPE)
    assert grandchild.stdout.readline() == b"ready\n"
elif os.environ.get("MOCK_SOCKET_GRANDCHILD"):
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])


def stop(_signal, _frame):
    if grandchild is not None and not stubborn_descendant:
        grandchild.terminate()
        grandchild.wait(timeout=2)
    raise SystemExit(0)


signal.signal(signal.SIGTERM, stop)
with open(log_path, "a") as log:
    log.write(json.dumps({"method": "fixture/spawn", "pid": os.getpid(), "socket": path,
                         "cwd": os.getcwd(), "actor": os.environ.get("BCN_ACTOR_BOT_ID"),
                         "grandchild": grandchild.pid if grandchild else None}) + "\n")

for fd in (1, 2):
    remaining = int(os.environ.get("MOCK_DIAGNOSTIC_BYTES", "0"))
    while remaining:
        chunk = (b"diagnostic-only\n" * 256)[:remaining]
        remaining -= os.write(fd, chunk)

if os.environ.get("MOCK_SOCKET_STARTUP") == "no-listen":
    time.sleep(300)
if os.environ.get("MOCK_SOCKET_STARTUP") == "unsafe-file":
    Path(path).write_text("not a socket")
    time.sleep(300)

time.sleep(float(os.environ.get("MOCK_SOCKET_READY_DELAY", "0")))
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(path)
os.chmod(path, 0o600)
listener.listen(1)
peer, _ = listener.accept()
if os.environ.get("MOCK_SOCKET_STARTUP") == "no-handshake":
    time.sleep(300)
headers = b""
while b"\r\n\r\n" not in headers:
    chunk = peer.recv(4096)
    if not chunk:
        raise SystemExit(0)
    headers += chunk
key = next(line.split(b":", 1)[1].strip() for line in headers.split(b"\r\n")
           if line.lower().startswith(b"sec-websocket-key:"))
accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
peer.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
             b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")


def send_frame(payload, opcode=1):
    length = len(payload)
    if length < 126:
        header = bytes([0x80 | opcode, length])
    elif length < 65536:
        header = bytes([0x80 | opcode, 126]) + struct.pack("!H", length)
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack("!Q", length)
    peer.sendall(header + payload)


def read_exact(length):
    data = b""
    while len(data) < length:
        chunk = peer.recv(length - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


class Reader:
    def __iter__(self):
        return self

    def __next__(self):
        while True:
            try:
                first, second = read_exact(2)
                opcode = first & 15
                length = second & 127
                if length == 126:
                    length = struct.unpack("!H", read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", read_exact(8))[0]
                assert second & 128, "client frames must be masked"
                mask = read_exact(4)
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(read_exact(length)))
                if opcode == 8:
                    send_frame(payload, 8)
                    raise StopIteration
                if opcode == 9:
                    send_frame(payload, 10)
                    continue
                if opcode == 10:
                    continue
                assert opcode == 1 and first & 128, "expected a complete text frame"
                request = json.loads(payload)
                if request.get("method") == "initialize" and os.environ.get("MOCK_SOCKET_INIT_ERROR"):
                    send_frame(json.dumps({"id": request["id"], "error": {"message": "fixture initialization rejected"}}).encode())
                    continue
                if request.get("method") == "bridge/transportSmokeUnknown":
                    send_frame(json.dumps({"id": request["id"], "error": {
                        "code": -32601, "message": "Method not found"}}).encode())
                    continue
                if request.get("method") == "turn/start" and request["params"]["input"][0]["text"] == "disconnect":
                    with open(log_path, "a") as log:
                        log.write(json.dumps({"method": "fixture/disconnect", "pid": os.getpid()}) + "\n")
                    peer.close()
                    # Exercise socket failure with an engine that stays alive.
                    time.sleep(300)
                return payload.decode() + "\n"
            except (EOFError, ConnectionError):
                raise StopIteration from None


class Writer:
    def __init__(self):
        self.pending = ""

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            send_frame(line.encode())
        return len(text)

    def flush(self):
        pass


# Include a control frame to exercise ping/pong independently of JSON-RPC.
send_frame(b"ready", 9)
sys.stdin = Reader()
sys.stdout = Writer()
sys.argv = [__file__, log_path]
runpy.run_path(str(Path(__file__).with_name("mock_codex_persistent.py")), run_name="__main__")
# Socket closure alone deliberately does not terminate the fixture. The owner
# must reap its process; desktop attachment must never signal an external host.
time.sleep(300)
