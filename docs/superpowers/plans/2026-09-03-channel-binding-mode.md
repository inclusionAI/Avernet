# 渠道绑定方式统一（binding_mode）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/openapi/v1/bots/{bot_id}/channels` 支持 `binding_mode: "plugin" | "bcn_gateway"`，bcn_gateway 渠道由 agentclaw 编排 BCS bindings API（target_type=bot）。

**Architecture:** 契约层加 `binding_mode` 顶层字段（缺省 plugin，存量零感知）+ 单一扁平 config schema + 模式校验矩阵；编排层在 `ChannelService` 增加第三分派分支（teclaw/openclaw 之外），经新 `HttpBcsChannelBindingClient` 同步 BCS；`ac_channel_config` 保持配置 SoT，`config["bcs_binding_id"]` 为服务端记账键；`engine_overrides_reader` 跳过 bcn 行。设计 spec：`docs/superpowers/specs/2026-09-03-channel-binding-mode-design.md`。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / injector DI / pytest(+asyncio) / httpx（MockTransport 测 wire 层）。

**分支:** `feat/channel-binding-mode`（已从 origin/dev 切出）。

**全局约定:**
- 所有 pytest 命令在 `src/backend/` 下执行：`cd src/backend && python -m pytest <path> -v`
- 提交信息用 `type(scope): description`，不加归属尾注
- **上线阻塞项（不在本计划内解决）**：① BCS 真实钉钉 provider（本仓库只有 test provider）；② BCS bindings 路由的服务间鉴权（当前 `require_authenticated_user` 人类会话）。本计划全部用 fake/mock 验证，不打真实 BCS。

---

### Task 1: 契约层 — binding_mode 字段与 create 侧校验矩阵

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/channels/schemas.py`
- Test: `src/backend/tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `test_channels_handlers.py` 末尾追加（文件已 import `pytest`、`ValidationError`、`ChannelCreate`）：

```python
# ── binding_mode 契约 ──────────────────────────────────────────────

def test_create_defaults_to_plugin_mode():
    channel = ChannelCreate(
        type="dingding",
        config={"client_id": "client-1", "client_secret": "secret-1"},
    )
    assert channel.binding_mode == "plugin"


def test_create_accepts_minimal_bcn_gateway_config():
    channel = ChannelCreate(
        type="dingding",
        binding_mode="bcn_gateway",
        config={
            "client_id": "client-1",
            "client_secret": "secret-1",
            "robot_code": "robot-1",
        },
    )
    assert channel.binding_mode == "bcn_gateway"


def test_create_requires_robot_code_for_bcn_gateway_mode():
    with pytest.raises(ValidationError) as exc:
        ChannelCreate(
            type="dingding",
            binding_mode="bcn_gateway",
            config={"client_id": "client-1", "client_secret": "secret-1"},
        )
    assert "robot_code" in str(exc.value)


def test_create_rejects_plugin_fields_for_bcn_gateway_mode():
    with pytest.raises(ValidationError) as exc:
        ChannelCreate(
            type="dingding",
            binding_mode="bcn_gateway",
            config={
                "client_id": "client-1",
                "client_secret": "secret-1",
                "robot_code": "robot-1",
                "dm_policy": "open",
            },
        )
    assert "dm_policy" in str(exc.value)


def test_create_rejects_bcn_fields_for_plugin_mode():
    with pytest.raises(ValidationError) as exc:
        ChannelCreate(
            type="dingding",
            config={
                "client_id": "client-1",
                "client_secret": "secret-1",
                "group_chat_scope": "per_sender",
            },
        )
    assert "group_chat_scope" in str(exc.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py -v -k binding_mode`
Expected: FAIL —— `test_create_defaults_to_plugin_mode` 报 `ChannelCreate` 无 `binding_mode` 字段（`ValidationError: Extra inputs are not permitted` 或属性缺失）

- [ ] **Step 3: 实现 schemas**

`schemas.py` 修改（保持既有字段不动）：

3a. import 行 `from pydantic import BaseModel, ConfigDict, Field` 改为：

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator
```

3b. 在 `ChannelStage = Literal["draft"]` 之后加：

```python
ChannelBindingMode = Literal["plugin", "bcn_gateway"]
GroupChatScope = Literal["per_sender", "conversation_shared"]
OutboundVisibility = Literal["full_transcript", "lead_only"]

# 模式校验矩阵：字段只属于一种绑定模式（见设计 spec §3.2）。
_PLUGIN_ONLY_FIELDS: tuple[str, ...] = (
    "card_template_key",
    "dm_policy",
    "allowlist",
    "reply_to_message",
    "aix_enable",
    "include_sender_name",
)
_BCN_ONLY_FIELDS: tuple[str, ...] = ("group_chat_scope", "outbound_visibility")


def validate_mode_matrix(
    mode: str,
    *,
    robot_code: str | None,
    fields_set: set[str],
) -> None:
    """Reject fields that do not belong to the channel's binding mode.

    Shared by the create-side pydantic validator and the router's update-side
    check (the update side knows the stored mode, which the body alone cannot).
    """
    if mode == "bcn_gateway":
        if not (robot_code or "").strip():
            raise ValueError(
                "robot_code is required when binding_mode is 'bcn_gateway'"
            )
        rejected = sorted(set(_PLUGIN_ONLY_FIELDS) & fields_set)
        if rejected:
            raise ValueError(
                "fields only valid for plugin channels were provided: "
                + ", ".join(rejected)
            )
    else:
        rejected = sorted(set(_BCN_ONLY_FIELDS) & fields_set)
        if rejected:
            raise ValueError(
                "fields only valid for bcn_gateway channels were provided: "
                + ", ".join(rejected)
            )
```

3c. `DingTalkChannelConfigCreate` 末尾（`include_sender_name` 之后）加两个字段：

```python
    group_chat_scope: GroupChatScope | None = Field(
        default=None,
        description="Group-session scoping; only valid when binding_mode is 'bcn_gateway'.",
    )
    outbound_visibility: OutboundVisibility | None = Field(
        default=None,
        description="Outbound transcript visibility; only valid when binding_mode is 'bcn_gateway'.",
    )
```

3d. `DingTalkChannelConfigUpdate` 末尾加：

```python
    group_chat_scope: GroupChatScope | None = Field(
        default=None,
        description="New group-session scoping; omit to keep. bcn_gateway channels only.",
    )
    outbound_visibility: OutboundVisibility | None = Field(
        default=None,
        description="New outbound visibility; omit to keep. bcn_gateway channels only.",
    )
