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
        # Fault-injection seams for the P0-2 policy tests: the real store's
        # refusal and fault shapes, so the pipeline's translation of them is
        # tested against the real taxonomy, not a stand-in exception.
        self.missing_blobs: set[str] = set()
        self.corrupt_blobs: set[str] = set()
        self.store_fault: Exception | None = None
        self.lookup_fault: Exception | None = None

    def store(
        self,
        fetched,
        *,
        scope,
        source_url,
        credential_name=None,
        modifier="",
        apply_id=None,
        category=None,
        entry_identity=None,
    ):
        if self.store_fault is not None:
            raise self.store_fault
        # Re-filing heals the address, the way the real store's write does:
        # a blob restored on disk stops being missing (and a quarantined
        # corruption mark cannot survive the fresh write).
        self.missing_blobs.discard(fetched.sha256)
        self.corrupt_blobs.discard(fetched.sha256)
        # Signed with the real W11 contract: the credential NAME, and the
        # apply/entry linkage the pipeline threads — no secret anywhere.
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
                "apply_id": apply_id,
                "category": category,
                "entry_identity": entry_identity,
            }
        )
        return record

    def read(self, digest: str) -> bytes:
        from agentclaw.community.core.bot_config_manifest.content.errors import (
            ContentIntegrityError,
            ContentMissingError,
        )

        if digest in self.missing_blobs:
            # The real store's terminal 404 shape: no stored content.
            raise ContentMissingError(f"no stored content for digest: {digest}")
        if digest in self.corrupt_blobs:
            # The real store's 500 shape: present, failing its own digest.
            raise ContentIntegrityError("stored blob fails its own digest")
        if digest not in self.blobs:
            raise AssertionError(f"no blob for {digest}")
        return self.blobs[digest]

    def latest_receipt(self, scope, *, source_url: str):
        if self.lookup_fault is not None:
            raise self.lookup_fault
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
        # name -> the canonical package the INSTALL published. A dry run
        # never touches this: the fake models "installed" the way the real
        # service does — only a completed upload/replace publishes.
        self.installed: dict[str, bytes] = {}
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
        self.installed[name] = package
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

    async def installed_package_digest(
        self, *, bot, bot_id: str, owner_id: str, name: str
    ):
        # The same verdict the real service computes: the digest of the bytes
        # an install published under the name — None when nothing was ever
        # installed (the dry-run shape).
        import hashlib

        package = self.installed.get(name)
        if package is None:
            return None
        return "sha256:" + hashlib.sha256(package).hexdigest()

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
        # The ``project`` flag of every write (W8): the real service projects
        # by default; the record-only wrapper passes ``False``.
        self.projections: list[bool] = []

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        return set(self.installed)

    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> frozenset[str]:
        return frozenset(self.platform_defaults)

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_platform_owned(server_code)
        self.activated.append(server_code)
        self.installed.add(server_code)
        return {}

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_platform_owned(server_code)
        self.deactivated.append(server_code)
        self.installed.discard(server_code)
        return {}

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_governed(skill_id)
        self.installed_skills.add(int(skill_id))
        self.skill_activations.append(int(skill_id))
        return {"id": skill_id, "changed": True}

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
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


