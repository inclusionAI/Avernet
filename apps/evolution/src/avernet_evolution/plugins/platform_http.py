"""Bot-scoped public HTTP boundary; deployment supplies service credentials.

No principal headers are synthesized here. Request credentials never become
background credentials. Redirects are refused. Deployment must disable ambient proxies; this adapter
refuses client cookie jars to prevent credentials leaking between scopes.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import httpx
from avernet_genome.canonical import revision_id, sha256_digest
from avernet_genome.gene_paths import persona_path, resource_path, skill_path
from avernet_genome.models import (
    BotRef,
    GenomeRevision,
    Provenance,
    ProvenanceKind,
    RevisionMetadata,
    RevisionStatus,
)
from avernet_genome.spec_models import GenomePolicy, GenomeSpec
from avernet_genome.tree import decode_tree_bytes

from avernet_evolution.core.evolution_run.errors import CandidateRejected, CandidateRejectionCode


@dataclass(frozen=True)
class PlatformCredential:
    """Deployment-resolved delegation, never inferred from a Bot owner.

    The platform must verify that this service credential is authorized to act
    for acting_user_id on this Bot. No browser cookie is persisted by Evolution.
    """

    headers: Mapping[str, str]
    acting_user_id: str


Credentials = Callable[[BotRef], Awaitable[PlatformCredential]]


class PlatformHttpError(RuntimeError):
    """Safe error: never includes URL, headers, response body or transport text."""


class BotHttp:
    def __init__(self, client: httpx.AsyncClient, credentials: Credentials):
        if client.cookies:
            raise ValueError("platform client must not contain ambient cookies")
        self.client, self.credentials = client, credentials

    async def request(self, bot, method, suffix, *, params=None, body=None, raw=False):
        # suffix is adapter-owned, never a caller-supplied URL. Encode identities
        # so no Bot/Session selector can escape the authorized route prefix.
        if (
            (suffix and not suffix.startswith("/"))
            or "?" in suffix
            or "#" in suffix
            or ".." in suffix.split("/")
        ):
            raise ValueError("invalid platform route")
        if self.client.cookies:
            raise PlatformHttpError("platform client acquired ambient cookies")
        url = f"/openapi/v1/bots/{quote(bot.bot_id, safe='')}{suffix}"
        credential = await self.credentials(bot)
        if not isinstance(credential, PlatformCredential) or not credential.acting_user_id.strip():
            raise PlatformHttpError("platform delegation is not configured")
        try:
            async with self.client.stream(
                method,
                url,
                headers=dict(credential.headers),
                params={
                    **{
                        k: v
                        for k, v in (params or {}).items()
                        if k not in {"user_id", "owner_id", "entity_id"}
                    },
                    "user_id": credential.acting_user_id,
                    "entity_id": bot.entity_id,
                },
                json=body,
                follow_redirects=False,
            ) as response:
                if response.status_code >= 300:
                    # Only this documented refusal is translated to a domain
                    # error. Do not expose arbitrary messages, reasons or paths.
                    if method == "POST" and suffix == "/genome/revisions" and response.status_code == 422:
                        await _candidate_refusal(response)
                    raise PlatformHttpError(f"platform request refused ({response.status_code})")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 5 * 1024 * 1024:
                        raise PlatformHttpError("platform response exceeds bound")
                    chunks.append(chunk)
                content = b"".join(chunks)
        except httpx.HTTPError:
            raise PlatformHttpError("platform transport unavailable") from None
        if raw:
            return content
        try:
            result = json.loads(content)
            # Public Backend uses HTTP*1000; a successful HTTP response with an
            # application error is not success. BCS whoami is a separate API.
            if result["code"] not in (200000, 201000):
                raise PlatformHttpError("platform application error")
            return result["data"]
        except (ValueError, KeyError, TypeError):
            raise PlatformHttpError("invalid platform response") from None


@dataclass(frozen=True)
class Drift:
    """What the platform observes live differs from the revision `active` names."""

    active: str
    observed: str


class HttpGenome:
    """Genome v1 client over the Bot-scoped public API.

    Drift reads the read-only ``/genome/drift`` observation: it records no
    revision and moves no ref. A platform may still inject its own observation
    adapter, but the default is a real read, never an assumed "no drift".
    """

    def __init__(self, http: BotHttp, drift=None):
        self.http, self._drift = http, drift

    async def drift(self, bot):
        if self._drift is not None:
            return await self._drift(bot)
        data = await self.http.request(bot, "GET", "/genome/drift")
        if not isinstance(data, dict) or "drift" not in data:
            raise PlatformHttpError("invalid drift response")
        payload = data["drift"]
        if payload is None:
            # `null` means no observed drift OR no active revision. A caller that
            # needs a baseline resolves `active` itself; do not invent one here.
            return None
        active, observed = payload.get("active"), payload.get("observed")
        if not isinstance(active, str) or not isinstance(observed, str) or not active or not observed:
            raise PlatformHttpError("invalid drift response")
        return Drift(active, observed)

    async def resolve_revision(self, bot, ref):
        data = await self.http.request(bot, "GET", "/genome/revisions/" + quote(ref, safe=""))
        meta = data["revision"]
        if meta["bot"] != {"bot_id": bot.bot_id, "owner_id": bot.owner_id}:
            raise PlatformHttpError("revision identity mismatch")
        spec, policy = GenomeSpec.from_json(data["spec"]), GenomePolicy.from_json(data["policy"])
        if data["genome_schema"] != 1 or revision_id(spec, policy) != meta["id"]:
            raise PlatformHttpError("revision digest mismatch")
        source = meta["created_by"]
        return GenomeRevision(
            1,
            RevisionMetadata(
                meta["id"],
                meta["seq"],
                meta["parent"],
                Provenance(
                    ProvenanceKind(source["kind"]),
                    source["actor"],
                    run_id=source.get("run_id"),
                    step=source.get("step"),
                ),
                datetime.fromisoformat(meta["created_at"]),
                RevisionStatus(meta["status"]),
                meta["patch_digest"],
                tuple(meta.get("evidence", [])),
                tuple(meta.get("evaluations", [])),
                dict(meta.get("annotations", {})),
            ),
            spec,
            policy,
        )

    async def read_content(self, bot, digest):
        data = await self.http.request(bot, "GET", "/genome/content/" + quote(digest, safe=""), raw=True)
        if sha256_digest(data) != digest:
            raise PlatformHttpError("content digest mismatch")
        return data

    async def snapshot_files(self, revision, *, bot):
        files = {}
        for item in revision.spec.persona:
            files[persona_path(item.type)] = await self.read_content(bot, item.digest)
        for item in revision.spec.skills:
            if item.origin == "local":
                tree = decode_tree_bytes(await self.read_content(bot, item.digest))
                if tree is None:
                    raise PlatformHttpError("local skill is not a content tree")
                for member, content in tree:
                    files[skill_path(item.name, member)] = content
        for item in revision.spec.resources:
            content = await self.read_content(bot, item.digest)
            if item.path.endswith("/"):
                tree = decode_tree_bytes(content)
                if tree is None:
                    raise PlatformHttpError("directory resource is not a content tree")
                for member, data in tree:
                    files[resource_path(item.path + member)] = data
            else:
                files[resource_path(item.path)] = content
        if sum(map(len, files.values())) > 5 * 1024 * 1024:
            raise PlatformHttpError("workspace snapshot exceeds bound")
        return files

    async def commit_patch(self, bot, patch, run_id, strategy_ref, allowed_genes, created_by):
        # The upstream service derives provenance from its verified caller; never
        # spoof created_by as a principal header or user_id.
        data = await self.http.request(
            bot,
            "POST",
            "/genome/revisions",
            body={
                "status": "candidate",
                "run": {"run_id": run_id, "strategy": strategy_ref, "allowed_genes": list(allowed_genes)},
                "patch": patch.to_json(),
            },
        )
        if data["status"] != "candidate":
            raise PlatformHttpError("platform did not record a candidate")
        return data["id"]

    async def move_ref(self, bot, ref, revision_id, actor):
        raise NotImplementedError("promotion is outside the 01-06 HTTP bridge")


async def _candidate_refusal(response: httpx.Response) -> None:
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > 65536:
            return  # Keep the generic refusal; do not buffer unbounded errors.
        chunks.append(chunk)
    try:
        result = json.loads(b"".join(chunks))
        reasons = result["data"]["reasons"]
        if (
            result["code"] == 422000
            and isinstance(reasons, list)
            and reasons
            and all(isinstance(r, dict) and r.get("code") == "already_recorded" for r in reasons)
        ):
            raise CandidateRejected(CandidateRejectionCode.ALREADY_RECORDED)
    except (ValueError, TypeError, KeyError):
        return  # Unknown/malformed errors remain generic, never success.
