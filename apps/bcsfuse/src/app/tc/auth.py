"""Retain TC shared Bearer and explicitly configured gateway authentication."""

import os

from fastapi import HTTPException, Request


def _trust_gateway_enabled():
    return os.environ.get("BCSFUSE_TRUST_GATEWAY", "").lower() == "true"


def require_oss_auth(request: Request):
    if _trust_gateway_enabled():
        return
    auth = request.app.state.context.registry.get("auth")
    if auth is None or not auth.validate_request(request):
        raise HTTPException(
            401, detail={"code": "UNAUTHORIZED", "message": "Authentication required"}
        )
