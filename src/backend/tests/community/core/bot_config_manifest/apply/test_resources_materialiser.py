"""Tests for the ``resources`` materialiser (W6).

Pins, by the work item's acceptance criteria:
- file entries materialise through ``ResourceFileService`` at a
  workspace-relative ``path`` (physical placement stays the engine's call);
- directory entries converge as the whole archive: the tree under ``path`` is
  replaced in full, including hand-added files (ownership rule);
- fetch failures abort the whole category before the first write (§3.2);
- the platform unpacks to a temporary location — a bad archive or failed fetch
  never reaches the bot;
- convergence writes are file-grained (teclaw per-file forwarding is the same
  transport), and the module never touches ``BotConfigArtifact``;
- archive limits (member count / unpacked size) apply with W1's keys.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
    FetchedEntry,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers.resources import (
    ResourcesMaterialiser,
)

from ._fakes import FakeResourceFileService, make_context


def _run(coro):
    return asyncio.run(coro)


class _StubEntryFetcher:
    """``EntryFetcher``'s test double: fixed bytes per URL, every call recorded.

    URLs ending in ``gone`` stand in for unreachable sources. It answers in
    the pipeline's own currency — a real :class:`FetchedEntry` — because the
    materialiser consumes ``.content``; a stub answering in the transport's
    ``FetchedObject`` shape would pass collection and fail on the first real
    resolve.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
        body = b"bytes-of-" + source_url.encode()
        return FetchedEntry(content=body, digest="sha256:stub", from_store=False)


def test_fake_resource_service_records_uploads_and_deletes():
    svc = FakeResourceFileService(exists_paths={"data/old.bin"})
    ctx = make_context(engine_type="claude_code")
    _run(
        svc.upload_file(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine_type,
            target_dir="data",
            filename="a.bin",
            data=b"hello",
        )
    )
    _run(
        svc.delete(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine_type,
            path="data/old.bin",
        )
    )
    assert svc.writes == {("data", "a.bin"): b"hello"}
    assert svc.deleted == ["data/old.bin"]
    # Deleted paths report absent — the fake must contract this, because the
    # plan stage's classification reads ``exists`` and would otherwise call
    # a deleted file "unchanged".
    assert (
        _run(
            svc.exists(
                entity_id=ctx.entity_id,
                bot_id=ctx.bot_id,
                engine_type=ctx.engine_type,
                path="data/old.bin",
            )
        )
        is False
    )


# --- file entries: URL fetch and inline content (Task 2) ---


def test_file_entry_from_url_resolves_to_intent_bytes():
    svc = FakeResourceFileService()
    stub = _StubEntryFetcher()
    m = ResourcesMaterialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(ctx, [{"path": "data/a.bin", "source": "https://x/a.bin"}])
    )
    assert resolved.ok
    assert [i.identity for i in resolved.intents] == ["data/a.bin"]
    assert resolved.intents[0].value == b"bytes-of-https://x/a.bin"
    # The funnel saw the entry's own identity under the resources category —
    # the W11 apply/entry linkage — with keep_last as the declared default.
    assert stub.calls == [
        {
            "source_url": "https://x/a.bin",
            "digest": None,
            "auth": None,
            "category": "resources",
            "keep_last": True,
            "entry_identity": "data/a.bin",
        }
    ]


def test_file_entry_inline_content_never_fetches():
    svc = FakeResourceFileService()
    stub = _StubEntryFetcher()
    m = ResourcesMaterialiser(svc, stub)
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": "notes/r.md", "content": "# rules"}]))
    assert resolved.ok
    assert [i.identity for i in resolved.intents] == ["notes/r.md"]
    assert resolved.intents[0].value == b"# rules"
    assert stub.calls == []  # inline content: no fetch, ever


def test_fetch_failure_aborts_whole_category():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(
        m.resolve(
            ctx,
            [
                {"path": "data/a.bin", "source": "https://x/a.bin"},
                {"path": "data/gone.bin", "source": "https://x/gone"},
            ],
        )
    )
    assert not resolved.ok
    assert [f.identity for f in resolved.failures] == ["data/gone.bin"]


def test_bad_path_entry_is_a_resolve_failure():
    svc = FakeResourceFileService()
    m = ResourcesMaterialiser(svc, _StubEntryFetcher())
    ctx = make_context(engine_type="claude_code")
    resolved = _run(m.resolve(ctx, [{"path": 123}]))
    assert not resolved.ok
