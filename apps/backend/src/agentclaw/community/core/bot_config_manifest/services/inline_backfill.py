"""Backfill of Manifest inline content into the content store (transitional).

Rows in ``ac_manifest_content`` filed while the store kept its bytes on each
backend instance's own disk have no object behind them now that the bytes
live in object storage. For content the platform fetched, the next apply
fetches again. Inline content has no source to fetch from: its only copy is
the bot's stored Manifest document. This service re-reads that document and
stores every inline body it declares, so an operator can repair a row
without waiting for (or forcing) an apply.

Manifest only. The Genome Registry's rows are repaired by re-capturing the
bot. Remove this module, with ``ContentStoreProtocol.ensure_stored``, once
the existing rows have been backfilled.
"""
from __future__ import annotations

from typing import Any, Iterator, Mapping

import yaml

from agentclaw.community.core.apply_engine.constructs import Category
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.compiler.declared import declared_entries
from agentclaw.community.core.bot_config_manifest.inline_backfill_protocol import (
    InlineBackfillEntry,
    InlineBackfillReport,
    InlineBackfillRowNotFound,
    ManifestInlineBackfillProtocol,
)
from agentclaw.community.core.content_store import ContentScope, ContentStoreProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: The ``produced://`` label every Manifest-compiler row carries (``manifest/inline``, ``manifest/tree``, …).
_MANIFEST_PRODUCER = "manifest"

#: The two categories whose entries may carry an inline ``content`` body, with the key that names the entry.
_INLINE_CATEGORIES = ((Category.IDENTITY, "type"), (Category.RESOURCES, "path"))


def inline_bodies(parsed: Mapping[str, Any]) -> Iterator[tuple[str, str, bytes]]:
    """Every inline body a parsed Manifest declares, as ``(category, identity, bytes)``.

    The bytes are the UTF-8 encoding of the ``content`` string, exactly
    what the compiler hashes and files for the entry::

        {"manifest": {"identity": [{"type": "SOUL.md", "content": "# Soul\\n"}]}}
        # -> ("identity", "SOUL.md", b"# Soul\\n")
    """
    for category, name_key in _INLINE_CATEGORIES:
        for entry in declared_entries(parsed, category) or ():
            if not isinstance(entry, Mapping):
                continue
            body, name = entry.get("content"), entry.get(name_key)
            if isinstance(body, str) and isinstance(name, str) and name:
                yield category.value, name, body.encode("utf-8")


class ManifestInlineBackfillService(ManifestInlineBackfillProtocol):
    """Reads the bot's stored Manifest and stores its inline bodies."""

    def __init__(self, manifests: BotConfigManifestServiceProtocol, content: ContentStoreProtocol) -> None:
        self._manifests = manifests
        self._content = content

    def backfill(self, *, entity_id: str, bot_id: str, digest: str) -> InlineBackfillReport:
        scope = ContentScope(env=get_current_env(), entity_id=entity_id, bot_id=bot_id)
        if not self._content.is_reachable(scope, digest, produced_by=_MANIFEST_PRODUCER):
            raise InlineBackfillRowNotFound(
                f"no Manifest content row for bot {bot_id!r} and digest {digest} in env {scope.env!r}"
            )
        record = self._manifests.get(entity_id=entity_id, bot_id=bot_id)
        parsed = yaml.safe_load(record.document) if record is not None else None
        entries: list[InlineBackfillEntry] = []
        if isinstance(parsed, Mapping):
            for category, identity, body in inline_bodies(parsed):
                stored = self._content.ensure_stored(body)
                entries.append(InlineBackfillEntry(category, identity, stored, len(body)))
        report = InlineBackfillReport(
            entity_id=entity_id,
            bot_id=bot_id,
            digest=digest,
            document_found=record is not None,
            row_digest_restored=any(entry.digest == digest for entry in entries),
            entries=tuple(entries),
        )
        logger.info(
            "[manifest.inline_backfill] bot_id=%s digest=%s document_found=%s restored=%s entries=%d",
            bot_id,
            digest,
            report.document_found,
            report.row_digest_restored,
            len(entries),
        )
        return report
