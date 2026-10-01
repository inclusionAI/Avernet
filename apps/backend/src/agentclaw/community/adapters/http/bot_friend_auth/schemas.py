"""Request/response schemas for the internal friend-auth-sync endpoint.

BCS (Rust) calls this after it has resolved a human→bot friend relationship
(landing an EdgeGrant). Backend resolves the bot's agent_code internally and
syncs a USER_AUTH relationship to AceAgent on BCS's behalf.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FriendAuthSyncAction(str, Enum):
    grant = "grant"
    revoke = "revoke"


class FriendAuthSyncRequest(BaseModel):
    bot_id: str = Field(..., min_length=1, description="backend bot_id")
    owner_work_no: str = Field(..., min_length=1, description="bot owner work no")
    human_work_no: str = Field(
        ..., min_length=1, description="friend work no (bare, no human_ prefix)"
    )
    action: FriendAuthSyncAction
    request_id: str | None = Field(None, description="optional idempotency key")


class FriendAuthSyncResponse(BaseModel):
    synced: bool = Field(..., description="whether the sync reached AceAgent")
    reason: str = Field(
        ..., description="created|already_exists|deleted|not_found"
    )
    auth_id: int | None = Field(None, description="AceAgent auth id on grant-create")
