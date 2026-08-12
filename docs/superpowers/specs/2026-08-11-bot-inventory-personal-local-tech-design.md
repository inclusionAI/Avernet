# Bot Inventory 个人云端 Bot / 本地 Bot 技术设计

- **日期**: 2026-08-11
- **负责人范围**: **Bot Inventory** 中的个人云端 Bot 与本地 Bot（desktop/local bot）能力；服务 Bot 发布生命周期、容器、评测、编辑页内核仅通过契约缝预留，不在本文实现范围。
- **目标**: 按 `src/backend/src/agentclaw/community/adapters/http/openapi_v1` 当前公共 API 架构，把个人云端 / 本地 Bot 拆成清晰功能模块，形成可实现、可测试、可与服务线并行的后端设计。
- **架构约束**:
  - `openapi_v1` handler 保持薄适配器：只做请求参数、鉴权依赖、schema 映射、调用 Service API、返回 `Envelope` / `Page`。
  - Bot Inventory 聚合、动作矩阵、展示状态映射等领域策略放在 `core/bot_inventory/`，不落在 HTTP router。
  - handler 使用当前公开面规范：`request: Request` + `owner_id: UserIdDep` + `Injected(XxxProtocol)` + `envelope()/created()/deleted()/page()` + `@envelope_errors`。
  - 用户维度统一使用 `?user_id=`（`UserIdDep`），不要在新公开端点里重新从 `PrincipalDep` 派生 owner。
  - 新子资源 router 必须挂在 `bots_router` 通配路径之前，避免 `/openapi/v1/bots/{bot_id}` 吃掉 literal segment。

---

## 1. 范围拆分

### 1.1 本文负责

| 功能域 | 资源对象 | 主要能力 |
| --- | --- | --- |
| Bot Inventory / 清单项 | 个人云端 Bot + 本地 Bot | 列表、筛选、详情、动作集合、展示态聚合 |
| 个人云端 Bot | `bot_type=personal` 且非 desktop | 创建、查看、更新、删除、重启、状态、Passport、初始化配置、沉寂激活 |
| 本地 Bot | `bot_type=desktop` | 创建、查看、列表、重启、删除、设备状态、打开目录；运行日志 / 引擎重启本期置灰 |
| 横向治理 | 业务空间上下文消费、动作策略、编辑锁 / 协作者可选接入 | 本模块不管理业务空间，只消费当前业务空间并输出一致动作 |

### 1.2 本文不负责，但预留契约

| 能力 | Owner | 本文处理方式 |
| --- | --- | --- |
| 服务 Bot 发布态：draft / staging / online / offline | 服务线 | 通过 `ServiceLifecyclePort` 预留，不由个人线实现 |
| 容器实例、服务评测、服务化升级 | 服务线 | `BotInventoryItem` 可组合子字段，个人线不实现对应 router |
| 业务空间管理 / 成员 / 迁移 | 业务空间 Owner | 本模块只消费业务空间上下文；不定义空间 CRUD、不落空间表、不拥有成员关系 |
| 任务护航 flow / nodes | BCS / 服务线 | 不进入本文 P0/P1 |

---

## 2. 对齐当前 openapi_v1 架构的关键订正

原方案中有几处需要按当前 `openapi_v1` 代码修正：

1. **用户身份依赖**  
   当前 `openapi_v1` 用户级公开端点不是 `principal: PrincipalDep + caller_owner_id(principal)` 模式，而是 `owner_id: UserIdDep`。`UserIdDep` 强制要求 `?user_id=` 且必须与已验证 principal 中的用户一致，错误语义由公共面统一处理。

2. **Page builder 参数顺序**  
   当前 `responses.page` 签名是 `page(total, items, request)`，不是 `page(items, total, page, page_size, request)`。

3. **新 router 注册分类**  
   用户级新组应加入 `_SUBGROUPS` 并使用 `USER_SCOPED_ERROR_RESPONSES`；不应加入 `_GROUPS_WITHOUT_CALLER_SCOPE`。`bot_logs` 当前是特殊历史组，不建议把新的用户级日志能力继续塞进它，除非同时改清楚 `user_id` 语义与响应表。

4. **路径组织**  
   采用 `openapi_v1/<component>/{router.py,schemas.py}`，组件前置路径：`/openapi/v1/bots/<component>/...`。不建 `openapi_v1/bot_inventory/` 这种聚合大包；公开适配层仍按组件拆在 `openapi_v1/inventory/`、`openapi_v1/local/` 等包下。

5. **Service API 优先**  
   HTTP router 注入 `agentclaw.community.api.*` 下的 Protocol；如果现有能力只有 concrete service，没有 Protocol，先补 Service API Protocol，再在 DI 里绑定，避免 adapter 依赖具体实现。

---

## 3. 推荐模块树

