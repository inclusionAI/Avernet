# Backend 接入 BCS caller 配置同步 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend 在 Bot caller 开/关落库成功后,把 per-bot caller 快照(`enabled` + `source_revision`)同步到 BCS 的权威端点 `PATCH /providers/{provider_id}/bots/{provider_bot_ref}/caller`,失败传播不吞。

**Architecture:** 传输复用 `BcnService`(已有 `bcn` 配置块 Bearer 凭据 + env-skip 模式);core 层新端口 `BcsCallerConfigSyncProtocol`,由 DI 模块里的薄适配器(`BcnBackedCallerConfigSync`)把 `BcnServiceError` 映射为域错误 `CallerBcsSyncError`;`CallerIdentityService.update_mcp_call_type` / `update_cli_call_type` 成功路径末尾调用端口。

**Tech Stack:** Python 3.12 + FastAPI + injector DI + httpx(经由 `HttpClient` 插件口)。测试 `DEPLOY_PROFILE=test uv run pytest`;传输测试用 `LocalHttpClient`(MockSeam,未 stub 的调用直接抛错,不会真发网络)。

**Spec:** `docs/superpowers/specs/2026-10-29-backend-bcs-caller-config-sync-design.md`(已提交 `e9962ff7b`)

**所有命令的 cwd 都是 `apps/backend/`。**

## 工程师必读事实(零上下文假设)

1. **触发点为什么是 update 落库后**:`CallerIdentityService.get_bot_call_type`(`src/agentclaw/community/core/caller_identity/service.py:428`)直接 `del stage, publish_id` 读 `ac_bots.call_type`——聚合落库即生效,没有 draft/online 分态,不挂 publish 流程。
2. **revision 现成**:repo 层 `replace_draft_call_type` 返回的 `DraftCallTypeMutationResult.revision` 就是本次 bump 后的 `ac_bots.caller_config_revision`(`core/repository/implementations/identity/caller_identity.py:182-199`);CLI 侧 `CliCallTypeMutationResult.caller_config_revision` 同理。BCS 的 `source_revision` 语义 = per-bot 单调 u64,与环境语义逐字对齐(CAS:更高才 applied,同值 unchanged,更低 stale)。
3. **BCS wire 契约**(见 `apps/bcs/crates/contracts/bcs-protocol/src/http/provider.rs:426`,`sync_status ∈ {applied, unchanged, stale}`,分歧在 200 响应体里表达、不出 4xx)。直达 BCS 返回裸 JSON `{"sync_status": ...}`;corp BCN 管理面包一层 `{"success": bool, "data": {...}}`(见 `switch_bot` 现有实现 `bcn_service.py:477`)。传输层两者都要兼容。
4. **BcnService env 门**:`_get_provider_config`(`bcn_service.py:61-104`)只在 `prod`/`pre`、或 `dev`+`provider_registration_enabled` 且凭据齐全时返回配置;否则方法应 skip(dev/singlebox 自动降级,不 new 配置项)。
5. **补偿不推 BCS**:Agent Principal 同步失败时 repo 值被补偿回滚;BCS 同步在补偿之后(成功路径),回滚值 = BCS 已知值,无需推。
6. **纪律**(CLAUDE.md):不整文件 format、只改该改的行;write 失败必须传播;`cargo` 无关;提交信息 `<type>(backend): <outcome>`。

## File structure map

| 动作 | 文件 | 职责 |
|---|---|---|
| Modify | `src/agentclaw/community/core/caller_identity/contracts.py` | 新增 `CallerBcsSyncError`(域错误族) |
| Modify | `src/agentclaw/community/core/caller_identity/protocols.py` | 新增 `BcsCallerConfigSyncProtocol` 端口 |
| Modify | `src/agentclaw/community/api/caller_identity_service.py` | api 层 re-export 两个新名字 |
| Modify | `src/agentclaw/community/core/bot_management/services/bcn_service.py` | 新增 `sync_bot_caller_config` 传输方法 |
| Modify | `src/agentclaw/community/di/modules/caller_identity_module.py` | 新增 `BcnBackedCallerConfigSync` 适配器 + 端口绑定 + service 装配传参 |
| Modify | `src/agentclaw/community/core/caller_identity/service.py` | 两个 update 挂钩 + 构造函数新依赖 |
| Create | `tests/community/core/caller_identity/test_bcs_caller_sync_contracts.py` | 契约微测(端口形状 + 错误 code) |
| Create | `tests/community/core/bot_management/services/test_bcn_service_sync_caller_config.py` | 传输层 TDD |
| Create | `tests/community/di/modules/test_caller_identity_module_bcs_sync.py` | 适配器微测 |
| Modify | `tests/community/core/caller_identity/test_service.py` | `_service` helper 注入 mock 端口 + MCP 侧新测试 |
| Modify | `tests/community/core/caller_identity/test_cli_owner_sync.py` | `_world` helper 注入 mock 端口 |

