"""Fakes for the apply engine's tests.

Each one **counts its calls**, because most of what these tests assert is the
*absence* of a write: convergence is "applying an unchanged document performs no
write", and all-or-nothing is "a category that could not be materialised wrote
nothing". Equal-looking output would prove neither.
"""

from __future__ import annotations

from ._resource_fakes import (
    FakeResourceRows as FakeResourceRows,
    resource_rows_for as resource_rows_for,
    FakeResourceFileService as FakeResourceFileService,
)


from ._capability_io_fakes import (
    FakeStartupScriptService as FakeStartupScriptService,
    PlatformPolicyConflict as PlatformPolicyConflict,
    FakeActivationService as FakeActivationService,
    FakeGitClient as FakeGitClient,
    FakeMcpAuth as FakeMcpAuth,
    FakeMcpConfigService as FakeMcpConfigService,
)


import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from agentclaw.community.core.ports.shared_skill_port import SharedSkillPort
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.capabilities import (
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialNotFoundError,
)
from agentclaw.community.core.bot_config_manifest.fetch.errors import (
    FetchedObject,
)
from agentclaw.community.core.bot_config_manifest.fetch.object_store import (
    ObjectFetchResult,
    ObjectFetchStatus,
    ObjectStoreTarget,
)


def fetched_object(
    body: bytes,
    *,
    url: str = "https://content.example/a.bin",
    content_type: str | None = "application/octet-stream",
) -> FetchedObject:
    """A receipt-bearing fetch result, the shape a source fetcher returns."""
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

    def store_produced(
        self,
        data: bytes,
        *,
        scope,
        origin: str,
        modifier: str = "",
        category=None,
        entry_identity=None,
    ):
        """The produced-content write path the Manifest compiler files inline bytes and trees through."""
        if self.store_fault is not None:
            raise self.store_fault
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        record = SimpleNamespace(
            digest=digest,
            source_url=f"produced://{origin}",
            credential_name=None,
            content_type=None,
            bytes=data,
        )
        self.receipts.append(record)
        self.blobs[digest] = data
        self.store_calls.append(
            {
                "scope": scope,
                "source_url": f"produced://{origin}",
                "credential_name": None,
                "modifier": modifier,
                "digest": digest,
                "apply_id": None,
                "category": category,
                "entry_identity": entry_identity,
            }
        )
        return record

    def is_reachable(
        self, scope, digest: str, *, produced_by: str | None = None
    ) -> bool:
        prefix = f"produced://{produced_by}/" if produced_by else None
        return any(
            r.digest == digest
            and (prefix is None or str(r.source_url).startswith(prefix))
            for r in self.receipts
        )

    def read(self, digest: str) -> bytes:
        from agentclaw.community.core.content_store.errors import (
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


@dataclass
class FakeBucket:
    """One bucket's contents and the credential entitled to read it."""

    objects: dict[str, bytes] = field(default_factory=dict)
    #: ``None`` means "any credential reads this bucket" — the shape a test
    #: that is not about authorisation wants.
    access_key_id: str | None = None
    #: When set, every read answers ``UNAVAILABLE`` with this detail.
    unavailable: str | None = None


class FakeObjectStore:
    """Stands in for ``AliyunObjectStore``: in-memory buckets, every status
    reachable by seeding, and every read recorded.

    A test double, not a shipped implementation — the real client has one
    road and this is where each :class:`ObjectFetchStatus` is given a shape a
    consumer test can drive:

    - an object that is present answers ``FOUND``
    - one that is not answers ``NOT_FOUND``
    - a target whose key pair does not match what the bucket was seeded with
      answers ``DENIED`` — credentials are compared rather than ignored,
      because a double that authorised everything would let a consumer
      bypass the credential entirely and still pass
    - an object over the caller's ``byte_limit`` answers ``TOO_LARGE``
      **without the caller ever holding it**
    - a bucket a test marks unreachable answers ``UNAVAILABLE``

    ``calls`` records ``(target, key, byte_limit)`` per read — the evidence a
    consumer test asserts on so a pipeline cannot bypass the store and pass.
    """

    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}
        self.calls: list[tuple[ObjectStoreTarget, str, int]] = []

    # --- seeding ------------------------------------------------------------

    def put(
        self,
        bucket: str,
        key: str,
        content: bytes,
        *,
        access_key_id: str | None = None,
    ) -> None:
        holder = self.buckets.setdefault(bucket, FakeBucket())
        holder.objects[key] = content
        if access_key_id is not None:
            holder.access_key_id = access_key_id

    def make_unavailable(
        self, bucket: str, detail: str = "endpoint unreachable"
    ) -> None:
        self.buckets.setdefault(bucket, FakeBucket()).unavailable = detail

    def reset(self) -> None:
        self.buckets.clear()
        self.calls.clear()

    # --- the read -----------------------------------------------------------

    def get(
        self, target: ObjectStoreTarget, key: str, *, byte_limit: int
    ) -> ObjectFetchResult:
        self.calls.append((target, key, byte_limit))
        where = f"{target.bucket}/{key}"
        bucket = self.buckets.get(target.bucket)

        if bucket is None:
            # An unknown bucket is not distinguishable from one this
            # credential may not see, and guessing which would leak the
            # difference. The store's own answer is the honest one.
            return ObjectFetchResult(
                ObjectFetchStatus.DENIED, detail=f"{where}: not authorized"
            )
        if bucket.unavailable is not None:
            return ObjectFetchResult(
                ObjectFetchStatus.UNAVAILABLE,
                detail=f"{where}: {bucket.unavailable}",
            )
        if (
            bucket.access_key_id is not None
            and bucket.access_key_id != target.access_key_id
        ):
            return ObjectFetchResult(
                ObjectFetchStatus.DENIED, detail=f"{where}: not authorized"
            )

        body = bucket.objects.get(key)
        if body is None:
            return ObjectFetchResult(
                ObjectFetchStatus.NOT_FOUND, detail=f"{where}: no such object"
            )
        if len(body) > byte_limit:
            # The bytes are NOT returned. A caller that received them would be
            # holding exactly what the cap exists to keep out of memory, and a
            # test asserting only on the status would not notice.
            return ObjectFetchResult(
                ObjectFetchStatus.TOO_LARGE,
                detail=(
                    f"{where}: object exceeds the {byte_limit}-byte cap "
                    f"({len(body)} bytes)"
                ),
            )
        return ObjectFetchResult(ObjectFetchStatus.FOUND, content=body)


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