```text
src/backend/src/agentclaw/community/
├── core/bot_inventory/
│   ├── README.md                         # Context Boundary，新增模块必须补
│   ├── __init__.py
│   ├── types.py                          # 展示层 domain DTO / enum
│   ├── protocols.py                      # 仅真实 seam Protocol
│   ├── policies/
│   │   ├── __init__.py
│   │   ├── combo_policy.py               # 创建组合、引擎能力矩阵
│   │   └── action_policy.py              # 展示动作矩阵
│   ├── services/
│   │   ├── __init__.py
│   │   ├── bot_inventory_service.py      # 个人云端 + 本地三源聚合
│   │   └── lifecycle_view.py             # display_state 映射
│   └── adapters/
│       ├── __init__.py
│       ├── noop_business_space.py        # P0 个人业务空间 fallback，仅消费方兜底
│       └── noop_service_lifecycle.py     # 服务线未接入前兜底
│
├── api/
│   └── bot_inventory_service.py           # 可选：若要把 BotInventoryService 作为 Service API 暴露
│
├── di/modules/
│   └── bot_inventory_module.py            # 组合根绑定 noop / prod 实现
│
└── adapters/http/openapi_v1/
    ├── __init__.py                       # 注册新 routers，放 bots_router 前
    ├── inventory/
    │   ├── __init__.py
    │   ├── router.py                     # GET list/detail/actions
    │   └── schemas.py
    ├── local/
    │   ├── __init__.py
    │   ├── router.py                     # 本地 Bot 创建 / 授权轮询 / 列表 / 重启 / 删除 / 打开目录 / 设备列表
    │   └── schemas.py
    ├── diagnostics/
    │   ├── __init__.py
    │   ├── router.py                     # 个人云端 Bot runtime logs / engine restart / health
    │   └── schemas.py
    ├── dormant/
    │   ├── __init__.py
    │   ├── router.py                     # 个人云端 Bot 激活
    │   └── schemas.py
    ├── edit_lock/                        # P1，可选
    │   ├── __init__.py
    │   ├── router.py
    │   └── schemas.py
    └── editors/                          # P1，可选
        ├── __init__.py
        ├── router.py
        └── schemas.py
```

> 若 `core/bot_inventory` 是边界显著模块，必须补 `README.md` 的 `## Context Boundary`，并声明 `internal_dependencies`，否则架构测试会失败。

---

## 4. Core 契约与领域类型

### 4.1 `types.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class DeployMode(str, Enum):
    CLOUD = "cloud"
    LOCAL = "local"


class BotInventoryKind(str, Enum):
    PERSONAL_CLOUD = "personal_cloud"
    LOCAL = "local"
    SERVICE = "service"          # 只为聚合兼容预留


class DisplayState(str, Enum):
    RUNNING = "running"
    PENDING = "pending"
    FAILED = "failed"
    DORMANT = "dormant"
    LOCAL_RUNNING = "local_running"
    LOCAL_OFFLINE = "local_offline"
    LOCAL_PENDING = "local_pending"
    LOCAL_FAILED = "local_failed"
    SERVICE_DRAFT = "service_draft"
    SERVICE_STAGING = "service_staging"
    SERVICE_ONLINE = "service_online"
    SERVICE_OFFLINE = "service_offline"


class BotAction(str, Enum):
    VIEW = "view"
    CHAT = "chat"
    EDIT = "edit"
    DELETE = "delete"
    RESTART = "restart"
    DATA_INIT = "data_init"
    ACTIVATE = "activate"
    OPEN_FOLDER = "open_folder"
    PASSPORT = "passport"
    ENGINE_CONFIG = "engine_config"
    RUNTIME_LOGS = "runtime_logs"        # local P0 disabled
    ENGINE_RESTART = "engine_restart"    # local P0 disabled


@dataclass(frozen=True)
class BusinessSpaceRef:
    space_id: str
    name: str
    kind: str  # 由业务空间 owner 定义；P0 fallback 使用 "personal"


@dataclass(frozen=True)
class BotInventoryItem:
    bot_id: str
    bot_name: str
    bot_desc: str
    engine: str
    bot_type: str
    kind: BotInventoryKind
    deploy_mode: DeployMode
    display_state: DisplayState
    status: str
    owner_entity_id: str
    space: BusinessSpaceRef | None
    avatar_url: str | None = None
    machine_id: str | None = None
    mount_path: str | None = None
    passport_id: str | None = None
    actions: tuple[BotAction, ...] = ()
    disabled_actions: Mapping[str, str] | None = None
```

### 4.2 `protocols.py`

只定义真实会被替换的 seam，不给稳定的 concrete 聚合服务滥加 Protocol。

```python
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .types import BotAction, DisplayState, BusinessSpaceRef


class BusinessSpaceContextProtocol(Protocol):
    """消费业务空间上下文的最小 Service API。

    本模块不是业务空间 owner：不定义空间 CRUD、不管理成员、不落空间表。
    当业务空间 owner 提供正式 Service API 后，本协议应改为直接消费 owner 的 API；P0 仅用个人空间 fallback。
    """

    def resolve_current(
        self, *, owner_id: str, header_space_id: str | None
    ) -> BusinessSpaceRef: ...

    def bot_space(
        self, *, bot: Mapping[str, Any], owner_id: str
    ) -> BusinessSpaceRef | None: ...

    def assert_bot_visible_in_current_space(
        self, *, bot: Mapping[str, Any], owner_id: str, current_space: BusinessSpaceRef
    ) -> None: ...


class ServiceLifecyclePort(Protocol):
    """服务 Bot 展示态 seam：由服务线在 P1/P2 提供真实实现。"""

    def display_state(self, *, bot: Mapping[str, Any]) -> DisplayState: ...

    def allowed_actions(self, *, bot: Mapping[str, Any]) -> Sequence[BotAction]: ...
```

---

## 5. 创建组合与动作策略

### 5.1 创建组合 `combo_policy.py`

```python
from dataclasses import dataclass

