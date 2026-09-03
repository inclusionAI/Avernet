"""ResourceFileService — provider-agnostic workspace-namespace operations.

The service addresses every file by ``workspace/<rel>`` and lets the dispatcher's
per-provider mapper compose the real address. These tests pin: logical addressing of
each op, the logic-view ``absolute_path``, file-browser filtering, the
``skills-local`` root injection (non-teclaw), and the arca download size guard.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.devices.services.device_filesystem import (
    FileTooLargeError as DeviceFileTooLargeError,
)
from agentclaw.community.core.resources.service import (
    DirectoryTooLargeError,
    ResourceNotFoundError,
)
from agentclaw.community.core.services import resource_file_service
from agentclaw.community.core.services.resource_file_service import (
    ResourceFileService,
    is_readonly,
)


def _svc(*, provider: str, device_fs: MagicMock):
    ctx = MagicMock()
    ctx.provider = provider
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = ctx
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    svc = ResourceFileService(
        publish_repo=MagicMock(),
        bot_repo=MagicMock(),
        resolver=resolver,
        device_fs_dispatcher=dispatcher,
    )
    return svc, dispatcher


_COORDS = dict(entity_type="staff", entity_id="u1", bot_id="bot-1", engine_type="openclaw")


# ── addressing + absolute_path presentation ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_addresses_workspace_namespace_and_builds_rel_path():
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=[{"name": "a.txt", "is_dir": False, "size": 3}])
    svc, dispatcher = _svc(provider="arca", device_fs=device_fs)

    items = await svc.list_dir(**_COORDS, path="sub")

    device_fs.list_dir.assert_any_await("workspace/sub")
    # dispatch addressed by the workspace namespace
    _, kwargs = dispatcher.dispatch_addressed.call_args
    assert kwargs["namespace"] == "workspace"
    assert items[0]["path"] == "sub/a.txt"
    assert items[0]["size"] == 3


@pytest.mark.asyncio
async def test_absolute_path_uses_engine_path():
    # absolute_path is the device's own absolute path (the engine's ``path`` field) —
    # container path for arca/openclaw/aicoding/claude_code, host path for local.
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=[
        {"name": "a.txt", "is_dir": False,
         "path": "/home/admin/.openclaw/workspace/sub/a.txt"},
    ])
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="sub")
    assert items[0]["absolute_path"] == "/home/admin/.openclaw/workspace/sub/a.txt"


@pytest.mark.asyncio
async def test_absolute_path_falls_back_to_logic_view_without_engine_path():
    # legacy teclaw: no absolute ``path`` exposed → logic view as a defensive default.
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=[{"name": "a.txt", "is_dir": False}])
    svc, _ = _svc(provider="teclaw", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="sub")
    assert items[0]["absolute_path"] == "workspace/sub/a.txt"


@pytest.mark.asyncio
async def test_path_uses_relative_path_when_present_recursive_safe():
    # relative_path is relative to the listed dir → request_path + relative_path keeps
    # a (future) recursive listing correct; name alone would flatten nested entries.
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=[
        {"name": "x.csv", "is_dir": False, "relative_path": "deep/x.csv",
         "path": "/home/admin/.openclaw/workspace/sub/deep/x.csv"},
    ])
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="sub")
    assert items[0]["path"] == "sub/deep/x.csv"


@pytest.mark.asyncio
async def test_path_falls_back_to_name_without_relative_path():
    # legacy teclaw: no relative_path → request_path + name (valid non-recursively).
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=[{"name": "x.csv", "is_dir": False}])
    svc, _ = _svc(provider="teclaw", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="sub")
    assert items[0]["path"] == "sub/x.csv"


# ── browser filtering ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root_listing_filters_hidden_and_dotfiles():
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=[
        [  # workspace root
            {"name": ".hidden", "is_dir": False},
            {"name": "state", "is_dir": True},
            {"name": "skills", "is_dir": True},
            {"name": "AGENTS.md", "is_dir": False},
            {"name": "data", "is_dir": True},
            {"name": "report.csv", "is_dir": False, "size": 10},
        ],
        [],  # workspace/skills probe → nothing to inject
    ])
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="")
    names = {i["name"] for i in items}
    assert names == {"data", "report.csv"}


@pytest.mark.asyncio
async def test_subdir_listing_does_not_filter_hidden_names():
    # hidden-name filtering only applies at the workspace root.
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=[{"name": "state", "is_dir": True}])
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="sub")
    assert [i["name"] for i in items] == ["state"]


# ── skills-local injection ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_local_injected_for_arca_when_present():
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=[
        [{"name": "data", "is_dir": True}],          # root
        [{"name": "skills-local", "is_dir": True,    # workspace/skills probe
          "path": "/home/admin/.openclaw/workspace/skills/skills-local"}],
    ])
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="")
    injected = [i for i in items if i["path"] == "skills/skills-local"]
    assert len(injected) == 1
    assert injected[0]["is_dir"] is True
    # absolute_path comes from the probed entry's own path
    assert injected[0]["absolute_path"] == "/home/admin/.openclaw/workspace/skills/skills-local"


@pytest.mark.asyncio
async def test_pool_skills_local_is_injected_when_legacy_bridge_is_retired():
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=[
        [
            {"name": "skills-pool", "is_dir": True},
            {"name": "data", "is_dir": True},
        ],
        [],
        [
            {
                "name": "skills-local",
                "is_dir": True,
                "path": (
                    "/home/admin/.openclaw/workspace/"
                    "skills-pool/skills-local"
                ),
            }
        ],
    ])
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    items = await svc.list_dir(**_COORDS, path="")

    assert {item["name"] for item in items} == {"data", "skills-local"}
    injected = next(item for item in items if item["name"] == "skills-local")
    assert injected["path"] == "skills-pool/skills-local"
    assert injected["absolute_path"].endswith("/skills-pool/skills-local")


@pytest.mark.asyncio
async def test_root_returns_only_one_skills_local_when_both_layouts_exist():
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=[
        [{"name": "data", "is_dir": True}],
        [
            {
                "name": "skills-local",
                "is_dir": True,
                "path": (
                    "/home/admin/.openclaw/workspace/skills/skills-local"
                ),
            }
        ],
        [
            {
                "name": "skills-local",
                "is_dir": True,
                "path": (
                    "/home/admin/.openclaw/workspace/"
                    "skills-pool/skills-local"
                ),
            }
        ],
    ])
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    items = await svc.list_dir(**_COORDS, path="")

    injected = [item for item in items if item["name"] == "skills-local"]
    assert len(injected) == 1
    assert injected[0]["path"] == "skills/skills-local"
    assert device_fs.list_dir.await_count == 2


@pytest.mark.asyncio
async def test_skills_local_not_injected_for_teclaw():
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=[{"name": "skills-local", "is_dir": True}])
    svc, _ = _svc(provider="teclaw", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="")
    # teclaw lists skills-local naturally; no synthetic "skills/skills-local" entry,
    # and no second probe call.
    assert all(i["path"] != "skills/skills-local" for i in items)
    assert device_fs.list_dir.await_count == 1


@pytest.mark.asyncio
async def test_skills_local_injected_for_baas_when_present():
    # openclaw runs on the baas provider and nests skills-local under the hidden
    # "skills" dir (like arca), so baas must probe workspace/skills and inject it.
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=[
        [{"name": "data", "is_dir": True}],          # root
        [{"name": "skills-local", "is_dir": True,    # workspace/skills probe
          "path": "/home/admin/.openclaw/workspace/skills/skills-local"}],
    ])
    svc, _ = _svc(provider="baas", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="")
    injected = [i for i in items if i["path"] == "skills/skills-local"]
    assert len(injected) == 1
    assert injected[0]["is_dir"] is True
    assert device_fs.list_dir.await_count == 2


@pytest.mark.asyncio
async def test_root_listing_survives_skills_probe_404():
    # container without a "skills" dir → engine 404 → device raises. the optional
    # skills-local probe must be swallowed so the root listing returns instead of 500.
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=[
        [{"name": "data", "is_dir": True}],                 # root → 200
        FileNotFoundError("workspace/skills not found"),    # skills probe → 404
        FileNotFoundError("workspace/skills-pool not found"),
    ])
    svc, _ = _svc(provider="baas", device_fs=device_fs)
    items = await svc.list_dir(**_COORDS, path="")
    assert [i["name"] for i in items] == ["data"]
    assert all(i["path"] != "skills/skills-local" for i in items)
    assert device_fs.list_dir.await_count == 3


# ── read / download-limit forwarding ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_addresses_workspace():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"hi")
    svc, _ = _svc(provider="local", device_fs=device_fs)
    out = await svc.read_file(**_COORDS, path="sub/a.txt")
    assert out == b"hi"
    device_fs.read_file.assert_awaited_once_with(
        "workspace/sub/a.txt", enforce_download_limit=False
    )


@pytest.mark.asyncio
async def test_read_forwards_enforce_download_limit_to_plugin():
    # the size guard lives in the plugin now — the service is provider-blind and just
    # forwards the flag.
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"hi")
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    await svc.read_file(**_COORDS, path="big.zip", enforce_download_limit=True)
    device_fs.read_file.assert_awaited_once_with(
        "workspace/big.zip", enforce_download_limit=True
    )


# ── create / delete / upload ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_directory_writes_keep_placeholder():
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock()
    svc, _ = _svc(provider="teclaw", device_fs=device_fs)
    await svc.create_directory(**_COORDS, path="newdir")
    device_fs.write_file.assert_awaited_once_with("workspace/newdir/.keep", b"")


def _fs_listing(*entries: dict) -> MagicMock:
    """A device whose parent listing answers the file-or-directory question."""
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=list(entries))
    device_fs.delete_file = AsyncMock(return_value=True)
    device_fs.delete_tree = AsyncMock(return_value=True)
    return device_fs


@pytest.mark.asyncio
async def test_delete_addresses_workspace():
    device_fs = _fs_listing({"name": "a.txt", "is_dir": False})
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    ok = await svc.delete(**_COORDS, path="sub/a.txt")
    assert ok is True
    device_fs.delete_file.assert_awaited_once_with("workspace/sub/a.txt")
    device_fs.delete_tree.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_routes_a_directory_through_delete_tree():
    """The engines split these across two operations (``remove`` vs ``rmtree``)
    and the single-file one does not recurse, so a directory sent to
    ``delete_file`` is simply not deleted."""
    device_fs = _fs_listing({"name": "docs", "is_dir": True})
    svc, _ = _svc(provider="teclaw", device_fs=device_fs)

    assert await svc.delete(**_COORDS, path="sub/docs") is True
    device_fs.delete_tree.assert_awaited_once_with("workspace/sub/docs")
    device_fs.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_asks_the_parent_not_the_path_itself():
    """Listing a *file* is not a defined operation on the engines, so probing
    the path directly would turn every file delete into an error."""
    device_fs = _fs_listing({"name": "a.txt", "is_dir": False})
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    await svc.delete(**_COORDS, path="sub/a.txt")

    device_fs.list_dir.assert_awaited_once_with("workspace/sub")


@pytest.mark.asyncio
async def test_delete_at_the_workspace_root_lists_the_root():
    device_fs = _fs_listing({"name": "a.txt", "is_dir": False})
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    await svc.delete(**_COORDS, path="a.txt")

    device_fs.list_dir.assert_awaited_once_with("workspace")


@pytest.mark.asyncio
async def test_delete_falls_back_to_the_file_branch_when_the_listing_fails():
    """A failed probe must not fail the delete — the file branch is what this
    did unconditionally before the directory support existed."""
    device_fs = _fs_listing()
    device_fs.list_dir = AsyncMock(side_effect=RuntimeError("engine down"))
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    assert await svc.delete(**_COORDS, path="sub/a.txt") is True
    device_fs.delete_file.assert_awaited_once_with("workspace/sub/a.txt")


@pytest.mark.asyncio
async def test_delete_still_reaches_hidden_system_names():
    """The probe uses the raw device listing rather than this class's filtered
    ``list_dir``, which drops dotfiles and the hidden system directories — those
    stay as deletable as they were before."""
    device_fs = _fs_listing({"name": "state", "is_dir": True})
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    assert await svc.delete(**_COORDS, path="state") is True
    device_fs.delete_tree.assert_awaited_once_with("workspace/state")


@pytest.mark.asyncio
async def test_upload_writes_workspace_rel_and_validates_extension():
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock()
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    res = await svc.upload_file(**_COORDS, target_dir="data", filename="x.csv", data=b"1,2")
    device_fs.write_file.assert_awaited_once_with("workspace/data/x.csv", b"1,2")
    assert res["path"] == "data/x.csv"
    assert res["absolute_path"] == "workspace/data/x.csv"
    assert res["size"] == 3

    with pytest.raises(ValueError):
        await svc.upload_file(**_COORDS, target_dir="", filename="x.exe", data=b"bad")


@pytest.mark.asyncio
async def test_upload_empty_filename_raises():
    svc, _ = _svc(provider="arca", device_fs=MagicMock())
    with pytest.raises(ValueError):
        await svc.upload_file(**_COORDS, target_dir="", filename="", data=b"x")


@pytest.mark.asyncio
async def test_upload_too_large_raises(monkeypatch):
    # The real ceiling is 500MB. Materialising ``b"x" * (500MB + 1)`` to trip the
    # size check cost ~11.6s of allocation alone — the single slowest
    # non-retry test in the suite. Shrink the ceiling instead: the predicate
    # compares a length against this module global, so a 16-byte bound
    # exercises exactly the same branch.
    # The admission rule now lives in ``file_service.admission_refusal`` and
    # reads *its own* module's globals at call time — patching
    # ``resource_file_service.MAX_FILE_SIZE`` (the old from-import binding)
    # gated nothing once the service delegated, which is how this test
    # proved the move: it went red first, then the target moved here.
    from agentclaw.community.core.resources.services import file_service

    monkeypatch.setattr(file_service, "MAX_FILE_SIZE", 16)

    svc, _ = _svc(provider="arca", device_fs=MagicMock())
    with pytest.raises(ValueError, match="File too large"):
        await svc.upload_file(
            **_COORDS, target_dir="", filename="big.csv",
            data=b"x" * (file_service.MAX_FILE_SIZE + 1),
        )


@pytest.mark.asyncio
async def test_upload_preserve_structure_keeps_nested_path_strips_traversal():
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock()
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    res = await svc.upload_file(
        **_COORDS, target_dir="data", filename="../sub/deep/x.csv", data=b"1",
        preserve_structure=True,
    )
    device_fs.write_file.assert_awaited_once_with("workspace/data/sub/deep/x.csv", b"1")
    assert res["path"] == "data/sub/deep/x.csv"
    assert res["name"] == "x.csv"


@pytest.mark.asyncio
async def test_list_dir_returns_empty_when_device_returns_none():
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=None)  # dir does not exist
    svc, _ = _svc(provider="arca", device_fs=device_fs)
    assert await svc.list_dir(**_COORDS, path="nope") == []


# ── publish-stage resolution (resolve_for_binding) ───────────────────────────


def _svc_publish(*, provider: str, device_fs: MagicMock, binding_ext: dict | None,
                 resolve_raises: Exception | None = None, record_found: bool = True):
    ctx = MagicMock()
    ctx.provider = provider
    resolver = MagicMock()
    if resolve_raises is not None:
        resolver.resolve_for_binding.side_effect = resolve_raises
    else:
        resolver.resolve_for_binding.return_value = ctx
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    publish_repo = MagicMock()
    if record_found:
        record = MagicMock()
        record.ext = {"binding": binding_ext} if binding_ext is not None else {}
        publish_repo.get_by_id.return_value = record
    else:
        publish_repo.get_by_id.return_value = None
    svc = ResourceFileService(
        publish_repo=publish_repo, bot_repo=MagicMock(),
        resolver=resolver, device_fs_dispatcher=dispatcher,
    )
    return svc, resolver, publish_repo


@pytest.mark.asyncio
async def test_publish_read_resolves_via_binding():
    """Publish read goes through resolve_for_binding and addresses workspace/<rel>."""
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"data")
    svc, resolver, _ = _svc_publish(
        provider="arca", device_fs=device_fs, binding_ext={"online": 7}
    )
    out = await svc.read_file(**_COORDS, path="sub/a.txt", publish_id="99")
    assert out == b"data"
    resolver.resolve_for_binding.assert_called_once_with(
        7, "u1", bot_id="bot-1", device_uuid=None
    )
    resolver.resolve_for_bot.assert_not_called()
    device_fs.read_file.assert_awaited_once_with(
        "workspace/sub/a.txt", enforce_download_limit=False
    )


@pytest.mark.asyncio
async def test_publish_online_takes_precedence_over_verify():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, resolver, _ = _svc_publish(
        provider="teclaw", device_fs=device_fs,
        binding_ext={"online": 11, "verify": 22},
    )
    await svc.read_file(**_COORDS, path="a.txt", publish_id="99")
    resolver.resolve_for_binding.assert_called_once_with(
        11, "u1", bot_id="bot-1", device_uuid=None
    )


@pytest.mark.asyncio
async def test_publish_falls_back_to_verify_when_no_online():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, resolver, _ = _svc_publish(
        provider="teclaw", device_fs=device_fs, binding_ext={"verify": 22},
    )
    await svc.read_file(**_COORDS, path="a.txt", publish_id="99")
    resolver.resolve_for_binding.assert_called_once_with(
        22, "u1", bot_id="bot-1", device_uuid=None
    )


@pytest.mark.asyncio
async def test_publish_missing_stage_binding_raises_valueerror():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, _, _ = _svc_publish(provider="arca", device_fs=device_fs, binding_ext={})
    with pytest.raises(ValueError):
        await svc.read_file(**_COORDS, path="a.txt", publish_id="99")
    device_fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_non_numeric_publish_id_raises_valueerror():
    """A non-numeric publish_id → int() fails in _stage_bind_id → None → ValueError."""
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, _, _ = _svc_publish(provider="arca", device_fs=device_fs, binding_ext={"online": 7})
    with pytest.raises(ValueError):
        await svc.read_file(**_COORDS, path="a.txt", publish_id="not-a-number")


@pytest.mark.asyncio
async def test_publish_record_not_found_raises_valueerror():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, _, _ = _svc_publish(
        provider="arca", device_fs=device_fs, binding_ext=None, record_found=False,
    )
    with pytest.raises(ValueError):
        await svc.read_file(**_COORDS, path="a.txt", publish_id="99")


@pytest.mark.asyncio
async def test_publish_zero_bind_id_treated_as_missing():
    """A stage bind_id of 0 is not a real binding (PKs are >=1) → ValueError, no read."""
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, _, _ = _svc_publish(
        provider="arca", device_fs=device_fs, binding_ext={"online": 0}
    )
    with pytest.raises(ValueError):
        await svc.read_file(**_COORDS, path="a.txt", publish_id="99")
    device_fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_non_numeric_bind_id_raises_valueerror():
    """A non-numeric stage bind_id must map to ValueError (400), not a raw 500."""
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, resolver, _ = _svc_publish(
        provider="arca", device_fs=device_fs, binding_ext={"online": "not-a-number"}
    )
    with pytest.raises(ValueError):
        await svc.read_file(**_COORDS, path="a.txt", publish_id="99")


@pytest.mark.asyncio
async def test_publish_resolve_failure_raises_valueerror():
    from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError

    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"x")
    svc, _, _ = _svc_publish(
        provider="arca", device_fs=device_fs, binding_ext={"online": 7},
        resolve_raises=DeviceNotBoundError("nope"),
    )
    with pytest.raises(ValueError):
        await svc.read_file(**_COORDS, path="a.txt", publish_id="99")


def test_is_readonly_rules():
    assert is_readonly(".env") is True
    assert is_readonly("AGENTS.md") is True       # identity file at root
    assert is_readonly("sub/AGENTS.md") is False  # only at root
    assert is_readonly("data/report.csv") is False


def test_resolves_from_di(test_injector):
    """The service is wired as a singleton and resolves from the real injector."""
    svc = test_injector.get(ResourceFileService)
    assert isinstance(svc, ResourceFileService)
    # same singleton instance both times
    assert test_injector.get(ResourceFileService) is svc


# ── iter_directory_files: the recursive walk behind download-dir ────────────
#
# The tree is stubbed at the device seam in *logical* space ("workspace/<rel>"),
# which is also what pins the addressing: the walk must never leak a container
# path upward. ``None`` from list_dir means "missing", per the DeviceFileSystem
# protocol.


def _tree_fs(tree: dict[str, list[dict] | None], files: dict[str, bytes | None]):
    """A device_fs stub: ``tree`` maps a logical dir to its listing (None =
    missing), ``files`` maps a logical file to its bytes (None = vanished)."""
    device_fs = MagicMock()

    async def _list(logical: str):
        return tree.get(logical)

    async def _read(logical: str, *, enforce_download_limit: bool = False):
        return files.get(logical)

    device_fs.list_dir = AsyncMock(side_effect=_list)
    device_fs.read_file = AsyncMock(side_effect=_read)
    return device_fs


def _walk_tree() -> tuple[dict, dict]:
    """A small workspace: docs/ nested, a dotfile, a hidden root file, a
    hidden root dir, and an identity-looking file *inside* docs (only the
    root level filters it)."""
    tree = {
        "workspace": [
            {"name": "MEMORY.md", "is_dir": False, "size": 5},
            {"name": ".env", "is_dir": False, "size": 3},
            {"name": "skills", "is_dir": True},
            {"name": "docs", "is_dir": True},
        ],
        "workspace/skills": [
            {"name": "x.md", "is_dir": False, "size": 1},
        ],
        "workspace/docs": [
            {"name": "a.txt", "is_dir": False, "size": 2},
            {"name": "MEMORY.md", "is_dir": False, "size": 4},
            {"name": "deep", "is_dir": True},
        ],
        "workspace/docs/deep": [
            {"name": ".secret", "is_dir": False, "size": 1},
            {"name": "b.txt", "is_dir": False, "size": 3},
        ],
    }
    files = {
        "workspace/MEMORY.md": b"root-m",
        "workspace/.env": b"env",
        "workspace/skills/x.md": b"x",
        "workspace/docs/a.txt": b"aa",
        "workspace/docs/MEMORY.md": b"mem4",
        "workspace/docs/deep/.secret": b"s",
        "workspace/docs/deep/b.txt": b"bbb",
    }
    return tree, files


async def _collect(svc: ResourceFileService, path: str) -> list[tuple[str, bytes]]:
    return [item async for item in svc.iter_directory_files(**_COORDS, path=path)]


@pytest.mark.asyncio
async def test_walk_yields_names_relative_to_the_requested_directory():
    tree, files = _walk_tree()
    svc, _ = _svc(provider="arca", device_fs=_tree_fs(tree, files))

    got = await _collect(svc, "docs")

    assert got == [
        ("MEMORY.md", b"mem4"),
        ("a.txt", b"aa"),
        ("deep/b.txt", b"bbb"),
    ]


@pytest.mark.asyncio
async def test_walk_resolves_the_device_context_once_for_the_whole_tree():
    tree, files = _walk_tree()
    device_fs = _tree_fs(tree, files)
    ctx = MagicMock()
    ctx.provider = "arca"
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = ctx
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    svc = ResourceFileService(
        publish_repo=MagicMock(), bot_repo=MagicMock(),
        resolver=resolver, device_fs_dispatcher=dispatcher,
    )

    await _collect(svc, "docs")

    assert resolver.resolve_for_bot.call_count == 1
    assert dispatcher.dispatch_addressed.call_count == 1


@pytest.mark.asyncio
async def test_root_walk_filters_like_the_browser_but_only_at_the_root():
    tree, files = _walk_tree()
    svc, _ = _svc(provider="arca", device_fs=_tree_fs(tree, files))

    got = await _collect(svc, "")

    names = [name for name, _ in got]
    # Root-level hidden names are filtered: MEMORY.md (basename), .env
    # (dotfile), skills/ (hidden dir, never descended into)…
    assert "MEMORY.md" not in names
    assert ".env" not in names
    assert not any(n.startswith("skills/") for n in names)
    # …while the *contents* of a regular directory keep everything non-dot:
    # docs/MEMORY.md is just a file there.
    assert "docs/MEMORY.md" in names
    assert "docs/a.txt" in names
    assert "docs/deep/b.txt" in names
    assert "docs/deep/.secret" not in names


@pytest.mark.asyncio
async def test_walk_missing_directory_is_not_found():
    tree, files = _walk_tree()
    svc, _ = _svc(provider="arca", device_fs=_tree_fs(tree, files))

    with pytest.raises(ResourceNotFoundError):
        await _collect(svc, "nope")


@pytest.mark.asyncio
async def test_walk_empty_directory_yields_nothing_and_is_not_an_error():
    svc, _ = _svc(provider="arca", device_fs=_tree_fs({"workspace/empty": []}, {}))

    assert await _collect(svc, "empty") == []


@pytest.mark.asyncio
async def test_walk_per_file_cap_is_preflighted_from_the_listing(monkeypatch):
    """A file whose *listed* size is over the cap is refused before a single
    byte is read — the cheap refusal is the whole point of the preflight."""
    monkeypatch.setattr(
        resource_file_service, "DIRECTORY_DOWNLOAD_MAX_FILE_BYTES", 5
    )
    tree = {"workspace": [{"name": "big.bin", "is_dir": False, "size": 10}]}
    device_fs = _tree_fs(tree, {})
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    with pytest.raises(DirectoryTooLargeError):
        await _collect(svc, "")

    device_fs.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_walk_file_count_cap(monkeypatch):
    monkeypatch.setattr(resource_file_service, "DIRECTORY_DOWNLOAD_MAX_FILES", 2)
    tree, files = _walk_tree()
    svc, _ = _svc(provider="arca", device_fs=_tree_fs(tree, files))

    with pytest.raises(DirectoryTooLargeError):
        await _collect(svc, "docs")


@pytest.mark.asyncio
async def test_walk_listed_total_cap(monkeypatch):
    monkeypatch.setattr(
        resource_file_service, "DIRECTORY_DOWNLOAD_MAX_TOTAL_BYTES", 5
    )
    tree, files = _walk_tree()
    svc, _ = _svc(provider="arca", device_fs=_tree_fs(tree, files))

    with pytest.raises(DirectoryTooLargeError):
        await _collect(svc, "docs")


@pytest.mark.asyncio
async def test_walk_streamed_total_recount_catches_a_lying_listing(monkeypatch):
    """The listing claimed tiny sizes; the bytes say otherwise."""
    monkeypatch.setattr(
        resource_file_service, "DIRECTORY_DOWNLOAD_MAX_TOTAL_BYTES", 3
    )
    tree = {"workspace": [{"name": "a.txt", "is_dir": False, "size": 1}]}
    files = {"workspace/a.txt": b"way-more-than-listed"}
    svc, _ = _svc(provider="arca", device_fs=_tree_fs(tree, files))

    with pytest.raises(DirectoryTooLargeError):
        await _collect(svc, "")


@pytest.mark.asyncio
async def test_walk_device_size_guard_maps_to_the_directory_error():
    tree = {"workspace": [{"name": "big.bin", "is_dir": False, "size": 1}]}
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=lambda logical: tree.get(logical))
    device_fs.read_file = AsyncMock(
        side_effect=DeviceFileTooLargeError("file exceeds 100 MB")
    )
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    with pytest.raises(DirectoryTooLargeError):
        await _collect(svc, "")


@pytest.mark.asyncio
async def test_walk_skips_entries_that_vanish_mid_walk():
    """The race rule: a workspace may change under the walk; gone is skipped."""
    tree = {
        "workspace": [
            {"name": "a.txt", "is_dir": False, "size": 2},
            {"name": "gone", "is_dir": True},
        ],
        "workspace/gone": None,  # listed, then deleted before the descent
    }
    files = {"workspace/a.txt": b"aa"}
    svc, _ = _svc(provider="arca", device_fs=_tree_fs(tree, files))

    assert await _collect(svc, "") == [("a.txt", b"aa")]


@pytest.mark.asyncio
async def test_walk_aborts_on_a_read_error():
    """…but an *error* aborts: a 200 archive silently missing files is worse
    than no archive."""
    tree = {"workspace": [{"name": "a.txt", "is_dir": False, "size": 2}]}
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(side_effect=lambda logical: tree.get(logical))
    device_fs.read_file = AsyncMock(side_effect=RuntimeError("device blew up"))
    svc, _ = _svc(provider="arca", device_fs=device_fs)

    with pytest.raises(RuntimeError):
        await _collect(svc, "")