class FakeGitClient:
    """The git transport for suites whose documents fetch nothing — every
    call is a wiring bug, so it counts and refuses.

    ``constructed`` is the class-level construction count the lifecycle
    suite reads: one session per apply means one client per apply, and the
    provider (not the test) is what builds it, so the only observable of
    "a session was built" is how many times the provider ran.
    """

    constructed: int = 0

    def __init__(self) -> None:
        self.calls = 0
        type(self).constructed += 1

    def fetch(self, spec, *, headers=None):
        self.calls += 1
        raise AssertionError(
            "this suite's document declares no git sources; "
            f"git fetch({spec.url!r}) must not run"
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
    apply_id: str | None = None,
    source_session=None,
    bot: dict[str, Any] | None = None,
) -> ApplyContext:
    """An ``ApplyContext`` with real capabilities resolved for a baas bot.

    ``source_session`` carries the W7 per-apply named-source state when a
    test drives the ``from``/git pipeline. ``bot`` overlays the default
    record — e.g. ``bot={"template_type": "applicationCoding"}`` for the
    runtime-routing cases: the bot record is what the engine-provisioning
    routing policy reads, and ``bot_type`` / ``engine_type`` stay the
    *capability* vocabulary while the materialiser derives the workspace
    address from the record.
    """
    record = {
        "bot_id": bot_id,
        "owner_id": owner_id,
        "entity_id": entity_id,
        "active_engine": engine_type,
        "bot_type": bot_type,
    }
    record.update(bot or {})
    return ApplyContext(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
        entity_id=entity_id,
        env="dev",
        tenant="teamclaw",
        engine_type=engine_type,
        bot_type=bot_type,
        apply_id=apply_id,
        source_session=source_session,
        bot=record,
        capabilities=resolve_capabilities(
            active_engine=engine_type,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        ),
    )


class FakeResourceFileService:
    """Stands in for ``ResourceFileService``: uploads and deletes, recorded.

    ``ResourceFileService`` is v1's single write chain for manifest resources
    (its dispatcher covers the arca / baas / teclaw transports uniformly), so
    the fake needs only the three entry points the materialiser calls:
    ``upload_file``, ``delete`` — plus ``exists`` for the plan stage's
    classification. Signatures mirror the port's apply-side surface (every
    parameter the materialiser passes; the router-only extras such as
    ``preserve_structure`` are deliberately absent), so a drift shows up as a
    TypeError in these tests before it shows up mid-apply in production.

    ``delete`` removes from the presence set as well — the real service's
    contract — because the plan stage classifies by ``exists`` and would
    otherwise call a deleted path "unchanged". Deleting a directory removes
    the whole subtree from presence (the real chain's ``delete_tree``
    branch); a path named in ``fail_deletes`` answers a silent ``False``
    with presence untouched — the real transports' contract, which catch
    their own errors and return ``False`` rather than raise, so a refused
    ``rmtree`` is only distinguishable by re-probing presence.
    """

    def __init__(
        self,
        exists_paths: set[str] | None = None,
        fail_deletes: set[str] | None = None,
    ) -> None:
        self.writes: dict[tuple[str, str], bytes] = {}
        self.deleted: list[str] = []
        # Full addressing of every call, so a test can pin *how* the write
        # stage addressed the workspace — same rationale as exists_probes.
        self.upload_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.exists_probes: list[dict[str, Any]] = []
        self._exists = set(exists_paths or ())
        self._fail_deletes = set(fail_deletes or ())

    def record_present(self, *paths: str) -> None:
        self._exists.update(paths)

    async def upload_file(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        target_dir: str,
        filename: str,
        data: bytes,
    ) -> dict[str, Any]:
        self.upload_calls.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "target_dir": target_dir,
                "filename": filename,
            }
        )
        self.writes[(target_dir, filename)] = data
        self._exists.add(f"{target_dir}/{filename}".replace("//", "/"))
        return {"path": f"{target_dir}/{filename}"}

    async def delete(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        self.delete_calls.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "path": path,
            }
        )
        if path in self._fail_deletes:
            # Silent refusal: the transport answered, nothing was removed,
            # presence still reports the tree — an exists re-probe sees it.
            return False
        self.deleted.append(path)
        self._exists = {
            p
            for p in self._exists
            if p != path and not p.startswith(f"{path}/")
        }
        return True

    async def exists(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
        publish_id: str | None = None,
        device_uuid: str | None = None,
    ) -> bool:
        # Recorded so a test can pin *how* the plan stage addressed the
        # workspace — the entity half must be the owner, the router's own
        # address, not the manifest's storage key.
        self.exists_probes.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "path": path,
            }
        )
        return path in self._exists
