"""Fakes for the apply engine's tests.

Each one **counts its calls**, because most of what these tests assert is the
*absence* of a write: convergence is "applying an unchanged document performs no
write", and all-or-nothing is "a category that could not be materialised wrote
nothing". Equal-looking output would prove neither.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.capabilities import (
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialNotFoundError,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchedObject,
)


def fetched_object(
    body: bytes, *, url: str = "https://content.example/a.bin",
    content_type: str | None = "application/octet-stream",
) -> FetchedObject:
    """A receipt-bearing fetch result, the shape ``GuardedFetcher`` returns."""
    return FetchedObject(
        bytes=body,
        sha256="sha256:" + hashlib.sha256(body).hexdigest(),
        url=url,
        content_type=content_type,
        fetched_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        size_bytes=len(body),
    )


class FakeManifestContent:
    """Stands in for the W11 store: receipts newest-last, blobs on demand.

    ``store`` sanitizes nothing (W11's own tests pin that); this fake keeps
    exactly the fields the pipeline consumes, plus every call for counting.
    """

    def __init__(self) -> None:
        self.receipts: list[Any] = []
        self.blobs: dict[str, bytes] = {}
        self.store_calls: list[dict[str, Any]] = []

    def store(self, fetched, *, scope, source_url, credential_name=None, modifier=""):
        record = SimpleNamespace(
            digest=fetched.sha256,
            source_url=source_url,
            credential_name=credential_name,
            content_type=fetched.content_type,
            bytes=fetched.bytes,
        )
        self.receipts.append(record)
        self.blobs[fetched.sha256] = fetched.bytes
        self.store_calls.append(
            {
                "scope": scope,
                "source_url": source_url,
                "credential_name": credential_name,
                "modifier": modifier,
                "digest": fetched.sha256,
            }
        )
        return record

    def read(self, digest: str) -> bytes:
        if digest not in self.blobs:
            raise AssertionError(f"no blob for {digest}")
        return self.blobs[digest]

    def latest_receipt(self, scope, *, source_url: str):
        for record in reversed(self.receipts):
            if record.source_url == source_url:
                return record
        return None


class FakeGuardedFetcher:
    """Stands in for the W2 transport: scripted successes or real error types.

    Records every request so tests can assert what the wire actually saw —
    the substituted URL, the declared digest, the credential binding.

    Implements the one rule of W2's contract a caller can lean on: a declared
    ``expected_digest`` is verified against the served bytes, and a mismatch
    is a fetch failure — never a "success with corrupted bytes". Without that
    in the fake, a materialiser relying on the pin would pass here while the
    real transport refused.
    """

    def __init__(
        self,
        responses: dict[str, FetchedObject] | None = None,
        failures: dict[str, Exception] | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.failures = dict(failures or {})
        self.requests: list[Any] = []

    def fetch(self, request):
        self.requests.append(request)
        failure = self.failures.get(request.url)
        if failure is not None:
            raise failure
        response = self.responses[request.url]
        if (
            request.expected_digest is not None
            and response.sha256 != request.expected_digest
        ):
            raise FetchFailedError("digest mismatch")
        return response


class FakeCredentials:
    """Stands in for W3: a named binding, live or missing.

    The binding object duck-satisfies both fetcher seams (injector and policy)
    in one, the way ``SourceCredentialBinding`` does.
    """

    def __init__(self, missing: set[str] | None = None) -> None:
        self.missing = set(missing or ())
        self.binding_calls: list[str] = []

    def binding(self, *, name: str):
        self.binding_calls.append(name)
        if name in self.missing:
            raise CredentialNotFoundError(f"credential '{name}' does not exist")
        return SimpleNamespace(
            headers_for=lambda url: {"X-Custom-Auth": f"payload-of-{name}"},
            reauthorize=lambda url: None,
        )


class FakeIdentityService:
    """Stands in for ``IdentityService``: files held, writes counted.

    The real service's positional contract per method (entity_type,
    entity_id, bot_id, then the operation's own args, then owner/operator) —
    the fake mirrors it so a signature drift shows up as a TypeError here
    before it shows up mid-apply in production.

    Empty content means the same as absent — the domain's own contract — so
    ``list_bot_files`` answers ``(file, bool(content))`` over the whole
    whitelist, exactly as the real one does.
    """

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.writes: list[dict[str, Any]] = []
        self.reads: list[str] = []
        self.listed: int = 0

    async def list_bot_files(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> list[tuple[str, bool]]:
        from agentclaw.community.core.services.identity import (
            VALID_IDENTITY_FILES,
        )

        self.listed += 1
        return [(ft, bool(self.files.get(ft))) for ft in VALID_IDENTITY_FILES]

    async def read_identity_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> str:
        self.reads.append(file_type)
        return self.files.get(file_type, "")

    async def update_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        operator_id: str,
        engine_type: str | None = None,
        *,
        stage: str = "draft",
    ):
        self.writes.append(
            {
                "file_type": file_type,
                "content": content,
                "operator": operator_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
            }
        )
        self.files[file_type] = content
        return SimpleNamespace(
            file_type=file_type,
            file_path=f"identity/{file_type}",
        )

    def write_count(self, *, file_type: str) -> int:
        return sum(1 for w in self.writes if w["file_type"] == file_type)

    @property
    def all_writes(self) -> int:
        return len(self.writes)


def identity_rig(files: dict[str, str] | None = None):
    """A materialiser over fakes: (materialiser, identity fake, fetcher fake).

    The fetched URL ``SOUL_URL`` serves ``SOUL_BODY`` for identity tests.
    """
    from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
        EntryFetcher,
    )
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.identity import (
        IdentityMaterialiser,
    )

    identity = FakeIdentityService(files)
    fetcher = FakeGuardedFetcher(responses={SOUL_URL: fetched_object(SOUL_BODY)})
    content = FakeManifestContent()
    pipeline = EntryFetcher(fetcher, content, FakeCredentials())
    return IdentityMaterialiser(identity, pipeline), identity, fetcher, content


SOUL_URL = "https://content.example/identity/soul.md"
SOUL_BODY = b"# team charter\nServe the customer honestly.\n"


class FakeSkillUploadService:
    """Stands in for ``LocalSkillUploadService``: same-name create-or-replace.

    The name is parsed from the uploaded package's own SKILL.md (by the real
    parser) — never taken from the caller — so the fake refuses what the real
    service refuses: an unparseable package, right here, in the materialiser's
    tests.
    """

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.rows: dict[str, dict[str, Any]] = {}
        self._next_id = 100

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        from agentclaw.community.core.skill_center.services.skill_parser import (
            SkillParser,
        )

        name = _skill_name_of(package, SkillParser())
        if name is None:
            raise _LocalSkillInvalidPackage()
        record = self.rows.get(name)
        operation = "created" if record is None else "replaced"
        if record is None:
            self._next_id += 1
            record = {"id": self._next_id, "name": name}
            self.rows[name] = record
        self.uploads.append(
            {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "actor_id": actor_id,
                "name": name,
                "package": package,
                "operation": operation,
            }
        )
        return {
            "operation": operation,
            "skill": {**record, "active": False},
            "actor_id": actor_id,
        }

    def uploaded_packages(self, *, name: str) -> list[bytes]:
        return [
            call["package"] for call in self.uploads if call["name"] == name
        ]


def _skill_name_of(package: bytes, parser: Any) -> str | None:
    """The package's own declared name, via its SKILL.md front matter."""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            members = [
                info
                for info in archive.infolist()
                if not info.is_dir() and info.filename.split("/")[-1] == "SKILL.md"
            ]
            if len(members) != 1:
                return None
            body = archive.read(members[0])
    except zipfile.BadZipFile:
        return None
    metadata = parser.parse_skill_markdown(body)
    return str(metadata.to_dict().get("name") or "")


