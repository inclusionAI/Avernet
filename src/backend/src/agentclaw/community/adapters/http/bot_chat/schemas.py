from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SessionListQueryParams(BaseModel):
    """Query parameters for listing bot chat sessions."""

    owner_id: Optional[str] = Field(default=None, description="Owner ID, mapped to Langfuse userId. Defaults to current user's staff_id")
    bot_id: Optional[str] = Field(default=None, description="Filter by identity.bot_id in trace metadata")
    trace_id: Optional[str] = Field(default=None, description="Filter by specific trace ID")
    session_id: Optional[str] = Field(default=None, description="Filter by session_id in trace metadata")
    query: Optional[str] = Field(default=None, description="Case-insensitive substring search on session name and user input")
    from_date: Optional[datetime] = Field(default=None, description="Start of time range (ISO 8601). Default: 24h ago")
    to_date: Optional[datetime] = Field(default=None, description="End of time range (ISO 8601). Default: now")
    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    limit: int = Field(default=20, ge=1, le=100, description="Items per page (max 100)")