```

3e. `DingTalkChannelConfig`（读投影）末尾加：

```python
    group_chat_scope: GroupChatScope | None = Field(
        default=None,
        description="Group-session scoping when binding_mode is 'bcn_gateway'; null otherwise.",
    )
    outbound_visibility: OutboundVisibility | None = Field(
        default=None,
        description="Outbound visibility when binding_mode is 'bcn_gateway'; null otherwise.",
    )
```

3f. `ChannelCreate`：`type` 字段之后加 `binding_mode` 字段和校验器：

```python
    binding_mode: ChannelBindingMode = Field(
        default="plugin",
        description=(
            "How the Channel connects: 'plugin' writes openclaw.json direct "
            "config; 'bcn_gateway' syncs a BCS binding (per-sender sessions)."
        ),
    )

    @model_validator(mode="after")
    def _check_binding_mode_matrix(self) -> "ChannelCreate":
        validate_mode_matrix(
            self.binding_mode,
            robot_code=self.config.robot_code,
            fields_set=self.config.model_fields_set,
        )
        return self
```

3g. `ChannelUpdate`：`description` 字段之后加：

```python
    binding_mode: ChannelBindingMode | None = Field(
        default=None,
        description="Must equal the stored mode; the binding mode is immutable after creation.",
    )
```

3h. `Channel`（响应）：`type` 字段之后加：

```python
    binding_mode: ChannelBindingMode = Field(
        default="plugin",
        description="How the Channel connects: 'plugin' or 'bcn_gateway'.",
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py -v`
Expected: PASS（新旧全部）

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/channels/schemas.py \
        src/backend/tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py
git commit -m "feat(channel): add binding_mode contract with mode validation matrix"
```

---

### Task 2: 错误域与 HTTP 映射 — 422 / 409

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/channel/errors.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py`（import 区 ~:113、映射表 ~:454）
- Test: `src/backend/tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `test_channels_handlers.py` 追加（先在文件顶部 import 区补充：）

```python
from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,
    ChannelModeViolationError,
)
```

```python
class _ModeViolationChannels(_Channels):
    async def set_channel_status(self, channel_id: int, status: str):
        raise ChannelModeViolationError("mode violation")


class _ConflictChannels(_Channels):
    async def set_channel_status(self, channel_id: int, status: str):
        raise ChannelBindingConflictError("conflict")


@pytest.mark.asyncio
async def test_update_status_maps_mode_violation_to_422():
    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_ModeViolationChannels([_record()]),
        locks=_Locks(),
    )
    assert response.status_code == 422
    assert json.loads(response.body)["message"] == "Channel mode violation"


@pytest.mark.asyncio
async def test_update_status_maps_binding_conflict_to_409():
    response = await update_channel_status(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelStatusUpdate(status="active"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=_ConflictChannels([_record()]),
        locks=_Locks(),
    )
    assert response.status_code == 409
    assert json.loads(response.body)["message"] == "Channel binding conflict"
```

> 注意：`update_channel_status` 的调用参数形态必须与文件内既有测试（如 `test_update_status_maps_sync_error` 一带，`_MissingBotChannels` 用例）保持一致——照抄邻近视例的参数列表；上面给出的是按 router 签名的形态，若既有视例有差异（如缺 `bot_id`），以既有视例为准。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py -v -k "mode_violation or binding_conflict"`
Expected: FAIL —— `ImportError: cannot import name 'ChannelModeViolationError'`

- [ ] **Step 3: 实现错误域与映射**

3a. `errors.py` 末尾追加：

```python
class ChannelModeViolationError(ChannelError):
    """The request's fields or binding mode conflict with the Channel's stored mode."""


class ChannelBindingConflictError(ChannelError):
    """BCS rejected the binding because it conflicts with an existing binding."""
```

3b. `responses.py`：import 区（`ChannelEditLockedError, ChannelNotFoundError, ChannelSyncError` 所在块，~:113）追加两个名字：

```python
    ChannelBindingConflictError,
    ChannelModeViolationError,
```

3c. `responses.py` 映射表（`ChannelNotFoundError: (404, "Not found")` 所在 dict，~:454）追加：

```python
    ChannelModeViolationError: (422, "Channel mode violation"),
    ChannelBindingConflictError: (409, "Channel binding conflict"),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/channel/errors.py \
        src/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py \
        src/backend/tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py
git commit -m "feat(channel): map mode violation to 422 and binding conflict to 409"
```

---

### Task 3: Router — 投影、注入、不可变校验、update 侧矩阵

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/channels/router.py`
- Test: `src/backend/tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py`（追加）

- [ ] **Step 1: 写失败测试**

追加（import 区补 `ChannelCreate` 已有；补 `ChannelUpdate` 已有）：

```python
def _bcn_body() -> ChannelCreate:
    return ChannelCreate(
        type="dingding",
        binding_mode="bcn_gateway",
        description="BCN channel",
        config={
            "client_id": "client-1",
            "client_secret": "secret-1",
            "robot_code": "robot-1",
        },
    )


@pytest.mark.asyncio
async def test_create_bcn_gateway_stores_mode_and_defaults():
    service = _Channels()

    await create_channel(
        bot_id="bot-1",
        body=_bcn_body(),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )

    create_kwargs = next(c for name, c in service.calls if name == "create")[1]
    assert create_kwargs["config"]["binding_mode"] == "bcn_gateway"
    assert create_kwargs["config"]["group_chat_scope"] == "per_sender"
    assert create_kwargs["config"]["outbound_visibility"] == "full_transcript"


@pytest.mark.asyncio
async def test_create_plugin_omits_bcn_keys():
    service = _Channels()

    await create_channel(
        bot_id="bot-1",
        body=ChannelCreate(
            type="dingding",
            config={"client_id": "client-1", "client_secret": "secret-1"},
        ),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )

    create_kwargs = next(c for name, c in service.calls if name == "create")[1]
    assert create_kwargs["config"]["binding_mode"] == "plugin"
    assert "group_chat_scope" not in create_kwargs["config"]
    assert "outbound_visibility" not in create_kwargs["config"]


@pytest.mark.asyncio
async def test_get_projects_binding_mode():
    service = _Channels([_record(config={**_record().config, "binding_mode": "bcn_gateway"})])

    response = await get_channel(
        bot_id="bot-1",
        channel_id=1,
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
    )
    payload = json.loads(response.body)["data"]
    assert payload["binding_mode"] == "bcn_gateway"
    assert payload["config"]["group_chat_scope"] == "per_sender"


@pytest.mark.asyncio
async def test_update_rejects_binding_mode_change():
    service = _Channels(
        [_record(config={**_record().config, "binding_mode": "plugin"})]
    )

    response = await update_channel(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelUpdate(binding_mode="bcn_gateway"),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )
    assert response.status_code == 422
    assert json.loads(response.body)["message"] == "Channel mode violation"


@pytest.mark.asyncio
async def test_update_rejects_plugin_field_on_bcn_channel():
    service = _Channels(
        [_record(config={
            **_record().config,
            "binding_mode": "bcn_gateway",
            "robot_code": "robot-1",
        })]
    )

    response = await update_channel(
        bot_id="bot-1",
        channel_id=1,
        body=ChannelUpdate(
            config={"client_id": "client-2", "client_secret": "secret-2", "dm_policy": "open"}
        ),
        request=_request(),
        user_id="owner-1",
        owner_id="owner-1",
        relay=_Relay(),
        service=service,
        locks=_Locks(),
        aix_config=AixConfig(),
    )
    assert response.status_code == 422
```

> `update_channel`/`get_channel` 的调用参数形态同样照抄文件内既有同名视例；上面按 router 签名给出，有差异以既有视例为准。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py -v -k "bcn or binding_mode or projects"`
Expected: FAIL —— `create_bcn_gateway_stores_mode_and_defaults` 里 `create_kwargs["config"]["binding_mode"]` KeyError（router 还没写入）

- [ ] **Step 3: 实现 router**

3a. import 区补：

```python
from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,  # 仅文档引用时可省；当前 router 不直接 raise
    ChannelEditLockedError,
    ChannelModeViolationError,
    ChannelNotFoundError,
    ChannelSyncError,
)
```
（在既有 `ChannelEditLockedError/ChannelNotFoundError/ChannelSyncError` import 块上加 `ChannelModeViolationError`。）

`.schemas` import 块补 `validate_mode_matrix`：

```python
from .schemas import (
    Channel,
    ChannelCreate,
    ChannelStatus,
    ChannelStatusUpdate,
    ChannelType,
    ChannelUpdate,
    DingTalkChannelConfig,
    validate_mode_matrix,
)
```

3b. `CHANNEL_WRITE_RESPONSES` 增加 409（422 不加——避免覆盖 FastAPI 对 pydantic 校验错误的自动 422 文档形状）：

```python
CHANNEL_WRITE_RESPONSES = {
    423: {
        "model": ErrorEnvelope,
        "description": (
            "A Bot with collaborators requires the caller to hold its edit lock."
        ),
        **error_example(423, "Edit lock required"),
    },
    409: {
        "model": ErrorEnvelope,
        "description": (
            "BCS rejected the binding because it conflicts with an existing binding."
        ),
        **error_example(409, "Channel binding conflict"),
    },
}
```

3c. `_safe_config` 返回值补两行（`include_sender_name=...` 之后）：

```python
        group_chat_scope=raw.get("group_chat_scope"),
        outbound_visibility=raw.get("outbound_visibility"),
