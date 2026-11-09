"""The Manifest inline backfill: re-read the stored document, store its inline bodies."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
import yaml

from agentclaw.community.core.bot_config_manifest.inline_backfill_protocol import InlineBackfillRowNotFound
from agentclaw.community.core.bot_config_manifest.services.inline_backfill import (
    ManifestInlineBackfillService,
    inline_bodies,
)
from agentclaw.community.core.content_store.errors import ContentMissingError, ContentStoreError
from agentclaw.community.core.content_store.models import ContentScope
from agentclaw.community.core.content_store.service import ContentStoreService
from agentclaw.community.testing.content_blob_store import LocalBlobStore
from agentclaw.community.utils.env_utils import get_current_env

SOUL = "# Soul\nYou are the support agent.\n"
NOTES = "refund policy: 30 days\n"
DOCUMENT = yaml.safe_dump(
    {
        "schema_version": 1,
        "manifest": {
            "identity": [
                {"type": "SOUL.md", "content": SOUL},
                {"type": "SAFETY.md", "from": "content", "subpath": "bots/safety.md"},
            ],
            "resources": [
                {"path": "docs/notes.md", "content": NOTES},
                {"path": "data/", "from": "content"},
            ],
        },
    }
)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Rows:
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


class _Manifests:
    def __init__(self, document: str | None) -> None:
        self.document = document

    def get(self, *, entity_id, bot_id):
        return None if self.document is None else SimpleNamespace(document=self.document)


def _world(tmp_path, document: str | None = DOCUMENT):
    """A bot whose inline SOUL.md row was filed on another instance: the row exists, the bytes do not."""
    rows = _Rows()
    scope = ContentScope(env=get_current_env(), entity_id="ent_a", bot_id="bot_1")
    ContentStoreService(rows, LocalBlobStore(tmp_path / "elsewhere")).store_produced(
        SOUL.encode("utf-8"), scope=scope, origin="manifest/inline"
    )
    content = ContentStoreService(rows, LocalBlobStore(tmp_path / "here"))
    return ManifestInlineBackfillService(_Manifests(document), content), content, rows


def test_inline_bodies_lists_identity_and_file_resources_only():
    assert list(inline_bodies(yaml.safe_load(DOCUMENT))) == [
        ("identity", "SOUL.md", SOUL.encode("utf-8")),
        ("resources", "docs/notes.md", NOTES.encode("utf-8")),
    ]
    assert list(inline_bodies({})) == []
    assert list(inline_bodies({"manifest": {"identity": None, "resources": ["not-a-mapping"]}})) == []


def test_backfill_stores_every_inline_body_and_files_no_row(tmp_path):
    service, content, rows = _world(tmp_path)
    with pytest.raises(ContentMissingError):
        content.read(_digest(SOUL))

    report = service.backfill(entity_id="ent_a", bot_id="bot_1", digest=_digest(SOUL))

    assert report.document_found and report.row_digest_restored
    assert [(e.category, e.identity, e.digest, e.size_bytes) for e in report.entries] == [
        ("identity", "SOUL.md", _digest(SOUL), len(SOUL.encode("utf-8"))),
        ("resources", "docs/notes.md", _digest(NOTES), len(NOTES.encode("utf-8"))),
    ]
    assert content.read(_digest(SOUL)) == SOUL.encode("utf-8")
    assert content.read(_digest(NOTES)) == NOTES.encode("utf-8")
    assert len(rows.rows) == 1
    # Idempotent.
    assert service.backfill(entity_id="ent_a", bot_id="bot_1", digest=_digest(SOUL)) == report


def test_backfill_reports_a_row_the_document_no_longer_declares(tmp_path):
    service, content, _ = _world(tmp_path, document="schema_version: 1\nmanifest:\n  identity: []\n")
    report = service.backfill(entity_id="ent_a", bot_id="bot_1", digest=_digest(SOUL))
    assert report.document_found and not report.row_digest_restored and report.entries == ()
    with pytest.raises(ContentMissingError):
        content.read(_digest(SOUL))


def test_backfill_reports_a_bot_with_no_stored_manifest(tmp_path):
    service, _, _ = _world(tmp_path, document=None)
    report = service.backfill(entity_id="ent_a", bot_id="bot_1", digest=_digest(SOUL))
    assert not report.document_found and not report.row_digest_restored and report.entries == ()


def test_backfill_refuses_a_row_this_bot_does_not_hold(tmp_path):
    service, _, rows = _world(tmp_path)
    with pytest.raises(InlineBackfillRowNotFound):
        service.backfill(entity_id="ent_a", bot_id="bot_other", digest=_digest(SOUL))
    with pytest.raises(InlineBackfillRowNotFound):
        service.backfill(entity_id="ent_a", bot_id="bot_1", digest=_digest("never stored"))
    # A genome row for the same digest is not a Manifest row.
    rows.rows[0] = rows.rows[0].model_copy(update={"source_url": "produced://genome/capture"})
    with pytest.raises(InlineBackfillRowNotFound):
        service.backfill(entity_id="ent_a", bot_id="bot_1", digest=_digest(SOUL))


def test_backfill_refuses_a_malformed_digest(tmp_path):
    service, _, _ = _world(tmp_path)
    with pytest.raises(ContentStoreError):
        service.backfill(entity_id="ent_a", bot_id="bot_1", digest="md5:abc")
