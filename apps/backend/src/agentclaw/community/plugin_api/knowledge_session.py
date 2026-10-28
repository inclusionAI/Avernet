"""Request-scoped knowledge authentication; never persist or log session material.

The actor is already authenticated by the browser adapter. Implementations MUST
verify any session-local identity against it and return only the credentials the
configured trusted knowledge origin needs. A missing credential is not an identity.
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class KnowledgeSessionPlugin(Plugin, Protocol):
    def headers(
        self, *, actor_id: str, session: Mapping[str, str]
    ) -> Mapping[str, str]: ...
