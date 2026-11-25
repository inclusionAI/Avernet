"""Scripted ECB HTTP responses, not an ECB ACL/grant/state-machine emulator.

Each test specifies the exact wire request and upstream answer. Unknown paths,
query/body changes and unintended outbound calls fail; no network fallback exists.
Real provider behavior is exercised separately by contract_tests/ecb.
"""

import json
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


@dataclass
class Reply:
    body: object
    status: int
    payload: dict


class EcbContract:
    def __init__(self):
        self.replies = {}
        self.requests = []
        self.errors = []
        self.app = FastAPI()
        self.app.add_api_route(
            "/{path:path}", self.receive, methods=["GET", "PUT", "POST", "DELETE"]
        )

    @staticmethod
    def key(method, path, params):
        return method, path, tuple(sorted((str(k), str(v)) for k, v in params.items()))

    def reply(self, method, path, payload, *, params=None, body=None, status=200):
        self.replies[self.key(method, path, params or {})] = Reply(
            body, status, payload
        )

    def send(self, request):
        self.requests.append(request)
        key = self.key(request.method, request.url.path, request.url.params)
        reply = self.replies.get(key)
        body = json.loads(request.content) if request.content else None
        if reply is None or body != reply.body:
            message = f"unexpected ECB request: {key!r} body={body!r}"
            self.errors.append(message)
            raise AssertionError(message)
        return httpx.Response(reply.status, json=reply.payload)

    async def receive(self, request: Request):
        raw = httpx.Request(
            request.method, str(request.url), content=await request.body()
        )
        response = self.send(raw)
        return JSONResponse(response.json(), status_code=response.status_code)


def detail(kb="kb", space="1", **changes):
    return {
        "space_id": space,
        "kb_id": kb,
        "name": "Knowledge",
        "description": "",
        "kgbase_status": "ready",
        "can_manage": True,
        "document_count": 0,
        "authorized_bot_count": 0,
        "created_at": None,
        "updated_at": None,
        "updated_at_basis": "unavailable",
        **changes,
    }


def grant(kb="kb", bot="bot", owner="owner", **changes):
    return {
        "id": "grant-1",
        "kb_id": kb,
        "platform": "teamclaw",
        "platform_env": "dev",
        "bot_id": bot,
        "user_id": owner,
        "bot_owner_id": owner,
        "bot_uuid": None,
        "granted": True,
        "revision": 1,
        "updated_by": "manager",
        **changes,
    }
