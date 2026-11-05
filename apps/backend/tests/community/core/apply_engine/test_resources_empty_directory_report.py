"""Empty workspace directories count as confirmed Resources removals."""
from datetime import datetime

import pytest

from agentclaw.community.core.ports.resource_file_port import WorkspaceEntry

from ._fakes import FakeResourceFileService, make_context
from .test_resources_materialiser import (
    _StubDeclaredSourceResolver,
    _resources_category,
    _resources_engine,
    _run,
)


class _WorkspaceWithEmptyDirectories(FakeResourceFileService):
    def __init__(self):
        super().__init__()
        self.directories = {"data", "data/empty", "skills", "skills/keep"}

    async def list_entries(self, *, prefix="", recursive=False, **kwargs):
        files = await super().list_entries(prefix=prefix, recursive=recursive, **kwargs)
        directories = (
            WorkspaceEntry(path, True)
            for path in sorted(self.directories)
            if (not prefix or path.startswith(f"{prefix}/")) and (recursive or "/" not in path)
        )
        return (*files, *directories)

    async def delete_dir(self, *, path, **kwargs):
        deleted = await super().delete_dir(path=path, **kwargs)
        if deleted:
            self.directories = {
                directory for directory in self.directories
                if directory != path and not directory.startswith(f"{path}/")
            }
        return deleted


def test_empty_directories_are_previewed_and_confirmed_even_when_a_skill_fails():
    workspace = _WorkspaceWithEmptyDirectories()
    engine = _resources_engine(workspace, _StubDeclaredSourceResolver())
    ctx = make_context(engine_type="claude_code")
    doc = {
        "schema_version": 1,
        "manifest": {
            "resources": [],
            "skills": [{"name": "missing", "source": {"protocol": "oss", "url": "https://example.com/missing.tgz"}}],
        },
    }
    for dry_run in (True, False):
        report = _run(engine.apply(
            ctx, doc, apply_id=f"a{dry_run}", trigger="explicit",
            started_at=datetime.now(), dry_run=dry_run,
        ))
        assert _resources_category(report).as_dict()["removed"] == ["data/", "data/empty/"]
        assert report.status.value == "PARTIAL"
        assert any(entry.construct.value == "capabilities" and entry.outcome.value == "failed" for entry in report.entries)
        assert ("data/empty" in workspace.directories) is dry_run
        assert "skills/keep" in workspace.directories


def test_apply_reports_directory_created_between_plan_and_clear_but_not_rebuilt_parent():
    class _WorkspaceWithConcurrentWrite(_WorkspaceWithEmptyDirectories):
        async def list_entries(self, **kwargs):
            result = await super().list_entries(**kwargs)
            if len(self.listing_calls) == 1:
                self.directories.add("new")
            return result

    workspace = _WorkspaceWithConcurrentWrite()
    engine = _resources_engine(workspace, _StubDeclaredSourceResolver())
    report = _run(engine.apply(
        make_context(engine_type="claude_code"),
        {"schema_version": 1, "manifest": {"resources": [{"path": "data/keep.md", "content": "keep"}]}},
        apply_id="actual", trigger="explicit", started_at=datetime.now(),
    ))

    assert _resources_category(report).as_dict()["removed"] == ["data/empty/", "new/"]
    assert workspace.present == {"data/keep.md"}
    assert "new" not in workspace.directories


@pytest.mark.parametrize(
    ("path", "report_recursively"),
    [("/data/notes.md", True), ("data/../skills", True), ("/data", False), ("../skills", False)],
)
def test_a_malformed_write_listing_prevents_all_deletion(path, report_recursively):
    class _MalformedWorkspace(_WorkspaceWithEmptyDirectories):
        async def list_entries(self, *, recursive=False, **kwargs):
            entries = await super().list_entries(recursive=recursive, **kwargs)
            if recursive == report_recursively and (not recursive or len(self.listing_calls) > 1):
                return (*entries, WorkspaceEntry(path, True))
            return entries

    workspace = _MalformedWorkspace()
    engine = _resources_engine(workspace, _StubDeclaredSourceResolver())
    report = _run(engine.apply(
        make_context(engine_type="claude_code"),
        {"schema_version": 1, "manifest": {"resources": []}},
        apply_id="malformed", trigger="explicit", started_at=datetime.now(),
    ))

    assert _resources_category(report).aborted
    assert workspace.deleted == []
    assert workspace.directories == {"data", "data/empty", "skills", "skills/keep"}


def test_apply_reports_empty_directory_replaced_with_same_named_file():
    workspace = _WorkspaceWithEmptyDirectories()
    workspace.directories = {"data.md"}
    engine = _resources_engine(workspace, _StubDeclaredSourceResolver())
    for dry_run in (True, False):
        report = _run(engine.apply(
            make_context(engine_type="claude_code"),
            {"schema_version": 1, "manifest": {"resources": [{"path": "data.md", "content": "file"}]}},
            apply_id=f"same-name-{dry_run}", trigger="explicit",
            started_at=datetime.now(), dry_run=dry_run,
        ))
        assert _resources_category(report).as_dict()["removed"] == ["data.md/"]


def test_recursive_listing_outside_its_prefix_prevents_all_deletion():
    class _AmbiguousRecursiveWorkspace(_WorkspaceWithEmptyDirectories):
        async def list_entries(self, *, recursive=False, **kwargs):
            entries = await super().list_entries(recursive=recursive, **kwargs)
            # Some transports can expose a basename in recursive results.
            return (*entries, WorkspaceEntry("keep", False)) if recursive else entries

    workspace = _AmbiguousRecursiveWorkspace()
    engine = _resources_engine(workspace, _StubDeclaredSourceResolver())
    report = _run(engine.apply(
        make_context(engine_type="claude_code"),
        {"schema_version": 1, "manifest": {"resources": []}},
        apply_id="root-authority", trigger="explicit", started_at=datetime.now(),
    ))

    assert workspace.deleted == []
    assert _resources_category(report).aborted
    assert workspace.directories == {"data", "data/empty", "skills", "skills/keep"}


def test_apply_does_not_list_descendants_of_protected_directories():
    class _WorkspaceWithBrokenSkillSymlink(_WorkspaceWithEmptyDirectories):
        async def list_entries(self, *, prefix="", recursive=False, **kwargs):
            if recursive and prefix in ("", "skills-pool"):
                raise RuntimeError("broken skill symlink in recursive listing")
            return await super().list_entries(prefix=prefix, recursive=recursive, **kwargs)

    workspace = _WorkspaceWithBrokenSkillSymlink()
    workspace.directories.add("skills-pool")
    engine = _resources_engine(workspace, _StubDeclaredSourceResolver())
    report = _run(engine.apply(
        make_context(engine_type="claude_code"),
        {"schema_version": 1, "manifest": {"resources": []}},
        apply_id="protected-symlink", trigger="explicit", started_at=datetime.now(),
    ))

    assert not _resources_category(report).aborted
    assert _resources_category(report).as_dict()["removed"] == ["data/", "data/empty/"]
    assert workspace.directories == {"skills", "skills/keep", "skills-pool"}
