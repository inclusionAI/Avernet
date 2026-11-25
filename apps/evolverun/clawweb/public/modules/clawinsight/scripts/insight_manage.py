#!/usr/bin/env python3
"""Signed management client; stdlib only, no automatic mutation retries or redirects."""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PREFIX = "/api/insight/v1"
MANAGEMENT = "/internal/governance/improvement-management"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["capabilities", "list", "get", "execute", "batch"])
    parser.add_argument("--id", type=int)
    parser.add_argument("--file", help="JSON command file; preserve requestId and version on recovery")
    parser.add_argument("--owner")
    parser.add_argument("--bot")
    parser.add_argument("--status")
    parser.add_argument("--cursor")
    args = parser.parse_args()
    base = os.environ.get("INSIGHT_BASE_URL", "").rstrip("/")
    identity = os.environ.get("INSIGHT_AGENT_ID", "")
    secret = os.getenv("INSIGHT_AGENT_SECRET", "")
    url = urlsplit(base)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")):
        parser.error("INSIGHT_BASE_URL must be HTTPS (HTTP is allowed only on loopback)")
    if not url.netloc or url.username or url.password or url.path or url.query or url.fragment:
        parser.error("INSIGHT_BASE_URL must be an origin without credentials, path, query or fragment")
    if not identity or not secret:
        parser.error("INSIGHT_AGENT_ID and INSIGHT_AGENT_SECRET must be configured")
    method, path, body = "GET", MANAGEMENT, None
    if args.operation in ("get", "execute"):
        if not args.id or args.id < 1:
            parser.error("positive --id required")
        path += "/" + str(args.id)
    if args.operation == "capabilities":
        path += "/capabilities"
    if args.operation == "batch":
        path += "/batch/execute"
    if args.operation in ("execute", "batch"):
        if not args.file:
            parser.error("--file required; requestId must be caller-stable")
        with open(args.file, encoding="utf-8") as stream:
            value = json.load(stream)
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        method = "POST"
    query = urlencode({k: v for k, v in {"ownerUserId": args.owner, "botId": args.bot, "status": args.status, "cursor": args.cursor}.items() if v}) if args.operation == "list" else ""
    stamp, nonce = str(int(time.time() * 1000)), str(uuid.uuid4())
    digest = hashlib.sha256(body or b"").hexdigest()
    # Express signs req.path within the /api/insight/v1 router, excluding query.
    canonical = "\n".join((method, path, stamp, nonce, digest)).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    request = Request(base + PREFIX + path + ("?" + query if query else ""), data=body, method=method, headers={
        "Content-Type": "application/json", "X-Agent-Id": identity, "X-Agent-Timestamp": stamp,
        "X-Agent-Nonce": nonce, "X-Agent-Body-SHA256": digest, "X-Agent-Signature": signature,
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            data = response.read(4_000_001)
            if len(data) > 4_000_000:
                raise RuntimeError("response exceeds limit")
            result = json.loads(data)
            print(json.dumps(result, ensure_ascii=False))
            if isinstance(result, dict) and any(not item.get("ok") for item in result.get("results", [])):
                return 2
        return 0
    except HTTPError as error:
        # Do not emit arbitrary response bodies that may contain sensitive gateway data.
        details = {}
        try:
            payload = json.loads(error.read(8192))
            if isinstance(payload, dict):
                details = {key: str(payload[key])[:1000] for key in ("code", "message") if key in payload}
        except (ValueError, OSError):
            pass
        print(json.dumps({"httpStatus": error.code, **details, "automaticRetry": False,
                          "next": "Read the item before any retry; preserve the original command/requestId."}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as error:
        print(json.dumps({"errorType": type(error).__name__, "outcome": "unknown" if method == "POST" else "failed",
                          "automaticRetry": False, "next": "Read back before retrying the original requestId."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
