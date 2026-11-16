"""Explicit source repair using the existing lease and atomic Doc publisher."""

import json
import logging
from time import monotonic
from uuid import uuid4

from search_core.api.index_builds import IndexBuildConflict
from search_core.core.preparation_lease import PreparationLease

logger = logging.getLogger(__name__)


def rebuild_source(
    repository,
    source_id,
    *,
    index_key,
    read_source,
    prepare_documents,
    lease_seconds=60.0,
):
    # Never read the historical vector cache: it may be the artifact being repaired.
    row = read_source(source_id)
    if row is None:
        raise KeyError(source_id)
    token = uuid4().hex
    if not repository.claim_preparation(
        source_id, row.revision, index_key, token, lease_seconds, force=True
    ):
        raise IndexBuildConflict("Source changed or preparation is already running")
    started, outcome, published = monotonic(), "failed", False
    try:
        with PreparationLease(
            repository, source_id, row.revision, index_key, token, lease_seconds
        ) as lease:
            documents = prepare_documents(row)
            if lease.lost.is_set() or not repository.publish_documents(
                documents, index_key, token
            ):
                outcome = "conflict"
                raise IndexBuildConflict("Source changed or preparation lease expired")
            published, outcome = True, "published"
        return {
            "published": True,
            "index_key": index_key,
            "saved_revision": row.revision,
        }
    finally:
        if not published:
            repository.release_preparation(
                source_id, row.revision, index_key, token, "REBUILD_FAILED"
            )
        logger.log(
            logging.INFO if published else logging.WARNING,
            json.dumps(
                {
                    "event": "search_source_rebuild",
                    "source_id": source_id,
                    "index_key": index_key,
                    "source_revision": row.revision,
                    "outcome": outcome,
                    "duration_ms": round((monotonic() - started) * 1000),
                }
            ),
        )
