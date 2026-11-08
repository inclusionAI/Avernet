#!/usr/bin/env python3
"""Provider owner boundary through live HTTP and Workbench WebSocket APIs."""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request
import uuid

from group_session_ws_probe import _read_upgrade_response, _masked_close_frame


class Story:
    def __init__(self, base: str, owner: str):
        self.base, self.owner = base, owner
        self.foreign = "scopeforeign" + uuid.uuid4().hex
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.groups, self.bots = [], []
        self.checks = 0

    def request(self, method, path, body=None, *, owner=None, token=None, status=200):
        headers = {"Content-Type": "application/json", "X-Mock-User-Id": owner or self.owner}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            result = self.http.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            result = error
        with result:
            code, raw = result.code, result.read()
        # Do not include response bodies: registration responses contain credentials.
        assert code == status, f"{method} {path}: expected {status}, received {code}"
        self.checks += 1
        return json.loads(raw) if raw else {}

    def bot(self, provider, owner, ref):
        result = self.request("POST", f"/providers/{provider['provider_id']}/bots", {
            "name": "Owner scope " + ref, "owners": [owner], "provider_bot_ref": ref,
        }, token=provider["provider_admin_token"])
        self.bots.append((provider, result))
        return result["bot_uuid"]

    def group(self, driver, members, visibility="private", status=200):
        result = self.request("POST", "/groups", {
            "driver_bot": driver, "visibility": visibility,
            "participants": [{"bot_uuid": member} for member in members],
        }, status=status)
        if status == 200:
            self.groups.append((result["id"], driver))
            return result["id"]

    def ws_connect(self, group, session, owner, allowed):
        url = urllib.parse.urlsplit(self.base)
        with socket.create_connection((url.hostname, url.port or 80), timeout=10) as conn:
            conn.settimeout(10)
            key = base64.b64encode(os.urandom(16)).decode()
            conn.sendall((f"GET /ws HTTP/1.1\r\nHost: {url.netloc}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                f"X-Mock-User-Id: {owner}\r\n\r\n").encode())
            assert b"101 Switching Protocols" in _read_upgrade_response(conn)
            frame = json.dumps({"type": "req", "id": "scope-connect", "method": "connect",
                "params": {"group_id": group, "session_id": session}}).encode()
            mask = os.urandom(4)
            length = len(frame)
            header = bytes([0x81, 0x80 | length]) if length < 126 else bytes([0x81, 0xfe]) + struct.pack("!H", length)
            conn.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(frame)))
            def read_exact(length):
                data = b""
                while len(data) < length:
                    chunk = conn.recv(length - len(data))
                    assert chunk, "Workbench closed before responding"
                    data += chunk
                return data
            for _ in range(12):
                opcode, size = read_exact(2)
                length = size & 127
                if length == 126:
                    length = struct.unpack("!H", read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", read_exact(8))[0]
                payload = read_exact(length)
                if opcode & 15 != 1:
                    continue
                result = json.loads(payload)
                if result.get("id") == "scope-connect":
                    assert result.get("ok") is allowed, f"Workbench owner boundary incorrect for {owner}"
                    self.checks += 1
                    conn.sendall(_masked_close_frame())
                    return
            raise AssertionError("Workbench did not return connect response")

    def run(self):
        self.request("POST", "/me/ensure-human", {})
        self.request("POST", "/me/ensure-human", {}, owner=self.foreign)
        provider = self.request("POST", "/providers", {
            "name": "Scope story", "auth": {"mode": "static_bearer"},
            "webhook_url": "https://provider.example.com/scope", "collaboration_scope": "standard",
        })
        pid, admin = provider["provider_id"], provider["provider_admin_token"]
        local = self.bot(provider, self.owner, "local-" + uuid.uuid4().hex)
        standard = self.request("POST", "/providers", {
            "name": "Cloud scope story", "auth": {"mode": "static_bearer"},
            "webhook_url": "https://provider.example.com/cloud",
        })
        cloud = self.bot(standard, self.owner, "cloud-" + uuid.uuid4().hex)
        outsider = self.bot(provider, self.foreign, "foreign-" + uuid.uuid4().hex)
        for bot in (local, cloud, outsider):
            self.request("PUT", f"/bots/{bot}/visibility", {"visibility": "public"},
                owner=self.foreign if bot == outsider else self.owner)
        group = self.group(cloud, [local, "human_" + self.owner], "public")
        self.request("PATCH", f"/providers/{pid}", {"collaboration_scope": "same_owner", "name": "Rejected rename"}, token=admin, status=409)
        metadata = self.request("GET", f"/providers/{pid}", token=admin)
        assert metadata["collaboration_scope"] == "standard" and metadata["name"] == "Scope story"
        self.request("PATCH", f"/groups/{group}", {"visibility": "private"})
        self.request("PATCH", f"/providers/{pid}", {"collaboration_scope": "same_owner"}, token=admin)
        assert self.request("GET", f"/providers/{pid}", token=admin)["collaboration_scope"] == "same_owner"
        self.request("PATCH", f"/providers/{pid}", {"collaboration_scope": "standard"}, token=admin, status=403)
        # New registration and attachment exercise the current restricted policy.
        third = self.bot(provider, self.owner, "third-" + uuid.uuid4().hex)
        self.request("PUT", f"/bots/{third}/visibility", {"visibility": "public"})
        self.request("POST", f"/groups/{group}/members", {"bot_uuid": third})
        self.request("POST", f"/groups/{group}/members", {"bot_uuid": outsider}, status=403)
        self.group(local, [outsider], status=403)
        self.group(outsider, [local], status=403)
        self.group(local, [cloud], "public", status=403)
        self.request("PATCH", f"/groups/{group}", {"visibility": "public"}, status=403)
        # The exact private-Bot collaboration requirement must not need a friendship.
        self.request("PUT", f"/bots/{cloud}/visibility", {"visibility": "private"})
        self.group(local, [cloud])
        self.request("PUT", f"/bots/{cloud}/visibility", {"visibility": "public"})
        invite = self.request("POST", f"/groups/{group}/invite-link", {})["invite_token"]
        self.request("POST", f"/groups/join/{invite}", {}, owner=self.foreign, status=403)
        self.request("POST", f"/groups/join/{invite}", {})
        session = self.request("POST", f"/groups/{group}/sessions", {}, status=201)["session_id"]
        self.request("POST", f"/groups/{group}/sessions", {}, owner=self.foreign, status=403)
        for path in (f"/sessions/{session}/messages",):
            self.request("GET", path)
            self.request("GET", path, owner=self.foreign, status=400)
        self.request("POST", f"/sessions/{session}/chat", {"message": "must not be delivered"}, owner=self.foreign, status=403)
        self.ws_connect(group, session, self.owner, True)
        self.ws_connect(group, session, self.foreign, False)
        # Removing all restricted Bots restores legacy behavior, without a permanent group policy.
        for bot in (third, local):
            self.request("DELETE", f"/groups/{group}/members/{bot}")
        self.request("PATCH", f"/groups/{group}", {"visibility": "public"})
        self.request("POST", f"/groups/join/{invite}", {}, owner=self.foreign)
        self.request("GET", f"/sessions/{session}/messages", owner=self.foreign)
        self.ws_connect(group, session, self.foreign, True)
        self.request("POST", f"/groups/{group}/members", {"bot_uuid": local}, status=403)
        print(f"Provider scope story: {self.checks} live API assertions passed")

    def cleanup(self):
        for group, driver in reversed(self.groups):
            self.request("DELETE", f"/groups/{group}?bot_id={driver}")
        for provider, bot in reversed(self.bots):
            self.request("DELETE", f"/providers/{provider['provider_id']}/bots/{bot['provider_bot_ref']}", token=provider["provider_admin_token"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    story = Story(args.base_url, args.owner)
    try:
        story.run()
    finally:
        story.cleanup()
