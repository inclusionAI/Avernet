"""Unit tests for LocalDeviceSyncPlugin BaaS mode (plan-03)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentclaw.community.core.devices.models import DeviceBindingContext
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasServiceError,
    HttpConnectionInfo,
)
from agentclaw.community.plugins.local.device_sync import LocalDeviceSyncPlugin


@pytest.fixture
def binding_ctx() -> DeviceBindingContext:
    return DeviceBindingContext(
        binding_id=42,
        device_id="bot-uuid-001",
        entity_id="staff_u001",
        adapter_port=20010,
        tenant="team_claw",
    )


@pytest.fixture
def mock_baas() -> MagicMock:
    m = MagicMock()
    m.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010",
        token="abc-token",
    )
    return m


def test_ctor_baas_mode_smoke(mock_baas, binding_ctx):
    fs = LocalDeviceSyncPlugin(
        skills_dir=None, baas_service=mock_baas, binding_ctx=binding_ctx
    )
    assert fs is not None
    assert fs._is_baas_mode is True


def test_ctor_pathlib_mode_smoke_no_args():
    fs = LocalDeviceSyncPlugin()
    assert fs is not None
    assert fs._is_baas_mode is False


def test_ctor_pathlib_mode_smoke_skills_dir_only(tmp_path):
    fs = LocalDeviceSyncPlugin(skills_dir=tmp_path / "skills")
    assert fs is not None
    assert fs._is_baas_mode is False


def test_ctor_partial_baas_args_falls_back_to_pathlib(mock_baas):
    """Only baas_service or only binding_ctx → fall back to pathlib mode."""
    fs1 = LocalDeviceSyncPlugin(baas_service=mock_baas, binding_ctx=None)
    fs2 = LocalDeviceSyncPlugin(baas_service=None, binding_ctx=MagicMock())
    assert fs1._is_baas_mode is False
    assert fs2._is_baas_mode is False


def _mock_httpx_response(
    status_code: int = 200,
    json_body: dict | None = None,
):
    if json_body is None:
        json_body = {"code": 0}
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request(
            "POST", "http://10.0.0.1:20010/api/file/symlink-sync"
        ),
    )


def _patch_async_client(response: httpx.Response):
    client = MagicMock()
    client.__aenter__ = MagicMock(return_value=_AsyncMock(client))
    client.__aexit__ = MagicMock(return_value=_AsyncMock(False))
    client.post = _AsyncMock(response)
    return client


class _AsyncMock(MagicMock):
    """Subclass to keep MagicMock auto-children working AND record calls."""

    def __init__(self, value=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._value = value

    def __call__(self, *args, **kwargs):
        super().__call__(*args, **kwargs)
        return self

    def __await__(self):
        async def _coro():
            return self._value

        return _coro().__await__()


@pytest.fixture
def fs_baas(mock_baas, binding_ctx) -> LocalDeviceSyncPlugin:
    return LocalDeviceSyncPlugin(
        skills_dir=None, baas_service=mock_baas, binding_ctx=binding_ctx
    )


def test_baas_sync_symlinks_happy(fs_baas, mock_baas):
    """非空 list → POST /api/skills/symlink/bindpath, body {symlinks, clean_target_dir: False}.

    路径/body 形态在 R2 修复后改为对齐线上 ArcaDeviceSyncPlugin (bindpath)。
    clean_target_dir=False (singlebox 宿主 skills/ 下有 skills-repo/skills-local
    系统软链,不能被 adapter 误清,孤儿由 SkillSetService 单删处理)。
    详细 path/body 断言在 test_sync_symlinks_non_empty_calls_bindpath 里覆盖；
    本测试保留 happy-path 烟测 (HTTP 200 → success=True + get_http_info 被调一次)。
    """
    response = _mock_httpx_response(
        status_code=200,
        json_body={
            "code": 0,
            "data": {
                "total": 2,
                "created": ["a", "b"],
                "updated": [],
                "kept": [],
                "removed": [],
            },
        },
    )
    client = _patch_async_client(response)

    symlinks = [
        {
            "source": "/home/admin/.openclaw/workspace/skills/skills-repo/A",
            "target": "/home/admin/.openclaw/workspace/skills/A",
        },
        {
            "source": "/home/admin/.openclaw/workspace/skills/skills-repo/B",
            "target": "/home/admin/.openclaw/workspace/skills/B",
        },
    ]

    with patch("httpx.AsyncClient", return_value=client):
        result = fs_baas.sync_symlinks(symlinks)

    assert result["success"] is True
    assert "created" in result["message"]

    mock_baas.get_http_info.assert_called_once()
    call_kwargs = mock_baas.get_http_info.call_args.kwargs
    assert call_kwargs["bind_id"] == 42
    assert call_kwargs["port"] == 20010
    assert call_kwargs["path"] == "/api/skills/symlink/bindpath"
    assert call_kwargs["device_affinity"] == "staff_u001"

    post_kwargs = client.post.call_args.kwargs
    assert post_kwargs["headers"]["openclawToken"] == "abc-token"
    assert post_kwargs["json"] == {
        "symlinks": symlinks,
        "clean_target_dir": False,
    }


def test_baas_sync_symlinks_5xx_returns_failure_dict(fs_baas):
    response = _mock_httpx_response(status_code=500, json_body={"error": "disk full"})
    client = _patch_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        result = fs_baas.sync_symlinks([{"source": "x", "target": "y"}])

    # sync_symlinks 契约：错误 → {"success": False, "message": ...}（spec §4.2）
    assert result["success"] is False
    assert "message" in result


def test_baas_sync_symlinks_baas_failure_returns_failure_dict(fs_baas, mock_baas):
    mock_baas.get_http_info.side_effect = BaasServiceError("baas down")
    result = fs_baas.sync_symlinks([{"source": "x", "target": "y"}])
    assert result["success"] is False
    assert "baas down" in result["message"]


def test_baas_sync_symlinks_empty_list_short_circuits(fs_baas, mock_baas):
    """R3 修复后：空 list 直接 return success,**不**调 adapter clean。

    背景: singlebox 宿主 skills/ 下除了 skill 软链还有 skills-repo (symlink) /
    skills-local (目录) 等系统软链,adapter clean_symlinks 不区分,会把 skills-repo
    一起清掉,破坏整个 skill 目录树。
    孤儿 skill 链清理由上层 SkillSetService.deactivate_skill 单删处理。
    详细行为断言见 test_sync_symlinks_empty_skips_adapter_clean。
    """
    result = fs_baas.sync_symlinks([])

    assert result["success"] is True
    # 不调 baas (短路 return)
    mock_baas.get_http_info.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# Task 1: bindpath/clean 接口对齐 (R2 修复)
# 参考线上 ArcaDeviceSyncPlugin.sync_symlinks 的请求形态
# ────────────────────────────────────────────────────────────────────


def test_sync_symlinks_non_empty_calls_bindpath(mock_baas, binding_ctx):
    """非空 symlinks 应该 POST 到 /api/skills/symlink/bindpath
    body: {symlinks: [...], clean_target_dir: False}
    source/target 保持绝对路径 (跟线上 ArcaDeviceSyncPlugin 一致)。
    clean_target_dir=False: singlebox 宿主 skills/ 下有 skills-repo/skills-local
    系统软链,不能让 adapter 误清,孤儿由 SkillSetService 单删处理。
    """
    plugin = LocalDeviceSyncPlugin(
        skills_dir=None, baas_service=mock_baas, binding_ctx=binding_ctx
    )
    mock_baas.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010/api/skills/symlink/bindpath",
        token="abc-token",
    )

    symlinks = [
        {
            "source": "/home/admin/.openclaw/workspace/skills/skills-repo/business/aml/aml-data-query",
            "target": "/home/admin/.openclaw/workspace/skills/aml-data-query",
        }
    ]

    captured = {}

    class _Resp:
        status_code = 200
        content = b'{"data": {"total": 1, "created": ["..."], "updated": [], "kept": [], "removed": []}}'

        def json(self):
            import json as _j
            return _j.loads(self.content)

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            captured["url"] = url
            captured["json"] = kw.get("json")
            captured["headers"] = kw.get("headers")
            return _Resp()

    with patch("httpx.AsyncClient", _Client):
        result = plugin.sync_symlinks(symlinks)

    assert result["success"] is True, result
    # get_http_info 的 path 应该带 /bindpath 后缀
    mock_baas.get_http_info.assert_called_once()
    _, kwargs = mock_baas.get_http_info.call_args
    assert kwargs["path"] == "/api/skills/symlink/bindpath", kwargs["path"]
    # body 形态: {symlinks, clean_target_dir}
    assert captured["json"] == {
        "symlinks": [
            {
                "source": "/home/admin/.openclaw/workspace/skills/skills-repo/business/aml/aml-data-query",
                "target": "/home/admin/.openclaw/workspace/skills/aml-data-query",
            }
        ],
        "clean_target_dir": False,
    }, captured["json"]


def test_sync_symlinks_empty_skips_adapter_clean(mock_baas, binding_ctx):
    """空 symlinks 应该直接 return success, **不调 adapter clean**。
    原因: LocalDeviceSyncPlugin 只服务 singlebox,宿主 skills/ 下除了 skill 软链还有
    skills-repo (symlink) / skills-local (目录) 等系统软链,adapter clean_symlinks
    不区分,会把 skills-repo 一起清掉,破坏整个 skill 目录树。
    孤儿 skill 链清理由 SkillSetService.deactivate_skill 在 remove 时单删处理。
    """
    plugin = LocalDeviceSyncPlugin(
        skills_dir=None, baas_service=mock_baas, binding_ctx=binding_ctx
    )

    result = plugin.sync_symlinks([])

    assert result["success"] is True, result
    assert "empty list" in result["message"]
    # 关键断言: 不调 baas (短路 return,不打 adapter)
    mock_baas.get_http_info.assert_not_called()


def test_sync_symlinks_skips_invalid_entries(mock_baas, binding_ctx):
    """跟线上 _infer_and_clean_symlinks 一致: 过滤 source/target 空的条目。"""
    plugin = LocalDeviceSyncPlugin(
        skills_dir=None, baas_service=mock_baas, binding_ctx=binding_ctx
    )
    mock_baas.get_http_info.return_value = HttpConnectionInfo(
        http_url="http://10.0.0.1:20010/api/skills/symlink/bindpath",
        token="abc-token",
    )

    symlinks_with_garbage = [
        {"source": "", "target": "/a"},  # 缺 source
        {"source": "/b", "target": ""},  # 缺 target
        {
            "source": "/home/admin/.openclaw/workspace/skills/skills-repo/x/y",
            "target": "/home/admin/.openclaw/workspace/skills/y",
        },
    ]

    captured = {}

    class _Resp:
        status_code = 200
        content = b'{"data": {"total": 1}}'

        def json(self):
            import json as _j
            return _j.loads(self.content)

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            captured["json"] = kw.get("json")
            return _Resp()

    with patch("httpx.AsyncClient", _Client):
        plugin.sync_symlinks(symlinks_with_garbage)

    assert len(captured["json"]["symlinks"]) == 1, captured["json"]
    assert captured["json"]["symlinks"][0]["target"] == "/home/admin/.openclaw/workspace/skills/y"
