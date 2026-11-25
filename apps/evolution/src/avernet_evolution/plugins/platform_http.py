"""Bot-scoped HTTP for application delegation or signed built-in callbacks.

Browser credentials never become background credentials. Deployment must
supply the service trust material and disable ambient proxies. The adapter
refuses redirects and client cookie jars to avoid leaking credentials.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlsplit

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

from avernet_evolution.adapters.backend_access import fingerprint
from avernet_evolution.adapters.backend_data import HEADER, PREFIX, issue_data_proof
from avernet_evolution.core.evolution_run.data_scope import current_data_scope
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
    """Safe error: never includes URL, headers, response body or transport text.

    ``status`` 是上游 HTTP 状态码（若已拿到响应）；调用方据此区分 404 这类
    有语义的拒绝，而不必解析文案。
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


class BotHttp:
    def __init__(
        self,
        client: httpx.AsyncClient,
        credentials: Credentials,
        *,
        data_origin: str = "",
        data_key: str = "",
        data_tenant: str = "",
    ):
        if client.cookies:
            raise ValueError("platform client must not contain ambient cookies")
        if data_origin:
            origin = urlsplit(data_origin)
            if (
                origin.scheme not in {"http", "https"}
                or not origin.hostname
                or origin.username is not None
                or origin.password is not None
                or origin.path not in {"", "/"}
                or origin.query
                or origin.fragment
                or len(data_key.encode()) < 32
                or not data_tenant.strip()
            ):
                raise ValueError("invalid built-in Evolution Backend data configuration")
        self.client, self.credentials = client, credentials
        self._data_origin, self._data_key, self._data_tenant = data_origin.rstrip("/"), data_key, data_tenant

    async def request(self, bot, method, suffix, *, params=None, body=None, raw=False):
        # suffix is adapter-owned, never a caller-supplied URL.
        if (
            (suffix and not suffix.startswith("/"))
            or "?" in suffix
            or "#" in suffix
            or ".." in suffix.split("/")
        ):
            raise ValueError("invalid platform route")
        if self.client.cookies:
            raise PlatformHttpError("platform client acquired ambient cookies")
        target = f"/{quote(bot.bot_id, safe='')}{suffix}"
        scope = current_data_scope.get()
        safe_params = {
            k: v for k, v in (params or {}).items() if k not in {"user_id", "owner_id", "entity_id"}
        }
        if scope is not None:
            if scope.bot != bot or not self._data_origin:
                raise PermissionError("built-in Backend data access is unavailable")
            # Build independently from the public client defaults: no app
            # auth, ambient Cookie, Gateway principal, or default query params.
            request = httpx.Request(
                method, self._data_origin + PREFIX + target, params=safe_params, json=body
            )
            request.headers[HEADER] = issue_data_proof(
                self._data_key,
                self._data_tenant,
                scope,
                fingerprint(
                    method, request.url.path, request.url.query.decode(), request.content, request.headers
                ),
            )
        else:
            credential = await self.credentials(bot)
            if not isinstance(credential, PlatformCredential) or not credential.acting_user_id.strip():
                raise PlatformHttpError("platform delegation is not configured")
            request = self.client.build_request(
                method,
                "/openapi/v1/bots" + target,
                headers=dict(credential.headers),
                params={**safe_params, "user_id": credential.acting_user_id, "entity_id": bot.entity_id},
                json=body,
            )
        try:
            response = await self.client.send(
                request,
                stream=True,
                follow_redirects=False,
                **({"auth": None} if scope is not None else {}),
            )
            try:
                if response.status_code >= 300:
                    if scope is not None and response.status_code == 403:
                        raise PermissionError("built-in Backend data access is unavailable")
                    # Only this documented refusal is translated to a domain
                    # error. Do not expose arbitrary messages, reasons or paths.
                    if method == "POST" and suffix == "/genome/revisions" and response.status_code == 422:
                        await _candidate_refusal(response)
                    raise PlatformHttpError(
                        f"platform request refused ({response.status_code})",
                        status=response.status_code,
                    )
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 5 * 1024 * 1024:
                        raise PlatformHttpError("platform response exceeds bound")
                    chunks.append(chunk)
                content = b"".join(chunks)
            finally:
                await response.aclose()
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
        return await self._read_scoped_drift(bot)

    async def _read_drift(self, bot) -> Drift | None:
        data = await self.http.request(bot, "GET", "/genome/drift")
        if not isinstance(data, dict) or "drift" not in data:
            raise PlatformHttpError("invalid drift response")
        payload = data["drift"]
        if payload is None:
            # `null` means no observed drift OR no active revision. A caller that
            # needs a baseline resolves `active` itself; do not invent one here.
            return None
        if not isinstance(payload, dict):
            raise PlatformHttpError("invalid drift response")
        active, observed = payload.get("active"), payload.get("observed")
        if not isinstance(active, str) or not isinstance(observed, str) or not active or not observed:
            raise PlatformHttpError("invalid drift response")
        return Drift(active, observed)

    async def resolve_revision(self, bot, ref):
        try:
            data = await self.http.request(bot, "GET", "/genome/revisions/" + quote(ref, safe=""))
        except PlatformHttpError as exc:
            # A real, equally scoped read must succeed before diagnosing a missing baseline.
            if exc.status == 404 and ref == "active" and await self._read_scoped_drift(bot) is None:
                from avernet_evolution.core.evolution_run.errors import GenomeBaselineMissing

                raise GenomeBaselineMissing(bot.bot_id) from None
            raise
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

    async def _read_scoped_drift(self, bot) -> Drift | None:
        try:
            return await self._read_drift(bot)
        except PlatformHttpError as probe:
            if probe.status == 404 and current_data_scope.get() is None:
                from avernet_evolution.core.evolution_run.errors import EvolutionAppNotGranted

                raise EvolutionAppNotGranted(bot.bot_id) from None
            raise

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