---

### Task 1: 域错误 + 端口协议 + api re-export

**Files:**
- Modify: `src/agentclaw/community/core/caller_identity/contracts.py`
- Modify: `src/agentclaw/community/core/caller_identity/protocols.py`
- Modify: `src/agentclaw/community/api/caller_identity_service.py`
- Test: `tests/community/core/caller_identity/test_bcs_caller_sync_contracts.py`(新建)

- [ ] **Step 1: 写失败的契约测试**

新建 `tests/community/core/caller_identity/test_bcs_caller_sync_contracts.py`:

```python
"""Contract checks for the BCS caller-config sync port and its domain error."""

from __future__ import annotations

from agentclaw.community.core.errors import InternalError


def _import_contracts():
    from agentclaw.community.core.caller_identity import contracts

    return contracts


def _import_protocols():
    from agentclaw.community.core.caller_identity import protocols

    return protocols


class _StubSync:
    def sync_caller_config(
        self, *, bot_id: str, owner_id: str, enabled: bool, source_revision: int
    ) -> None:
        del bot_id, owner_id, enabled, source_revision


def test_bcs_caller_sync_protocol_is_satisfied_by_method_shape() -> None:
    protocols = _import_protocols()
    assert isinstance(_StubSync(), protocols.BcsCallerConfigSyncProtocol)


def test_caller_bcs_sync_error_is_an_internal_error_family_member() -> None:
    contracts = _import_contracts()
    assert issubclass(contracts.CallerBcsSyncError, InternalError)
    assert "CallerBcsSyncError" in contracts.__all__
    assert "BcsCallerConfigSyncProtocol" in _import_protocols().__all__
```

(用延迟 import,让 *缺失* 状态下 import 错误被测试兜住而不是 collection error。)

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/caller_identity/test_bcs_caller_sync_contracts.py -v`
Expected: FAIL(ImportError / AttributeError:`CallerBcsSyncError` / `BcsCallerConfigSyncProtocol` 不存在)

- [ ] **Step 3: 实现**

`contracts.py` — 在 `CallerMcpSyncError` 定义之后追加(风格对齐 `CallerMcpSyncError`,`core/caller_identity/contracts.py:61-63`):

```python
class CallerBcsSyncError(InternalError):
    def __init__(self) -> None:
        super().__init__("CALLER_BCS_SYNC_FAILED")
```

`contracts.py` `__all__` 里按字母序插入 `"CallerBcsSyncError"`。

`protocols.py` — 在 `CallerMcpSyncProtocol`(`protocols.py:15-29`)之后追加:

```python
@runtime_checkable
class BcsCallerConfigSyncProtocol(Protocol):
    """Push the Bot caller-config snapshot to the BCS authoritative sink."""

    def sync_caller_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        enabled: bool,
        source_revision: int,
    ) -> None: ...