# ── the declared object-source rig ──────────────────────────────────────────
# A ``source`` is a declaration: ``{protocol: oss, bucket, key, auth}`` or
# ``{protocol: git, ...}``. The object road is the one a consumer test reaches
# for when what it is really pinning is the *fetch* — pinning, keep_last,
# budget, receipts — so these constants and helpers give every such test one
# bucket, one credential and one spelling of the declaration.

#: The bucket the rigs seed. A document names it; the endpoint never appears
#: in a document, which is the whole security property of this road.
OSS_BUCKET = "manifest-fixtures"
#: The credential name a declared source carries — ``auth`` is mandatory on
#: this road, since the endpoint is a property of the credential row.
OSS_AUTH = "oss-cred"
OSS_ENDPOINT = "https://oss-cn-shanghai.aliyuncs.com"
#: Deliberately short and with no provider prefix. Nothing on this road
#: validates a key id's shape — the store fake compares it as an opaque
#: string — while anything token-shaped, or merely 12+ characters with a
#: little entropy, trips ``scripts/ci/check_secrets.py`` on the field name
#: alone and blocks every push that touches this line.
OSS_ACCESS_KEY_ID = "fake-ak"


class FakeObjectCredentials(FakeCredentials):
    """:class:`FakeCredentials` plus the half only the object road reads.

    The base fake's binding answers the two header seams; an ``oss`` source
    also asks it for an :class:`ObjectStoreTarget`, and *that* is where the
    endpoint and the key pair come from — never from the document.
    """

    def binding(self, *, name: str):
        binding = super().binding(name=name)
        binding.object_store_target = lambda bucket: ObjectStoreTarget(
            endpoint=OSS_ENDPOINT,
            bucket=bucket,
            access_key_id=OSS_ACCESS_KEY_ID,
            secret_access_key="fake-sk",  # short, for the reason above
            region="cn-shanghai",
        )
        return binding


