"""One knowledge-domain-to-HTTP mapping at the delivery boundary."""

import json

from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from fastapi import HTTPException
from fastapi.routing import APIRoute

STATUS = {
    "INVALID_KNOWLEDGE_INPUT": 400,
    "KNOWLEDGE_CHAT_ACCESS_DENIED": 403,
    "KNOWLEDGE_CONVERSATION_UNSUPPORTED": 422,
    "SPACE_MEMBER_REQUIRED": 403,
    "KNOWLEDGE_BOT_NOT_IN_SPACE": 404,
    "KNOWLEDGE_MANAGE_REQUIRED": 403,
    "KNOWLEDGE_REMOTE_INVALID_INPUT": 400,
    "KNOWLEDGE_REMOTE_ACCESS_DENIED": 403,
    "KNOWLEDGE_REMOTE_NOT_FOUND": 404,
    "KNOWLEDGE_REMOTE_CONFLICT": 409,
    "KNOWLEDGE_REMOTE_RETIRED": 410,
    "KNOWLEDGE_SYNC_RUN_INVALID": 502,
    "KNOWLEDGE_SYNC_RUN_NOT_FOUND": 404,
}


class KnowledgeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def mapped(request):
            try:
                return await handler(request)
            except KnowledgeManagementError as exc:
                if exc.code == "KNOWLEDGE_UPLOAD_TYPE_UNSUPPORTED":
                    raise HTTPException(
                        400, detail="Only TXT and Markdown files are supported"
                    ) from None
                raise HTTPException(
                    STATUS.get(exc.code, 503), detail={"code": exc.code}
                ) from None
            except KnowledgeError as exc:
                # Compatibility with the existing Bot-selection contract.
                raise HTTPException(exc.status, detail={"code": exc.code}) from None
            except json.JSONDecodeError:
                raise HTTPException(
                    503, detail={"code": "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"}
                ) from None

        return mapped