```

`protocols.py` `__all__` 追加 `"BcsCallerConfigSyncProtocol"`(列表按现有顺序,加在 `"CallerMcpSyncProtocol"` 旁边)。

`api/caller_identity_service.py` — import 块从 `caller_identity_service_protocol` 追加两个 re-export(放在 `CallerMcpSyncError` 旁边),`__all__` 同步加 `"CallerBcsSyncError"` 与 `"BcsCallerConfigSyncProtocol"`:

```python
from agentclaw.community.core.caller_identity.protocols import (
    BcsCallerConfigSyncProtocol,
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/caller_identity/test_bcs_caller_sync_contracts.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/caller_identity/contracts.py \
        src/agentclaw/community/core/caller_identity/protocols.py \
        src/agentclaw/community/api/caller_identity_service.py \
        tests/community/core/caller_identity/test_bcs_caller_sync_contracts.py
git commit -m "feat(backend): add the BCS caller-config sync port and domain error

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: `BcnService.sync_bot_caller_config` 传输方法

**Files:**
- Modify: `src/agentclaw/community/core/bot_management/services/bcn_service.py`(新方法放在 `delete_provider_bot` 之后、`get_attributes` 之前)
- Test: `tests/community/core/bot_management/services/test_bcn_service_sync_caller_config.py`(新建)

- [ ] **Step 1: 写失败的传输测试**

新建 `tests/community/core/bot_management/services/test_bcn_service_sync_caller_config.py`(骨架逐字抄 `test_bcn_service_switch_bot.py:1-90` 的 fixture/mock 风格):

```python
"""Tests for BcnService.sync_bot_caller_config.

caller 配置快照同步(PATCH /providers/{pid}/bots/{ref}/caller)。
对应实现: bcn_service.sync_bot_caller_config
风格: LocalHttpClient + MockSeam——stub 响应、断言调用,未 stub 即抛错。
"""
from typing import Callable
from unittest.mock import Mock, patch

import httpx
import pytest

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnService,
    BcnServiceError,
)
from agentclaw.community.di.config import BcnConfig
from agentclaw.community.plugins.local.http_client import LocalHttpClient

_TEST_BCN_CONFIG = BcnConfig(
    base_url="http://fake-bcn:21000",
    provider_id="prv_40354c8a",
    provider_admin_token="test-bcn-token",
)

_EXPECTED_PATH = "/providers/prv_40354c8a/bots/20260502_1cjjh1ik:100000/caller"
_EXPECTED_PAYLOAD = {
    "snapshot": {
        "source_revision": 5,
        "caller_identity": {
            "enabled": True,
            "minimum_isolation": "caller_container",
        },
    }
}


@pytest.fixture
def http() -> LocalHttpClient:
    return LocalHttpClient(base_url="http://fake-bcn:21000")


@pytest.fixture
def service(http) -> BcnService:
    return BcnService(http_client=http, config=_TEST_BCN_CONFIG, timeout=5.0)


def _ok_response(status: int = 200, body: dict | None = None) -> Mock:
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.text = ""
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=Mock(), response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _sync(service: BcnService, *, enabled: bool = True, revision: int = 5) -> dict:
    return service.sync_bot_caller_config(
        teamclaw_bot_uuid="20260502_1cjjh1ik",
        owner_workno="100000",
        enabled=enabled,
        source_revision=revision,
    )


class TestSyncCallerConfigEnvSelection:
    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="dev",
    )
    def test_dev_env_skipped_no_http_call(self, _mock_env, service, http):
        result = _sync(service)

        assert result["skipped"] is True
        assert result["provider_bot_ref"] == "20260502_1cjjh1ik:100000"
        assert http.calls_to("patch") == []


class TestSyncCallerConfigTransport:
    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_applied_returns_sync_status(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "applied"}))

        result = _sync(service)

        assert result["skipped"] is False
        assert result["sync_status"] == "applied"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_unchanged_returns_sync_status(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "unchanged"}))

        result = _sync(service)

        assert result["sync_status"] == "unchanged"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_envelope_wrapped_success_response_is_unwrapped(self, _mock_env, service, http):
        # corp BCN 管理面会包 {"success": true, "data": {...}} — 必须解开。
        http.set_response("patch", _ok_response(200, {
            "success": True,
            "data": {"sync_status": "applied"},
        }))

        result = _sync(service)

        assert result["sync_status"] == "applied"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_stale_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "stale"}))

        with pytest.raises(BcnServiceError):
            _sync(service)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_http_404_raises_bcn_service_error(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(404, {"error": "provider bot not found"}))

        with pytest.raises(BcnServiceError):
            _sync(service)

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_request_shape_path_payload_and_auth(self, _mock_env, service, http):
        http.set_response("patch", _ok_response(200, {"sync_status": "applied"}))

        _sync(service, enabled=True, revision=5)

        calls = http.calls_to("patch")
        assert len(calls) == 1
        record = calls[0]
        assert record.args[0] == _EXPECTED_PATH
        assert record.kwargs["json"] == _EXPECTED_PAYLOAD
        assert record.kwargs["headers"]["Authorization"] == "Bearer test-bcn-token"

    @patch(
        "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
        return_value="prod",
    )
    def test_timeout_raises_bcn_service_error(self, _mock_env, service, http):
        def _raise_timeout(*_args, **_kwargs):
            raise httpx.TimeoutException("read timeout")

        http.set_override("patch", _raise_timeout)

        with pytest.raises(BcnServiceError):
            _sync(service)
```

注意:MockSeam 的 `set_override`/`set_response`/`calls_to` 见 `plugins/local/_mock_seam.py` 文档;`CallRecord` 字段为 `(method, args, kwargs)`,所以 `record.args[0]` 是 path 位置参、`record.kwargs["json"]` 是 payload。若断言时字段名对不上,以 `plugins/local/_mock_seam.py` 实际实现为准调整(不要改 prod 代码来迁就测试)。

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_bcn_service_sync_caller_config.py -v`
Expected: FAIL — `AttributeError: 'BcnService' object has no attribute 'sync_bot_caller_config'`

- [ ] **Step 3: 实现传输方法**

`bcn_service.py` — `delete_provider_bot` 方法之后追加(错误处理骨架逐字对齐 `switch_bot`,`bcn_service.py:470-515`):

```python
    def sync_bot_caller_config(
        self,
        *,
        teamclaw_bot_uuid: str,
        owner_workno: str,
        enabled: bool,
        source_revision: int,
    ) -> Dict[str, Any]:
        """同步 Bot caller 配置快照到 BCS 权威端点。

        调用 PATCH /providers/{provider_id}/bots/{provider_bot_ref}/caller,
        按 ``source_revision`` CAS(BCS 侧 per-bot 单调版本)。成功 sync_status
        ∈ {applied, unchanged};stale 表示 BCS 侧持有更高 revision(两边
        计数器分叉),按错误传播,不静默吞掉。

        只传非身份控制面数据(enabled / minimum_isolation);身份材料
        (token / session / iam)绝不进入本链路 —— bcs-domain caller_identity
        契约红线。

        Args:
            teamclaw_bot_uuid: TC 的 bot uuid
            owner_workno: bot 所有者工号
            enabled: bot 级 caller 开关(bot_call_type == CALLER)
            source_revision: ac_bots.caller_config_revision(本次 bump 后的值)

        Returns:
            dict: skipped=True(env 无凭据跳过)/ sync_status + provider_bot_ref

        Raises:
            BcnServiceError: stale / 4xx / 5xx / 超时 / 其他网络异常
        """
        provider_bot_ref = f"{teamclaw_bot_uuid}:{owner_workno}"
        env = get_current_env()

        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            logger.info(
                f"[BcnService.sync_bot_caller_config] env={env} skipped "
                f"(no provider credentials), provider_bot_ref={provider_bot_ref}"
            )
            return {
                "skipped": True,
                "provider_bot_ref": provider_bot_ref,
            }

        provider_id = provider_cfg["provider_id"]
        provider_admin_token = provider_cfg["provider_admin_token"]
        payload: Dict[str, Any] = {
            "snapshot": {
                "source_revision": source_revision,
                "caller_identity": {
                    "enabled": enabled,
                    "minimum_isolation": "caller_container",
                },
            }
        }
        path = f"/providers/{provider_id}/bots/{provider_bot_ref}/caller"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider_admin_token}",
        }
        logger.info(
            f"[BcnService.sync_bot_caller_config] PATCH {path} "
            f"enabled={enabled} source_revision={source_revision}"
        )

        try:
            response = self._http.patch(
                path, json=payload, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
            body = response.json()
            # 直达 BCS 返回裸 PATCH 响应;corp BCN 管理面包
            # {"success": bool, "data": {...}}。有信封时解开,绝不杜撰载荷。
            if isinstance(body, dict) and isinstance(body.get("success"), bool):
                if not body["success"]:
                    error_msg = (
                        body.get("error")
                        or body.get("message")
                        or "Unknown error"
                    )
                    raise BcnServiceError(
                        f"BCN caller config sync API error: {error_msg}"
                    )
                body = body.get("data") or {}
            sync_status = str((body or {}).get("sync_status") or "")
            if sync_status not in ("applied", "unchanged"):
                raise BcnServiceError(
                    f"BCN caller config sync rejected: sync_status={sync_status!r} "
                    f"(stale = BCS holds a higher source_revision) "
                    f"provider_bot_ref={provider_bot_ref} "
                    f"source_revision={source_revision}"
                )
            logger.info(
                f"[BcnService.sync_bot_caller_config] OK "
                f"provider_bot_ref={provider_bot_ref} sync_status={sync_status} "
                f"enabled={enabled} source_revision={source_revision}"
            )
            return {
                "skipped": False,
                "sync_status": sync_status,
                "provider_bot_ref": provider_bot_ref,
            }
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500] if e.response else "No response"
            status = e.response.status_code if e.response else "N/A"
            logger.error(
                f"[BcnService.sync_bot_caller_config] HTTP error: "
                f"status={status} body={error_body} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(
                f"BCN caller config sync HTTP error: {status} - {error_body}"
            )
        except httpx.TimeoutException as e:
            logger.error(
                f"[BcnService.sync_bot_caller_config] Timeout: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN caller config sync timeout: {e}")
        except BcnServiceError:
            raise
        except Exception as e:  # pragma: no cover - 传输层安全网
            logger.error(
                f"[BcnService.sync_bot_caller_config] Unexpected error: {e} "
                f"provider_bot_ref={provider_bot_ref}"
            )
            raise BcnServiceError(f"BCN caller config sync error: {e}")