class _LocalSkillInvalidPackage(Exception):
    """The shape LocalSkillInvalidPackageError takes in these tests."""


class FakeCapabilityReader:
    """Stands in for ``BotCapabilityStateReader``: the active set + governance.

    ``reader.member_skill_ids`` is what the flush says the Bot's Sets supply;
    narrowing removals by it mirrors the ``mcp`` materialiser's
    platform-default narrowing — same refusal, same source of truth.
    """

    def __init__(
        self,
        assets: list[Any] | None = None,
        member_ids: set[int] | None = None,
    ) -> None:
        self.assets = tuple(assets or ())
        self.member_ids = frozenset(member_ids or ())
        self.asset_reads: list[dict[str, Any]] = []

    def active_skill_assets(self, *, bot_id: str, owner_id: str, bot=None):
        self.asset_reads.append({"bot_id": bot_id, "owner_id": owner_id})
        return self.assets

    def member_skill_ids(self, *, bot):
        return self.member_ids


def skill_asset(skill_id: int, name: str, git_path: str = "local://x"):
    """One active-skill asset, the RegisteredSkillAsset shape."""
    return SimpleNamespace(skill_id=skill_id, name=name, git_path=git_path)


def build_skill_zip(name: str, *, extra: list[tuple[str, bytes]] | None = None) -> bytes:
    """A real, valid skill package zip: SKILL.md with front matter, plus any
    extra members — the discipline of driving fakes with true bytes."""
    import io
    import zipfile

    manifest = (
        f"---\nname: {name}\ndescription: {name} test skill.\n---\n# {name}\n"
    ).encode()
    entries: list[tuple[str, bytes]] = [("SKILL.md", manifest)]
    entries.extend(extra or [])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in entries:
            archive.writestr(zipfile.ZipInfo(path), content)
    return stream.getvalue()


