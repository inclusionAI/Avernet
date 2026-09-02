"""User-granted account-level authorizations for third-party applications.

A user authorizes one named application to act as them on the public API's
**user-level** operations — the ones that name no bot. The record this module
owns is what the admission seam checks for every ``USER_GATED`` operation an
application reaches with no human on the wire.

It is the account-level sibling of :mod:`~agentclaw.community.core.bot_app_grant`,
and deliberately independent of it: a bot grant says which *bot* an application
may reach as a user, this says whether it may act as the user *at all* where
no bot is addressed. Neither implies the other.
"""

from agentclaw.community.core.user_app_grant.models import (
    UserAppGrantLogModel,
    UserAppGrantModel,
    UserAppGrantRecord,
    UserGrantAction,
)

__all__ = [
    "UserAppGrantLogModel",
    "UserAppGrantModel",
    "UserAppGrantRecord",
    "UserGrantAction",
]