SUPPORTED_ENGINES = frozenset(
    {"moltis", "openclaw", "hermes", "aicoding", "claude_code", "teclaw"}
)
LOCAL_CAPABLE_ENGINES = frozenset({"openclaw", "claude_code"})
PERSONAL_CLOUD_CAPABLE_ENGINES = SUPPORTED_ENGINES
SERVICE_CAPABLE_ENGINES = frozenset({"openclaw", "claude_code", "teclaw"})


@dataclass(frozen=True)
class ComboDecision:
    ok: bool
    reason: str | None = None


def assert_personal_cloud_create(engine: str, space_kind: str) -> ComboDecision:
    if engine not in PERSONAL_CLOUD_CAPABLE_ENGINES:
        return ComboDecision(False, f"unsupported engine: {engine}")
    if space_kind not in {"personal", "team"}:
        return ComboDecision(False, "personal cloud bot requires a valid business space")
    return ComboDecision(True)


def assert_local_create(engine: str, space_kind: str) -> ComboDecision:
    if engine not in LOCAL_CAPABLE_ENGINES:
        return ComboDecision(False, f"local bot does not support engine: {engine}")
    if space_kind != "personal":
        return ComboDecision(False, "local bot is personal business-space only in this phase")
    return ComboDecision(True)
```

> `SUPPORTED_ENGINES` 后续应尽量引用 `core/workspace/constants.py` 或统一 engine registry，避免长期双源。若要新增 `teclaw` 到工程常量，应作为独立加法改动并补测试。
>
> **teclaw 引擎归属（2026-08-12 融志确认，产品权威口径）**：teclaw **仅支持云端 Bot**，**本地 Bot 不支持 teclaw**。故 `LOCAL_CAPABLE_ENGINES` 不含 teclaw（`openclaw` / `claude_code` 两种）；`PERSONAL_CLOUD_CAPABLE_ENGINES = SUPPORTED_ENGINE_TYPES` 仍含 teclaw。本地路径 `assert_local_create("teclaw", "personal")` 在前门即拒，避免请求进入 desktop/BaaS provisioning 深处才失败。`SERVICE_CAPABLE_ENGINES` 同步保留 teclaw（服务 Bot 走云端编排）。

### 5.2 动作矩阵 `action_policy.py`

| Bot 类型 | 状态 | actions | disabled_actions |
| --- | --- | --- | --- |
| 个人云端 | ACTIVE / RUNNING | `view, chat, edit, restart, delete, passport, engine_config, data_init` | — |
| 个人云端 | PENDING | `view` | `chat/edit/restart`: `bot not ready` |
| 个人云端 | FAILED | `view, delete` | `restart`: `bot provisioning failed` |
| 个人云端 | DORMANT | `view, activate, delete` | `chat/edit/restart`: `activate first` |
| 本地 | local running | `view, chat, edit, restart, delete, open_folder` | `runtime_logs/engine_restart`: `not supported in this phase` |
| 本地 | local offline | `view, restart, delete, open_folder` | `chat/edit`: `device offline` |
| 本地 | local pending | `view, delete` | `chat/edit/restart`: `local bot not ready` |

---

## 6. 聚合服务设计

### 6.1 `BotInventoryService`

职责：把 `BotServiceProtocol` 的个人云端 Bot 与 `DesktopBotServiceProtocol` 的本地 Bot 合成Bot Inventory 清单项。它是 core service，不知道 FastAPI / Request / Envelope。

依赖：

- `BotServiceProtocol`: 个人云端 Bot 列表、详情。
- `DesktopBotServiceProtocol`: 本地 Bot 列表、ownership、状态。
- `BusinessSpaceContextProtocol`: 消费外部业务空间上下文，用于列表过滤与可见性校验；不管理空间。
- `BotLifecycleView`: 展示态与动作矩阵。

伪代码：

```python
class BotInventoryService:
    def list_items(
        self,
        *,
        owner_id: str,
        space: BusinessSpaceRef | None,
        keyword: str | None,
        engine: str | None,
        deploy_mode: DeployMode | None,
        page: int,
        page_size: int,
    ) -> tuple[list[BotInventoryItem], int]:
        cloud_rows = []
        if deploy_mode in (None, DeployMode.CLOUD):
            result = self._bot.list_bots_by_conditions(
                owner_id=owner_id,
                bot_name=keyword,
                engine=engine,
                status=None,
                page=1,
                page_size=min(page * page_size, 200),
            )
            cloud_rows = [r for r in result["items"] if r.get("bot_type") == "personal"]

        local_rows = []
        if deploy_mode in (None, DeployMode.LOCAL):
            local_rows = self._desktop.list_user_bots(owner_id)
            if keyword:
                local_rows = [r for r in local_rows if keyword in (r.get("bot_name") or "")]
            if engine:
                local_rows = [r for r in local_rows if (r.get("active_engine") or r.get("engine_type")) == engine]

        cards = [self._to_cloud_card(r, owner_id) for r in cloud_rows]
        cards += [self._to_local_card(r, owner_id) for r in local_rows]
        if space is not None:
            cards = [c for c in cards if c.space and c.space.space_id == space.space_id]

        cards.sort(key=lambda c: (c.deploy_mode.value, c.bot_name, c.bot_id))
        total = len(cards)
        start = (page - 1) * page_size
        return cards[start : start + page_size], total
