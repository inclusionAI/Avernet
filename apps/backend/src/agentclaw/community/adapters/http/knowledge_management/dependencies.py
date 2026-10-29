"""Request-scoped authentication and resource context for content delivery."""

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.knowledge_management.router import (
    execute as execute_sync_knowledge_command,
)
from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.api.knowledge_management_service import (
    KnowledgeManagementServiceProtocol,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.di import Injected
from agentclaw.community.kernel.knowledge import KnowledgeError, require_knowledge_id
from fastapi import Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool


def segment(value):
    try:
        return require_knowledge_id(value, "identifier")
    except ValueError:
        raise HTTPException(400, detail={"code": "INVALID_KNOWLEDGE_INPUT"}) from None


class ContentContext:
    def __init__(self, space_id, kb_id, request, user, service, config, remote):
        self.space_id, self.kb_id = space_id, kb_id
        self.user, self.service, self.config, self.remote = (
            user,
            service,
            config,
            remote,
        )
        try:
            self.access = remote.prepare_access(
                actor_id=user.staffId, session=request.cookies
            )
        except KnowledgeError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from None

    async def authorize(self):
        # Local space-membership gate only: every proxied provider operation
        # enforces its own native KB ACL with the forwarded session, so a
        # catalog-detail preflight here would only duplicate that round trip.
        def authorize_space():
            self.service.authorize_space(
                space_id=self.space_id,
                actor_id=self.user.staffId,
            )

        await run_in_threadpool(
            execute_sync_knowledge_command, self.config, authorize_space
        )

    async def call(self, operation, **kwargs):
        try:
            reply = await getattr(self.remote, operation)(
                kb_id=self.kb_id, access=self.access, **kwargs
            )
            return Response(reply.body, status_code=reply.status, headers=reply.headers)
        except KnowledgeError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from None

    async def source(self, source_id):
        # metadata is KB-scoped and verifies the Source belongs to this exact KB;
        # do not proxy a global Source ID on the strength of another KB's ACL.
        response = await self.call("source", source_id=segment(source_id))
        if response.status_code != 200:
            return response
        return None


async def context(
    space_id: int,
    kb_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: KnowledgeManagementServiceProtocol = Injected(
        KnowledgeManagementServiceProtocol
    ),
    config: KnowledgeManagementConfig = Injected(KnowledgeManagementConfig),
    remote: KnowledgeContentServiceProtocol = Injected(KnowledgeContentServiceProtocol),
):
    ctx = ContentContext(space_id, kb_id, request, user, service, config, remote)
    segment(kb_id)
    await ctx.authorize()
    return ctx
