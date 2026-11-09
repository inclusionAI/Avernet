# Backend Bot Avatar and Collaboration Repair Implementation Plan

> Historical implementation plan. The 2026-11-05 scope update supersedes its
> caller-repair and blank-summary-unconfirmed steps: repair now returns only
> ownership/profile/managers, and acknowledged blank summaries succeed.
> See [the current design](design.md) and
> [the follow-up plan](repair-scope-update-plan.md).


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让现有 Bot 注册/名称描述更新链路同步头像，并提供 `POST /openapi/v1/bots/{bot_id}/collaboration/repair`，手动修复已存在 BCS Bot 的当前协作资料。

**Architecture:** 正常编辑继续 best-effort；修复是独立、请求内执行的 Service API，复用事实读取、Owner/managers projector、单次发送 Plugin 和原 metadata/caller transport。新接口严格 existing-only，返回执行报告而非入队回执；生产请求侧 client 不与后台 loop 混用，不新增 worker、重试、持久化状态或配置平台。

**Tech Stack:** Python 3.12、Protocol/dataclass、Injector、SQLAlchemy、FastAPI、httpx/既有 HttpClient、pytest/pytest-asyncio；不新增第三方依赖。

**Spec:** [design.md](design.md)，尤其 §6–11；BCS 提供方独立计划见 [bcs-plan.md](bcs-plan.md)。

## Global Constraints

- 精确路径：`POST /openapi/v1/bots/{bot_id}/collaboration/repair`，不使用 `/bcs/repair`。
- “BCS 对应 Bot 不存在时返回明确错误，不自动注册、不创建、不恢复已删除 Bot。”
- 只有 Backend 当前 Owner 可修复；沿用 App grant、admission、租户、地址解析和授权机制。
- BCS 身份复用既有 `bcn_bot_reference`；保留 personal/default 的注册规则和 service 转换引用，service Bot handoff 不以当前 Owner 重拼地址。
- 修复范围为 `ownership`、`profile`（名称/描述/头像）、`managers`、适用的 `caller_config`；不盲目覆盖审批、visibility、friend_ext、运行时或凭证。
- 手动修复每项至多一次写入，不调用仅入队的 request_bot，不调用带默认重试的 sync_once；不改变既有后台同步规则。
- Core 不导入 HTTP 框架、具体 Plugin 或读取环境；装配只在 composition root。
- Backend 自身数据库读写失败报错，不当作空事实或成功；下游分项失败必须明确报告。
- 除 Task 1 经批准保留的 BotService 历史豁免外，新增/修改源码 ≤1,000 行；不修改 BCS、前端或无关业务，不降低 CI/覆盖阈值。
- 本文所有步骤是未来执行指令；编写基线 `a9b62a1f2`，测试“预期”不是验证证据。

## PR #3569 review fixes (2026-11-04)

1. Add SQL-reader and consumer regressions for personal/default Bots and
   converted service Bots. Resolve their established reference through
   `bcn_bot_reference`, including the saved `bcn_provider_bot_ref`, and reject
   invalid stored references before sending.
2. Add production-adapter and consumer regressions for empty/blank summaries.
   The existing BCS onboard contract preserves the old summary and does not
   echo it; after a confirmed onboard, report `profile=unknown` with
   `collaboration_summary_unconfirmed` for these inputs. Keep the avatar/name
   write and subsequent independent items, without adding another write or
   changing the BCS contract. Align the recording implementation and docs.
3. Cover multiline credential constructors and field-like literal values in
   the secret scanner. Exempt lookup keys only in their actual AST position;
   inspect complete multiline calls before declaring them safe.
4. Diagnose the failing Backend job from its logs/artifacts, reproduce its
   failure, and fix the cause without reducing test/coverage thresholds.
5. Run focused regressions, architecture and contract suites, the Backend CI
   gate, source-size checks and the secret scanner over the final changes.

---

## 0. 准备、命令和文件地图

- [ ] 阅读根 `AGENTS.md`、`CONTEXT-MAP.md`、design 的架构引用、`apps/backend/specs/2026-10-27-bcs-manager-sync/spec.md`、`apps/backend/specs/2026-11-01-bcs-ownership-sync/spec.md`，以及 `ownership_sync`、`manager_sync`、`caller_identity` 的 README。
- [ ] 检查已有工作区并安装当前 worktree hooks；不清理用户文件、不自动创建 worktree。

```bash
git status --short --branch
git merge-base --is-ancestor a9b62a1f2 HEAD
bash devops/hook/install_git_hooks.sh
(cd apps/backend && uv sync --frozen)
export AVATAR_BACKEND_BASE="$(git rev-parse HEAD)"
pt() { (cd apps/backend && DEPLOY_PROFILE=test SERVER_ENV=dev uv run --frozen pytest "$@"); }
```

- [ ] 保存基线测试；依赖/环境失败不属于有效 RED：

```bash
pt tests/community/core/bot_management/services \
   tests/community/core/bot_collaborator/manager_sync \
   tests/community/contracts/test_bcs_managers.py \
   tests/community/contracts/test_bcs_ownership.py -q
```

