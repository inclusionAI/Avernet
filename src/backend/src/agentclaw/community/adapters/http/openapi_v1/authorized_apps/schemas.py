"""Response shapes for owner-granted bot authorizations.

Two views of one record, so two models rather than one shared shape. The owner
asking "which apps can reach my bot?" already knows the bot and wants the app;
the app asking "which of this owner's bots may I reach?" already knows itself
and wants the bots. A single model carrying both halves would make every
response restate what its caller supplied.

Neither exposes ``owner_id`` or ``avernet_tenant``. Both are on the record — the
later machine-caller path resolves ownership from them — but on this surface
they are the caller's own identity handed back to them, and a tenant is not
something this API has ever named in a body.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuthorizedApp(BaseModel):
    """An application authorized to reach a bot — the owner's view."""

    app_id: int = Field(..., description="The authorized application's id.")
    app_name: str = Field(
        ...,
        description=(
            "The application's name as it stood when the authorization was "
            "granted. A snapshot, not a live lookup: it records what was "
            "consented to, so a later rename does not rewrite the record."
        ),
    )
    bot_id: str = Field(..., description="The bot this authorization covers.")
    granted_at: datetime = Field(
        ...,
        description=(
            "When this authorization began. Unchanged by re-granting an "
            "authorization that is already in force."
        ),
    )


class AuthorizedBot(BaseModel):
    """A bot the calling application may reach — the application's view."""

    bot_id: str = Field(..., description="The bot this authorization covers.")
    granted_at: datetime = Field(
        ..., description="When this authorization began."
    )


__all__ = ["AuthorizedApp", "AuthorizedBot"]