```

3d. `_project` 补（`type=...` 之后）：

```python
        binding_mode=record.config.get("binding_mode", "plugin"),
```

3e. `create_channel` 中 `config = body.config.model_dump()` 一段替换为：

```python
    config = body.config.model_dump()
    for bcn_key in ("group_chat_scope", "outbound_visibility"):
        if config.get(bcn_key) is None:
            config.pop(bcn_key, None)
    config["binding_mode"] = body.binding_mode
    if body.binding_mode == "bcn_gateway":
        config.setdefault("group_chat_scope", "per_sender")
        config.setdefault("outbound_visibility", "full_transcript")
    config["aix_preview_url"] = aix_config.preview_url
```

3f. `update_channel` 中 `_owned_channel(...)` 取得 record 之后、`config = dict(record.config)` 一段替换为：

```python
    stored_mode = record.config.get("binding_mode", "plugin")
    if body.binding_mode is not None and body.binding_mode != stored_mode:
        raise ChannelModeViolationError(
            "binding_mode is immutable; delete and recreate the Channel to switch"
        )
    config = dict(record.config)
    patch: dict[str, Any] = {}
    if body.config is not None:
        patch = body.config.model_dump(exclude_unset=True)
        if patch.get("client_secret") is None:
            patch.pop("client_secret", None)
        config.update(patch)
    try:
        validate_mode_matrix(
            stored_mode,
            robot_code=config.get("robot_code"),
            fields_set=set(patch),
        )
    except ValueError as exc:
        raise ChannelModeViolationError(str(exc)) from exc
```

（后续 `config.setdefault("aix_preview_url", ...)`、`service.update_channel(...)`、`if record.status == "1": await _sync_active(...)` 原样保留。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/channels/router.py \
        src/backend/tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py
git commit -m "feat(channel): project binding_mode and enforce immutability on update"
```

---

### Task 4: BCS 编排客户端 — 协议、HTTP 实现、配置、DI

**Files:**
- Create: `src/backend/src/agentclaw/community/core/channel/services/bcs_binding_client.py`
- Modify: `src/backend/src/agentclaw/community/di/config.py`（`BcnConfig` 旁，~:68）
- Modify: `src/backend/src/agentclaw/community/di/modules/config_module.py`（bcn provider 后，~:354）
- Modify: `src/backend/src/agentclaw/community/di/modules/channel_module.py`
- Test: Create `src/backend/tests/community/core/channel/test_bcs_binding_client.py`

- [ ] **Step 1: 写失败测试**

新建 `test_bcs_binding_client.py`：

