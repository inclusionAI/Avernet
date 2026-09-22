"""DI bindings for the friend-auth-sync internal endpoint.

BotRepository / PassportPlugin / AuthRelationshipPlugin are already bound by
the modules that own them (repository module, identity module,
bot_public_module). This module only binds the service Protocol → impl.
"""
from injector import Binder, Module, singleton

from agentclaw.community.api.bot_friend_auth_service import (
    FriendAuthSyncServiceProtocol,
)
from agentclaw.community.core.bot_public.services.friend_auth_sync_service import (
    FriendAuthSyncService,
)


class BotFriendAuthModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            FriendAuthSyncServiceProtocol,
            to=FriendAuthSyncService,
            scope=singleton,
        )
