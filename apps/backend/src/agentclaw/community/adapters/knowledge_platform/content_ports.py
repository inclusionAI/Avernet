"""The subset of the pooled async HTTP library required by content delivery."""

from collections.abc import Callable, Mapping
from typing import Any, AsyncContextManager, Protocol

import httpx
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_content import ContentAccess

# An ordinary constructor policy, not a lifecycle/plugin capability.
ContentAccessPolicy = Callable[[str, Mapping[str, str]], ContentAccess]


def deny_content_access(actor_id: str, session: Mapping[str, str]) -> ContentAccess:
    raise KnowledgeError("KNOWLEDGE_CONTENT_UNAVAILABLE", 503)


class AsyncContentTransport(Protocol):
    def stream(
        self, method: str, url: str, **kwargs: Any
    ) -> AsyncContextManager[httpx.Response]: ...
    def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request: ...
    async def send(
        self, request: httpx.Request, *, stream: bool = False
    ) -> httpx.Response: ...