核心新增文件（均在 `apps/backend/`）：

| 路径 | 责任 |
| --- | --- |
| `src/agentclaw/community/kernel/collaboration_repair.py` | facts、结果、稳定错误分类，不含 HTTP |
| `src/agentclaw/community/core/bot_management/collaboration_repair/service_protocol.py` | 新 Service API 所属契约 |
| `src/agentclaw/community/api/collaboration_repair_service.py` | 对 adapter 的协议 re-export，避免新增 Core→api 反向依赖 |
| `src/agentclaw/community/plugin_api/bcs_collaboration_metadata.py` | metadata 存在性/资料/caller 的单次出站契约 |
| `src/agentclaw/community/core/repository/protocols/bot/collaboration_repair.py` | read-only profile/caller facts |
| `src/agentclaw/community/core/repository/implementations/bot/collaboration_repair.py` | 明确 tenant/env/PK 的事实读取 |
| `src/agentclaw/community/core/bot_management/collaboration_repair/profile.py` | 头像来源、显式清空和资料投影 |
| `src/agentclaw/community/core/bot_management/collaboration_repair/service.py` | 一次修复编排，不持有后台线程/队列 |
| `src/agentclaw/community/plugins/community/bcs_collaboration_metadata.py` | 组合既有 BcnService transport 与新出站协议，保留类型化结果 |
| `src/agentclaw/community/di/modules/collaboration_repair_module.py` | request-scoped 装配与资源关闭 |
| `src/agentclaw/community/plugins/local/bcs_collaboration_metadata.py` | recording local 实现 |
| `src/agentclaw/community/adapters/http/openapi_v1/bots/collaboration_repair.py` | 新 HTTP 路由 |
| `src/agentclaw/community/adapters/http/openapi_v1/bots/schemas_collaboration_repair.py` | 请求报告 schema |
| `src/agentclaw/community/core/bot_management/collaboration_repair/README.md` | Context Boundary、能力边界与失败语义 |

各新 Python 包添加空 `__init__.py`，不启动资源。下列 tasks 列出修改点、测试与必要拆分；所有路径均相对仓库根，表格明确前缀时除外。

## Task 1: 保留 BotService 结构，仅增加头像调用点

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py`
- Create: `apps/backend/src/agentclaw/community/core/bot_management/collaboration_repair/profile.py`

经维护者在 PR #3569 明确批准，BotService 沿用基线已有的超长文件豁免。
本 PR 不整体搬迁旧方法，不新增尺寸豁免；完整生命周期拆分留给独立重构。
新增头像投影留在独立 feature 模块，旧服务只增加必要的调用及参数传递。
原公共入口、构造参数和测试 patch 路径保持不变。

- [ ] 运行头像注册/编辑及 BotService 行为测试，保留显式清空、非法投影省略、
  稳定 entity 身份和 best-effort 失败语义。
- [ ] 确认未修改方法保持原样，现有文件尺寸门禁仍执行，新增源码不超过 1,000 行。

## Task 2: 现有 BCN transport 扩展头像和类型化结果

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py`
- Create: 同目录 `bcn_transport_types.py`、`bcn_roster.py`
- Modify: `apps/backend/tests/community/core/bot_management/services/test_bcn_service.py`、`test_bcn_service_register_provider.py`、`test_bcn_service_sync_caller_config.py`
- Create: 同测试目录 `test_bcn_service_avatar.py`、`test_bcn_request_errors.py`

**Interfaces:**
- `onboard_bot(..., *, avatar_url: str | None = None, timeout_seconds: float | None = None)`；None 在这个既有可省略的输入边界表示“不发送字段/沿用默认超时”，不是领域缺失值。
- `register_provider_bot(..., avatar_url: str | None = None)`：None 不发送；`""` 发送清空。
- `sync_bot_caller_config(..., provider_bot_ref: str | None = None, timeout_seconds: float | None = None)`：新增 reference 只用于已经解析的稳定地址；旧调用缺省保持既有行为。
- `get_attributes(..., timeout_seconds: float | None = None)` 和 onboard/caller 暴露单次发送超时。
- `BcnBotMissingError`、`BcnUnavailableError`、`BcnRequestError` 继承 `BcnServiceError`，保留旧捕获兼容；request error 带有限 `kind/code`，不靠解析错误字符串识别 404。

- [ ] 先把 roster cache/list 方法原样移至 `bcn_roster.py::BcnRosterMixin`，为 981 行的 bcn_service 留出空间。错误定义移到 `bcn_transport_types.py` 并在原模块显式 re-export；不让新文件回导 facade。`BcnRequestError(kind, code)` 构造器保留两个只读属性；kind 使用已有 `DeliveryFailure`。HTTP 状态在 transport 捕获处转换为固定 code（如 `http_403`），不能把原始 body 放入异常。
- [ ] **RED：** 使用 `HttpClient` mock 构建 BcnService，测试 omitted、设置和清空 payload：