```python
"""HttpBcsChannelBindingClient wire-level tests (MockTransport, no real BCS)."""
import json
from datetime import UTC, datetime

import httpx
import pytest

from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,
    ChannelSyncError,
)
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.core.channel.services.bcs_binding_client import (
    HttpBcsChannelBindingClient,
)

_BINDING_PATH = "/openapi/v1/collaboration/channels/bindings"


def _record(**config_extra) -> ChannelRecord:
    config = {
        "client_id": "client-1",
        "client_secret": "secret-1",
        "robot_code": "robot-1",
        "binding_mode": "bcn_gateway",
    }
    config.update(config_extra)
    return ChannelRecord(
        id=7,
        type="dingding",
        description=None,
        identity_id="user-1",
        bind_bot_id="bot-1",
        config=config,
        status="1",
        deleted=0,
        gmt_create=datetime(2026, 9, 3, tzinfo=UTC),
        gmt_modified=datetime(2026, 9, 3, tzinfo=UTC),
        env="dev",
        stage="draft",
    )


def _client(handler) -> HttpBcsChannelBindingClient:
    return HttpBcsChannelBindingClient(
        base_url="http://bcs.test",
        service_token="token-1",
        transport=httpx.MockTransport(handler),
    )


def _ok(data) -> dict:
    return {"code": 20100, "message": "Created", "data": data, "request_id": "r-1"}


@pytest.mark.asyncio
async def test_ensure_active_posts_binding_and_returns_id():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer token-1"
        body = json.loads(request.content)
        assert body["channel_type"] == "dingtalk"
        assert body["account_ref"] == "client-1"
        assert body["target"] == {"bot": {"bot_id": "bot-1"}}
        assert body["group_chat_scope"] == "per_sender"
        assert body["outbound_visibility"] == "full_transcript"
        assert body["config"]["robot_code"] == "robot-1"
        assert body["config"]["send_mode"] == {"mode": "normal", "message_type": "markdown"}
        return httpx.Response(201, json=_ok({"id": "bcs-1"}))

    assert await _client(handler).ensure_active(_record()) == "bcs-1"
    assert ("POST", _BINDING_PATH) in seen


@pytest.mark.asyncio
async def test_ensure_active_maps_streaming_card_mode():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["config"]["send_mode"] == {
            "mode": "streaming_card",
            "card_template_id": "card-1",
            "fallback_message_type": "markdown",
        }
        return httpx.Response(201, json=_ok({"id": "bcs-1"}))

    record = _record(enable_streaming_cards=True, card_template_id="card-1")
    await _client(handler).ensure_active(record)


@pytest.mark.asyncio
async def test_ensure_active_with_stored_id_patches_active():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "PATCH":
            assert json.loads(request.content) == {"active": True}
            return httpx.Response(200, json=_ok({"id": "bcs-1"}))
        return httpx.Response(201, json=_ok({"id": "unexpected"}))

    record = _record(bcs_binding_id="bcs-1")
    assert await _client(handler).ensure_active(record) == "bcs-1"
    assert ("PATCH", f"{_BINDING_PATH}/bcs-1") in seen
    assert not any(m == "POST" for m, _ in seen)


@pytest.mark.asyncio
async def test_ensure_active_recovers_id_via_by_target_on_conflict():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(409, json={"code": 40900, "message": "conflict"})
        if request.url.path.endswith("/by-target"):
            assert "target_type=bot" in str(request.url)
            assert "target_id=bot-1" in str(request.url)
            return httpx.Response(200, json=_ok({
                "items": [{"id": "bcs-9", "account_ref": "client-1"}]
            }))
        return httpx.Response(200, json=_ok({"id": "bcs-9"}))

    assert await _client(handler).ensure_active(_record()) == "bcs-9"
    assert ("PATCH", f"{_BINDING_PATH}/bcs-9") in seen


@pytest.mark.asyncio
async def test_ensure_active_conflict_without_recovery_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"code": 40900, "message": "conflict"})
        return httpx.Response(200, json=_ok({"items": []}))

    with pytest.raises(ChannelBindingConflictError):
        await _client(handler).ensure_active(_record())


@pytest.mark.asyncio
async def test_push_config_patches_full_config():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        body = json.loads(request.content)
        assert set(body) == {"config"}
        assert body["config"]["client_id"] == "client-1"
        return httpx.Response(200, json=_ok(None))

    await _client(handler).push_config(_record(), binding_id="bcs-1")


@pytest.mark.asyncio
async def test_unconfigured_base_url_raises_sync_error():
    client = HttpBcsChannelBindingClient(base_url="", service_token="")
    with pytest.raises(ChannelSyncError):
        await client.ensure_active(_record())


@pytest.mark.asyncio
async def test_network_error_raises_sync_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ChannelSyncError):
        await _client(handler).ensure_active(_record())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/channel/test_bcs_binding_client.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agentclaw.community.core.channel.services.bcs_binding_client'`

- [ ] **Step 3: 实现客户端**

新建 `bcs_binding_client.py`：