def build_skill_tgz(rows: list[tuple[str, bytes]]) -> bytes:
    """A real tar.gz carrying ``rows`` (path without leading ./, bytes)."""
    import io
    import tarfile

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path, content in rows:
            info = tarfile.TarInfo(path)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def real_validator() -> Any:
    """The production validator, over the production parser — no fake."""
    from agentclaw.community.core.skill_center.services.skill_parser import (
        SkillParser,
    )
    from agentclaw.community.core.skill_center.skill_package import (
        SkillPackageValidator,
    )

    return SkillPackageValidator(SkillParser())




class FakeStartupScriptService:
    """Stands in for ``BotStartupScriptService``, recording every call."""

    def __init__(self, body: str = "") -> None:
        self.body = body
        self.puts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []

    def get_body(self, *, entity_id: str, bot_id: str) -> str:
        return self.body

    def put(self, *, entity_id: str, bot_id: str, script: str, modifier: str) -> None:
        self.puts.append(
            {
                "entity_id": entity_id,
                "bot_id": bot_id,
                "script": script,
                "modifier": modifier,
            }
        )
        self.body = script

    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        self.deletes.append({"entity_id": entity_id, "bot_id": bot_id})
        existed = bool(self.body)
        self.body = ""
        return existed

    @property
    def writes(self) -> int:
        return len(self.puts) + len(self.deletes)


class PlatformPolicyConflict(Exception):
    """Stands in for ``SkillSetControlPlaneConflictError``.

    Imported by shape rather than by name to keep these fakes free of the
    skill_center import graph; what matters to the engine is that the real
    service raises *something* from inside a write.
    """