```

> P0 允许内存 merge + 分页；必须设置源查询上限，避免一次拉全量。后续若规模变大，再把各源升级为 cursor 聚合。

### 6.2 `BotLifecycleView`

职责：把内部状态映射成Bot Inventory 展示态，并输出动作矩阵。

- `bot_type == "desktop"`：使用 desktop / BaaS 状态映射到 `LOCAL_*`。
- `bot_type == "personal"`：使用 `status` + dormant 标记映射到 `RUNNING/PENDING/FAILED/DORMANT`。
- `bot_type == "service"`：委托 `ServiceLifecyclePort`，仅为服务线兼容预留。

---

## 7. HTTP 功能模块设计

### 7.1 `inventory`：Bot Inventory 列表 / 详情 / 动作

**路径前缀**: `/openapi/v1/bots/inventory`  
**包**: `openapi_v1/inventory/`

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/openapi/v1/bots/inventory` | 列个人云端 + 本地 Bot 清单项 |
| GET | `/openapi/v1/bots/inventory/{bot_id}` | 查单个清单项 |
| GET | `/openapi/v1/bots/inventory/{bot_id}/actions` | 查当前可用动作 |

handler 形态：

```python
@router.get("", response_model=Envelope[Page[BotInventoryItemResp]], responses=USER_SCOPED_403)
@envelope_errors
async def list_inventory_bots(
    request: Request,
    page_params: PageParamsDep,
    owner_id: UserIdDep,
    x_space_id: str | None = Header(default=None, alias="X-Space-Id"),
    keyword: str | None = None,
    engine: str | None = None,
    deploy_mode: DeployModeQuery | None = None,
    view: BotInventoryService = Injected(BotInventoryService),
    business_space: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
) -> Envelope[Page[BotInventoryItemResp]]:
    space = business_space.resolve_current(owner_id=owner_id, header_space_id=x_space_id)
    items, total = view.list_items(
        owner_id=owner_id,
        space=space,
        keyword=keyword,
        engine=engine,
        deploy_mode=deploy_mode,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page(total, [_to_resp(item) for item in items], request)
```

### 7.2 `diagnostics`：个人云端 Bot 诊断能力

**路径前缀**: `/openapi/v1/bots/diagnostics`  
**包**: `openapi_v1/diagnostics/`

该组件是从另一版方案中最值得借鉴的拆分：运行日志、引擎重启、健康检查都不是 `bots` 的基础 CRUD，也不适合塞进现有 `bot_logs` 或 `engine_runtime/engine` 组。原因：

- `bot_logs` 现有 `user_id` 是 traces 过滤语义，和公开面通用的 `user_id=调用者` 语义相反。
- `engine_runtime/engine` 现有文档对 restart/switch 有排除说明，直接扩进去容易制造两个“restart”动词。
- `diagnostics` 明确表达“诊断/运维动作”，与 `POST /openapi/v1/bots/{bot_id}/restart` 的“重启 Bot 部署”区分。

| Method | Path | 说明 | P0 策略 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/bots/diagnostics/{bot_id}/runtime-logs` | 读取个人云端 Bot 运行日志 | 只读、白名单路径、限制 tail/timeout |
| POST | `/openapi/v1/bots/diagnostics/{bot_id}/engine-restart` | 重启 / 重载引擎进程 | 默认桥接 BaaS exec，待 engine owner 确认 |
| GET | `/openapi/v1/bots/diagnostics/{bot_id}/health` | 获取最近健康检查结果 | P0 noop，P2 接 harness |
| POST | `/openapi/v1/bots/diagnostics/{bot_id}/health-check` | 触发健康检查 | 仅 cloud + openclaw |

类型裁决：

- 仅个人云端 Bot 开放。
- 本地 Bot 本期返回不支持，前端动作置灰。
- 服务 Bot 属服务线，不在本文操作。

```python
def require_personal_cloud_bot(bot: Mapping[str, Any]) -> None:
    if bot.get("bot_type") == "desktop":
        raise EngineBotTypeNotSupportedError("diagnostics not offered for local bots")
    if bot.get("bot_type") == "service":
        raise BotOperationNotAllowedError("service bots are not handled by personal/local diagnostics")