```python
"""BCS channel-binding client for ``bcn_gateway`` channels.

``ac_channel_config`` stays the configuration source of truth; this client
projects a row into the BCS collaboration surface, which owns runtime message
routing (per-sender sessions, multi-instance affinity) for those channels.

Wire contract mirrors
``src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/`` (routes/channel.rs,
dto/channel.rs); the BCS envelope is ``{code, message, data, request_id}``.

NOTE (integration blocker, spec §7): the BCS bindings routes currently
authenticate a human session (``require_authenticated_user``). Live calls need
BCS-side service-token support; until then this client is exercised through
fakes/mocked transport only.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,
    ChannelSyncError,
)
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.log import get_logger

logger = get_logger()

_BINDING_PATH = "/openapi/v1/collaboration/channels/bindings"


@runtime_checkable
class BcsChannelBindingClientProtocol(Protocol):
    """Port used by ``ChannelService`` to orchestrate BCS bindings."""

    async def ensure_active(self, channel: ChannelRecord) -> str:
        """Create (or reactivate) the binding; returns the BCS binding id.

        Recovers a lost ``bcs_binding_id`` via the by-target lookup when BCS
        answers 409 on create, so activation stays idempotent.
        """
        ...

    async def push_config(
        self, channel: ChannelRecord, *, binding_id: str
    ) -> None:
        """Full-replace the binding config (agentclaw is the source of truth)."""
        ...

    async def set_active(self, binding_id: str, *, active: bool) -> None: ...

    async def delete_binding(self, binding_id: str) -> None: ...


def _binding_payload(channel: ChannelRecord) -> dict[str, Any]:
    """Map a stored bcn_gateway channel row to the BCS create-body shape."""
    config = channel.config
    if config.get("enable_streaming_cards", False):
        send_mode: dict[str, Any] = {
            "mode": "streaming_card",
            "card_template_id": config.get("card_template_id") or "",
            "fallback_message_type": "markdown",
        }
    else:
        send_mode = {"mode": "normal", "message_type": "markdown"}
    return {
        "channel_type": "dingtalk",
        "account_ref": config.get("client_id") or "",
        "target": {"bot": {"bot_id": channel.bind_bot_id}},
        "group_chat_scope": config.get("group_chat_scope", "per_sender"),
        "outbound_visibility": config.get("outbound_visibility", "full_transcript"),
        "config": {
            "robot_code": config.get("robot_code") or config.get("client_id") or "",
            "client_id": config.get("client_id") or "",
            "client_secret": config.get("client_secret") or "",
            "send_mode": send_mode,
        },
    }


class HttpBcsChannelBindingClient:
    """HTTP implementation against the BCS collaboration OpenAPI."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        self._transport = transport

    def _require_configured(self) -> None:
        if not self._base_url:
            raise ChannelSyncError("BCS binding client is not configured")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        return headers

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._require_configured()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    json=json_body,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise ChannelSyncError("BCS binding request failed") from exc
        if response.status_code == 409:
            raise ChannelBindingConflictError(
                "BCS binding conflicts with an existing binding"
            )
        if response.status_code >= 400:
            raise ChannelSyncError(
                f"BCS binding request failed: {response.status_code}"
            )
        return response.json()

    async def ensure_active(self, channel: ChannelRecord) -> str:
        binding_id = str(channel.config.get("bcs_binding_id") or "")
        if binding_id:
            await self.set_active(binding_id, active=True)
            return binding_id
        try:
            envelope = await self._request(
                "POST", _BINDING_PATH, json_body=_binding_payload(channel)
            )
        except ChannelBindingConflictError:
            recovered = await self._find_binding_id(channel)
            if recovered is None:
                raise
            await self.set_active(recovered, active=True)
            return recovered
        return str(envelope["data"]["id"])

    async def _find_binding_id(self, channel: ChannelRecord) -> str | None:
        """by-target lookup to recover a binding id after a lost writeback."""
        query = (
            "?target_type=bot"
            f"&target_id={quote(channel.bind_bot_id)}"
            "&channel_type=dingtalk"
        )
        envelope = await self._request("GET", f"{_BINDING_PATH}/by-target{query}")
        account_ref = channel.config.get("client_id") or ""
        for item in envelope.get("data", {}).get("items", []):
            if item.get("account_ref") == account_ref:
                recovered = str(item.get("id") or "")
                return recovered or None
        return None

    async def push_config(
        self, channel: ChannelRecord, *, binding_id: str
    ) -> None:
        if not binding_id:
            raise ChannelSyncError("channel has no bcs_binding_id to update")
        config = _binding_payload(channel)["config"]
        await self._request(
            "PATCH", f"{_BINDING_PATH}/{binding_id}", json_body={"config": config}
        )

    async def set_active(self, binding_id: str, *, active: bool) -> None:
        if not binding_id:
            raise ChannelSyncError("channel has no bcs_binding_id to update")
        await self._request(
            "PATCH",
            f"{_BINDING_PATH}/{binding_id}",
            json_body={"active": active},
        )

    async def delete_binding(self, binding_id: str) -> None:
        await self._request("DELETE", f"{_BINDING_PATH}/{binding_id}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/channel/test_bcs_binding_client.py -v`
Expected: PASS（9 条）

- [ ] **Step 5: 配置与 DI 装配**

5a. `di/config.py`：`BcnConfig`（~:68）之后加：

```python
@dataclass
class BcsBindingConfig:
    """BCS channel-binding orchestration endpoint (yaml ``bcs_binding`` block).

    Neutral empty defaults: the community build embeds no BCS host or service
    token; corp overlays provide them. Empty ``base_url`` ⇒ the client raises
    :class:`ChannelSyncError` on use, so ``bcn_gateway`` channels are
    corp-overlay-only.
    """

    base_url: str = ""
    service_token: str = ""
    timeout_seconds: float = 10.0
```

5b. `di/modules/config_module.py`：bcn provider（~:338-354）之后加（`_block` 用法与 bcn 一致）：

```python
    @singleton
    @provider
    def bcs_binding(self) -> cfg.BcsBindingConfig:
        """BCS bindings endpoint for bcn_gateway channels (neutral empty; corp
        env overlays set the host and service token)."""
        block = _block("bcs_binding")
        defaults = cfg.BcsBindingConfig()
        return cfg.BcsBindingConfig(
            base_url=str(block.get("base_url", defaults.base_url)),
            service_token=str(block.get("service_token", defaults.service_token)),
            timeout_seconds=float(
                block.get("timeout_seconds", defaults.timeout_seconds)
            ),
        )
```

5c. `di/modules/channel_module.py`：import 区加：

```python
from agentclaw.community.core.channel.services.bcs_binding_client import (
    BcsChannelBindingClientProtocol,
    HttpBcsChannelBindingClient,
)
from agentclaw.community.di import config as cfg
```

类内追加 provider：

```python
    @singleton
    @provider
    def _bcs_channel_binding_client(
        self, config: cfg.BcsBindingConfig
    ) -> BcsChannelBindingClientProtocol:
        """BCS bindings orchestration client for ``bcn_gateway`` channels."""
        return HttpBcsChannelBindingClient(
            base_url=config.base_url,
            service_token=config.service_token,
            timeout=config.timeout_seconds,
        )
```

- [ ] **Step 6: 验证 DI 解析不破**

Run: `cd src/backend && python -m pytest tests/community/core/channel/ -v`
Expected: PASS（channel 模块测试全绿）

- [ ] **Step 7: Commit**

```bash
git add src/backend/src/agentclaw/community/core/channel/services/bcs_binding_client.py \
        src/backend/src/agentclaw/community/di/config.py \
        src/backend/src/agentclaw/community/di/modules/config_module.py \
        src/backend/src/agentclaw/community/di/modules/channel_module.py \
        src/backend/tests/community/core/channel/test_bcs_binding_client.py
git commit -m "feat(channel): add BCS binding orchestration client with DI wiring"
```

---

### Task 5: ChannelService — bcn 分派、生命周期、remove_channel

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/channel/services/channel_service.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/channels/router.py`（delete 切到 remove_channel）
- Test: `src/backend/tests/community/core/channel/test_channel_service.py`（改 fixture + 追加）
- Test: `src/backend/tests/community/adapters/http/openapi_v1/channels/test_channels_handlers.py`（_Channels fake 补 remove_channel）

- [ ] **Step 1: 写失败测试**

1a. `test_channel_service.py`：import 区补：

```python
from unittest.mock import AsyncMock