```python
from unittest.mock import Mock
import httpx
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.core.bot_management.services.bcn_service import BcnService

def test_onboard_sends_explicit_avatar_clear():
    http = Mock(spec=HttpClient)
    http.post.return_value = httpx.Response(200,
        json={"bot_uuid": "b:e", "onboarded": True, "name": "N"},
        request=httpx.Request("POST", "https://example.com/admin/bots/onboard"))
    service = BcnService(http)
    service.onboard_bot("b:e", "N", "D", avatar_url="")
    assert http.post.call_args.kwargs["json"]["avatar_url"] == ""
    assert http.post.call_count == 1
```

- [ ] 运行 `pt tests/community/core/bot_management/services/test_bcn_service_avatar.py -q`，预期新参数 TypeError。
- [ ] 构造 payload 时只用 `is not None` 判断，禁止 `if avatar_url`；保留既有 auth、路径、成功响应和注册幂等逻辑。payload 代码为：

```python
if avatar_url is not None:
    payload["avatar_url"] = avatar_url
```

- [ ] 在原 httpx 异常处理处保留 404/401/403/503/超时的类型，不让通用 `except Exception` 再包成字符串。`get_attributes` 缺失与失败严格区分；onboard 的 `onboarded=false` 原样保留给调用者解释。所有 request timeout 使用传入 remaining，None 才沿用 `_timeout`。
- [ ] caller reference 为显式传入值时使用它构造 URL，不能重拼当前 owner；新增测试使用 `owner_workno="new-owner"`、`provider_bot_ref="b:stable"`，断言 URL 只含稳定后缀、source_revision 未改变。
- [ ] **GREEN：** 运行四个修改/新增 transport suites，加 `test_bcn_service_attributes.py`、`test_bcn_handoff_identity.py`。无头像的旧 payload 精确断言保持。
- [ ] Commit：`feat(backend): carry avatars through existing bcn transport`。

## Task 3: 日常注册和编辑的头像触发与投影

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/bot_management/collaboration_repair/profile.py`
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py` 的创建、编辑、同步、启动及重启调用点
- Create: `apps/backend/tests/community/core/bot_management/services/test_bot_avatar_sync.py`
- Modify: 现有 `test_bot_service_bcn_sync.py`、`test_bot_service_create_bcn_register.py` 的实际 payload/patch 目标

**Interfaces:** `avatar_from_record(bot: Mapping[str, object]) -> str` 返回 `""` 表示明确无头像；`_sync_bot_to_bcn` 对 profile 使用已保存记录，注册 helper 新增可省略的 `avatar_url` 边界参数但实际 Backend callers 总传当前投影。

- [ ] **RED：** 先测试数据来源优先级，不对显式空字符串作 truthiness fallback：

```python
import pytest
from agentclaw.community.core.bot_management.collaboration_repair.profile import avatar_from_record

@pytest.mark.parametrize("bot, expected", [
    ({"ext": {"avatar_url": "https://example.com/a.png"}}, "https://example.com/a.png"),
    ({"avatar_url": "", "ext": {"avatar_url": "old"}}, ""),
    ({"avatar_url": None, "ext": {"avatar_url": "legacy"}}, "legacy"),
    ({"ext": {"avatar_url": ""}}, ""),
    ({"ext": {}}, ""),
])
def test_avatar_source_preserves_clear(bot, expected):
    assert avatar_from_record(bot) == expected
```

- [ ] 运行 `pt tests/community/core/bot_management/services/test_bot_avatar_sync.py -q`，预期缺模块/函数。
- [ ] 实现 source helper：顶层 string（含空串）优先；顶层缺失/None 才查 ext 中 string；两者均缺失返回空串；明确存在但类型非法报 ValueError，不默认为空。ext 若是原始 JSON 字符串，使用既有 record 结构化解析入口，不能 ad hoc 截取。
- [ ] 在已有 repository 更新成功后触发：

```python
avatar_changed = ext is not None and "avatar_url" in ext
if sync_to_bcn and (bot_name is not None or bot_desc is not None or avatar_changed):
    self._sync_bot_to_bcn(**sync_kwargs)
```

`sync_kwargs` 使用原有地址和请求认证透传；helper 再从已保存 Bot 解析头像。读取失败不发送空头像；日常出站失败保持原 best-effort 日志，不把本地写失败吞掉。
- [ ] 为只改头像、清空、名称/描述连带头像、不相关 ext、sync_to_bcn=False、本地写失败、下游失败逐项写断言。复用现有 `_make_bot_service` fixture 的构造参数，不减少真实调用链。
- [ ] 在所有**原本就注册**的调用点传头像，保持既有条件、provider reference、connection mode、DRM 与 skipped 行为；不为未注册 Bot 新增触发点。
- [ ] **GREEN：** `pt tests/community/core/bot_management/services -q`。对 `_sync_bot_to_bcn` 的 skipped/onboarded=false 记录非成功日志，测试中不能只断言方法被调用。
- [ ] Commit：`fix(backend): sync avatar-only edits and explicit clears`。

## Task 4: 修复类型、协议与只读 facts

