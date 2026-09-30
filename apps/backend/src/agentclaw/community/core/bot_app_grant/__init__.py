"""Owner-granted bot authorizations for third-party applications.

Two records. A bot's owner or collaborator authorizes one named application to
reach one named bot; the public API's machine-caller path is checked against
that. And a user authorizes one named application to act as them where **no
bot is addressed** — the consent a creation is admitted on, since a bot grant
cannot name a bot that does not exist yet.

See :mod:`~agentclaw.community.core.bot_app_grant.models` for why each record is
two tables rather than one.
"""

from agentclaw.community.core.bot_app_grant.models import (
    BotAppGrantLogModel,
    BotAppGrantModel,
    BotAppGrantRecord,
    GrantAction,
    UserAppGrantLogModel,
    UserAppGrantModel,
    UserAppGrantRecord,
)

__all__ = [
    "BotAppGrantLogModel",
    "BotAppGrantModel",
    "BotAppGrantRecord",
    "GrantAction",
    "UserAppGrantLogModel",
    "UserAppGrantModel",
    "UserAppGrantRecord",
]