from agentclaw.community.core.channel.errors import (
    ChannelBindingConflictError,
    ChannelSyncError,
)
```

fixture 区补（`mock_device_sync_dispatcher` 之后）：

```python
@pytest.fixture
def mock_bcs_client():
    """Mock BcsChannelBindingClientProtocol."""
    client = MagicMock()
    client.ensure_active = AsyncMock(return_value="bcs-binding-1")
    client.push_config = AsyncMock()
    client.set_active = AsyncMock()
    client.delete_binding = AsyncMock()
    return client
```

`channel_service` fixture 参数与构造补 `mock_bcs_client`：

```python
@pytest.fixture
def channel_service(
    mock_repository,
    mock_resolver,
    mock_device_fs_dispatcher,
    mock_bot_service,
    mock_device_sync_dispatcher,
    mock_bcs_client,
):
    """Create ChannelService with mocked dependencies."""
    return ChannelService(
        repository=mock_repository,
        resolver=mock_resolver,
        device_fs_dispatcher=mock_device_fs_dispatcher,
        bot_service=mock_bot_service,
        device_sync_dispatcher=mock_device_sync_dispatcher,
        bcs_client=mock_bcs_client,
    )
```

文件末尾追加：

```python
def _bcn_record(status: str = "1", **config_extra) -> ChannelRecord:
    config = {
        "client_id": "client-1",
        "client_secret": "secret-1",
        "robot_code": "robot-1",
        "binding_mode": "bcn_gateway",
    }
    config.update(config_extra)
    return _make_channel_record(status=status, config=config)


