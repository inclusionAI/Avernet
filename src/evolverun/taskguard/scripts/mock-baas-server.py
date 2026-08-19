#!/usr/bin/env python3
"""Mock BaaS server for local development/testing.

Provides the OpenAPI endpoints that baas-call executor needs:
- POST /openapi/v1/messages  → submit message to bot
- GET  /openapi/v1/messages/{id} → poll message status
- GET  /openapi/v1/runs/{id} → poll run status

Note: This is NOT a real BaaS service. It returns mock responses
so that the baas-call executor pipeline can be tested end-to-end.
"""

import hashlib
import os
import time
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Mock BaaS Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Valid API keys (prefix:id format from baas_api_key table)
VALID_API_KEYS = {
    "TnT9oudj7YGMGxiZSfDwk8O0J7Pe9xPY",
    "test-local-key",
}

# In-memory message store: message_id -> message_data
messages: dict[str, dict[str, Any]] = {}

# Simulated bot response delay read from MOCK_BOT_DELAY env var (default 2s)


def validate_api_key(authorization: str = Header(default="")) -> str:
    """Validate Bearer API key from Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    api_key = authorization[7:].strip()
    if api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


class MessageRequest(BaseModel):
    bot_id: str | None = None
    content: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] | None = None


class MessageResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: dict[str, Any] | None = None
    trace_id: str | None = None


@app.post("/openapi/v1/messages")
async def create_message(
    request: MessageRequest,
    api_key: str = Depends(validate_api_key),
):
    """Submit a message to a bot (mock)."""
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    session_id = request.session_id or f"ses_{uuid.uuid4().hex[:12]}"

    messages[message_id] = {
        "message_id": message_id,
        "session_id": session_id,
        "bot_id": request.bot_id or "unknown",
        "content": request.content or "",
        "status": "running",
        "created_at": time.time(),
        "api_key_prefix": api_key[:8],
    }

    return MessageResponse(
        code=0,
        message="success",
        data={
            "message_id": message_id,
            "session_id": session_id,
            "run_id": f"run_{uuid.uuid4().hex[:16]}",
        },
        trace_id=uuid.uuid4().hex[:32],
    )


@app.get("/openapi/v1/messages/{message_id}")
async def get_message(
    message_id: str,
    api_key: str = Depends(validate_api_key),
):
    """Poll message status (mock). Returns completed after delay."""
    msg = messages.get(message_id)
    if not msg:
        return MessageResponse(
            code=40401,
            message="Message not found",
            data=None,
        )

    delay = float(os.environ.get("MOCK_BOT_DELAY", "2"))
    elapsed = time.time() - msg["created_at"]
    if elapsed < delay:
        status = "running"
        result = None
    else:
        status = "completed"
        result = {
            "content": f"Mock bot response for message {message_id}. "
                       f"Bot: {msg['bot_id']}, Input: {msg['content'][:100]}",
        }

    return MessageResponse(
        code=0,
        message="success",
        data={
            "message_id": message_id,
            "session_id": msg["session_id"],
            "bot_id": msg["bot_id"],
            "status": status,
            "result": result,
        },
        trace_id=uuid.uuid4().hex[:32],
    )


@app.get("/openapi/v1/runs/{run_id}")
async def get_run(
    run_id: str,
    api_key: str = Depends(validate_api_key),
):
    """Poll run status (mock)."""
    # Look up by run_id prefix
    for msg in messages.values():
        msg_run_id = f"run_{msg['message_id'].replace('msg_', '')}"
        if run_id == msg_run_id or run_id.endswith(msg["message_id"][-16:]):
            delay = float(os.environ.get("MOCK_BOT_DELAY", "2"))
            elapsed = time.time() - msg["created_at"]
            if elapsed < delay:
                status = "running"
                result = None
            else:
                status = "completed"
                result = {
                    "content": f"Mock bot response for run {run_id}. "
                               f"Bot: {msg['bot_id']}",
                }
            return MessageResponse(
                code=0,
                message="success",
                data={
                    "run_id": run_id,
                    "session_id": msg["session_id"],
                    "bot_id": msg["bot_id"],
                    "status": status,
                    "result": result,
                },
                trace_id=uuid.uuid4().hex[:32],
            )

    return MessageResponse(
        code=40401,
        message="Run not found",
        data=None,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-baas-server"}


@app.get("/")
async def root():
    return {
        "service": "mock-baas-server",
        "endpoints": [
            "POST /openapi/v1/messages",
            "GET /openapi/v1/messages/{message_id}",
            "GET /openapi/v1/runs/{run_id}",
            "GET /health",
        ],
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Mock BaaS Server")
    parser.add_argument("--port", type=int, default=8899, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind")
    parser.add_argument("--delay", type=float, default=2, help="Mock bot response delay (seconds)")
    parser.add_argument("--api-key", type=str, default=None, help="Additional API key to accept")
    args = parser.parse_args()

    if args.api_key:
        VALID_API_KEYS.add(args.api_key)

    # Update module-level delay via os.environ so route handlers pick it up
    os.environ["MOCK_BOT_DELAY"] = str(args.delay)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()