**Files:**
- Create: 文件地图中的 kernel、service_protocol、api re-export、repository protocol/implementation、metadata Plugin API、README
- Create: `apps/backend/tests/community/core/bot_management/collaboration_repair/test_facts.py`、`test_report.py`

**Interfaces:** 后续任务统一使用以下类型，字段不可在各层改名：

```python
from dataclasses import dataclass
from typing import Literal
from agentclaw.community.kernel.bot_manager_sync import SyncKey

Component = Literal["ownership", "profile", "managers", "caller_config"]
ItemStatus = Literal["succeeded", "failed", "unknown", "skipped_dependency", "not_applicable"]
ReportStatus = Literal["succeeded", "partial_failed", "failed"]

@dataclass(frozen=True)
class RepairCommand:
    key: SyncKey
    actor_user_id: str

@dataclass(frozen=True)
class ProfileSnapshot:
    bot_id: str
    bcs_bot_id: str
    name: str
    summary: str
    avatar_url: str

@dataclass(frozen=True)
class CallerSnapshot:
    bcs_bot_id: str
    enabled: bool
    source_revision: int

@dataclass(frozen=True)
class RepairFacts:
    key: SyncKey
    profile: ProfileSnapshot
    owner_user_id: str
    caller: CallerSnapshot | None

@dataclass(frozen=True)
class RepairItem:
    component: Component
    status: ItemStatus
    error_code: str | None = None

@dataclass(frozen=True)
class RepairReport:
    bot_id: str
    status: ReportStatus
    items: tuple[RepairItem, ...]
```

`caller=None` 仅表示该 Bot 没有适用的已建立 caller 配置；`error_code=None` 仅表示无错误。key、Owner、身份、name、summary、avatar_url 不改成 optional。

协议签名（用 Protocol/abstractmethod 正式声明）：

```python
class CollaborationRepairReadRepository(Protocol):
    def read_facts(self, key: SyncKey) -> RepairFacts: ...

class CollaborationRepairServiceProtocol(Protocol):
    async def repair(self, command: RepairCommand) -> RepairReport: ...

class BcsCollaborationMetadataPlugin(Plugin, Protocol):
    async def exists(self, bcs_bot_id: str, *, timeout_seconds: float) -> bool: ...
    async def update_profile(self, snapshot: ProfileSnapshot, *, timeout_seconds: float) -> None: ...
    async def sync_caller(self, snapshot: CallerSnapshot, *, timeout_seconds: float) -> None: ...
    async def aclose(self) -> None: ...
```

以上省略号是 Protocol 的合法抽象方法体，不是待补实现。Service Protocol 定义在 Core 所属模块，api 文件显式 re-export。新增 `MetadataDeliveryError(kind: DeliveryFailure, code: str)`、`RepairPreconditionError(code: str)` 及 `RepairFactsError(code: str)`，有限 code 定义在 kernel；HTTP 状态只在 adapter 映射。

- [ ] **RED：** `test_report.py` 覆盖报告聚合：

```python
from agentclaw.community.kernel.collaboration_repair import RepairItem, report_status

def test_unknown_is_not_success():
    items = (RepairItem("ownership", "succeeded"),
             RepairItem("profile", "unknown", "delivery_timeout"))
    assert report_status(items) == "partial_failed"
    assert report_status((RepairItem("ownership", "failed", "owner_drift"),)) == "failed"
    assert report_status((RepairItem("ownership", "succeeded"),
                          RepairItem("caller_config", "not_applicable"))) == "succeeded"
```

- [ ] 运行 `pt tests/community/core/bot_management/collaboration_repair/test_report.py -q`，预期缺新值。
- [ ] `report_status` 按设计计算 bad={failed,unknown,skipped_dependency}，有 bad 且有 succeeded → partial_failed，有 bad 无 succeeded → failed，否则 succeeded；拒绝空报告，固定由 service 产出四项。
- [ ] SQL reader 复用 `SqlOwnershipSyncReadRepository` 的 `tenant/env/pk/is_delete=0` 过滤与 session 生命周期，读取 BotModel 当前名称、描述、ext、owner、entity、bot_call_type、caller_config_revision；调用 Task 3 avatar helper，不触发任何业务 mutator。
- [ ] `test_facts.py` 用 `world.get(DatabasePlugin)` 和既有 `seed_sync_facts` 构造真实 SQL facts；再更新头像/caller 字段。测试 owner≠entity、已删除、跨租户、错误 key、SQL 异常、显式清空、合法 disabled caller。Caller applicability 复用已有领域规则与已保存 revision，不用 `enabled=False` 代替“不适用”，不 bump revision。
- [ ] **GREEN：** `pt tests/community/core/bot_management/collaboration_repair/test_facts.py tests/community/core/bot_management/collaboration_repair/test_report.py -q`。记录字段读取清单；未知 enum/坏 JSON/缺身份均不产生默认 payload。
- [ ] Commit：`feat(backend): define collaboration repair facts and result contracts`。

## Task 5: 单次 metadata 适配与 recording 实现