class FakeActivationService:
    """Stands in for ``DirectActivationService``, recording every call.

    Both pairs the real service owns — the MCP pair and the skill pair —
    because both materialisers share one service instance and a fake split by
    pair would pass the registry while failing the first apply that touched
    the other half.

    ``platform_defaults`` models the guard the real service runs *before* the
    permission check: ``activate_mcp``/``deactivate_mcp`` raise
    ``SkillSetControlPlaneConflictError`` on an engine/template default code.
    The fake raises too — without that, a materialiser that forgot to ask about
    platform defaults would pass every test here while half-writing the category
    in production, which is exactly the gap this models. ``governed_skills``
    models the same refusal for skills: ids a Set or the default set supplies.
    """

    def __init__(
        self,
        installed: set[str] | None = None,
        platform_defaults: set[str] | None = None,
        governed_skills: set[int] | None = None,
    ) -> None:
        self.installed = set(installed or ())
        self.platform_defaults = set(platform_defaults or ())
        self.governed_skills = set(governed_skills or ())
        self.activated: list[str] = []
        self.deactivated: list[str] = []
        self.installed_skills: set[int] = set()
        self.skill_activations: list[int] = []
        self.skill_deactivations: list[int] = []

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        return set(self.installed)

    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> frozenset[str]:
        return frozenset(self.platform_defaults)

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        self._refuse_if_platform_owned(server_code)
        self.activated.append(server_code)
        self.installed.add(server_code)
        return {}

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        self._refuse_if_platform_owned(server_code)
        self.deactivated.append(server_code)
        self.installed.discard(server_code)
        return {}

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        self._refuse_if_governed(skill_id)
        self.installed_skills.add(int(skill_id))
        self.skill_activations.append(int(skill_id))
        return {"id": skill_id, "changed": True}

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        self._refuse_if_governed(skill_id)
        self.installed_skills.discard(int(skill_id))
        self.skill_deactivations.append(int(skill_id))
        return {"id": skill_id, "changed": True}

    def _refuse_if_platform_owned(self, server_code: str) -> None:
        if server_code in self.platform_defaults:
            raise PlatformPolicyConflict("RESOURCE_MANAGED_BY_PLATFORM_POLICY")

    def _refuse_if_governed(self, skill_id: str) -> None:
        if int(skill_id) in self.governed_skills:
            raise PlatformPolicyConflict("RESOURCE_MANAGED_BY_SKILL_SET")

    @property
    def writes(self) -> int:
        return (
            len(self.activated)
            + len(self.deactivated)
            + len(self.skill_activations)
            + len(self.skill_deactivations)
        )


class FakeMcpAuth:
    """Permission answers, per server code.

    ``denied`` names servers the tenant may not enable; ``outage`` names ones
    whose lookup returns the fail-open shape the catalogue gives during an
    upstream outage — which a desired-state write must read as "no".
    """

    def __init__(
        self, denied: set[str] | None = None, outage: set[str] | None = None
    ) -> None:
        self.denied = set(denied or ())
        self.outage = set(outage or ())

    def check_mcp_permission_detail(
        self, user_id: str, server_code: str
    ) -> dict[str, Any]:
        if server_code in self.denied:
            return {"has_permission": False, "access_level": None}
        if server_code in self.outage:
            # The documented outage sentinel: advisory "yes" with no level.
            return {"has_permission": True, "access_level": None}
        return {"has_permission": True, "access_level": "PUBLIC"}


def make_context(
    *,
    bot_id: str = "b_1",
    owner_id: str = "u_owner",
    actor_id: str = "u_actor",
    entity_id: str = "u_owner",
    engine_type: str = "claude_code",
    bot_type: str = "personal",
) -> ApplyContext:
    """An ``ApplyContext`` with real capabilities resolved for a baas bot."""
    return ApplyContext(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
        entity_id=entity_id,
        env="dev",
        tenant="teamclaw",
        engine_type=engine_type,
        bot_type=bot_type,
        bot={
            "bot_id": bot_id,
            "owner_id": owner_id,
            "entity_id": entity_id,
            "active_engine": engine_type,
            "bot_type": bot_type,
        },
        capabilities=resolve_capabilities(
            active_engine=engine_type,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        ),
    )
