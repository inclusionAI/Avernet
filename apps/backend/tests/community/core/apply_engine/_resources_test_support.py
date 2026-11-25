"""Tests for the ``resources`` materialiser (W6, revised for #2162).

Pins, by the work item's acceptance criteria:
- file entries materialise through ``ResourceFileService`` at a
  workspace-relative ``path`` (physical placement stays the engine's call);
- directory entries expand into their members; the root (``data/``) is owned
  whole: every apply clears it and writes the declaration back, so a path the
  previous apply wrote and this one omits, and a file the bot wrote there
  itself, both go — and ``plan`` reports them as ``removals`` (ownership rule);
- nothing outside the root is ever touched or reported;
- fetch failures and admission refusals abort the whole category before the
  root is cleared (§3.2);
- a clear that fails fails every member with nothing written; the platform's
  resource rows follow the written files, and a rows failure is a confirmed
  partial write;
- convergence writes are file-grained (teclaw per-file forwarding is the same
  transport), and the module never touches ``BotConfigArtifact``;
- archive limits (member count / unpacked size) apply with W1's keys.
"""

from __future__ import annotations


from tests.community.core.apply_engine._capability_facts import (
    build_test_materialisers as build_materialisers,
)


import asyncio


from types import SimpleNamespace


from typing import Any


from agentclaw.community.core.bot_config_manifest.sources.entry_delivery import (
    BlobDelivery,
    EntryFetchError,
    FetchedEntry,
    GitDelivery,
    GitEntrySource,
)


from agentclaw.community.core.bot_config_manifest.fetch.errors import (
    FetchRefusedError,
)


from agentclaw.community.core.apply_engine.delivery import ArcaDelivery


from agentclaw.community.core.apply_engine.orchestrator import (
    ApplyOrchestrator,
)


from ._empty_cli_tools import EmptyCliTools


from ._fakes import (
    EngineRig,
    FakeActivationService,
    FakeCapabilityReader,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeSkillUploadService,
    FakeStartupScriptService,
    build_skill_tgz,
    compiling_resources,
    declared_session,
    make_context,
    manifest_compiler,
    real_validator,
    resource_rows_for,
)


_arca_steps = ArcaDelivery(lambda: None).steps_for


def _run(coro):
    return asyncio.run(coro)


def _materialiser(svc, resolver, *, source_session=None, resource_rows=None):
    """The resources materialiser fed through the real entry compiler.

    Raw entries go through ``compile_resources`` over ``resolver`` and a
    fresh content store, and the typed result is what the materialiser
    reads back — the road a real apply takes. ``source_session`` is what a
    ``from`` name resolves against; the compiler's session carries it.
    ``resource_rows`` is the rows factory the write stage replaces the
    bot's rows through; a test that does not read the rows gets a recorder
    nobody looks at.
    """
    return compiling_resources(
        svc,
        resolver,
        FakeManifestContent(),
        source_session=source_session,
        resource_rows=resource_rows,
    )


_BUCKET = "cdn"


def _src(key: str, **extra) -> dict:
    """The declared ``source`` an entry carries to read one object."""
    return {
        "protocol": "oss",
        "bucket": _BUCKET,
        "key": key,
        "auth": "oss-cred",
        **extra,
    }


class _RecordingStore:
    """The content store a :class:`GitDelivery` files through, recorded.

    The git road's delivery now writes its own receipt, so the double that
    used to record ``file_bytes`` records the store call instead — in the
    store's own vocabulary, which is what the real one receives.
    """

    def __init__(self, filed: list[dict[str, Any]]) -> None:
        self._filed = filed

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
        self._filed.append(
            {
                "content": fetched.bytes,
                "source_url": source_url,
                "category": category,
                "entry_identity": entry_identity,
                "credential_name": credential_name,
            }
        )
        return SimpleNamespace(digest=fetched.sha256)


