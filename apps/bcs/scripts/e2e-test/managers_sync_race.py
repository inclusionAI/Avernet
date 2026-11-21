#!/usr/bin/env python3
"""Concurrent managers-sync race helper.

Opens N HTTP connections to the BCS managers endpoint, waits for all
TCP connections to be established, then sends all request bodies at
the same instant. This maximises the chance of an optimistic-lock
conflict on the server side, exercising the retry+backoff path.

Usage:
  python3 managers_sync_race.py \
      --base-url http://127.0.0.1:21000 \
      --bot-id bot_abc123 \
      --principal <app-principal> \
      --bodies '[["human_a","human_b"],["human_a","human_c"]]' \
      --concurrency 4

Output: JSON with {"ok": N, "total": M, "codes": [...]}
"""

import argparse
import json
import socket
import sys
import time
import urllib.request


def build_request(base_url, bot_id, principal, body):
    url = f"{base_url}/api/v1/collaboration/bots/{bot_id}/managers"
    data = json.dumps({"manager_user_ids": body}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "X-Avernet-Principal": principal,
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    return req


def send_all(base_url, bot_id, principal, bodies, timeout=120):
    """Open N connections, wait for all sockets to connect, send bodies together."""
    n = len(bodies)
    sockets = []
    requests = []

    # Build raw HTTP requests
    for body in bodies:
        data = json.dumps({"manager_user_ids": body}).encode("utf-8")
        path = f"/api/v1/collaboration/bots/{bot_id}/managers"
        raw = (
            f"PUT {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            f"X-Avernet-Principal: {principal}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + data
        requests.append(raw)

    # Parse host and port
    url = base_url
    if url.startswith("http://"):
        url = url[7:]
    elif url.startswith("https://"):
        url = url[8:]
    host, _, port = url.partition(":")
    port = int(port) if port else 80

    # Open all connections
    for _ in range(n):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
        except OSError as e:
            return {"ok": 0, "total": n, "error": str(e), "codes": []}
        sockets.append(sock)

    # Send all requests
    for sock, raw in zip(sockets, requests):
        try:
            sock.sendall(raw)
        except OSError:
            pass

    # Read all responses
    codes = []
    ok = 0
    deadline = time.time() + timeout
    for sock in sockets:
        sock.settimeout(max(0.1, deadline - time.time()))
        try:
            response = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                except socket.timeout:
                    break
            # Parse HTTP status code
            header_end = response.find(b"\r\n\r\n")
            if header_end >= 0:
                status_line = response[:header_end].split(b"\r\n")[0]
                parts = status_line.split(b" ")
                if len(parts) >= 2:
                    try:
                        code = int(parts[1])
                        codes.append(code)
                        if code == 200:
                            ok += 1
                        continue
                    except ValueError:
                        pass
            codes.append(0)
        except OSError:
            codes.append(0)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    return {"ok": ok, "total": n, "codes": codes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--bodies", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    bodies = json.loads(args.bodies)
    result = send_all(
        args.base_url, args.bot_id, args.principal,
        bodies[: args.concurrency], args.timeout,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