**Files:**
- Create: `apps/backend/src/agentclaw/community/plugins/community/bcs_collaboration_metadata.py`
- Create: `apps/backend/src/agentclaw/community/plugins/local/bcs_collaboration_metadata.py`
- Create: `apps/backend/tests/community/plugins/community/test_bcs_collaboration_metadata.py`
- Modify: Task 2 transport 方法，仅为保留 typed missing/unavailable/unknown 信息

**Interfaces:** `BcnCollaborationMetadataAdapter(bcn_service, profile_headers: Mapping[str,str])` 实现 Task 4 Plugin，位于 production community plugin；Core 不导入 BcnService。`RecordingBcsCollaborationMetadataPlugin` 记录 `calls: list[tuple[str, object]]`、`existing: set[str]`、可注入 `failures: dict[str, Exception]`；不存在返回 False，不自动创建。

- [ ] **RED：** 用 `Mock(spec=BcnService)` 配合真实 adapter 断言：

```python
import pytest
from unittest.mock import Mock
from agentclaw.community.plugins.community.bcs_collaboration_metadata import BcnCollaborationMetadataAdapter
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.kernel.collaboration_repair import ProfileSnapshot

@pytest.mark.asyncio
async def test_profile_uses_existing_onboard_only():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = {"bot_uuid": "b:e", "onboarded": True}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    await port.update_profile(ProfileSnapshot("b", "b:e", "N", "D", ""), timeout_seconds=2)
    assert bcn.onboard_bot.call_count == 1
    assert bcn.onboard_bot.call_args.kwargs["avatar_url"] == ""
    bcn.register_provider_bot.assert_not_called()
```

- [ ] 运行 `pt tests/community/plugins/community/test_bcs_collaboration_metadata.py -q`，预期缺 adapter。
- [ ] adapter 通过 `asyncio.to_thread` 调用原同步 HttpClient 链路，并向每个 transport 传入 remaining timeout。profile 转调 onboard，exists 转调 provider-scoped get_attributes，caller 转调 sync_bot_caller_config 并传稳定 reference；不创建新 URL 或新重试。
- [ ] typed mapping：真实 404→exists False；其他异常不能变 False。skipped→`RepairPreconditionError("collaboration_sync_unavailable")`；onboarded=false→`MetadataDeliveryError(REJECTED,"collaboration_bot_not_found")`；malformed/ID mismatch→UNKNOWN；caller stale/conflict→明确 failed，不加 revision。
- [ ] 同步 thread 取消不能强停底层 HTTP：transport 自身必须设置 remaining timeout；取消后不启动其他项，不在未结束发送上关闭共享 client。BcnService/HttpClient 使用已有生命周期，adapter.aclose 不关闭借来的 singleton。
- [ ] 实现 local fake：exists 只查 existing，profile/caller 写前检查 existing；记录调用后按 failures 注入异常。profiles 存储按 bcs_bot_id，不提供 register 方法，aclose 仅标记关闭。
- [ ] **GREEN：** 单测覆盖缺失、404、503、超时、2xx false、ID mismatch、skipped、caller stable ref/CAS，检查写入每项 ≤1 次、注册为0。确认日志没有完整 headers 或原始响应体。
- [ ] Commit：`feat(backend): adapt existing bcn calls for single-pass repair`。

## Task 6: 请求内修复编排与分项失败

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/bot_management/collaboration_repair/service.py`
- Create: `apps/backend/tests/community/core/bot_management/collaboration_repair/test_service.py`
- Create: 同测试目录 `factories.py`

**Interfaces:**
`CollaborationRepairService(reader, ownership_reader, managers_reader, metadata, ownership, managers, *, timeout_seconds: float, clock: Callable[[], float])`；均为 Task 4/现有 read 和 Plugin 契约，参数必填，clock 由 DI 注入 time.monotonic。公开方法 `repair(RepairCommand) -> RepairReport`。

Consumes：`OwnershipSyncReadRepository.read_facts(key)`、`ManagerSyncReadRepository.read_facts(key)`、各自 `build_snapshot`、`BcsOwnershipPlugin.replace_ownership`、`BcsManagersPlugin.replace_managers`。Produces 固定 ownership/profile/managers/caller_config 顺序的四项报告。

- [ ] **RED：** factories 创建 service，用 Mock readers、AsyncMock metadata/owner/managers 和 Task 4 dataclass。fixture 固定 key=`SyncKey("tenant-a","dev",7)`、stable=`"b:e"`、current_owner=`"owner-new"`；metadata.exists True，owner 返回匹配 OwnershipChange，managers 返回 ManagerSetChange。

```python
import pytest
from agentclaw.community.kernel.collaboration_repair import RepairPreconditionError

@pytest.mark.asyncio
async def test_missing_target_sends_no_writes(repair_case):
    repair_case.metadata.exists.return_value = False
    with pytest.raises(RepairPreconditionError, match="collaboration_bot_not_found"):
        await repair_case.service.repair(repair_case.command)
    repair_case.ownership.replace_ownership.assert_not_awaited()
    repair_case.managers.replace_managers.assert_not_awaited()
    repair_case.metadata.update_profile.assert_not_awaited()
    repair_case.metadata.sync_caller.assert_not_awaited()
