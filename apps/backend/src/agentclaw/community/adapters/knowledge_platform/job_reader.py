"""KB-scoped compatibility lookup; global jobs do not carry a safe KB scope."""

import asyncio
import json
from dataclasses import replace
from urllib.parse import quote

from agentclaw.community.kernel.knowledge import KnowledgeError


def invalid():
    return KnowledgeError("KNOWLEDGE_JOB_RESPONSE_INVALID", 502)


async def read_business_job(remote, *, kb_id, job_id, headers):
    offset, seen = 0, set()
    try:
        async with asyncio.timeout(8):
            for _ in range(2):
                reply = await remote.call(
                    "GET",
                    f"/api/v1/knowledge/kb/{quote(kb_id, safe='')}/ingest-jobs",
                    headers=headers,
                    params={"offset": offset, "limit": 100},
                )
                if reply.status != 200:
                    return reply
                page = json.loads(reply.body)
                if not isinstance(page, dict):
                    raise invalid()
                jobs, total = page.get("jobs"), page.get("total")
                if (
                    not isinstance(jobs, list)
                    or len(jobs) > 100
                    or type(total) is not int
                    or total < 0
                    or type(page.get("offset")) is not int
                    or page["offset"] != offset
                    or type(page.get("limit")) is not int
                    or page["limit"] != 100
                    or offset + len(jobs) > total
                    or (not jobs and offset < total)
                ):
                    raise invalid()
                match = None
                for job in jobs:
                    if (
                        not isinstance(job, dict)
                        or not isinstance(job.get("job_id"), str)
                        or not job["job_id"]
                        or job["job_id"] in seen
                        or not isinstance(job.get("status"), str)
                        or not job["status"]
                        or not isinstance(job.get("items"), list)
                        or ("kb_id" in job and job["kb_id"] != kb_id)
                    ):
                        raise invalid()
                    seen.add(job["job_id"])
                    if job["job_id"] == job_id:
                        match = job
                if match is not None:
                    return replace(reply, body=json.dumps(match).encode())
                offset += len(jobs)
                if offset >= total:
                    raise KnowledgeError("KNOWLEDGE_JOB_NOT_FOUND", 404)
    except TimeoutError:
        raise KnowledgeError("KNOWLEDGE_JOB_LOOKUP_TIMEOUT", 503) from None
    except (ValueError, UnicodeDecodeError):
        raise invalid() from None
    raise KnowledgeError("KNOWLEDGE_JOB_LOOKUP_LIMIT", 503)
