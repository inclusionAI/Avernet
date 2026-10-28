"""Bounded snapshot restore and incremental apply extracted from TC persistence."""


def apply_snapshot_pages(pages, batch_size, upsert, stale_ids):
    """Apply source pages; caller owns index locking, cleanup and readiness."""
    count = 0
    for points in pages:
        if not points or len(points) > batch_size:
            raise ValueError("persistence backend yielded an invalid vector page")
        upsert(points)
        stale_ids.difference_update(point.id for point in points)
        count += len(points)
    return count


def apply_change_pages(pages, checkpoint, upsert, delete, advance):
    """Advance only after a whole page succeeds, making failed pages replayable."""
    upserted = deleted = 0
    for changes in pages:
        if changes.upserts:
            upsert(changes.upserts)
            upserted += len(changes.upserts)
        if changes.deleted_ids:
            delete(changes.deleted_ids)
            deleted += len(changes.deleted_ids)
        checkpoint = max(checkpoint, changes.checkpoint)
        advance(checkpoint)
    return {"upserted": upserted, "deleted": deleted}