class _StubDeclaredSourceResolver:
    """``DeclaredSourceResolver``'s test double, every call recorded.

    Addresses ending in ``gone`` stand in for unreachable sources. With
    ``fixed_body``, every address serves the same bytes (an archive); without
    it, the address's own ``bytes-of-<address>``. It answers in the pipeline's own
    currency — a real :class:`FetchedEntry` — because the materialiser
    consumes ``.content``; a stub answering in the transport's
    ``FetchedObject`` shape would pass collection and fail on the first real
    resolve.
    """

    def __init__(
        self,
        fixed_body: bytes | None = None,
        *,
        git_trees: dict[str, dict[str, bytes]] | None = None,
        moved_from: str | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.filed: list[dict[str, Any]] = []
        self._fixed_body = fixed_body
        #: repository url → the whole tree it delivers, path → bytes.
        self.git_trees = git_trees or {}
        self.moved_from = moved_from
        #: When set, ``resolve`` answers with it verbatim — the way to
        #: stand in for what the git road's ``_keep_last`` hands back.
        self.declared_override = None
        #: The store a git delivery files through, recording into ``filed``.
        self.store = _RecordingStore(self.filed)

    def fetch(
        self,
        ctx: Any,
        *,
        source_url: str,
        digest: str | None = None,
        auth: str | None = None,
        category: str = "resources_file",
        keep_last: bool = False,
        entry_identity: str | None = None,
    ) -> FetchedEntry:
        self.calls.append(
            {
                "source_url": source_url,
                "digest": digest,
                "auth": auth,
                "category": category,
                "keep_last": keep_last,
                "entry_identity": entry_identity,
            }
        )
        if source_url.endswith("gone"):
            raise EntryFetchError("source unreachable")
        body = (
            self._fixed_body
            if self._fixed_body is not None
            else b"bytes-of-" + source_url.encode()
        )
        return FetchedEntry(content=body, digest="sha256:stub", from_store=False)

    def resolve(
        self,
        ctx: Any,
        *,
        entry: dict[str, Any],
        category: str = "resources_file",
        entry_identity: str | None = None,
    ):
        """The declared-source front door, in the double.

        Mirrors the real dispatch closely enough to be worth trusting: a
        ``from`` name or an inline ``source`` object resolves through
        ``sources`` on the apply context, and a git protocol answers with a
        tree rather than bytes — which is the branch the materialiser has to
        get right. A ``source`` that is not a declaration object is refused
        here exactly as the real door refuses it, so the double cannot serve
        a spelling production no longer accepts.
        """
        if getattr(self, "declared_override", None) is not None:
            return BlobDelivery(self.declared_override)
        decl: dict[str, Any] | None = None
        if isinstance(entry.get("from"), str):
            sources = getattr(ctx, "source_session", None)
            declared = getattr(sources, "sources", {}) if sources else {}
            decl = dict(declared.get(entry["from"], {}))
            if not decl:
                raise EntryFetchError(
                    f"'from' names source {entry['from']!r}, which is not "
                    "declared under 'sources'"
                )
        elif isinstance(entry.get("source"), dict):
            decl = dict(entry["source"])
        else:
            raise EntryFetchError(
                "an entry must name one of 'from', 'source' or 'content', "
                "and 'source' is a declaration object carrying a 'protocol'"
            )

        if decl is not None and decl.get("protocol") == "git":
            key = decl["url"]
            tree = self.git_trees.get(key)
            if tree is None:
                raise EntryFetchError("git fetch failed")
            self.calls.append(
                {
                    "source_url": key,
                    "category": category,
                    "entry_identity": entry_identity,
                    "subpath": _compose(decl.get("subpath"), entry.get("subpath")),
                    "auth": decl.get("auth"),
                }
            )
            return GitDelivery(
                _stub_git_source(
                    tree,
                    subpath=_compose(decl.get("subpath"), entry.get("subpath")),
                    auth=decl.get("auth"),
                    url=key,
                    moved=self.moved_from,
                ),
                self.store,
            )

        url = decl.get("url") or f"oss://{decl.get('bucket')}/{decl.get('key')}"
        return BlobDelivery(
            self.fetch(
                ctx,
                source_url=url,
                digest=entry.get("digest"),
                auth=(decl or {}).get("auth", entry.get("auth")),
                category=category,
                keep_last=(entry.get("on_fetch_failure", "keep_last") == "keep_last"),
                entry_identity=entry_identity,
            )
        )


def _compose(source_subpath: str | None, entry_subpath: str | None) -> str | None:
    """The composition ``DeclaredSourceResolver`` performs, so the double addresses the
    same tree the real fetcher would."""
    if entry_subpath is None:
        return source_subpath
    if not source_subpath:
        return entry_subpath
    return source_subpath.rstrip("/") + "/" + entry_subpath.lstrip("/")


class _StubCheckout:
    """A ``GitCheckout``'s shape where a consumer reaches it.

    ``files``/``read_file`` take the subpath and the per-entry byte cap the way
    the guarded readers do, and refuse in the transport's own currency
    (``FetchRefusedError``) so ``GitEntrySource`` performs the translation it
    performs in production rather than the double doing it for it.
    """

    def __init__(self, members: dict[str, bytes], sha: str = "b" * 40) -> None:
        self._members = members
        self.sha = sha
        self.root = None
        self.tree_bytes = sum(len(v) for v in members.values())

    def files(self, subpath=None, *, file_limit=None):
        if not subpath:
            return sorted(self._members.items())
        prefix = subpath.rstrip("/") + "/"
        found = sorted(
            (name[len(prefix) :], data)
            for name, data in self._members.items()
            if name.startswith(prefix)
        )
        if not found:
            raise FetchRefusedError(f"subpath {subpath!r} selects nothing")
        return found

    def read_file(self, subpath=None, *, file_limit=None):
        try:
            return self._members[subpath]
        except KeyError:
            raise FetchRefusedError(
                f"subpath {subpath!r} is not a file in the tree"
            ) from None


def _stub_git_source(members, *, subpath, auth, url, moved=None):
    """A **real** :class:`GitEntrySource` over a fake checkout.

    Not a look-alike: the materialiser dispatches on the type, exactly as the
    identity and skills materialisers do, and a duck-typed double would let a
    change to that dispatch pass its own tests while failing in production.
    """
    return GitEntrySource(
        checkout=_StubCheckout(members),
        source_url=url,
        subpath=subpath,
        moved_from=moved,
        auth=auth,
    )


def _tgz(member: dict[str, bytes]) -> bytes:
    """A real tar.gz carrying ``member`` — true bytes, not synthetic objects."""
    return build_skill_tgz(list(member.items()))


def _write_through(m, ctx, entries):
    resolved = _run(m.resolve(ctx, entries))
    assert resolved.ok, [f.reason for f in resolved.failures]
    plan = _run(m.plan(ctx, resolved.intents))
    return plan, _run(m.write(ctx, plan)).entries


def _resources_engine(svc, fetcher, *, resource_rows=None):
    """An orchestrator over the real registry and the real compiler, resources wired to the rig.

    One content store for both halves: the compiler files every entry's
    bytes there, and the materialiser reads them back by digest.
    """
    content = FakeManifestContent()
    orchestrator = ApplyOrchestrator(
        build_materialisers(
            script_service=FakeStartupScriptService(),
            activation_service=FakeActivationService(),
            mcp_auth_service=FakeMcpAuth(),
            mcp_config_service=object(),
            identity_service=FakeIdentityService(),
            upload_service=FakeSkillUploadService(),
            capability_reader=FakeCapabilityReader(),
            package_validator=real_validator(),
            content=content,
            resource_service=svc,
            resource_rows=resource_rows or resource_rows_for()[0],
            cli_tool_service=EmptyCliTools(),
            shared_skills=object(),
        ),
        steps=_arca_steps,
    )
    return EngineRig(orchestrator, manifest_compiler(content=content, resolver=fetcher))


_DOCUMENT = {
    "schema_version": 1,
    "manifest": {
        "resources": [
            {
                "path": "data/wrap/",
                "unpack": "tar.gz",
                "source": _src("t.tgz"),
            },
            {"path": "data/notes/r.md", "content": "# rules"},
        ]
    },
}


def _resources_category(report):
    return next(c for c in report.categories if c.construct.value == "resources")


_REPO = "https://code.example.com/team/content.git"


_TREE = {
    "kb/faq.csv": b"q,a\n",
    "kb/pricing.csv": b"sku,price\n",
    "kb/deep/nested/notes.md": b"# nested\n",
    "elsewhere/ignore.md": b"not selected\n",
}


def _git_session(*, subpath=None, auth=None):
    """A source session declaring one git source named ``content``.

    It rides on the compiler's session (not the apply context, which no
    longer carries one), so it is handed to the rig rather than the call.
    """
    decl = {"protocol": "git", "url": _REPO, "ref": "v1.2.0"}
    if subpath is not None:
        decl["subpath"] = subpath
    if auth is not None:
        decl["auth"] = auth
    return declared_session(sources={"content": decl})


def _git_rig(svc, stub, *, subpath=None, auth=None):
    """The materialiser over ``stub``, resolving ``from: content`` through :func:`_git_session`."""
    return _materialiser(
        svc, stub, source_session=_git_session(subpath=subpath, auth=auth)
    )


def _ctx():
    return make_context(engine_type="claude_code")
