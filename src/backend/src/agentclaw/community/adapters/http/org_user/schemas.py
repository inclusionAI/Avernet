"""Response contract for the ordinary current-user endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class OrgUserResponse(BaseModel):
    """User profile asserted by the verified principal token."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    display_name: str | None = None
    full_name: str | None = None
