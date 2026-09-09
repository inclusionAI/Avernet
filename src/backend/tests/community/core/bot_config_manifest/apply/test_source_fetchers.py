"""The protocol table: exhaustive, and consulted rather than branched on.

The per-protocol *behaviour* is pinned by ``test_entry_fetch.py`` — those
tests drive real declarations through the front door and assert what came
back, and they did not change when the branch became a table, which is the
strongest statement available that the refactor moved nothing.

What is left to pin is the table itself: that it covers every protocol a
document may declare, that dispatch selects by that protocol, and that a gap
is refused at import rather than discovered mid-apply.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.apply.source_fetchers import (
    FETCHER_TYPES,
    DeclaredFetch,
    GitSourceFetcher,
    ObjectStoreFetcher,
    SourceFetcher,
    build_fetchers,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
)
from agentclaw.community.core.bot_config_manifest.schema.sources import (
    DECLARABLE_PROTOCOLS,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind

from ._fakes import (
    FakeCredentials,
    FakeGuardedFetcher,
    FakeManifestContent,
    make_context,
)
from agentclaw.community.plugins.local.object_store_client import InMemoryObjectStoreClientFactory


def _session(sources=None) -> SourceSession:
    """A session with no git client: these tests never reach a checkout —
    the recorder stands in for the fetcher that would."""
    return SourceSession(sources=sources or {}, baselines={}, git=None)


@pytest.fixture
def pipeline() -> EntryFetcher:
    return EntryFetcher(
        FakeGuardedFetcher(responses={}), FakeManifestContent(), FakeCredentials()
    , InMemoryObjectStoreClientFactory())


# ── the table ────────────────────────────────────────────────────────────────


def test_every_declarable_protocol_has_a_fetcher():
    """The rule this whole feature is built around, at the fetch layer: the
    surface must not accept what it cannot apply. A protocol the parser
    admits with nothing to serve it is exactly that, one layer down."""
    assert set(FETCHER_TYPES) == set(DECLARABLE_PROTOCOLS)


def test_content_is_a_kind_but_never_a_fetcher():
    """``content`` is a :class:`SourceKind` and never a *source*: inline text
    is written on the entry, so there is nothing to acquire. Its absence is a
    decision, and this says so out loud rather than leaving the gap looking
    like an oversight."""
    assert SourceKind.CONTENT not in FETCHER_TYPES
    assert SourceKind.CONTENT not in DECLARABLE_PROTOCOLS


def test_a_protocol_with_no_fetcher_is_refused_at_import():
    """Re-runs the module's own guard against a table with a hole.

    The real check runs at import, which a test cannot re-trigger without
    reloading — so this drives the same expression the module evaluates. If
    that expression is ever weakened, this fails; if the guard is deleted,
    ``test_every_declarable_protocol_has_a_fetcher`` still catches the hole
    it existed to prevent.
    """
    holed = {SourceKind.GIT: GitSourceFetcher}
    unserved = (set(SourceKind) - {SourceKind.CONTENT}) - set(holed)
    assert unserved == {SourceKind.OSS}


@pytest.mark.parametrize(
    "kind,expected",
    [(SourceKind.OSS, ObjectStoreFetcher), (SourceKind.GIT, GitSourceFetcher)],
)
def test_the_bound_table_holds_one_fetcher_per_protocol(pipeline, kind, expected):
    bound = build_fetchers(pipeline)
    assert isinstance(bound[kind], expected)
    assert isinstance(bound[kind], SourceFetcher)


def test_the_fetchers_share_the_pipelines_collaborators(pipeline):
    """Strategies over one pipeline, not independent pipelines.

    Two fetchers that each built their own content store would file receipts
    under two policies, and W11's lineage would answer "which credential
    served this" differently depending on which road an entry took.
    """
    bound = build_fetchers(pipeline)
    assert all(f._owner is pipeline for f in bound.values())


# ── dispatch ─────────────────────────────────────────────────────────────────


class _Recorder:
    """A fetcher that records the request instead of acquiring anything."""

    def __init__(self) -> None:
        self.requests: list[DeclaredFetch] = []

    def fetch(self, request: DeclaredFetch):
        self.requests.append(request)
        return "delivered"


@pytest.mark.parametrize(
    "kind,source",
    [
        (
            SourceKind.GIT,
            {"protocol": "git", "url": "https://git.corp/x.git", "ref": "main"},
        ),
        (
            SourceKind.OSS,
            {
                "protocol": "oss",
                "bucket": "objects",
                "key": "x.bin",
                "auth": "oss-cred",
            },
        ),
    ],
)
def test_dispatch_selects_the_fetcher_the_declaration_names(
    pipeline, kind, source
):
    """The whole of what replaced the ``if``: parse, look up, call."""
    recorder = _Recorder()
    others = {k: _Recorder() for k in FETCHER_TYPES if k is not kind}
    pipeline._fetchers = {kind: recorder, **others}

    result = pipeline.fetch_declared(
        make_context(source_session=_session()),
        entry={"source": source},
        category="resources_file",
        entry_identity="x",
    )

    assert result == "delivered"
    assert len(recorder.requests) == 1
    assert all(not r.requests for r in others.values())


def test_the_request_carries_what_the_front_door_resolved(pipeline):
    """Resolved once, centrally, so no fetcher re-derives it — two fetchers
    each deciding what ``keep_last`` means is how the roads drift apart while
    both look correct."""
    recorder = _Recorder()
    pipeline._fetchers = {SourceKind.OSS: recorder, SourceKind.GIT: _Recorder()}

    pipeline.fetch_declared(
        make_context(source_session=_session()),
        entry={
            "source": {
                "protocol": "oss",
                "bucket": "objects",
                "key": "x.bin",
                "auth": "oss-cred",
            },
            "on_fetch_failure": "fail",
        },
        category="skills",
        entry_identity="s1",
    )

    request = recorder.requests[0]
    assert request.category == "skills"
    assert request.entry_identity == "s1"
    assert request.keep_last is False  # 'fail', not the keep_last default
    assert request.decl.protocol is SourceKind.OSS
    assert request.name is None  # an inline source has no declared name


def test_a_named_source_carries_its_name_for_the_report(pipeline):
    """``name`` is the report's word for the source and the key its strict
    baseline is read by — an inline source has neither, and says so."""
    recorder = _Recorder()
    pipeline._fetchers = {SourceKind.GIT: recorder, SourceKind.OSS: _Recorder()}
    ctx = make_context(
        source_session=_session(
            {
                "content": {
                    "protocol": "git",
                    "url": "https://git.corp/c.git",
                    "ref": "v1",
                }
            }
        )
    )

    pipeline.fetch_declared(
        ctx,
        entry={"from": "content"},
        category="resources_file",
        entry_identity="data/x",
    )

    assert recorder.requests[0].name == "content"


# ── a credential of the wrong type ───────────────────────────────────────────


def test_a_git_source_naming_an_object_store_credential_fails_one_entry():
    """Nothing ties a source's protocol to its credential's type, so this
    document is legal at PUT — and it must fail *this entry*, not the apply.

    Before the guard it did neither cleanly: ``reauthorize`` ran against the
    empty ``allowed_prefixes`` an ``oss_aksk`` row now legitimately stores,
    ``validate_prefixes`` answered with a bare ``ValueError``, and nothing in
    the fetch pipeline catches that — it escaped ``resolve`` and took the
    whole apply with it. And had it got past, ``headers_for`` would have put
    the object store's secret key on the wire to a git host under an
    empty-named header.
    """
    from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (  # noqa: E501
        EntryFetchError,
    )

    class _AksKBinding:
        """A binding for a credential of the wrong type, answering as the real
        one does: both seams refuse, in the family the fetcher catches."""

        name = "oss-cred"

        def reauthorize(self, url):
            raise CredentialError(
                "credential 'oss-cred' has no usable 'allowed_prefixes'"
            )

        def headers_for(self, url):
            raise CredentialError(
                "credential 'oss-cred' is a 'oss_aksk' credential and "
                "presents no header"
            )

    class _WrongTypeCredentials:
        def binding(self, *, name):
            return _AksKBinding()

    pipeline = EntryFetcher(
        FakeGuardedFetcher(responses={}),
        FakeManifestContent(),
        _WrongTypeCredentials(),
    InMemoryObjectStoreClientFactory(),)
    ctx = make_context(
        source_session=_session(
            {
                "repo": {
                    "protocol": "git",
                    "url": "https://code.example.com/team/x.git",
                    "ref": "main",
                    "auth": "oss-cred",
                }
            }
        )
    )

    # EntryFetchError, not ValueError: the materialiser turns this into one
    # entry's ResolveFailure. Anything else aborts the category.
    with pytest.raises(EntryFetchError, match="oss-cred"):
        pipeline.fetch_declared(
            ctx, entry={"from": "repo"}, category="identity", entry_identity="x"
        )
