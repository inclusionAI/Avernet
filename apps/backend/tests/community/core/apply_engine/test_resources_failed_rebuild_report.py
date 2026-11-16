"""A failed replacement cannot hide a directory the resource clear removed."""

from datetime import datetime

import pytest

from agentclaw.community.core.apply_engine.constructs import Category
from agentclaw.community.core.apply_engine.desired_state import (
    ContentRef,
    Declaration,
    DesiredResourceFile,
    DesiredState,
)
from agentclaw.community.core.apply_engine.materialisers.resources import (
    ResourcesMaterialiser,
)
from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
from agentclaw.community.core.apply_engine.order import ApplyStep
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus, EntryOutcome
from agentclaw.community.core.ports.resource_file_port import WorkspaceEntry

from ._fakes import FakeManifestContent, FakeResourceRows, make_context


class _WorkspaceWithFailedChildUpload:
    """Filesystem boundary: the clear succeeds, then uploading the child fails."""

    def __init__(self):
        self.directories = {"lost"}

    async def list_entries(self, *, prefix="", **kwargs):
        return tuple(
            WorkspaceEntry(path, True) for path in self.directories if not prefix
        )

    async def delete_dir(self, *, path, **kwargs):
        self.directories.remove(path)
        return True

    async def upload_file(self, **kwargs):
        raise RuntimeError("resource transport unavailable")


@pytest.mark.asyncio
async def test_failed_child_upload_reports_the_confirmed_parent_removal_and_partial_status():
    workspace = _WorkspaceWithFailedChildUpload()
    rows = FakeResourceRows()
    content = FakeManifestContent()
    digest = "sha256:" + "ab" * 32
    content.blobs[digest] = b"new child"
    materialiser = ResourcesMaterialiser(workspace, content, lambda bot_id: rows)
    engine = ApplyOrchestrator(
        {Category.RESOURCES: materialiser},
        steps=lambda phase: (ApplyStep(Category.RESOURCES, 0),),
    )
    state = DesiredState(
        resources=Declaration(
            (DesiredResourceFile("lost/new.txt", ContentRef(digest)),)
        )
    )
    ctx = make_context(engine_type="openclaw")
    preview = await engine.apply(
        ctx,
        state,
        apply_id="preview",
        trigger="explicit",
        started_at=datetime.now(),
        dry_run=True,
    )
    assert preview.categories[0].removals == ()
    assert workspace.directories == {"lost"}
    assert rows.replacements == []

    report = await engine.apply(
        ctx, state, apply_id="actual", trigger="explicit", started_at=datetime.now()
    )
    assert workspace.directories == set()
    assert report.categories[0].removals == ("lost/",)
    assert report.entries[0].outcome is EntryOutcome.FAILED
    assert report.entries[0].reason == "resource delivery failed"
    assert report.status is ApplyStatus.PARTIAL
    assert rows.rows == []
