"""``file_produced`` puts back bytes whose provenance row outlived the blob."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_config_manifest.compiler.entries._fetch import (
    ORIGIN_INLINE,
    EntryFetchError,
    file_produced,
)
from agentclaw.community.core.content_store.errors import ContentMissingError, ContentStoreFault
from agentclaw.community.core.content_store.models import ContentScope
from agentclaw.community.core.content_store.service import ContentStoreService
from agentclaw.community.testing.content_blob_store import LocalBlobStore

SCOPE = ContentScope(env="dev", entity_id="ent_a", bot_id="bot_1")
SESSION = SimpleNamespace(scope=SCOPE, actor_id="user_1")
BODY = b"# Soul\nYou are the support agent.\n"


class _Rows:
    """In-memory provenance log with the two calls ``store_produced`` and ``is_reachable`` make."""

    def __init__(self) -> None:
        self.rows: list = []

    def add(self, record):
        self.rows.append(record)
        return record

    def has_digest(self, *, env, entity_id, bot_id, digest, source_url_prefix=None):
        return any(
            (r.env, r.entity_id, r.bot_id, r.digest) == (env, entity_id, bot_id, digest)
            and (source_url_prefix is None or r.source_url.startswith(source_url_prefix))
            for r in self.rows
        )


def _file(content) -> str:
    return asyncio.run(
        file_produced(SESSION, content, BODY, origin=ORIGIN_INLINE, category="identity", identity="SOUL.md")
    )


def test_file_produced_restores_missing_bytes_when_row_exists(tmp_path):
    """The apply that runs on an instance that never held the blob: the row exists, the bytes do not."""
    rows = _Rows()
    first = ContentStoreService(rows, LocalBlobStore(tmp_path / "pod-a"))
    digest = _file(first)
    assert len(rows.rows) == 1

    second = ContentStoreService(rows, LocalBlobStore(tmp_path / "pod-b"))
    with pytest.raises(ContentMissingError):
        second.read(digest)

    assert _file(second) == digest
    assert second.read(digest) == BODY
    assert len(rows.rows) == 1


def test_a_store_fault_while_restoring_is_the_entrys_failure(tmp_path):
    rows = _Rows()
    content = ContentStoreService(rows, LocalBlobStore(tmp_path / "pod-a"))
    _file(content)

    def _fault(data):
        raise ContentStoreFault("the object storage did not store the blob")

    content.ensure_stored = _fault
    with pytest.raises(EntryFetchError, match="could not be filed"):
        _file(content)


def test_a_local_write_failure_while_restoring_is_the_entrys_failure(tmp_path):
    rows = _Rows()
    content = ContentStoreService(rows, LocalBlobStore(tmp_path / "pod-a"))
    _file(content)

    def _disk_full(data):
        raise OSError(28, "No space left on device")

    content.ensure_stored = _disk_full
    with pytest.raises(EntryFetchError, match="could not be filed"):
        _file(content)