def oss_source(
    key: str, *, bucket: str = OSS_BUCKET, auth: str | None = OSS_AUTH
) -> dict[str, Any]:
    """The declared ``source`` an entry carries to read one object."""
    decl: dict[str, Any] = {"protocol": "oss", "bucket": bucket, "key": key}
    if auth is not None:
        decl["auth"] = auth
    return decl


def seeded_object_store(objects: dict[str, bytes] | None = None) -> FakeObjectStore:
    """A store holding ``key → bytes`` under :data:`OSS_BUCKET`, readable by
    the key pair :class:`FakeObjectCredentials` presents."""
    store = FakeObjectStore()
    for key, body in (objects or {}).items():
        store.put(OSS_BUCKET, key, body, access_key_id=OSS_ACCESS_KEY_ID)
    return store


def declared_session(**kwargs):
    """The per-apply source session a declared source needs.

    Every entry that reaches a fetcher carries one — the ``from`` road reads
    ``sources`` out of it and the git road checks out through it — so a rig
    driving a declared source hands the context one even when the object road
    it takes never looks inside.
    """
    from agentclaw.community.core.bot_config_manifest.sources.source_session import (
        SourceSession,
    )

    kwargs.setdefault("sources", {})
    kwargs.setdefault("baselines", {})
    kwargs.setdefault("git", None)
    return SourceSession(**kwargs)


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
    """A compiling identity materialiser over fakes: (materialiser, identity fake, object store,
    content store).

    The declared source :data:`SOUL_SOURCE` reads :data:`SOUL_KEY` out of the
    seeded bucket and serves :data:`SOUL_BODY`.
    """
    from agentclaw.community.core.bot_config_manifest.sources.source_resolver import (
        DeclaredSourceResolver,
    )

    from ._apply_assembly import compiling_identity

    identity = FakeIdentityService(files)
    objects = seeded_object_store({SOUL_KEY: SOUL_BODY})
    content = FakeManifestContent()
    pipeline = DeclaredSourceResolver(content, FakeObjectCredentials(), objects)
    return compiling_identity(identity, pipeline, content), identity, objects, content


SOUL_KEY = "identity/soul.md"
SOUL_SOURCE = oss_source(SOUL_KEY)
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
        self.deleted: list[str] = []
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

    async def delete_local_skill(
        self,
        *,
        skill_id: str,
        name: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
    ) -> None:
        self.deleted.append(skill_id)
        self.rows.pop(name, None)
        self.installed.pop(name, None)

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
        return [call["package"] for call in self.uploads if call["name"] == name]


class FakeLocalSkillDeleteService:
    """The explicit delete Service API over the package fake's shared rows."""

    def __init__(self, packages: FakeSkillUploadService) -> None:
        self._packages = packages

    async def delete_local_skill(
        self, *, skill_id: str, owner_id: str, user_id: str
    ) -> None:
        name = next(
            (
                name
                for name, row in self._packages.rows.items()
                if str(row["id"]) == str(skill_id)
            ),
            None,
        )
        if name is None:
            return
        await self._packages.delete_local_skill(
            skill_id=skill_id,
            name=name,
            bot_id="fake-bot",
            owner_id=owner_id,
            actor_id=user_id,
        )


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
        local_assets: list[Any] | None = None,
    ) -> None:
        self.assets = tuple(assets or ())
        self.member_ids = frozenset(member_ids or ())
        self.local_assets = tuple(
            local_assets
            if local_assets is not None
            else [
                asset
                for asset in self.assets
                if str(getattr(asset, "git_path", "")).startswith("local://")
            ]
        )
        self.asset_reads: list[dict[str, Any]] = []

    def active_skill_assets(self, *, bot_id: str, owner_id: str, bot=None):
        self.asset_reads.append({"bot_id": bot_id, "owner_id": owner_id})
        return self.assets

    def member_skill_ids(self, *, bot):
        return self.member_ids

    def local_skill_assets(self, *, bot_id: str, owner_id: str, bot=None):
        return self.local_assets


