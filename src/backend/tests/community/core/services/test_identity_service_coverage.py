"""Coverage for IdentityService branches not hit by the happy-path tests:

- entity-level get/list,
- the generic (path-based) read_file/write_file used by patch_engine,
- the publish-device read error paths.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.services.identity import IdentityService


def _svc(*, resolver=None, dispatcher=None, bot_repo=None, publish_repo=None, path_factory=None):
    return IdentityService(
        path_factory=path_factory or MagicMock(),
        publish_repo=publish_repo or MagicMock(),
        bot_repo=bot_repo or MagicMock(),
        resolver=resolver or MagicMock(),
        device_fs_dispatcher=dispatcher or MagicMock(),
    )


# ── entity-level get / list ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_entity_file_routes_default_bot_and_returns_logic_view():
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"# entity")
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = MagicMock()

    svc = _svc(resolver=resolver, dispatcher=dispatcher, bot_repo=bot_repo)
    resp = await svc.get_entity_file("staff", "100018", "RULES.md", "op-1")

    assert resp.content == "# entity"
    assert resp.file_path == "identity/RULES.md"
    # entity-level addresses the ``default`` bot
    _, kwargs = dispatcher.dispatch_addressed.call_args
    assert kwargs["bot_id"] == "default"
    device_fs.read_file.assert_awaited_once_with("identity/RULES.md")


@pytest.mark.asyncio
async def test_list_entity_files_reports_logic_view_paths(tmp_path):
    pf = MagicMock()
    pf.get_entity_identity_dir.return_value = tmp_path
    svc = _svc(path_factory=pf)

    resp = await svc.list_entity_files("staff", "100018")
    assert resp.success is True
    assert all(item.file_path == f"identity/{item.file_type}" for item in resp.files)


# ── generic read_file / write_file (path-based, patch_engine) ─────────

@pytest.mark.asyncio
async def test_generic_read_file_arca_branch():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(return_value=b"# c")
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = MagicMock()
    svc = _svc(resolver=resolver, dispatcher=dispatcher, bot_repo=MagicMock())
    with patch("agentclaw.community.core.devices.services.device_info.get_device_info", return_value=("arca", "sbx-1")):
        out = await svc.read_file(Path("/x/RULES.md"), bot_id="b", owner_id="o")
    assert out == "# c"
    device_fs.read_file.assert_awaited_once_with("/x/RULES.md")


@pytest.mark.asyncio
async def test_generic_read_file_local_fs(tmp_path):
    svc = _svc()
    f = tmp_path / "RULES.md"
    f.write_text("# local", encoding="utf-8")
    assert await svc.read_file(f) == "# local"
    assert await svc.read_file(tmp_path / "missing.md") == ""


@pytest.mark.asyncio
async def test_generic_read_file_wraps_error_as_internal():
    svc = _svc(bot_repo=MagicMock())
    with patch("agentclaw.community.core.devices.services.device_info.get_device_info", side_effect=RuntimeError("boom")):
        with pytest.raises(InternalError):
            await svc.read_file(Path("/x"), bot_id="b", owner_id="o")


@pytest.mark.asyncio
async def test_generic_write_file_arca_branch():
    device_fs = MagicMock()
    device_fs.write_file = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = MagicMock()
    svc = _svc(resolver=resolver, dispatcher=dispatcher, bot_repo=MagicMock())
    with patch("agentclaw.community.core.devices.services.device_info.get_device_info", return_value=("arca", "sbx-1")):
        await svc.write_file(Path("/x/RULES.md"), "# c", bot_id="b", owner_id="o")
    device_fs.write_file.assert_awaited_once_with("/x/RULES.md", b"# c")


@pytest.mark.asyncio
async def test_generic_write_file_local_fs(tmp_path):
    svc = _svc()
    target = tmp_path / "sub" / "RULES.md"
    await svc.write_file(target, "# local")
    assert target.read_text(encoding="utf-8") == "# local"


@pytest.mark.asyncio
async def test_generic_write_file_wraps_error_as_internal():
    svc = _svc(bot_repo=MagicMock())
    with patch("agentclaw.community.core.devices.services.device_info.get_device_info", side_effect=RuntimeError("boom")):
        with pytest.raises(InternalError):
            await svc.write_file(Path("/x"), "# c", bot_id="b", owner_id="o")


# ── sync_agents_md swallows device errors ────────────────────────────

@pytest.mark.asyncio
async def test_sync_agents_md_swallows_device_error():
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = RuntimeError("device down")
    svc = _svc(resolver=resolver, bot_repo=bot_repo)
    # must not raise — the AGENTS.md sync is best-effort
    await svc.sync_agents_md("staff", "100018", "bot-1", "openclaw", owner_id="100018")


# ── publish-device read error paths ──────────────────────────────────

def _publish_svc(*, record, resolver=None):
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = record
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    return _svc(publish_repo=publish_repo, resolver=resolver or MagicMock(), bot_repo=bot_repo)


@pytest.mark.asyncio
async def test_publish_read_record_missing_returns_empty():
    svc = _publish_svc(record=None)
    resp = await svc.get_bot_file("staff", "100018", "bot-1", "RULES.md", "op", publish_id="9")
    assert resp.content == ""


@pytest.mark.asyncio
async def test_publish_read_resolve_failure_returns_empty():
    from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError
    record = MagicMock()
    record.ext = {"binding": {"online": 7}}
    resolver = MagicMock()
    resolver.resolve_for_binding.side_effect = DeviceNotBoundError("no binding")
    svc = _publish_svc(record=record, resolver=resolver)
    resp = await svc.get_bot_file("staff", "100018", "bot-1", "RULES.md", "op", publish_id="9")
    assert resp.content == ""


@pytest.mark.asyncio
async def test_publish_read_unexpected_error_returns_empty():
    record = MagicMock()
    record.ext = {"binding": {"online": 7}}
    resolver = MagicMock()
    resolver.resolve_for_binding.return_value = MagicMock()
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(side_effect=TypeError("boom"))
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = record
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    svc = _svc(publish_repo=publish_repo, resolver=resolver, dispatcher=dispatcher, bot_repo=bot_repo)
    resp = await svc.get_bot_file("staff", "100018", "bot-1", "RULES.md", "op", publish_id="9")
    assert resp.content == ""


# ── _device_read: 404 缺省 → 空内容;非 404 仍抛 ──────────────────────────
# baas/arca DeviceFileSystem.read_file 故意不吞 404,由 identity service 在
# _device_read 这层翻译成"缺省即空"。回归 bug:trace 0b446a28...e77d9 —
# 用户从未编辑过 RULES.md,容器侧 /api/file/read 返 404,router 吃 500。

def _read_svc(*, device_fs):
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw"}
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = device_fs
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = MagicMock()
    return _svc(resolver=resolver, dispatcher=dispatcher, bot_repo=bot_repo)


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x.invalid/api/file/read")
    resp = httpx.Response(status_code, request=req)
    return httpx.HTTPStatusError(f"{status_code}", request=req, response=resp)


@pytest.mark.asyncio
async def test_get_bot_file_translates_404_to_empty_content():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(side_effect=_http_status_error(404))
    svc = _read_svc(device_fs=device_fs)

    resp = await svc.get_bot_file("staff", "424353", "20260626_l0cxye1j", "RULES.md", "op-1")

    assert resp.success is True
    assert resp.content == ""
    assert resp.file_path == "identity/RULES.md"


@pytest.mark.asyncio
async def test_get_bot_file_surfaces_non_404_http_error():
    device_fs = MagicMock()
    device_fs.read_file = AsyncMock(side_effect=_http_status_error(502))
    svc = _read_svc(device_fs=device_fs)

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await svc.get_bot_file("staff", "424353", "20260626_l0cxye1j", "RULES.md", "op-1")
    assert exc.value.response.status_code == 502
