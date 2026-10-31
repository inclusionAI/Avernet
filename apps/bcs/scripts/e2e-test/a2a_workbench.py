"""Public Workbench WebSocket requests for the live A2A cancellation story."""
import base64
import json
import os
import socket
import struct
from urllib.parse import urlsplit

from group_session_ws_probe import _masked_close_frame, _read_upgrade_response


def abort_task(base, owner, group, session, bot):
    url = urlsplit(base)
    with socket.create_connection((url.hostname, url.port or 80), timeout=10) as conn:
        conn.settimeout(10)
        key = base64.b64encode(os.urandom(16)).decode()
        conn.sendall((f"GET /ws HTTP/1.1\r\nHost: {url.netloc}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"X-Mock-User-Id: {owner}\r\n\r\n").encode())
        assert b"101 Switching Protocols" in _read_upgrade_response(conn)

        def exact(length):
            result = b""
            while len(result) < length:
                chunk = conn.recv(length - len(result))
                assert chunk, "Workbench closed before responding"
                result += chunk
            return result

        def request(method, params):
            frame = json.dumps({"type": "req", "id": method, "method": method,
                                "params": params}).encode()
            mask = os.urandom(4)
            header = (bytes([0x81, 0x80 | len(frame)]) if len(frame) < 126
                      else b"\x81\xfe" + struct.pack("!H", len(frame)))
            conn.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(frame)))
            for _ in range(100):
                opcode, size = exact(2)
                length = size & 127
                if length == 126:
                    length = struct.unpack("!H", exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", exact(8))[0]
                payload = exact(length)
                if opcode & 15 != 1:
                    continue
                result = json.loads(payload)
                if result.get("id") == method:
                    assert result.get("ok"), f"Workbench rejected {method}"
                    return result
            raise AssertionError("Workbench did not return " + method)

        request("connect", {"group_id": group, "session_id": session})
        result = request("chat.abort", {"group_id": group, "session_id": session, "bot_id": bot})
        conn.sendall(_masked_close_frame())
        return result