def skill_asset(
    skill_id: int,
    name: str,
    git_path: str = "local://x",
    mcp_dependencies: tuple[object, ...] = (),
):
    """One active-skill asset, the RegisteredSkillAsset shape."""
    return SimpleNamespace(
        skill_id=skill_id,
        name=name,
        git_path=git_path,
        mcp_dependencies=mcp_dependencies,
    )


def build_skill_zip(
    name: str, *, extra: list[tuple[str, bytes]] | None = None
) -> bytes:
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


def make_context(
    *,
    bot_id: str = "b_1",
    owner_id: str = "u_owner",
    actor_id: str = "u_actor",
    entity_id: str = "u_owner",
    engine_type: str = "claude_code",
    bot_type: str = "personal",
    apply_id: str | None = None,
    bot: dict[str, Any] | None = None,
) -> ApplyContext:
    """An ``ApplyContext`` with real capabilities resolved for a baas bot.

    ``bot`` overlays the default
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
        bot=record,
        capabilities=resolve_capabilities(
            active_engine=engine_type,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        ),
    )


def arca_only_engine_test(_engine: str | None) -> bool:
    """Every bot is ARCA."""
    return False


def teclaw_engine_test(engine: str | None) -> bool:
    """The engine authority's answer, spelled the way production spells it."""
    return (engine or "").lower() == "teclaw"


async def no_redeliver(ctx) -> None:
    """The closing step, doing nothing — for a teclaw rig that is not about it."""
    return None


async def unreachable_redeliver(ctx) -> None:
    raise AssertionError("this rig never reaches the closing redeliver")


class FakeSharedSkills(SharedSkillPort):
    """``SharedSkillPort`` over two dicts: Center name → skill ids visible to the bot, repo path → (id, name).

    A name with several ids is ambiguous; a missing one is not found — the real lookup's three answers.
    """

    def __init__(
        self,
        center: dict[str, list[str]] | None = None,
        repo: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.center = dict(center or {})
        self.repo = dict(repo or {})
        self.asked: list[tuple[str, str, str]] = []

    def find_center_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, name: str
    ) -> str:
        from agentclaw.community.core.ports.shared_skill_port import (
            SharedSkillAmbiguous,
            SharedSkillNotFound,
        )

        self.asked.append(("center", name, actor_id))
        ids = self.center.get(name, [])
        if not ids:
            raise SharedSkillNotFound(
                f"no Skill Center skill named {name!r} is available to this bot"
            )
        if len(ids) > 1:
            raise SharedSkillAmbiguous(
                f"{len(ids)} Skill Center skills named {name!r} are available to this bot"
            )
        return ids[0]

    def find_repo_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, name: str, path: str
    ) -> str:
        from agentclaw.community.core.ports.shared_skill_port import SharedSkillNotFound

        self.asked.append(("repo", path, actor_id))
        if path not in self.repo:
            raise SharedSkillNotFound(
                f"no shared-repository skill at {path!r} is available to this bot"
            )
        skill_id, found = self.repo[path]
        if found != name:
            raise SharedSkillNotFound(
                f"the shared-repository skill at {path!r} is named {found!r}, not {name!r}"
            )
        return skill_id