```

`BaasRuntimeLogsAdapter.read` 必须满足：

- 只允许预定义日志路径，例如 `/logs/engine.log`、`/home/admin/logs/*.log`。
- 拒绝 `..`、绝对路径拼接、shell 注入。
- `tail` 设上下限，例如 `1..2000`。
- `level` 只做行过滤，不拼进 shell。
- 下游失败抛可映射 domain error，不返回成功空日志掩盖失败。

### 7.3 `bots` 既有组：个人云端 Bot CRUD 继续复用

个人云端 Bot 的基础 CRUD 已在 `openapi_v1/bots/router.py`：

| Method | Path | 现状 | 本文要求 |
| --- | --- | --- | --- |
| POST | `/openapi/v1/bots` | 创建 personal/service | 保持 `BotType = Literal["personal", "service"]`；个人线只使用 `personal` |
| GET | `/openapi/v1/bots` | list | 保留；Bot Inventory 新列表走 `/inventory`，避免改变旧契约 |
| GET | `/openapi/v1/bots/{bot_id}` | detail | 保留 |
| PUT | `/openapi/v1/bots/{bot_id}` | 更新名称 / 描述 | 保留 |
| DELETE | `/openapi/v1/bots/{bot_id}` | 删除；拒绝 desktop/service | 保留，个人云端可用 |
| POST | `/openapi/v1/bots/{bot_id}/restart` | 重启；拒绝 desktop | 保留，个人云端可用 |
| GET | `/openapi/v1/bots/{bot_id}/status` | runtime readiness | 保留 |
| GET | `/openapi/v1/bots/{bot_id}/passport` | Passport | 保留 |
| GET/PUT | `/openapi/v1/bots/{bot_id}/engine-config` | 引擎配置 | 保留 |

**可选加法**：`BotCreate.init_config: bool = False`。

- 加字段是 backward-compatible，但要同步 `bots.openapi.json` 和 `_compat`。
- handler 在 bot 创建完成后调用 data-init 能力；若创建走 202 Passport 授权等待，则必须在 `auth-status` 完成创建路径也能应用同一语义，否则同一请求参数在同步 / 异步创建上语义不一致。
- 如果 data-init 是 async service，router 不应静默吞写失败；失败要通过 domain error 映射为错误 envelope。

### 7.4 `local`：本地 Bot

**路径前缀**: `/openapi/v1/bots/local`  
**包**: `openapi_v1/local/`

| Method | Path | 说明 | 委托 |
| --- | --- | --- | --- |
| POST | `/openapi/v1/bots/local` | 创建本地 Bot，可能返回 201 或 202 授权等待 | `DesktopBotServiceProtocol.apply_passport_before_create` |
| GET | `/openapi/v1/bots/local/{bot_id}/auth-status` | 本地 Bot 授权轮询并完成创建 | `DesktopBotServiceProtocol.create_after_authorization` + Passport/AuthRelationship |
| GET | `/openapi/v1/bots/local` | 列本地 Bot，可选；Bot Inventory 列表已覆盖 | `DesktopBotServiceProtocol.list_user_bots` |
| POST | `/openapi/v1/bots/local/{bot_id}/restart` | 重启本地 Bot | `DesktopBotServiceProtocol.restart` |
| DELETE | `/openapi/v1/bots/local/{bot_id}` | 删除本地 Bot | `DesktopBotServiceProtocol.delete` |
| POST | `/openapi/v1/bots/local/{bot_id}/open-folder` | 打开本地目录 | `DesktopBotServiceProtocol.open_folder` |
| GET | `/openapi/v1/bots/local/devices` | 设备列表，可用于创建选择 machine | `DesktopBotServiceProtocol.list_devices` |
| GET | `/openapi/v1/bots/local/devices/{machine_id}/files` | 选择本地挂载目录 | `DesktopBotServiceProtocol.list_directory` |

路由声明顺序必须保证 literal 在通配前：`/devices`、`/devices/{machine_id}/files` 等必须写在 `/{bot_id}` 前，避免被 `{bot_id}` 吞掉。

创建请求建议：

```python
class LocalBotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_name: str
    bot_desc: str = ""
    engine: str
    machine_id: str
    mount_path: str | None = None
    avatar_url: str | None = None
    init_config: bool = False
```

本地 Bot 创建可能也需要 Passport 授权，因此推荐沿用 `openapi_v1/bots` 的 201 / 202 双态模型：

- 201：创建完成，返回 `BotInventoryItemResp` 或 `LocalBotResp`。
- 202：返回授权 iframe / redirect，后续通过本地专属 auth-status 或复用现有完成流程创建。

> 如果 desktop 内部 service 当前只有 `apply_passport_before_create` + `create_after_authorization`，不要在公开 handler 里绕过授权流直接拼 create；应先补一个清晰的 Service API 方法或 helper 流程，避免把内部流程状态泄漏到 adapter。

### 7.5 `dormant`：个人云端 Bot 激活

**路径前缀**: `/openapi/v1/bots/dormant`  
**包**: `openapi_v1/dormant/`

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/openapi/v1/bots/dormant/{bot_id}/activate` | 激活沉寂的个人云端 Bot |

规则：

- 仅允许 `bot_type == "personal"` 且 `deploy_mode == cloud`。
- 本地 Bot 不进入 dormant 回收；服务 Bot 生命周期由服务线处理。
- handler 先 `bot_service.get_bot(bot_id, owner_id)` 做 ownership / tenant guard，再委托 dormant service。
- 若 dormant capability 尚无 `api/*Protocol`，先补 `DormantBotServiceProtocol`。

### 7.6 `edit-lock` / `editors`：P1 横向治理

这两个模块不限定服务 Bot，也会影响个人云端 Bot 的编辑体验；本地 Bot P0 可先不接协作者。

| 组件 | 路径 | 规则 |
| --- | --- | --- |
| `edit-lock` | `/openapi/v1/bots/edit-lock/{bot_id}` | 获取 / 释放 / 抢占编辑锁；个人云端可用，本地 P0 可返回不支持或仅 owner 可锁 |
| `editors` | `/openapi/v1/bots/editors/{bot_id}` | 个人空间个人 Bot 默认无协作者；团队空间 Bot 才允许编辑授权 |

策略必须放在 core policy 或 collaborator service，不要散落在 router。

---

## 8. 业务空间上下文消费设计

本模块只是 **业务空间使用方**，不是业务空间 owner。这里的设计目标是让 Bot Inventory 在当前业务空间视角下展示和校验 Bot，而不是管理空间本身。

### 8.1 明确不负责

本模块不做：

- 业务空间 CRUD。
- 空间成员 CRUD。
- 空间迁移。
- 空间权限模型定义。
- 空间持久化表设计。
- 空间 owner 的 prod adapter 实现。

这些能力由业务空间 owner 提供。本模块只能消费其 Service API / header 上下文。

### 8.2 上下文来源

前端空间切换器发送：

```http
X-Space-Id: <space_id>
```

`X-Space-Id` 表示当前业务空间视角，不是租户、不是 user_id，也不是 Bot owner。公开面仍然同时使用：

| 概念 | 来源 | 含义 | 本模块用途 |
| --- | --- | --- | --- |
| tenant | gateway principal / middleware | 数据隔离租户 | 由既有 tenant guard 处理 |
| `user_id` | query string / `UserIdDep` | 请求代表的最终用户 | owner scope / 调用者一致性校验 |
| business space | `X-Space-Id` | 当前业务空间视角 | Inventory 过滤、Bot 可见性校验 |

### 8.3 最小消费契约

本模块只需要一个“业务空间上下文消费”能力：

```python
class BusinessSpaceContextProtocol(Protocol):
    def resolve_current(
        self, *, owner_id: str, header_space_id: str | None
    ) -> BusinessSpaceRef: ...

    def bot_space(
        self, *, bot: Mapping[str, Any], owner_id: str
    ) -> BusinessSpaceRef | None: ...

    def assert_bot_visible_in_current_space(
        self, *, bot: Mapping[str, Any], owner_id: str, current_space: BusinessSpaceRef
    ) -> None: ...
```

> 如果业务空间 owner 已经提供正式 `BusinessSpaceServiceProtocol`，实现时应优先直接消费 owner 的 Protocol；本文中的 `BusinessSpaceContextProtocol` 只是 Bot Inventory 对外部能力的最小需求描述，不代表本模块拥有业务空间领域。

### 8.4 P0 fallback

P0 在业务空间 prod API 未接入时只允许个人业务空间 fallback：

- `resolve_current(owner_id, None)` 返回个人业务空间。
- `resolve_current(owner_id, non_personal_space)` 若无法向空间 owner 校验，应 fail closed 或返回“不支持当前空间”。
- 本地 Bot P0 只属于个人业务空间。
- 个人云端 Bot 若没有业务空间字段，则默认落个人业务空间。

如果产品本期必须支持团队 / 业务空间切换，则 P0 不能使用 permissive noop，必须接入空间 owner 的正式 Service API。

### 8.5 哪些 OpenAPI 使用业务空间上下文

业务空间只影响“当前视角下能否看到 / 操作某个 Bot”，不替代 `user_id` owner guard。个人云端 Bot 与本地 Bot 的使用边界如下：

| OpenAPI 组件 | Endpoint | 是否消费 `X-Space-Id` | 用途 | P0 行为 |
| --- | --- | --- | --- | --- |
| `inventory` | `GET /openapi/v1/bots/inventory` | 是 | 当前空间下的个人云端 + 本地 Bot 清单过滤 | `None` 默认个人空间；非个人空间需空间 owner 校验，否则 fail closed / 不支持 |
| `inventory` | `GET /openapi/v1/bots/inventory/{bot_id}` | 是 | 单 Bot 详情可见性校验 | 先 owner guard，再校验 Bot 是否属于当前空间 |
| `inventory` | `GET /openapi/v1/bots/inventory/{bot_id}/actions` | 是 | 当前空间下动作可用性；例如团队空间协作能力后续只在可见空间生效 | 先 owner guard，再空间可见性校验 |
| `bots` 既有组 | `POST /openapi/v1/bots`，且 `bot_type=personal` | 是 | 创建个人云端 Bot 时绑定到当前业务空间 | 未接空间 owner API 时只能创建到个人空间；若传非个人空间且无法校验则拒绝 |
| `bots` 既有组 | `GET /openapi/v1/bots` | 不建议改旧契约 | 旧列表保持 owner 维度；空间化列表走 `inventory` | 不作为空间视图入口，避免影响历史调用方 |
| `bots` 既有组 | `GET/PUT/DELETE /openapi/v1/bots/{bot_id}`，`POST /restart`，`GET /status`，`GET /passport`，`GET/PUT /engine-config` | 建议消费 | 个人云端 Bot 的详情 / 更新 / 删除 / 重启 / 状态 / Passport / 引擎配置操作前做当前空间可见性校验 | header 缺省为个人空间；空间不匹配返回不可见 / 不允许 |
| `diagnostics` | `GET /runtime-logs`，`POST /engine-restart`，`GET /health`，`POST /health-check` | 是 | 个人云端诊断操作前确认 Bot 在当前空间可见 | 仅 personal cloud；空间不可见则拒绝 |
| `dormant` | `POST /openapi/v1/bots/dormant/{bot_id}/activate` | 是 | 激活沉寂个人云端 Bot 前确认当前空间可见 | 仅 personal cloud；空间不可见则拒绝 |
| `local` | `POST /openapi/v1/bots/local` | 是，但只允许个人空间 | 本地 Bot 创建只能发生在个人业务空间 | `resolve_current` 后要求 `space.kind == "personal"` |
| `local` | `GET /openapi/v1/bots/local` | 是，但只允许个人空间 | 本地 Bot 列表是个人空间资源视图 | 非个人空间返回不支持或空列表；建议 fail closed |
| `local` | `GET /openapi/v1/bots/local/{bot_id}`，`GET /openapi/v1/bots/local/{bot_id}/auth-status`，`POST /openapi/v1/bots/local/{bot_id}/restart`，`DELETE /openapi/v1/bots/local/{bot_id}`，`POST /openapi/v1/bots/local/{bot_id}/open-folder` | 是，但只允许个人空间 | 本地 Bot 单资源操作前确认当前空间是个人空间，并做 desktop ownership guard | 非个人空间拒绝；本地 Bot 不挂团队空间 |
| `local` | `GET /openapi/v1/bots/local/devices`，`GET /openapi/v1/bots/local/devices/{machine_id}/files` | 是，但只允许个人空间 | 设备 / 文件选择属于本地个人资源，不暴露到团队空间 | 非个人空间拒绝 |

结论：

- **空间视图主入口是 `inventory`**，前端空间切换后应优先刷新 `/bots/inventory`。
- **个人云端 Bot 创建与操作需要带空间上下文**：创建时用于绑定；操作时用于可见性校验。
- **本地 Bot 只消费空间上下文做限制**：本地 Bot 本期固定个人空间，不支持团队 / 业务空间归属。
- **旧 `GET /openapi/v1/bots` 不承担空间过滤职责**：避免破坏已有 owner 维度列表语义。

### 8.6 handler 使用原则

- list 类端点：解析 `X-Space-Id`，交给 `BusinessSpaceContextProtocol.resolve_current`，再传入对应 service 做过滤或 personal-only 校验。
- create 类端点：先解析当前业务空间；个人云端 Bot 记录空间归属，本地 Bot 校验必须是个人空间。
- per-bot 端点：先通过原有 service `get_bot/verify_ownership` 做 owner guard；如果当前业务空间会影响可见性，再调用 `assert_bot_visible_in_current_space`。
- 本地 Bot per-bot 端点：先解析当前业务空间并要求 personal，再做 desktop ownership guard；不把本地 Bot 挂到团队空间。
- 不新增散落的 `if x_space_id` 鉴权逻辑。
- 不新增 `/spaces` endpoint；空间列表由业务空间 owner 的 API 提供。

---

## 9. OpenAPI 注册与契约同步

### 9.1 router 注册

`openapi_v1/__init__.py`：

```python
from .inventory import router as inventory_router
from .local import router as local_router
from .diagnostics import router as diagnostics_router
from .dormant import router as dormant_router
from .edit_lock import router as edit_lock_router
from .editors import router as editors_router

_SUBGROUPS = [
    authorized_apps_router,
    authorized_bots_router,
    identity_router,
    resources_router,
    routines_router,
    skills_router,
    inventory_router,
    local_router,
    diagnostics_router,
    dormant_router,
    edit_lock_router,
    editors_router,
]
```

所有这些都是 user-scoped 公开端点，应继承 `USER_SCOPED_ERROR_RESPONSES` 与 `_PUBLIC_AUTH`。

### 9.2 OpenAPI schema

需要同步：

- `gateway/configs/schemas/bots.openapi.json`
- 公开契约兼容测试 `_compat.py` / 路径签名测试
- 新 schema 的 `extra="forbid"` 行为
- `user_id` query 参数出现在所有 user-scoped 新端点上

---

## 10. DI / Composition Root

`di/modules/bot_inventory_module.py` 负责装配：

```python
class BotInventoryModule:
    @provide
    def business_space(self) -> BusinessSpaceContextProtocol:
        return NoopBusinessSpaceContext()

    @provide
    def service_lifecycle(self) -> ServiceLifecyclePort:
        return NoopServiceLifecyclePort()

    @provide
    def bot_lifecycle_view(
        self,
        service_lifecycle: ServiceLifecyclePort,
    ) -> BotLifecycleView:
        return BotLifecycleView(service_lifecycle=service_lifecycle)

    @provide
    def bot_inventory_service(
        self,
        bot_service: BotServiceProtocol,
        desktop_service: DesktopBotServiceProtocol,
        business_space: BusinessSpaceContextProtocol,
        lifecycle_view: BotLifecycleView,
    ) -> BotInventoryService:
        return BotInventoryService(
            bot_service=bot_service,
            desktop_service=desktop_service,
            business_space=business_space,
            lifecycle_view=lifecycle_view,
        )
```

组合根可以选择 noop/prod；core 和 router 不读环境变量、不直接选择实现。

---

## 11. 错误语义

| 场景 | 推荐异常 | HTTP / Envelope |
| --- | --- | --- |
| bot 不存在或不属于 owner | `BotNotFoundError` | 404 envelope |
| `user_id` 与 principal 不一致 | `UserIdMismatchError` | 403 envelope（已有） |
| 本地 Bot 调用云端专属操作 | `BotOperationNotAllowedError` | 409 envelope |
| 非 dormant Bot 激活 | `BotOperationNotAllowedError` | 409 envelope |
| 不支持的 engine / combo | `UnsupportedEngineError` 或 `BotOperationNotAllowedError` | 400/409 envelope |
| 本地 Bot 不存在或非本人 | desktop ownership error / `NotFound` | 404 envelope，文案与普通 bot 404 保持一致 |
| 本地 / 服务 Bot 调个人云端 diagnostics | `EngineBotTypeNotSupportedError` / `BotOperationNotAllowedError` | 501 / 409 envelope |
| Desktop 上游 BaaS 失败 | `DesktopBotServiceError` / `DesktopBotOrphanError` | 502 envelope；子类映射放父类前 |

不要在 handler 中手写错误 envelope；抛 domain error，由 `@envelope_errors` 和全局 handler 转换。

---

## 12. 测试计划

### 12.1 单元测试

- `core/bot_inventory/policies/test_combo_policy.py`
  - 个人云端合法 engine / business space。
  - 本地 Bot P0 仅个人业务空间。
  - 不支持 engine 返回明确 reason。
- `core/bot_inventory/services/test_lifecycle_view.py`
  - personal ACTIVE/PENDING/FAILED/DORMANT 映射。
  - desktop running/offline/pending 映射。
  - actions / disabled_actions 矩阵。
- `core/bot_inventory/services/test_bot_inventory_service.py`
  - cloud + local merge。
  - keyword / engine / deploy_mode / business space 过滤。
  - 分页稳定排序。

### 12.2 openapi_v1 handler 测试

- `tests/community/adapters/http/openapi_v1/inventory/test_inventory_handlers.py`
  - 缺 `user_id` → 422 envelope。
  - `user_id` mismatch → 403 envelope。
  - 成功 list 返回 `Envelope[Page[BotInventoryItemResp]]`。
- `tests/community/adapters/http/openapi_v1/local/test_local_handlers.py`
  - create local 201。
  - 不支持 engine → 400/409。
  - restart/delete/open-folder 委托 desktop protocol。
- `tests/community/adapters/http/openapi_v1/diagnostics/test_diagnostics_handlers.py`
  - personal cloud runtime logs / health / health-check 成功委托。
  - local/service diagnostics 被拒。
  - runtime logs 路径白名单、tail 上限、level 过滤。
- `tests/community/adapters/http/openapi_v1/dormant/test_dormant_handlers.py`
  - personal dormant activate 成功。
  - desktop/service activate 被拒。
- path convention 测试覆盖 `inventory/local/diagnostics/dormant` literal 均在 `bots` 通配前。

### 12.3 架构 / 契约测试

- `tests/architecture/test_module_boundaries.py`
- openapi public namespace / path convention tests
- generated `bots.openapi.json` 兼容测试
- backend SAST / changed-line coverage

---

## 13. 分阶段落地

### P0：个人云端 + 本地 Bot 主链路

1. 新增 `core/bot_inventory` 类型、policy、lifecycle view、inventory service、README Context Boundary。
2. 新增 DI module，绑定业务空间 fallback / noop service lifecycle。
3. 新增 `openapi_v1/inventory`：列表、详情、动作。
4. 新增 `openapi_v1/local`：创建、列表、重启、删除、打开目录、设备列表。
5. 新增 `openapi_v1/diagnostics`：个人云端运行日志、引擎重启、健康检查。
6. 新增 `openapi_v1/dormant`：个人云端激活。
7. 可选加 `BotCreate.init_config`，但必须处理同步创建与 Passport 异步完成两条路径一致性。
8. 更新 OpenAPI schema 与兼容测试。

### P1：治理能力

1. `edit-lock` 接入个人云端 Bot。
2. `editors` 接入团队空间个人 Bot；个人空间 Bot 默认不可加协作者。
3. 业务空间上下文消费接口收口；prod 实现由业务空间 owner 提供。

### P2/P3：与服务线和空间线集成

1. 服务线实现 `ServiceLifecyclePort`，Bot Inventory 列表可展示 service Bot 精确态。
2. 业务空间 owner 提供正式 Service API 后，本模块切换为消费该实现。
3. 若 desktop 端补齐 runtime logs / engine restart，再解除 local disabled action。

---

## 14. 关键风险与决策点

| 风险 | 决策 / 缓解 |
| --- | --- |
| `local` 创建授权流不清晰 | 不在 handler 拼状态机；先补 Service API 或复用 desktop 现有授权流程 |
| `init_config` 在 202 授权路径丢语义 | 要么同步完成路径一起支持，要么 P0 不加该字段 |
| `bot_logs` 现有组 `user_id` 语义特殊 | 借鉴另一版方案，新增 `diagnostics` 承载用户级运行日志，不塞进 `bot_logs` |
| `engine_runtime/engine` restart 语义敏感 | 借鉴另一版方案，使用 `/diagnostics/{bot_id}/engine-restart`，避免和 Bot restart / engine surface 排除清单冲突 |
| 业务空间正式 API 未就绪 | 本模块只能个人业务空间 fallback；若产品要求团队/业务空间切换，必须依赖空间 owner 提供 prod API |
| 聚合分页不是真数据库全局分页 | P0 限源查询上限 + 稳定排序；规模风险作为后续 cursor 方案 |
| service 与 personal/local 边界混淆 | 本文 router 只实现 personal/local；service 经 `ServiceLifecyclePort` seam 显示，不操作 |

---

## 15. 推荐 PR 标题

```text
feat(backend): add personal and local bot inventory APIs
```

PR 描述按仓库模板填写：

```markdown
## Problem
Bot Inventory 需要统一展示和管理个人云端 Bot 与本地 Bot；现有公开 API 分散在 bots/desktop 内部能力中，缺少 openapi_v1 对齐的聚合清单面。

## Solution
新增 core/bot_inventory 聚合与策略层，新增 openapi_v1 inventory/local/dormant 组件，复用 BotServiceProtocol 与 DesktopBotServiceProtocol，并保持 user_id-scoped 公开面契约。

## Validation
- <列实际运行测试>
```
