"""Owner-granted bot authorizations for third-party applications.

A bot's owner authorizes one named application to reach one named bot. The
record this module owns is what a later machine-only call path will be checked
against; nothing here admits such a caller today.

See :mod:`~agentclaw.community.core.bot_app_grant.models` for why the record is
two tables rather than one.
"""

from agentclaw.community.core.bot_app_grant.models import (
    BotAppGrantLogModel,
    BotAppGrantModel,
    BotAppGrantRecord,
    GrantAction,
)

__all__ = [
    "BotAppGrantLogModel",
    "BotAppGrantModel",
    "BotAppGrantRecord",
    "GrantAction",
]
