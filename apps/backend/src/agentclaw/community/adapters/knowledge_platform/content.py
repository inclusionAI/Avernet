"""ECB implementation of the public resource-oriented content capability."""

import json
from collections.abc import Mapping
from urllib.parse import quote

from agentclaw.community.adapters.knowledge_platform.content_client import (
    EcbKnowledgeContentClient,
)
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    ContentAccessPolicy,
    deny_content_access,
)
from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.core.knowledge_management.content_policy import (
    validate_sync_run,
)
from agentclaw.community.kernel.knowledge import require_knowledge_id
from agentclaw.community.kernel.knowledge_content import ContentAccess

BASE = "/api/v1/knowledge"


def segment(value: str) -> str:
    return quote(require_knowledge_id(value, "identifier"), safe="")


class RemoteKnowledgeContentService(KnowledgeContentServiceProtocol):
    def __init__(
        self,
        client: EcbKnowledgeContentClient,
        access_policy: ContentAccessPolicy = deny_content_access,
    ):
        self.client, self.access_policy = client, access_policy

    def prepare_access(
        self, *, actor_id: str, session: Mapping[str, str]
    ) -> ContentAccess:
        return self.access_policy(actor_id, session)

    @staticmethod
    def path(kb_id: str, suffix: str = "") -> str:
        return BASE + f"/kb/{segment(kb_id)}" + suffix

    async def sources(
        self, *, kb_id: str, access: ContentAccess, offset: int, limit: int
    ):
        return await self.client.call(
            "GET",
            self.path(kb_id, "/sources"),
            headers=access.credentials,
            params={"offset": offset, "limit": limit},
        )

    async def source(self, *, kb_id: str, source_id: str, access: ContentAccess):
        return await self.client.call(
            "GET",
            self.path(kb_id, f"/sources/{segment(source_id)}"),
            headers=access.credentials,
        )

    async def upload(
        self,
        *,
        kb_id: str,
        access: ContentAccess,
        files: list[tuple[str, bytes, str]],
        on_name_conflict: str,
    ):
        return await self.client.call(
            "POST",
            self.path(kb_id, "/sources/upload"),
            headers=access.credentials,
            files=[("files", f) for f in files],
            data={"on_name_conflict": on_name_conflict},
        )

    async def replace(
        self,
        *,
        kb_id: str,
        source_id: str,
        access: ContentAccess,
        file: tuple[str, bytes, str],
        expected_content_hash: str,
    ):
        return await self.client.call(
            "POST",
            self.path(kb_id, f"/sources/{segment(source_id)}/replace"),
            headers=access.credentials,
            files=[("file", file)],
            data={"expected_content_hash": expected_content_hash},
        )

    async def delete(self, *, kb_id: str, source_id: str, access: ContentAccess):
        return await self.client.call(
            "DELETE",
            self.path(kb_id, f"/sources/{segment(source_id)}"),
            headers=access.credentials,
        )

    async def raw(self, *, kb_id: str, source_id: str, access: ContentAccess):
        metadata = await self.source(kb_id=kb_id, source_id=source_id, access=access)
        if metadata.status != 200:
            return metadata, None
        return await self.client.raw(
            BASE + f"/sources/{segment(source_id)}/raw", headers=access.credentials
        )

    async def yuque_documents(
        self, *, kb_id: str, access: ContentAccess, query: str, offset: int, limit: int
    ):
        return await self.client.call(
            "GET",
            BASE + "/data-source/connectors/yuque/personal-docs",
            headers=access.credentials,
            params={"kb_id": kb_id, "q": query, "offset": offset, "limit": limit},
        )

    async def import_yuque(self, *, kb_id: str, access: ContentAccess, body: dict):
        return await self.client.call(
            "POST",
            BASE + "/data-source/connectors/yuque/import-docs",
            headers=access.credentials,
            body={**body, "kb_id": kb_id},
        )

    async def sync(self, *, kb_id: str, source_id: str, access: ContentAccess):
        metadata = await self.source(kb_id=kb_id, source_id=source_id, access=access)
        if metadata.status != 200:
            return metadata
        return await self.client.call(
            "POST",
            BASE + f"/knowledge-sources/{segment(source_id)}/sync",
            headers=access.credentials,
        )

    async def sync_run(
        self, *, kb_id: str, source_id: str, run_id: str, access: ContentAccess
    ):
        metadata = await self.source(kb_id=kb_id, source_id=source_id, access=access)
        if metadata.status != 200:
            return metadata
        reply = await self.client.call(
            "GET",
            BASE + f"/data-source/sync-runs/{segment(run_id)}",
            headers=access.credentials,
        )
        if reply.status == 200:
            validate_sync_run(
                json.loads(reply.body), kb_id=kb_id, source_id=source_id, run_id=run_id
            )
        return reply

    async def get_schedule(self, *, kb_id: str, source_id: str, access: ContentAccess):
        metadata = await self.source(kb_id=kb_id, source_id=source_id, access=access)
        if metadata.status != 200:
            return metadata
        return await self.client.call(
            "GET",
            BASE + f"/knowledge-sources/{segment(source_id)}/auto-sync",
            headers=access.credentials,
        )

    async def put_schedule(
        self, *, kb_id: str, source_id: str, access: ContentAccess, body: dict
    ):
        metadata = await self.source(kb_id=kb_id, source_id=source_id, access=access)
        if metadata.status != 200:
            return metadata
        return await self.client.call(
            "PUT",
            BASE + f"/knowledge-sources/{segment(source_id)}/auto-sync",
            headers=access.credentials,
            body=body,
        )

    async def tasks(
        self, *, kb_id: str, access: ContentAccess, offset: int, limit: int
    ):
        return await self.client.call(
            "GET",
            self.path(kb_id, "/ingest-tasks"),
            headers=access.credentials,
            params={"offset": offset, "limit": limit},
        )

    async def revision(self, *, kb_id: str, revision_id: str, access: ContentAccess):
        return await self.client.call(
            "GET",
            self.path(kb_id, f"/source-revisions/{segment(revision_id)}"),
            headers=access.credentials,
        )

    async def activities(
        self, *, kb_id: str, access: ContentAccess, offset: int, limit: int
    ):
        return await self.client.call(
            "GET",
            self.path(kb_id, "/activities"),
            headers=access.credentials,
            params={"offset": offset, "limit": limit},
        )

    async def job(self, *, kb_id: str, job_id: str, access: ContentAccess):
        return await self.client.job(
            kb_id=kb_id, job_id=job_id, headers=access.credentials
        )