```

`repair_case` fixture 在 `factories.py` 返回 SimpleNamespace，测试模块通过 `pytest_plugins` 或显式 fixture import 注册；所有对象由上述构造器创建，不调用私有 service 方法。

- [ ] 运行 `pt tests/community/core/bot_management/collaboration_repair/test_service.py -q`，预期缺编排。
- [ ] 实现顺序：read_facts→exists→读取/投影 Owner→replace_ownership→profile→重新读取/投影 managers→replace_managers→适用 caller。DB facts 读取在 to_thread 中按既有 reader scope 执行，关闭 session 后才做网络 I/O。
- [ ] 校验每次 reader key、稳定 identity 与初始解析身份一致；初次读取后确认 `command.actor_user_id == facts.owner_user_id`，这是统一 owner 授权后的事实漂移检查，不是第二套权限规则。失配时在任何出站前返回 `RepairPreconditionError("owner_changed")`（409）。Owner 必须与当前已提交事实匹配。遇到 Owner 在请求期间变化，不写旧 managers；返回 owner_drift/依赖跳过，不新增跨服务锁或 retry。
- [ ] 写一个有限的 deadline helper：

```python
def remaining(deadline: float, clock) -> float:
    seconds = deadline - clock()
    if seconds <= 0:
        raise RepairPreconditionError("repair_deadline_exceeded")
    return seconds
```

进入执行后 deadline 错误映射对应项 failed/unknown 和后续 skipped_dependency；未发送的项不标 unknown。每次调用都传当前 remaining，外层 await 也使用该预算，包括三个 to_thread 事实读取。超时后丢弃线程结果，不等待线程完成；读取自身的未分类 TimeoutError 不等同于请求截止时间耗尽。
- [ ] Owner 异常或不匹配就停止其他写入；owner missing-no-op→collaboration_bot_not_found。profile 失败可继续 managers/caller，除非目标已消失或截止时间耗尽。manager facts read 已分类错误由编排层纳入 failed 分项，保留已执行结果并跳过 caller，不能发送空名单；managers missing 同样停止 caller。
- [ ] 将已知 DeliveryFailure.NOT_SENT/REJECTED 映射 failed，UNKNOWN 映射 unknown；消息使用有限 code。不要捕获 BaseException；未分类异常和 Backend DB 错误仍抛出，不假装成功报告。
- [ ] 增加测试：Owner first、fresh managers、owner exclusion/Space 规则、unknown、skipped、partial_failed、caller not_applicable/disabled、timeout、取消、每项一次、无队列调用、无凭证/运行时写入。`AsyncMock.attach_mock` 或事件列表断言实际顺序，不只断言最终状态。
- [ ] **GREEN：** `pt tests/community/core/bot_management/collaboration_repair -q`；既有 owner/managers suite 再跑，确认重试和后台 behavior 未改。
- [ ] Commit：`feat(backend): repair collaboration state in one bounded request`。

## Task 7: DI、客户端生命周期和消费者契约

**Files:**
- Create: `apps/backend/src/agentclaw/community/di/modules/collaboration_repair_module.py`
- Modify: `apps/backend/src/agentclaw/community/di/container.py`
- Create: `apps/backend/tests/community/di/test_collaboration_repair_module.py`
- Create: `apps/backend/tests/community/contracts/test_bcs_collaboration_metadata.py`
- Modify: `apps/backend/tests/community/architecture/test_protocol_contracts.py` 的协议映射（按当前声明形式登记，不降低检查）
- Modify: 新模块 README Context Boundary

**Interfaces:** composition root 提供 `CollaborationRepairFactory.open(profile_headers: Mapping[str,str]) -> AsyncContextManager[CollaborationRepairServiceProtocol]`。Factory 是 DI 装配对象，不是 Core 的业务依赖；HTTP adapter 仅提交受筛选的认证上下文，不选择实现。

- [ ] **RED：** 写请求 loop 与后台 loop 分离测试；在请求 factory open 中执行 service，两次请求关闭后 owner/managers 请求侧 client 都已关闭，后台 stack 的 plugin/client ID 与请求侧不同。disabled 配置时工厂产出明确 unavailable，不偷偷启用同步。
- [ ] 运行 `pt tests/community/di/test_collaboration_repair_module.py -q`，预期缺装配。
- [ ] Factory 复用现有 BcnService、ManagerSyncConfig、trusted principal minter 与只读 repo。为当前 async request **新建**现有 HttpBcsOwnershipPlugin/HttpBcsManagersPlugin 实例，在 finally 调用各自 aclose；不从 ManagerSyncStack 借出绑定后台 loop 的实例。local profile 使用 recording 实现，不要求私有服务。
- [ ] timeout 从既有 transport timeout 注入，建立一个总预算，不新增可调重试或持久化配置。缺少 owner/managers enable、provider 凭证或正确 scope 时在首次写入之前报告 unavailable。
- [ ] 在 metadata consumer 契约 suite 中用 `world` 安装 local module、真实 SQL reader 和实际 CollaborationRepairService；seed 使用现有 `seed_sync_facts`，断言记录调用：

```python
@pytest.mark.asyncio
async def test_consumer_uses_metadata_plugin(repair_world):
    command, service, metadata = repair_world
    report = await service.repair(command)
    assert report.status == "succeeded"
    assert [name for name, value in metadata.calls] == ["exists", "profile"]

