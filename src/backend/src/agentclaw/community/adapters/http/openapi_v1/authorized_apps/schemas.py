"""Response shapes for user-granted bot authorizations.

Two views of one record, so two models rather than one shared shape. Someone
asking "which apps can reach my bot?" already knows the bot and wants the app;
the app asking "which bots may I reach?" already knows itself and wants the
bots. A single model carrying both halves would make every response restate
what its caller supplied.

The bot's view carries ``user_id`` — **who delegated** the access — and that is
not symmetry with the record, it is the point. A bot's owner may now find a
grant on their bot that a collaborator created; without the delegator named,
they would see that an application has access and have no way to learn who let
it in or whose access it borrows. The application's view omits it, because there
it would only repeat the ``user_id`` the caller sent.

Neither exposes ``owner_id`` or ``avernet_tenant``. Both are on the record — the
machine-caller path resolves the addressed bot from ``owner_id`` — but on this
surface the owner is already implied by the bot in the path, and a tenant is not
something this API has ever named in a body.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuthorizedApp(BaseModel):
    """An application authorized to reach a bot — the bot's view."""

    app_id: int = Field(..., description="The authorized application's id.")
    user_id: str = Field(
        ...,
        description=(
            "The user who granted this authorization, and whose access the "
            "application acts with. Not necessarily the bot's owner: a "
            "collaborator may delegate the access they hold, and the "
            "application then reaches the bot as them, bounded by their "
            "permissions at the time of each request."
        ),
    )
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