```

(`self._http.patch` 已存在:`plugin_api/http_client.py:119`。)

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_bcn_service_sync_caller_config.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_management/services/bcn_service.py \
        tests/community/core/bot_management/services/test_bcn_service_sync_caller_config.py
git commit -m "feat(backend): add BcnService caller-config snapshot sync transport

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: DI 适配器 + 端口绑定

**Files:**
- Modify: `src/agentclaw/community/di/modules/caller_identity_module.py`
- Test: `tests/community/di/modules/test_caller_identity_module_bcs_sync.py`(新建)

- [ ] **Step 1: 写失败的适配器测试**

新建 `tests/community/di/modules/test_caller_identity_module_bcs_sync.py`:

```python
"""Adapter tests: BcnService transport outcomes map onto the domain sync port."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnServiceError,
)
from agentclaw.community.core.caller_identity.contracts import CallerBcsSyncError
from agentclaw.community.core.caller_identity.protocols import (
    BcsCallerConfigSyncProtocol,
)
from agentclaw.community.di.modules.caller_identity_module import (
    BcnBackedCallerConfigSync,
)


def _adapter(transport: MagicMock) -> BcnBackedCallerConfigSync:
    adapter = BcnBackedCallerConfigSync(bcn_service=transport)
    assert isinstance(adapter, BcsCallerConfigSyncProtocol)
    return adapter


def test_transport_error_maps_to_caller_bcs_sync_error() -> None:
    transport = MagicMock()
    transport.sync_bot_caller_config.side_effect = BcnServiceError("boom")

    with pytest.raises(CallerBcsSyncError):
        _adapter(transport).sync_caller_config(
            bot_id="bot-1", owner_id="owner-1", enabled=True, source_revision=3
        )


def test_skip_result_is_silent_success() -> None:
    transport = MagicMock()
    transport.sync_bot_caller_config.return_value = {"skipped": True}

    result = _adapter(transport).sync_caller_config(
        bot_id="bot-1", owner_id="owner-1", enabled=True, source_revision=3
    )

    assert result is None
    transport.sync_bot_caller_config.assert_called_once_with(
        teamclaw_bot_uuid="bot-1",
        owner_workno="owner-1",
        enabled=True,
        source_revision=3,
    )


def test_applied_result_is_silent_success() -> None:
    transport = MagicMock()
    transport.sync_bot_caller_config.return_value = {
        "skipped": False,
        "sync_status": "applied",
    }

    assert (
        _adapter(transport).sync_caller_config(
            bot_id="bot-1", owner_id="owner-1", enabled=False, source_revision=4
        )
        is None
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/di/modules/test_caller_identity_module_bcs_sync.py -v`
Expected: FAIL — ImportError(`BcnBackedCallerConfigSync` 不存在)

- [ ] **Step 3: 实现适配器与绑定**

`caller_identity_module.py`:

import 区追加(放在现有 caller_identity core import 附近,保持既有 import 风格、不做无关重排):

```python
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError
from agentclaw.community.core.caller_identity.contracts import CallerBcsSyncError
from agentclaw.community.core.caller_identity.protocols import (
    BcsCallerConfigSyncProtocol,
)
from agentclaw.community.log import get_logger
```

模块级(在 `class CallerIdentityModule` 定义之前)加适配器类:

```python
class BcnBackedCallerConfigSync:
    """Adapt BcnService transport outcomes onto the caller sync port.

    transport 的 BcnServiceError 在端口层折叠为域错误 CallerBcsSyncError,
    交由调用方(更新操作)整体失败传播;skipped 是 env 门降级,静默成功。
    """

    def __init__(self, bcn_service: BcnService) -> None:
        self._bcn_service = bcn_service

    def sync_caller_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        enabled: bool,
        source_revision: int,
    ) -> None:
        logger = get_logger()
        try:
            result = self._bcn_service.sync_bot_caller_config(
                teamclaw_bot_uuid=bot_id,
                owner_workno=owner_id,
                enabled=enabled,
                source_revision=source_revision,
            )
        except BcnServiceError as exc:
            logger.warning(
                "caller_config_bcs_sync_failed bot_id=%s owner_id=%s "
                "enabled=%s source_revision=%s error=%s",
                bot_id,
                owner_id,
                enabled,
                source_revision,
                exc,
            )
            raise CallerBcsSyncError from exc
        if result.get("skipped"):
            logger.info(
                "caller_config_bcs_sync_skipped bot_id=%s owner_id=%s "
                "enabled=%s source_revision=%s",
                bot_id,
                owner_id,
                enabled,
                source_revision,
            )
        else:
            logger.info(
                "caller_config_bcs_sync_succeeded bot_id=%s owner_id=%s "
                "enabled=%s source_revision=%s sync_status=%s",
                bot_id,
                owner_id,
                enabled,
                source_revision,
                result.get("sync_status"),
            )
```

`CallerIdentityModule` 里加端口 provider(放在 `caller_identity_service` provider 之前):

```python
    @singleton
    @provider
    @inject
    def bcs_caller_config_sync(
        self,
        bcn_service: BcnService,
    ) -> BcsCallerConfigSyncProtocol:
        """Bind the BcnService-backed adapter to the caller sync port."""
        return BcnBackedCallerConfigSync(bcn_service)
```

(BcnService 由 `BotManagementModule` 的 `bcn_service` provider 提供,同一容器装配,`di/container.py:201`;跨模块注入无需新绑定。)

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/di/modules/test_caller_identity_module_bcs_sync.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/di/modules/caller_identity_module.py \
        tests/community/di/modules/test_caller_identity_module_bcs_sync.py
git commit -m "feat(backend): bind the BcnService-backed BCS caller sync adapter

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 挂钩 `update_mcp_call_type` / `update_cli_call_type`

**Files:**
- Modify: `src/agentclaw/community/core/caller_identity/service.py`
- Modify: `src/agentclaw/community/di/modules/caller_identity_module.py`(`caller_identity_service` provider 传参)
- Modify: `tests/community/core/caller_identity/test_service.py`(`_service` helper + 新测试)
- Modify: `tests/community/core/caller_identity/test_cli_owner_sync.py`(`_world` helper)

- [ ] **Step 1: 写失败的服务层测试**

`test_service.py`:

1. `_service` helper(`test_service.py:50-97`,完整替换为下面这版——变化点仅三处:签名加 `bcs_caller_sync=None`、构造传入、`SimpleNamespace` 暴露;其余行保持逐字不变):

```python
def _service(
    *,
    bot: dict[str, object],
    cli_scope_reconciler=None,
    repository=None,
    passport_plugin=None,
    without_cli_scope_reconciler: bool = False,
    collaborator_service=None,
    bcs_caller_sync=None,
):
    bot_repository = MagicMock()
    bot_repository.get_by_id.return_value = bot
    bot_repository.get_unique_by_id.return_value = bot
    bot_repository.get_by_id_and_owner.return_value = bot
    bot_repository.get_by_id_and_entity.return_value = bot
    collaborator_repository = MagicMock()
    lock_repository = MagicMock()
    lock_repository.get_by_key.return_value = None
    mcp_provider = MagicMock()
    repository = repository or MagicMock()
    mcp_sync_service = AsyncMock()
    passport_plugin = None if without_cli_scope_reconciler else passport_plugin or MagicMock()
    cli_scope_reconciler = (
        None
        if without_cli_scope_reconciler
        else cli_scope_reconciler or MagicMock()
    )
    bcs_caller_sync = bcs_caller_sync or MagicMock()
    service = CallerIdentityService(
        session_validator=MagicMock(),
        bot_repository=bot_repository,
        collaborator_repository=collaborator_repository,
        lock_repository=lock_repository,
        mcp_provider=mcp_provider,
        repository=repository,
        mcp_sync_service=mcp_sync_service,
        passport_plugin=passport_plugin,
        cli_scope_reconciler=cli_scope_reconciler,
        collaborator_service=collaborator_service,
        bcs_caller_sync=bcs_caller_sync,
    )
    return service, SimpleNamespace(
        bot_repository=bot_repository,
        collaborator_repository=collaborator_repository,
        lock_repository=lock_repository,
        mcp_provider=mcp_provider,
        repository=repository,
        mcp_sync_service=mcp_sync_service,
        passport_plugin=passport_plugin,
        cli_scope_reconciler=cli_scope_reconciler,
        bcs_caller_sync=bcs_caller_sync,
    )
```

2. import 区追加:

```python
from agentclaw.community.core.caller_identity.contracts import CallerBcsSyncError
```

3. 在 `test_mcp_update_syncs_complete_identity_manifest_to_agent_principal`(`test_service.py:738`)之后追加三个测试(前两个的布置逐字复用该测试的 `_service`/mock 安排):

```python
@pytest.mark.asyncio
async def test_mcp_update_pushes_caller_snapshot_to_bcs() -> None:
    service, deps = _service(bot=_bot())
    deps.bot_repository.get_by_id_and_entity.return_value = _bot()
    deps.lock_repository.get_by_key.return_value = SimpleNamespace(
        holder_user_id="owner-1",
        id=7,
    )
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar", "name": "Calendar"},
    ]
    deps.repository.replace_draft_call_type.return_value = DraftCallTypeMutationResult(
        previous_explicit_call_type=None,
        bot_call_type=McpCallType.CALLER,
        revision=5,
    )
    deps.repository.list_draft_call_types.return_value = {
        "calendar": McpCallType.CALLER,
    }
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.return_value = {
        "success": True,
    }

    await service.update_mcp_call_type(
        bot_id="bot-1",
        server_code="calendar",
        call_type=McpCallType.CALLER,
        actor_id="owner-1",
        lock_epoch=7,
        entity_id="entity-1",
    )

    deps.bcs_caller_sync.sync_caller_config.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        enabled=True,
        source_revision=5,
    )


@pytest.mark.asyncio
async def test_mcp_update_folds_owner_aggregate_to_disabled_bcs_snapshot() -> None:
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = None
    deps.repository.replace_draft_call_type.return_value = DraftCallTypeMutationResult(
        previous_explicit_call_type=McpCallType.CALLER,
        bot_call_type=McpCallType.OWNER,
        revision=6,
    )
    deps.repository.list_draft_call_types.return_value = {}
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.return_value = {
        "success": True,
    }

    await service.update_mcp_call_type(
        bot_id="bot-1",
        server_code="calendar",
        call_type=McpCallType.OWNER,
        actor_id="owner-1",
    )

    deps.bcs_caller_sync.sync_caller_config.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        enabled=False,
        source_revision=6,
    )


@pytest.mark.asyncio
async def test_mcp_update_bcs_sync_failure_raises_without_compensation() -> None:
    # BCS 同步失败 → 整体失败传播(CallerBcsSyncError),但 Agent Principal
    # 同步已经成功,补偿绝不能跑(补偿只回滚引擎侧失败)。
    service, deps = _service(bot=_bot())
    deps.mcp_provider.collect_bot_active_mcps.return_value = [
        {"server_code": "calendar"}
    ]
    deps.lock_repository.get_by_key.return_value = None
    deps.repository.replace_draft_call_type.return_value = DraftCallTypeMutationResult(
        previous_explicit_call_type=None,
        bot_call_type=McpCallType.CALLER,
        revision=1,
    )
    deps.repository.list_draft_call_types.return_value = {
        "calendar": McpCallType.CALLER,
    }
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.return_value = {
        "success": True,
    }
    deps.bcs_caller_sync.sync_caller_config.side_effect = CallerBcsSyncError()

    with pytest.raises(CallerBcsSyncError):
        await service.update_mcp_call_type(
            bot_id="bot-1",
            server_code="calendar",
            call_type=McpCallType.CALLER,
            actor_id="owner-1",
        )

    deps.repository.compensate_draft_call_type.assert_not_called()
```

4. 既有 CLI 成功测试 `test_cli_caller_updates_full_scope_and_returns_bot_aggregate`(`test_service.py:119`)末尾追加断言(它已布置 `caller_config_revision=1`)——在最后的 `logged` 断言块之后加:

```python
    deps.bcs_caller_sync.sync_caller_config.assert_called_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        enabled=True,
        source_revision=1,
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/caller_identity/test_service.py -v`
Expected: 新测试 FAIL(TypeError:构造函数无 `bcs_caller_sync` 参数);既有测试同样 FAIL(它们经共享 helper 走同一构造)

- [ ] **Step 3: 实现服务挂钩**

`core/caller_identity/service.py`:

1. protocols import 块追加 `BcsCallerConfigSyncProtocol`(`service.py:49-53`):
2. 构造函数(`service.py:72-102`)在 `session_validator` 之后加必填参数(不 `T | None`——BCS 端口在所有 profile 都可绑定,传输层自降级):

```python
        bcs_caller_sync: BcsCallerConfigSyncProtocol,
```

并存储(与其它 `self._xxx` 同风格):

```python
        self._bcs_caller_sync = bcs_caller_sync
```

3. `update_mcp_call_type`(`service.py:104-224`)在 Agent Principal 同步检查块结束(raise `CallerMcpSyncError` 的 if 块之后)、最终 `logger.info(... "caller_mcp_call_type_updated ...")` 之前插入:

```python
        self._bcs_caller_sync.sync_caller_config(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            enabled=mutation.bot_call_type is McpCallType.CALLER,
            source_revision=mutation.revision,
        )
```

(`mutation.revision` 即本次 bump 后的 `ac_bots.caller_config_revision`,见事实 2。)

4. `update_cli_call_type`(`service.py:226-387`)在 reconcile try/except 成功之后、最终 `logger.info(... "cli_call_type_update_succeeded ...")` 之前插入:

```python
        self._bcs_caller_sync.sync_caller_config(
            bot_id=bot_id,
            owner_id=str(bot["owner_id"]),
            enabled=mutation.bot_call_type is McpCallType.CALLER,
            source_revision=mutation.caller_config_revision,
        )
```

(注意:这两处插入**不包**任何 try/except——端口层已把 transport 错误折叠为域错误 `CallerBcsSyncError`,域错误向上自然传播,补偿逻辑不涉及。)

5. `di/modules/caller_identity_module.py` 的 `caller_identity_service` provider 签名与传参加入:

```python
    @singleton
    @provider
    @inject
    def caller_identity_service(
        self,
        bot_repository: BotRepository,
        collaborator_repository: CollaboratorRepositoryProtocol,
        lock_repository: BotCollabLockRepositoryProtocol,
        mcp_provider: BotMCPProvider,
        repository: CallerIdentityRepositoryProtocol,
        mcp_sync_service: MCPSyncServiceProtocol,
        passport_plugin: PassportPlugin,
        collaborator_service: CoreCollaboratorServiceProtocol,
        session_validator: CallerSessionValidator,
        bcs_caller_sync: BcsCallerConfigSyncProtocol,
    ) -> CallerIdentityService:
        """Construct the draft configuration and Agent Principal sync service."""
        return CallerIdentityService(
            bot_repository=bot_repository,
            collaborator_repository=collaborator_repository,
            lock_repository=lock_repository,
            mcp_provider=mcp_provider,
            repository=repository,
            mcp_sync_service=mcp_sync_service,
            passport_plugin=passport_plugin,
            collaborator_service=collaborator_service,
            session_validator=session_validator,
            bcs_caller_sync=bcs_caller_sync,
        )
```

6. `tests/community/core/caller_identity/test_cli_owner_sync.py` 的 `_world`(`test_cli_owner_sync.py:72-78`)——完整替换其中 `CallerIdentityService(...)` 构造为(仅加最后一行):

```python
    service = CallerIdentityService(
        session_validator=MagicMock(),
        bot_repository=bot_repository, collaborator_repository=MagicMock(),
        lock_repository=lock_repository, mcp_provider=mcp_provider,
        repository=repository, mcp_sync_service=MagicMock(),
        passport_plugin=passport, cli_scope_reconciler=reconciler,
        bcs_caller_sync=MagicMock(),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/caller_identity/ -v`
Expected: 全部 passed(含既有补偿/回滚测试)

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/caller_identity/service.py \
        src/agentclaw/community/di/modules/caller_identity_module.py \
        tests/community/core/caller_identity/test_service.py \
        tests/community/core/caller_identity/test_cli_owner_sync.py
git commit -m "feat(backend): push the caller config snapshot to BCS on call-type updates

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 全量验证

- [ ] **Step 1: 受影响的服务/端点套件**

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/caller_identity \
  tests/community/core/bot_management/services/test_bcn_service_sync_caller_config.py \
  tests/community/di/modules/test_caller_identity_module_bcs_sync.py \
  tests/community/core/caller_identity/test_bcs_caller_sync_contracts.py -v
```
Expected: 全部 passed

- [ ] **Step 2: 路由层(真实 DI 容器装配校验)**

`tests/community/endpoints/test_caller_identity_router.py` 与 `tests/community/adapters/http/openapi_v1/test_caller_identity_endpoints.py` 用的主容器会真实解析新增的 provider 链(caller_identity_service → BcsCallerConfigSyncProtocol → BcnService → QUALIFIER_BCN HttpClient + BcnConfig)。

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_caller_identity_router.py \
  tests/community/adapters/http/openapi_v1/test_caller_identity_endpoints.py -v
```
Expected: 全部 passed。若容器解析报缺绑定,说明某个 profile 容器没装 `BotManagementModule`/`HttpClientModule`——**先查 profile 装配表**(`di/profile_modules.py` / `di/container.py:201` 附近)确认根因,禁止用 `T | None` 默认值绕过。

- [ ] **Step 3: changed-line 覆盖率门(CI 前置自检)**

无法在本地直接跑出与 CI 完全一致的门,改为跑完整受影响目录 + 让 pre-push 全门接管:

```bash
OCB_PRE_PUSH_RUN_CI=1 git push  # 提交最终任务时;含 SAST + 单测 + changed-line 覆盖 ≥80%
```
Expected: hook 通过。若覆盖率不足,补的对应用例放回 Task 4 的测试文件。

- [ ] **Step 4: 手动/环境验证记录(不阻塞合并)**

singlebox 无 `bcn` 凭据,backend 侧只会走 skip 路径——确认日志即可:

```bash
./singlebox/singlebox.sh start bcs_frontend   # 或已有环境
# 在 workbench 里对一个 service bot 切换某 MCP 的 call-type,然后:
grep "caller_config_bcs_sync_skipped" <backend log 路径>
```
Expected: 出现 `caller_config_bcs_sync_skipped ... enabled=... source_revision=...`(证明挂钩真实生效,而非只在单测里)。

BCS 侧端点的行为(含无 `[caller_identity]` 装配段时 PATCH 存储 snapshot 是否可用——spec §8 待验证项)由 BCS 自己的契约测试与 `apps/bcs/scripts/e2e-test/caller_identity_story.py` 覆盖,不在本 backend 改动面内;若 e2e 验证发现 BCS 侧行为不符,**不改 backend**,先在 spec 上补记 + 起 BCS 侧问题。

- [ ] **Step 5: Commit(若有收尾改动)并汇报**

向调用方汇报:改了什么、哪些测试跑过(附命令与结果)、单步 Step 4 是否执行、未跑的部分如实说明。