@pytest.mark.asyncio
async def test_missing_consumer_never_creates(repair_world):
    command, service, metadata = repair_world
    metadata.existing.clear()
    with pytest.raises(RepairPreconditionError, match="collaboration_bot_not_found"):
        await service.repair(command)
    assert [name for name, value in metadata.calls] == ["exists"]
```

`repair_world` fixture 在本测试文件定义：通过 `world.injector.binder.install(ConsumerModule())` 绑定服务、real reader 和三种 recording Plugin；返回 command、`world.get(CollaborationRepairService)`、`world.get(BcsCollaborationMetadataPlugin)`。测试中的 Bot 不适用 caller，避免无意漏断言。
- [ ] **GREEN：** `pt tests/community/di/test_collaboration_repair_module.py tests/community/contracts/test_bcs_collaboration_metadata.py tests/community/contracts/test_bcs_managers.py tests/community/contracts/test_bcs_ownership.py tests/community/architecture -q`。逐个修复真实边界声明，不增加全模块 import 豁免。
- [ ] Commit：`feat(backend): wire request-scoped collaboration repair plugins`。

## Task 8: HTTP 路由、Owner 权限和报告 schema

**Files:**
- Create: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/collaboration_repair.py`、`schemas_collaboration_repair.py`
- Modify: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`、`authorization_rules.py`、`admission_routes.py`、`responses.py`
- Create: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/errors_collaboration_repair.py`
- Create: `apps/backend/tests/community/adapters/http/openapi_v1/test_collaboration_repair.py`
- Modify: route authorization/admission inventory 的现有精确快照

**Interfaces:** 唯一路由为确认路径；无业务 request body。响应是 `Envelope<CollaborationRepairReport>`，模型字段保持 Task 4 名称；前置错误是既有 `ErrorEnvelope(data=None)`。

- [ ] **RED：** 使用现有 `user_scoped_client`、`bind_bot_access_seam`、`mount_public_error_handlers` 与本 route；Mock factory 返回 AsyncMock Service Protocol。验证真实 owner 可进入 handler、非 owner 在任何 factory 外呼前拒绝。

```python
def assert_repair_response(response):
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "partial_failed"
    assert [item["component"] for item in data["items"]] == [
        "ownership", "profile", "managers", "caller_config"]
    assert data["items"][2]["status"] == "unknown"
    assert "token" not in str(response.json()).lower()
```

fixture 的 service 返回 Task 4 dataclass，只有 managers unknown；不能让 adapter 把报告统一转成 succeeded。
- [ ] 运行 `pt tests/community/adapters/http/openapi_v1/test_collaboration_repair.py -q`，预期路由404/缺实现。
- [ ] 新 router 使用 `PublicAPIRoute`，在 authorization_rules 增加 OWNER_SCOPED，在 admission_routes 增加 GRANT_CHECKED_OWN_BOT；注册 `__init__.py`。复用现有 resolved Bot 地址和 verified caller；构造 `RepairCommand(SyncKey(tenant,env,bot_pk), actor_user_id)`，不允许客户端覆盖 tenant/env/BCS ID。
- [ ] profile_headers 只取现有允许转发的 Authorization/Cookie，交给请求 factory；不放进 Core command、日志或响应。前置权限由既有统一 seam 执行，不另写一套只看 caller string 的权限逻辑。
- [ ] 前置错误映射表写在新 errors 模块：missing→404、ambiguous/invalid facts→409、disabled→503、existence unavailable/timeout→502/504；未经认证/授权保留原链路401/403或隐藏策略。执行中的已知分项失败返回200报告，未分类异常仍500。
- [ ] `responses.py` 基线993行，加入映射前将通用 `envelope/page/created/accepted` helpers 与直接依赖常量移动到新 `response_envelopes.py` 并 re-export；不要超过1,000行。`authorization_rules.py` 基线986行，若新增行使其超限，抽出现有 metadata 路由表到 `authorization_rules_metadata.py`，通过 dict unpack 合并，不修改权限内容。
- [ ] OpenAPI声明401/403/404/409/502/503/504 ErrorEnvelope及200 report；明确200不等于所有项成功。测试旧 `/bcs/repair` 仍404、拒绝任意写入 payload、缺失无注册、App-only 无用户拒绝、handoff 后旧 Owner 拒绝、新 Owner 使用 stable ID。
- [ ] **GREEN：**

```bash
pt tests/community/adapters/http/openapi_v1/test_collaboration_repair.py \
   tests/community/adapters/http/openapi_v1/test_authorization_inventory.py \
   tests/community/adapters/http/openapi_v1/test_admission_inventory.py \
   tests/community/adapters/http/openapi_v1/test_bots_endpoints.py -q
```

- [ ] Commit：`feat(backend): expose owner-scoped collaboration repair endpoint`。