class TestBcnGatewayLifecycle:
    @pytest.mark.asyncio
    async def test_activate_creates_binding_and_persists_id(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        record = _bcn_record(status="0")
        mock_repository.get_by_id.return_value = record
        mock_repository.update_by_id = MagicMock()
        mock_repository.update_status_by_id = MagicMock()

        await channel_service.set_channel_status(1, "1")

        mock_bcs_client.ensure_active.assert_awaited_once_with(record)
        stored = mock_repository.update_by_id.call_args.kwargs["config"]
        assert stored["bcs_binding_id"] == "bcs-binding-1"
        mock_repository.update_status_by_id.assert_called_once_with(
            channel_id=1, status="1"
        )

    @pytest.mark.asyncio
    async def test_activate_failure_does_not_persist_status(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(status="0")
        mock_repository.update_status_by_id = MagicMock()
        mock_bcs_client.ensure_active.side_effect = ChannelSyncError("BCS down")

        with pytest.raises(ChannelSyncError):
            await channel_service.set_channel_status(1, "1")
        mock_repository.update_status_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_deactivate_patches_binding_inactive(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(
            status="1", bcs_binding_id="bcs-binding-1"
        )
        mock_repository.update_status_by_id = MagicMock()

        await channel_service.set_channel_status(1, "0")

        mock_bcs_client.set_active.assert_awaited_once_with(
            "bcs-binding-1", active=False
        )
        mock_repository.update_status_by_id.assert_called_once_with(
            channel_id=1, status="0"
        )

    @pytest.mark.asyncio
    async def test_deactivate_without_binding_id_only_persists(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(status="0")
        mock_repository.update_status_by_id = MagicMock()

        await channel_service.set_channel_status(1, "0")

        mock_bcs_client.set_active.assert_not_awaited()
        mock_repository.update_status_by_id.assert_called_once_with(
            channel_id=1, status="0"
        )

    @pytest.mark.asyncio
    async def test_sync_active_ensures_and_pushes_config(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        record = _bcn_record(status="1", bcs_binding_id="bcs-binding-1")
        mock_repository.get_by_id.return_value = record

        await channel_service.sync_active_channel(1)

        mock_bcs_client.ensure_active.assert_awaited_once_with(record)
        mock_bcs_client.push_config.assert_awaited_once_with(
            record, binding_id="bcs-binding-1"
        )

    @pytest.mark.asyncio
    async def test_remove_channel_deletes_row_then_binding(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(
            bcs_binding_id="bcs-binding-1"
        )
        mock_repository.delete_by_id = MagicMock()

        await channel_service.remove_channel(1)

        mock_repository.delete_by_id.assert_called_once_with(channel_id=1)
        mock_bcs_client.delete_binding.assert_awaited_once_with("bcs-binding-1")

    @pytest.mark.asyncio
    async def test_remove_channel_swallows_binding_delete_failure(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(
            bcs_binding_id="bcs-binding-1"
        )
        mock_repository.delete_by_id = MagicMock()
        mock_bcs_client.delete_binding.side_effect = ChannelSyncError("BCS down")

        await channel_service.remove_channel(1)  # best-effort: 不抛

        mock_repository.delete_by_id.assert_called_once_with(channel_id=1)

    @pytest.mark.asyncio
    async def test_remove_plugin_channel_never_calls_bcs(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _make_channel_record()
        mock_repository.delete_by_id = MagicMock()

        await channel_service.remove_channel(1)

        mock_bcs_client.delete_binding.assert_not_awaited()
```

1b. `test_channels_handlers.py`：`_Channels` fake 类内追加（`delete` 方法之后）：

```python
    async def remove_channel(self, channel_id: int):
        self.calls.append(("remove", channel_id))
        self.delete(channel_id)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/channel/test_channel_service.py -v -k Bcn`
Expected: FAIL —— `TypeError: ChannelService.__init__() got an unexpected keyword argument 'bcs_client'`

- [ ] **Step 3: 实现 channel_service**

3a. import 区补：

```python
from agentclaw.community.core.channel.errors import ChannelError
from agentclaw.community.core.channel.services.bcs_binding_client import (
    BcsChannelBindingClientProtocol,
)
```

3b. `__init__` 加第 6 个依赖：

```python
    @inject
    def __init__(
        self,
        repository: ChannelRepository,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
        bot_service: BotService,
        device_sync_dispatcher: DeviceSyncDispatcher,
        bcs_client: BcsChannelBindingClientProtocol,
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher
        self._bot_service = bot_service
        self._device_sync_dispatcher = device_sync_dispatcher
        self._bcs_client = bcs_client
```

3c. 类内加静态方法和记账方法（放在 `_is_teclaw_bot` 附近）：

```python
    @staticmethod
    def _is_bcn_channel(record: ChannelRecord) -> bool:
        """Whether the row routes through the BCS binding orchestration."""
        return record.config.get("binding_mode") == "bcn_gateway"

    def _store_bcs_binding_id(self, record: ChannelRecord, binding_id: str) -> None:
        """Persist the server-managed ``bcs_binding_id`` into the row's config."""
        if record.config.get("bcs_binding_id") == binding_id:
            return
        config = dict(record.config)
        config["bcs_binding_id"] = binding_id
        self._repository.update_by_id(
            channel_id=record.id,
            type=record.type,
            description=record.description,
            identity_id=record.identity_id,
            bind_bot_id=record.bind_bot_id,
            config=config,
            status=record.status,
            stage=record.stage,
        )
        record.config = config
```

3d. `set_channel_status`（取得 channel 判空后、teclaw 分支前）插入 bcn 分支：

```python
        if self._is_bcn_channel(channel):
            # 与 openclaw 路径同序：先 BCS（可能抛，fail-closed）再落库。
            if status == "1":
                binding_id = await self._bcs_client.ensure_active(channel)
                self._store_bcs_binding_id(channel, binding_id)
            else:
                binding_id = str(channel.config.get("bcs_binding_id") or "")
                if binding_id:
                    await self._bcs_client.set_active(binding_id, active=False)
            self.update_status(channel_id, status)
            return
```

3e. `_dispatch_channel_sync` 顶部插入 bcn 分支 + 新增 `_sync_bcn_binding`：

```python
    async def _dispatch_channel_sync(self, channel: ChannelRecord, *, action: str) -> None:
        """Route a channel sync to the bot's container path.

        bcn_gateway → BCS binding orchestration; teclaw → recompose + deliver
        (best-effort); everything else → the existing direct ``openclaw.json``
        write (may raise; preserved verbatim).
        """
        if self._is_bcn_channel(channel):
            await self._sync_bcn_binding(channel, action=action)
            return
        if self._is_teclaw_bot(channel.bind_bot_id, channel.identity_id):
            await self._deliver_teclaw_channel(channel)
        else:
            await self.sync_channel_to_openclaw(channel.id, action=action)

    async def _sync_bcn_binding(self, channel: ChannelRecord, *, action: str) -> None:
        """apply → ensure active + push config; remove → deactivate. Fail-closed."""
        if action == "apply":
            binding_id = await self._bcs_client.ensure_active(channel)
            self._store_bcs_binding_id(channel, binding_id)
            await self._bcs_client.push_config(channel, binding_id=binding_id)
        elif action == "remove":
            binding_id = str(channel.config.get("bcs_binding_id") or "")
            if binding_id:
                await self._bcs_client.set_active(binding_id, active=False)
        else:
            raise ValueError(f"Invalid action: {action}")
```

3f. 类内追加 `remove_channel`（`delete` 方法之后）：

```python
    async def remove_channel(self, channel_id: int) -> None:
        """Delete the row; bcn_gateway rows also best-effort delete their BCS binding.

        Ordering (spec §4.3): the router deactivates an active channel first
        (fail-closed), then this deletes the local row, then the BCS binding —
        a binding-delete failure only logs, so a transient BCS miss can never
        block removing the configuration row.
        """
        record = self._repository.get_by_id(channel_id)
        binding_id = ""
        if record is not None and self._is_bcn_channel(record):
            binding_id = str(record.config.get("bcs_binding_id") or "")
        self._repository.delete_by_id(channel_id=channel_id)
        if binding_id:
            try:
                await self._bcs_client.delete_binding(binding_id)
            except ChannelError as exc:
                logger.warning(
                    "[ChannelService] best-effort BCS binding delete failed "
                    "for binding_id=%s: %s",
                    binding_id,
                    exc,
                )
```

3g. router `delete_channel` 最后一行 `service.delete(channel_id=channel_id)` 替换为：

```python
    await service.remove_channel(channel_id)
```

- [ ] **Step 4: 修复其他直接构造 ChannelService 的测试**

Run: `cd src/backend && grep -rn "ChannelService(" tests/ src/ --include="*.py" | grep -v "channel_service.py" | grep -v test_channel_service.py | grep -v __pycache__`
对每处直接构造补 `bcs_client=MagicMock()`（`from unittest.mock import MagicMock`）。典型命中：`tests/community/core/channel/test_channel_service_uses_resolver.py`。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/channel/ tests/community/adapters/http/openapi_v1/channels/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A src/backend/src/agentclaw/community/core/channel/services/channel_service.py \
        src/backend/src/agentclaw/community/adapters/http/openapi_v1/channels/router.py \
        src/backend/tests/community/core/channel/ \
        src/backend/tests/community/adapters/http/openapi_v1/channels/
git commit -m "feat(channel): dispatch bcn_gateway lifecycle to BCS binding client"
```

---

### Task 6: engine_overrides_reader — bcn 行跳过

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/channel/services/engine_overrides_reader.py`
- Test: `src/backend/tests/community/core/channel/test_engine_overrides_reader.py`

- [ ] **Step 1: 写失败测试**

在 `test_engine_overrides_reader.py` 追加（复用文件内既有的 repo/insert helper，命名以文件内现名为准；下面按 `insert_channel` 关键字风格写）：

```python
def test_bcn_gateway_rows_are_skipped(world_with_channel_repo):
    """bcn_gateway 行不进 engine_overrides —— 凭证在 BCS 侧，不在引擎直连配置。"""
    world_with_channel_repo.insert_channel(
        type="dingding",
        description=None,
        identity_id="user-1",
        bind_bot_id="bot-1",
        config={"client_id": "client-1", "binding_mode": "bcn_gateway"},
        status="1",
        stage=None,
    )

    overrides = reader_for("user-1", "bot-1").overrides_for_stage(
        user_id="user-1", bot_id="bot-1", accept_stages={None, "", "draft"}
    )
    assert overrides == {}
```

> 视例的 fixture/helper 名（`world_with_channel_repo`、`reader_for`）以该文件既有视例的写法为准照抄改造——关键是：插入一条 `binding_mode="bcn_gateway"`、`status="1"`、stage 命中的行，断言 `overrides_for_stage` 返回 `{}`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && python -m pytest tests/community/core/channel/test_engine_overrides_reader.py -v -k bcn`
Expected: FAIL —— 返回了 `{"channels": {"dingding": ...}}` 而非 `{}`

- [ ] **Step 3: 实现**

`overrides_for_stage` 的循环加一个 continue（`if record.status != "1" ...` 之后）：

```python
        for record in records:
            if record.status != "1" or record.stage not in accept_stages:
                continue
            if record.config.get("binding_mode") == "bcn_gateway":
                # bcn_gateway rows ride in BCS, not the engine_overrides payload
                # (channels.dingding is the plugin direct-connect shape).
                continue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && python -m pytest tests/community/core/channel/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/channel/services/engine_overrides_reader.py \
        src/backend/tests/community/core/channel/test_engine_overrides_reader.py
git commit -m "feat(channel): skip bcn_gateway rows in engine_overrides"
```

---

### Task 7: Gateway schema 双侧手工同步

**Files:**
- Modify: `src/gateway/configs/schemas/bots.openapi.json`（手工增量，勿全量 regen）
- Modify: `src/gateway/tests/fixtures/bots.openapi.json`（同一份拷贝）
- Modify: ocb 仓库同名文件（`~/IdeaProjects/ocb`，路径以 `find ~/IdeaProjects/ocb -name bots.openapi.json` 为准）

- [ ] **Step 1: 生成参考 dump**

```bash
cd src/backend && python scripts/dump_openapi.py /tmp/public-openapi.json
```
Expected: `wrote public OpenAPI to /tmp/public-openapi.json`

- [ ] **Step 2: 提取需合并的 schema 片段**

从 `/tmp/public-openapi.json` 的 `components.schemas` 提取以下 6 个（含新 `binding_mode` / `group_chat_scope` / `outbound_visibility` 字段定义），以及 channels 各写接口 `responses` 里新增的 `409` 块：

`Channel`、`ChannelCreate`、`ChannelUpdate`、`DingTalkChannelConfig`、`DingTalkChannelConfigCreate`、`DingTalkChannelConfigUpdate`

```bash
python3 - <<'EOF'
import json
dump = json.load(open("/tmp/public-openapi.json"))
names = ["Channel", "ChannelCreate", "ChannelUpdate",
         "DingTalkChannelConfig", "DingTalkChannelConfigCreate",
         "DingTalkChannelConfigUpdate"]
out = {n: dump["components"]["schemas"][n] for n in names}
json.dump(out, open("/tmp/channel-schemas.json", "w"), indent=2, ensure_ascii=False)
print("wrote /tmp/channel-schemas.json")
EOF
```

- [ ] **Step 3: 手工合并进 bots.openapi.json**

对 `src/gateway/configs/schemas/bots.openapi.json`：
1. 用 `/tmp/channel-schemas.json` 里的同名 schema **整体替换** `components.schemas` 下的对应条目（整体替换比逐字段插入安全——这 6 个 schema 全部字段都来自我们的模型）。
2. 在四个渠道写接口（POST/GET `{channel_id}` PATCH、PUT status、DELETE）的 `responses` 里，按 dump 里同接口的 `409` 块补齐（若无则按 423 块的结构手写：description + `$ref` ErrorEnvelope + example）。
3. 同步 `src/gateway/tests/fixtures/bots.openapi.json`（直接 `cp` 覆盖）。

验证 JSON 合法：

```bash
python3 -c "import json; json.load(open('src/gateway/configs/schemas/bots.openapi.json'))" && echo OK
```

- [ ] **Step 4: ocb 侧同步**

```bash
OCB_PATH=$(find ~/IdeaProjects/ocb -name bots.openapi.json | head -1)
cp src/gateway/configs/schemas/bots.openapi.json "$OCB_PATH"
```
在 ocb 仓库单独提交（提交前 `git -C ~/IdeaProjects/ocb branch --show-current` 确认分支；若当前分支与本次无关，问用户切哪个分支）。

- [ ] **Step 5: 跑 gateway schema 测试**

Run: `cd src/gateway && python -m pytest tests/unit/core/forwarding/test_served_openapi.py tests/unit/core/forwarding/test_combined_schema_catalogs.py -v`
Expected: PASS

- [ ] **Step 6: Commit（avernet 侧）**

```bash
git add src/gateway/configs/schemas/bots.openapi.json src/gateway/tests/fixtures/bots.openapi.json
git commit -m "feat(channel): publish binding_mode in gateway bots.openapi.json"
```

---

### Task 8: 收尾验证 — 全量测试与覆盖率门

**Files:** 无新文件

- [ ] **Step 1: 受影响面全量**

```bash
cd src/backend && python -m pytest tests/community/core/channel/ \
  tests/community/adapters/http/openapi_v1/channels/ \
  tests/community/endpoints/test_publish_per_stage_channels.py \
  tests/community/endpoints/test_bot_workshop_channels_space.py -v
```
Expected: PASS

- [ ] **Step 2: 本地全量 CI（changed-line coverage ≥80% 门）**

```bash
cd src/backend && bash scripts/ci_test.sh
```
Expected: 全绿 + coverage 达标。若 changed-line coverage 不足，回补测试到新增分支（重点：`_sync_bcn_binding` 的 else 分支、`_find_binding_id` 的无匹配返回）。

- [ ] **Step 3: 推分支**

```bash
git push -u origin feat/channel-binding-mode
```

- [ ] **Step 4: 开 PR（base: dev）**

PR title: `feat(channel): unify DingTalk binding modes behind binding_mode`
PR desc 按 Problem/Solution/Validation 结构；标注两个上线阻塞项（BCS 钉钉 provider、BCS 服务间鉴权），链接 spec 与本计划。

---

## Self-Review 记录

- **Spec 覆盖**：§3 契约（Task 1/3）、§4 编排（Task 4/5）、§5 下发（Task 6）、§6 鉴权（复用既有门槛，无新 task 需要）、§7 外部依赖（计划头部声明为非本计划范围）、§8 测试交付（Task 7/8）。§3.2 的 422 不进 OpenAPI responses 声明的决策已写入 Task 3 步骤 3b。
- **占位符**：三处"以文件内既有视例为准"的标注（Task 2/3 的 handler 调用形态、Task 6 的 fixture 名）是防跑偏的参照指令，不是未完成内容——代码已完整给出。
- **类型一致性**：`validate_mode_matrix`（Task 1 定义 / Task 3 使用）、`ensure_active`/`push_config(channel, *, binding_id)`（Task 4 定义 / Task 5 使用）、`ChannelModeViolationError`/`ChannelBindingConflictError`（Task 2 定义 / Task 3/4 使用）已核对一致。