## Task 9: 完整回归、契约交接与真实联调

**Files:**
- Modify: 新模块 README、`apps/backend/docs/bot-ownership-sync-operations.md`（区分旧入队修复与新请求内修复）
- Create: `apps/backend/tests/community/acceptance/collaboration_repair/conftest.py`、`test_collaboration_repair_live.py`、`live_case.py`
- Modify: `singlebox/ci/singlebox_coverage_modules.yaml`（Backend 修复模块验收 target、完整分母与实测阈值，不改 BCS 门槛）

**Interfaces:** 全部实测结果引用当前实现提交；BCS先完成并提供 bcs-validation 中的 schema/接口版本。不能把未升级 BCS 的忽略字段2xx当成功验收。

- [ ] 从既有 `tests/community/acceptance/bot_ownership_sync/conftest.py`、`live_case.py` 复用单盒启动、真实授权与隔离数据 helper；新建 `collaboration_repair` 自己的 fixture，不调用旧 `repair_driver.py` 或内存队列来冒充新 HTTP 接口。新建 live story：通过正式生命周期先注册 Bot，调用 Backend 头像编辑，读取 BCS V1 Group/Session；再改/清空头像读回。人为制造已存在 Bot 的名称/Owner/managers 漂移，调用新接口后从 BCS 权威查询读回。
- [ ] 另一个 story 在 BCS 目标缺失时调用 repair，断言明确错误、下游 Bot 表/查询数量不增加；不调用注册让这个测试“通过”。不对测试之外的现有数据执行破坏操作。
- [ ] 执行本模块完整命令，记录失败与环境缺项：

```bash
pt tests/community/core/bot_management/services \
   tests/community/core/bot_management/collaboration_repair \
   tests/community/core/bot_collaborator/manager_sync \
   tests/community/contracts/test_bcs_collaboration_metadata.py \
   tests/community/contracts/test_bcs_ownership.py \
   tests/community/contracts/test_bcs_managers.py \
   tests/community/architecture -q
bash apps/backend/scripts/ci_test.sh --base "$AVATAR_BACKEND_BASE" --head HEAD
bash singlebox/ci/singlebox_coverage.sh
python singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

必须在 registry 增加 `collaboration_repair` Backend module，`acceptance_targets` 为 `tests/community/acceptance/collaboration_repair`；Core 分母包含整个 `core/bot_management/collaboration_repair/`；Router 为本次唯一 POST；Plugin 分母包含 metadata 的 exists/update_profile/sync_caller/aclose 的实际实现。复用 Owner/managers 的生产代码保持现有分母，不改旧阈值。先运行 `bash singlebox/ci/singlebox_coverage.sh --module collaboration_repair` 测得完整分母覆盖率，按仓库规则建立该新模块阈值并保留报告，再跑上述默认全部模块命令。不能省略函数来抬高覆盖，不能写未实测的通过数字。共享 coverage registry 属于本计划的 Backend 验证配置，不是 BCS 实现变更。

- [ ] 检查所有新增/修改源码行数、`git diff --check`、密钥扫描；没有数据库迁移、runtime state、私有 endpoint 或新增依赖。
- [ ] 在 PR 的 Validation 中记录最终命令退出码、实现 commit、live BCS 版本、4 项读回结果、缺失 Bot 零创建、无自动重试、超时/取消局限及未执行项；不另存过程性验证文档，不将仅记录 mock 调用当真实远端证据。
- [ ] 交接发布顺序：BCS先上线含独立列和 existing-only 行为，Backend后上线；回滚Backend不删BCS列、不撤销既有已成功修复的Owner。前端后续依据 report.status 展示成功/部分失败，不在此 plan 实现按钮。
- [ ] Commit：`test(backend): verify avatar sync and manual collaboration repair`。未经另行要求不 push、不创建 PR。

## 自检与覆盖映射

| Design 要求 | Task |
| --- | --- |
| 原注册/名称描述链路头像、清空与旧调用兼容 | 2–3 |
| stable identity、current Owner、完整协作者映射 | 4、6、8 |
| 名称/描述/头像/Owner/managers/caller 范围 | 3–6 |
| 不自动创建、missing-no-op、stale、skipped | 5–6、8–9 |
| 单次发送、deadline、unknown、部分结果 | 5–6 |
| 权限/App grant/tenant、准确路径、无客户端覆盖 | 8 |
| Plugin 生命周期、不同 loop、local conformance | 7 |
| 文件尺寸与不混入 BCS/前端实现 | 1–2、8–9 |
| 文档、架构门禁、真实联调和发布依赖 | 7–9 |

编写阶段只进行文档/路径/契约自检；未执行本计划、未运行实现测试，所有任务待实施。

## 计划编写验证

已核对 design 覆盖、跨任务 DTO/协议一致性、真实路由/测试/coverage 入口，以及既有 owner/managers 只读投影和单次发送接口。本地相对链接、代码围栏、Python/JSON/Bash 示例语法和空白检查通过。未修改业务代码、未执行拆分或测试任务；全部复选框保持待实施。
