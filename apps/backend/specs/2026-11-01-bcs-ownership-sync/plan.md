# Backend → BCS Bot Ownership Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend 完整提交 Bot Owner 变更后，以非阻塞、无持久补偿的 best-effort 通道调用 BCS ownership PUT，并在确认后刷新 managers。

**Architecture:** Owner 使用独立的 Service API、事实读取和 Plugin API，复用现有 managers 的一个有界 dispatcher/lifecycle。队列按 `SyncKey` 合并显式工作类型，每次 attempt 读取当前事实；Owner 失败不改写业务结果，也不阻塞已有 managers 工作。BCS 仍执行自身授权和事务，Backend 不新增 outbox、Redis 协调或 Owner 初始化。

**Tech Stack:** Python 3.12、typing Protocol/dataclass、Injector、SQLAlchemy、asyncio/threading、httpx、PyJWT、pytest/pytest-asyncio、真实单盒 BCS；不新增第三方依赖。

---

## 0. 执行约定与范围

- 依据：[已确认设计](spec.md)，spec 提交 `9e0a36f1688df42db64596baf73c1b7d73059237`；功能基线 `2c12ae7900577df1f1b558d17162c9e63582d685`。
- 本文生成日期：2026-11-01。本文保留原实施步骤并更新执行状态；代码示意和预期结果**不替代 validation.md 中的实际验证证据**。
- 当前隔离 worktree/分支为 `ownership-sync-backend` / `feat/ownership-sync-backend`。执行前使用 using-git-worktrees 技能检查并复用，不切走用户分支、不再复制 worktree。
- 本功能是一个 Backend 消费侧改动，不拆成 BCS 实现任务；不改 BCS wire contract 或 SQL schema。
- 各 task 的 RED → GREEN → commit 顺序不可跳过。RED 必须是目标断言/缺少新增能力导致，依赖安装失败不是有效 RED。
- 路径均相对仓库根目录。文件定位采用路径 + 方法名，避免把会漂移的行号当作补丁坐标。
- 代码块指定新增接口、测试及关键函数体；嵌入已有文件时保留未提及的行为。每个参数/helper 必须来自本文、列出的现有模块或当前 task 的新增文件，不能留下假实现。
- 每个复选步骤只做一个动作；表格中的测试行逐行执行一次 RED/GREEN，避免把一整张矩阵当成一次大改。
- 协议/Plugin/消费者分步提交允许短暂未接通；Task 1–4 是一个契约交付单元，完成前不发布或声称完整架构门禁已通过。
- 用户已授权在当前分支使用 subagent-driven-development 执行；各任务已实施并双阶段审阅，未新建工作区、未合并或推送。

### 0.1 读前提与环境命令

- [x] 阅读 `AGENTS.md`、`CONTEXT-MAP.md`、`docs/agents/domain.md`、spec，以及 `docs/arch/arch.rules.md`、`docs/arch/ci.enforce.md`、`docs/arch/context-boundary-format.md`、`docs/arch/protocol-contract-tests.md`。
- [x] 读取 `/tmp/CODE_REVIEW.md`（若存在）；检查 worktree 与基线：

```bash
git status --short --branch
git merge-base --is-ancestor 2c12ae7900577df1f1b558d17162c9e63582d685 HEAD
git log -3 --oneline
```

预期：当前工作基于功能基线，用户改动被识别并保留。不得 reset/stash 用户文件。

- [x] 安装当前 worktree hooks。基线实际入口为下列路径，而非根指引中的旧路径：

```bash
bash devops/hook/install_git_hooks.sh
git config --get core.hooksPath
```

预期：`.githooks`。不关闭 pre-push，不使用 `--no-verify`。

- [x] 准备 Backend Python 环境：

```bash
(cd apps/backend && uv sync --frozen)
```

预期：Python 3.12 环境和锁定依赖安装成功；失败则记录原因，不修改 lock 或切换私有源。
本文以下简写命令都在仓库根目录执行：

```bash
pt() { (cd apps/backend && DEPLOY_PROFILE=test SERVER_ENV=dev uv run --frozen pytest "$@"); }
export OWNER_SYNC_BASE=9e0a36f1688df42db64596baf73c1b7d73059237
```

- [x] 保存最接近基线的回归结果：

```bash
pt tests/community/core/bot_collaborator/manager_sync \
   tests/community/core/bot_management/test_owner_handoff.py \
   tests/community/core/bot_management/test_owner_handoff_spaces.py \
   tests/community/contracts/test_bcs_managers.py \
   tests/community/di/test_bot_manager_sync_config.py -q
```

预期：PASS；预存失败需在 `validation.md` 中分开记录和复现，不能删除断言。

## 1. 文件与责任映射

代码保持在既有层，避免把所有内容塞进 dispatcher 或 `bot_service.py`。

| 新增文件（均在 `apps/backend/` 下） | 责任 |
| --- | --- |
| `src/agentclaw/community/kernel/bot_ownership_sync.py` | Owner facts/snapshot/result，不包含 I/O |
| `src/agentclaw/community/api/bot_ownership_sync.py` | 只准入的 Owner Service API v1 |
| `src/agentclaw/community/plugin_api/bcs_ownership.py` | 单次投递 Plugin API v1 和分类异常 |
| `src/agentclaw/community/core/repository/protocols/bot/ownership_sync.py` | Owner 独立读取与分页 |
| `src/agentclaw/community/core/repository/implementations/bot/ownership_sync.py` | SQL 实现，复用 BotModel、tenant guard |
| `src/agentclaw/community/core/bot_management/ownership_sync/__init__.py` | 包标记，不启动资源 |
| `src/agentclaw/community/core/bot_management/ownership_sync/projector.py` | 必填身份验证与稳定地址构造 |
| `src/agentclaw/community/core/bot_management/ownership_sync/service.py` | fresh-read、deadline、重试、确认 |
| `src/agentclaw/community/core/bot_management/ownership_sync/notifications.py` | post-commit 异常隔离和禁用实现 |
| `src/agentclaw/community/core/bot_management/ownership_sync/repair.py` | 单 Bot/分页人工重新提交 |
| `src/agentclaw/community/core/bot_management/ownership_sync/README.md` | 领域与 Context Boundary |
| `src/agentclaw/community/plugins/community/bcs_ownership.py` | 真 HTTP、服务签名、wire 解析 |
| `src/agentclaw/community/plugins/local/bcs_ownership.py` | 按 key 隔离的 recording fake |
| `src/agentclaw/community/di/modules/bot_ownership_sync_module.py` | Owner reader/consumer/repair 绑定，无第二套 loop |
| `docs/bot-ownership-sync-operations.md` | 配置、诊断、人工重新提交、回滚 |

修改的生产文件：

- `apps/backend/src/agentclaw/community/kernel/bot_manager_sync.py`：增加工作类型与 no-op outcome，不改变旧枚举值。
- `apps/backend/src/agentclaw/community/core/bot_collaborator/manager_sync/dispatcher.py`、`apps/backend/src/agentclaw/community/core/bot_collaborator/manager_sync/diagnostics.py`、`apps/backend/src/agentclaw/community/core/bot_collaborator/manager_sync/lifecycle.py`：共享执行设施。
- `apps/backend/src/agentclaw/community/core/bot_management/owner_handoff/service.py`：成功提交后通知。
- `apps/backend/src/agentclaw/community/di/bot_manager_sync_config.py`：新增默认关闭字段。
- `apps/backend/src/agentclaw/community/di/modules/bot_manager_sync_module.py`：共享 stack 和两个 Service API 的同实例绑定。
- `apps/backend/src/agentclaw/community/di/modules/infrastructure/test/bot_manager_sync.py`：两个 recording Plugin。
- `apps/backend/src/agentclaw/community/di/container.py`：注册 Owner DI module。
- `apps/backend/src/agentclaw/community/configs/application-singlebox.yaml`：显式启用本地 Owner 通道，不启用生产 handoff。

既有 `profile_modules.py` 已在 TEST/CORP_TEST 安装 recording module、singlebox 不安装；优先通过该 module 扩展，不重复新增 profile 分支。
Repository 协议直接从新文件导入，不为无使用者的 re-export 修改聚合 `__init__.py`。
各 task 下列出测试/文档/验收辅助文件的精确路径。

## 2. 依赖与提交检查点

```text
Task 1 类型/协议/投影 → Task 2 独立读取 → Task 3 投递插件
                                    → Task 4 编排/消费者契约
Task 4 → Task 5 混合调度 → Task 6 生命周期/配置/DI
Task 6 → Task 7 业务触发 → Task 8 人工重新提交
Task 8 → Task 9 架构/文档/完整回归 → Task 10 真实 BCS 验收
Task 10 → Task 11 覆盖门禁/最终交付
```

不把 Task 5–7 并行编辑同一 DI/dispatcher 文件；先完成契约和确定性调度测试再接业务。

### Task 1: 固定 v1 类型、协议和纯投影

**Files:**
- Create: `apps/backend/src/agentclaw/community/kernel/bot_ownership_sync.py`
- Create: `apps/backend/src/agentclaw/community/api/bot_ownership_sync.py`
- Create: `apps/backend/src/agentclaw/community/plugin_api/bcs_ownership.py`
- Create: `apps/backend/src/agentclaw/community/core/repository/protocols/bot/ownership_sync.py`
- Create: `apps/backend/src/agentclaw/community/core/bot_management/ownership_sync/__init__.py`、`apps/backend/src/agentclaw/community/core/bot_management/ownership_sync/projector.py`
- Modify: `apps/backend/src/agentclaw/community/kernel/bot_manager_sync.py`
- Test: `apps/backend/tests/community/core/bot_management/ownership_sync/test_projector.py`

- [x] 写入首个投影测试及该目录的空 `__init__.py`：

```python
from dataclasses import replace
import pytest
from agentclaw.community.kernel.bot_manager_sync import SyncKey, SnapshotUnavailable
from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts
from agentclaw.community.core.bot_management.ownership_sync.projector import build_snapshot


def test_owner_changes_do_not_change_bcs_address():
    facts = OwnershipFacts(SyncKey("t", "dev", 7), "bot", "stable", "new-owner")
    snapshot = build_snapshot(facts)
    assert snapshot.bcs_bot_id == "bot:stable"
    assert snapshot.owner_user_id == "new-owner"
    assert snapshot.key == facts.key


@pytest.mark.parametrize("field,value", [
    ("bot_id", ""), ("entity_id", ""), ("owner_id", ""),
    ("owner_id", " "), ("owner_id", " owner"),
])
def test_invalid_identity_never_gets_a_fallback(field, value):
    facts = OwnershipFacts(SyncKey("t", "dev", 7), "bot", "stable", "owner")
    with pytest.raises(SnapshotUnavailable):
        build_snapshot(replace(facts, **{field: value}))
```

- [x] RED：`pt tests/community/core/bot_management/ownership_sync/test_projector.py -q`。预期缺少新增模块。
- [x] 在新增 kernel 中定义以下完整数据类型；在既有 kernel 中增加 `SyncWorkKind` 和 `SyncOutcome.BOT_MISSING_NOOP = "bot_missing_noop"`：

```python
from dataclasses import dataclass
from agentclaw.community.kernel.bot_manager_sync import SyncKey

@dataclass(frozen=True)
class OwnershipFacts:
    key: SyncKey
    bot_id: str
    entity_id: str
    owner_id: str

@dataclass(frozen=True)
class OwnershipSnapshot:
    key: SyncKey
    bcs_bot_id: str
    owner_user_id: str

@dataclass(frozen=True)
class OwnershipChange:
    bot_id: str
    owner_user_id: str | None
    previous_owner_user_id: str | None
    changed: bool
    request_id: str
```

```python
class SyncWorkKind(StrEnum):
    MANAGERS = "managers"
    OWNERSHIP = "ownership"
```

- [x] 定义三个窄协议和异常；下述方法均为 v1，文件只导入 typing、相应 kernel、Plugin marker：

```python
# api/bot_ownership_sync.py
@runtime_checkable
class BotOwnershipSyncProtocol(Protocol):
    def request_ownership_sync(
        self, *, bot_pk: int, env: str, avernet_tenant: str, reason: SyncReason,
    ) -> SubmissionResult: ...

# core/repository/protocols/bot/ownership_sync.py
@runtime_checkable
class OwnershipSyncReadRepository(Protocol):
    def read_facts(self, key: SyncKey) -> OwnershipFacts: ...
    def page_keys(
        self, *, avernet_tenant: str, env: str, after_pk: int, limit: int,
    ) -> tuple[SyncKey, ...]: ...

# plugin_api/bcs_ownership.py
class BcsOwnershipError(Exception):
    def __init__(self, kind: DeliveryFailure, code: str) -> None:
        super().__init__(f"{kind.value} {code}")
        self.kind, self.code = kind, code

@runtime_checkable
class BcsOwnershipPlugin(Plugin, Protocol):
    async def replace_ownership(
        self, snapshot: OwnershipSnapshot, *, timeout_seconds: float,
    ) -> OwnershipChange: ...
    async def aclose(self) -> None: ...
```

`...` 仅用于 Protocol 声明，不是实现占位。Owner 请求类型不可 optional。

- [x] 实现投影函数；身份空白不做悄悄修剪：

```python
from agentclaw.community.kernel.bot_manager_sync import SnapshotUnavailable
from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts, OwnershipSnapshot


def build_snapshot(facts: OwnershipFacts) -> OwnershipSnapshot:
    values = {
        "tenant": facts.key.avernet_tenant, "env": facts.key.env,
        "bot_id": facts.bot_id, "entity_id": facts.entity_id, "owner_id": facts.owner_id,
    }
    for name, value in values.items():
        if not isinstance(value, str) or not value or value != value.strip():
            raise SnapshotUnavailable(f"invalid_{name}")
    if type(facts.key.bot_pk) is not int or facts.key.bot_pk < 1:
        raise SnapshotUnavailable("invalid_bot_pk")
    return OwnershipSnapshot(facts.key, f"{facts.bot_id}:{facts.entity_id}", facts.owner_id)
```

- [x] 增加 tenant/env/PK 的负例，GREEN 同一文件；预期所有投影断言通过。
- [x] 提交上述精确文件：`git commit -m "feat(backend): define owner sync contracts and stable projection"`。

### Task 2: 独立 Owner SQL reader 与分页

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/repository/implementations/bot/ownership_sync.py`
- Test: `apps/backend/tests/community/repository/bot/test_ownership_sync_reader.py`
- Reference: `apps/backend/tests/community/core/bot_collaborator/manager_sync/factories.py`

- [x] 通过既有 `world`/DatabasePlugin 和 `seed_sync_facts` 写失败测试，证明不依赖协作者或 Space：

```python
from agentclaw.community.core.repository.implementations.bot.ownership_sync import SqlOwnershipSyncReadRepository
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.core.bot_collaborator.manager_sync.factories import seed_sync_facts


def test_read_owner_ignores_missing_space_and_large_roster(world):
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(db, tenant="t", env="dev", bot_pk=7,
        entity_id="stable", owner_id="B", space_id=99999,
        rows=tuple((f"u{i}", "member") for i in range(1001)))
    reader = SqlOwnershipSyncReadRepository(db)
    facts = reader.read_facts(key)
    assert (facts.key, facts.entity_id, facts.owner_id) == (key, "stable", "B")
    assert reader.page_keys(avernet_tenant="t", env="dev", after_pk=0, limit=100) == (key,)
```

若该目录没有导出 `world`，在此测试文件显式导入
`tests.community.framework.fixtures` 的 `app_with_testing_modules, world`，不要改全局 conftest。

- [x] RED：`pt tests/community/repository/bot/test_ownership_sync_reader.py -q`。
- [x] 实现有范围的 ORM 查询，类名为 `SqlOwnershipSyncReadRepository`，构造函数 `@inject __init__(db: DatabasePlugin)`：

```python
def read_facts(self, key: SyncKey) -> OwnershipFacts:
    with avernet_tenant_scope(key.avernet_tenant):
        try:
            with self._db.orm_session() as session:
                bot = session.query(BotModel).filter(
                    BotModel.id == key.bot_pk,
                    BotModel.avernet_tenant == key.avernet_tenant,
                    BotModel.env == key.env,
                    BotModel.is_delete == 0,
                ).one_or_none()
                if bot is None:
                    raise SnapshotUnavailable("bot_not_found")
                return OwnershipFacts(key, bot.bot_id, bot.entity_id, bot.owner_id)
        except SQLAlchemyError as exc:
            raise SnapshotUnavailable("read_error") from exc


def page_keys(self, *, avernet_tenant, env, after_pk, limit):
    bounded = max(0, min(limit, 100))
    if bounded == 0:
        return ()
    with avernet_tenant_scope(avernet_tenant):
        try:
            with self._db.orm_session() as session:
                rows = session.query(BotModel.id).filter(
                    BotModel.avernet_tenant == avernet_tenant,
                    BotModel.env == env,
                    BotModel.is_delete == 0,
                    BotModel.id > after_pk,
                ).order_by(BotModel.id.asc()).limit(bounded).all()
                return tuple(SyncKey(avernet_tenant, env, pk) for (pk,) in rows)
        except SQLAlchemyError as exc:
            raise SnapshotUnavailable("read_error") from exc
```

补齐显式参数/返回类型及 `SQLAlchemyError/DatabasePlugin/BotModel/avernet_tenant_scope` 导入。
不导入 managers reader 或协作者/Space Model，不写数据库。

- [x] 逐项补齐测试：错 tenant、错 env、已删除、缺失、数据库异常、独立 session 修改 Owner 后再次读取、空页、exclusive 游标、limit 0/101、无协作者 Bot。
- [x] GREEN：`pt tests/community/repository/bot/test_ownership_sync_reader.py tests/community/repository/bot/test_manager_sync_reader.py -q`。
- [x] 提交 reader 与测试：`git commit -m "feat(backend): read current owner facts independently of managers"`。

### Task 3: 单次 HTTP Plugin 与 recording Plugin

**Files:**
- Create: `apps/backend/src/agentclaw/community/plugins/community/bcs_ownership.py`
- Create: `apps/backend/src/agentclaw/community/plugins/local/bcs_ownership.py`
- Test: `apps/backend/tests/community/plugins/community/test_bcs_ownership.py`
- Test: `apps/backend/tests/community/plugins/local/test_bcs_ownership.py`

- [x] 写 `httpx.MockTransport` 测试，不连接真实网络：

```python
import json
import httpx
import pytest
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.bot_ownership_sync import OwnershipSnapshot
from agentclaw.community.plugins.community.bcs_ownership import HttpBcsOwnershipPlugin

@pytest.mark.asyncio
async def test_put_uses_stable_path_and_service_principal():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"code": 20000, "message": "ok",
            "request_id": "req-1", "data": {"bot_id": "bot:stable",
            "owner_user_id": "B", "previous_owner_user_id": "A", "changed": True}})
    async with httpx.AsyncClient(base_url="http://bcs.test",
            transport=httpx.MockTransport(handler)) as client:
        plugin = HttpBcsOwnershipPlugin(base_url="http://bcs.test",
            mint_principal=lambda tenant: f"test-principal-{tenant}", client=client)
        result = await plugin.replace_ownership(
            OwnershipSnapshot(SyncKey("t", "dev", 7), "bot:stable", "B"), timeout_seconds=1)
        await plugin.aclose()
        assert not client.is_closed
    assert result.owner_user_id == "B" and result.changed is True
    assert len(seen) == 1
    assert seen[0].url.raw_path == b"/api/v1/collaboration/bots/bot%3Astable/ownership"
    assert json.loads(seen[0].content) == {"owner_user_id": "B"}
    assert seen[0].headers["X-Avernet-Principal"] == "test-principal-t"
    assert "Authorization" not in seen[0].headers
```

- [x] RED：`pt tests/community/plugins/community/test_bcs_ownership.py -q`。
- [x] 创建 `HttpBcsOwnershipPlugin`，保留 `HttpBcsManagersPlugin` 既有连接所有权模式：构造参数为 `base_url`、`mint_principal`、有效外部边界 `client: httpx.AsyncClient | None = None`。注册 `@plugin_impl(mode=Mode.PROD, rationale="direct trusted ownership sync")`，无 sibling Plugin import。

`replace_ownership` 的单次发送主体：

```python
try:
    principal = self._mint_principal(snapshot.key.avernet_tenant)
    if not isinstance(principal, str) or not principal:
        raise ValueError("missing_principal")
except Exception:
    raise BcsOwnershipError(DeliveryFailure.NOT_SENT, "principal_mint_failed") from None
path = "/api/v1/collaboration/bots/" + quote(snapshot.bcs_bot_id, safe="") + "/ownership"
try:
    async with asyncio.timeout(timeout_seconds):
        response = await self._borrow_client().put(path,
            headers={"X-Avernet-Principal": principal},
            json={"owner_user_id": snapshot.owner_user_id}, timeout=timeout_seconds)
        return _verify_envelope(response)
except TimeoutError:
    raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "timeout") from None
except httpx.ConnectError:
    raise BcsOwnershipError(DeliveryFailure.NOT_SENT, "connect_error") from None
except httpx.ConnectTimeout:
    raise BcsOwnershipError(DeliveryFailure.NOT_SENT, "connect_timeout") from None
except httpx.PoolTimeout:
    raise BcsOwnershipError(DeliveryFailure.NOT_SENT, "pool_timeout") from None
except httpx.ReadTimeout:
    raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "read_timeout") from None
except httpx.WriteTimeout:
    raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "write_timeout") from None
except httpx.HTTPError:
    raise BcsOwnershipError(DeliveryFailure.UNKNOWN, "transport_error") from None
```

连接管理的完整方法：

```python
def _borrow_client(self):
    if self._client is None:
        self._client = httpx.AsyncClient(base_url=self._base_url)
    return self._client

async def aclose(self):
    if self._owns_client and self._client is not None:
        client, self._client = self._client, None
        await client.aclose()
```

- [x] 实现 `_verify_envelope`；响应 ID/Owner 与发送目标的比较留给 Core：

```python
def _verify_envelope(response: httpx.Response) -> OwnershipChange:
    status = response.status_code
    if 400 <= status < 500 and status not in {408, 425, 429}:
        raise BcsOwnershipError(DeliveryFailure.REJECTED, f"http_{status}")
    if status != 200:
        raise BcsOwnershipError(DeliveryFailure.UNKNOWN, f"http_{status}")
    malformed = BcsOwnershipError(DeliveryFailure.UNKNOWN, "malformed_envelope")
    try:
        body = response.json()
    except ValueError:
        raise malformed from None
    if not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 20000:
        raise malformed
    if not isinstance(body.get("message"), str):
        raise malformed
    data = body.get("data")
    required = {"bot_id", "owner_user_id", "previous_owner_user_id", "changed"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise malformed
    def valid_id(value):
        return isinstance(value, str) and bool(value) and value == value.strip()
    if not valid_id(body.get("request_id")) or not valid_id(data["bot_id"]):
        raise malformed
    owner, previous, changed = data["owner_user_id"], data["previous_owner_user_id"], data["changed"]
    if type(changed) is not bool:
        raise malformed
    if owner is not None and not valid_id(owner):
        raise malformed
    if previous is not None and not valid_id(previous):
        raise malformed
    if changed and (owner is None or previous is None or previous == owner):
        raise malformed
    if not changed and previous is not None:
        raise malformed
    return OwnershipChange(data["bot_id"], owner, previous, changed, body["request_id"])
```

- [x] 注册 `RecordingBcsOwnershipPlugin`（`Mode.LOCAL/Flavor.FAKE`）；以 `snapshot.key` 保存 Owner，不使用单个全局 Owner：

```python
class RecordingBcsOwnershipPlugin(BcsOwnershipPlugin):
    def __init__(self):
        self.calls: list[OwnershipSnapshot] = []
        self.outcomes: deque[OwnershipChange | BcsOwnershipError] = deque()
        self.owners: dict[SyncKey, str] = {}

    async def replace_ownership(self, snapshot, *, timeout_seconds):
        self.calls.append(snapshot)
        if self.outcomes:
            result = self.outcomes.popleft()
            if isinstance(result, BcsOwnershipError):
                raise result
            return result
        previous = self.owners.get(snapshot.key, snapshot.owner_user_id)
        changed = previous != snapshot.owner_user_id
        self.owners[snapshot.key] = snapshot.owner_user_id
        return OwnershipChange(snapshot.bcs_bot_id, snapshot.owner_user_id,
            previous if changed else None, changed, f"recorded-{len(self.calls)}")

    async def aclose(self):
        return None
```

recording 默认仅模拟已初始化目标；缺失 Bot/version-0/未知 Human 必须通过 scripted outcomes 表达，不能拿 fake 默认行为证明真实注册流程。

- [x] 按以下矩阵逐例补测试并 GREEN：

| 输入 | 断言 |
| --- | --- |
| 真实 signer + MockTransport | HS256/iss/aud/kid/60s/app-only/tenant；无用户主体 |
| 变更、幂等、缺失 Bot null | 精确结果字段；no-op 不抛“Owner 必填”错误 |
| 缺字段、bool-as-code、int-as-changed、矛盾 previous | UNKNOWN，无原始 response 出现在异常字符串 |
| 400/401/403/404/409 | REJECTED，单次请求 |
| 408/425/429/500/503、损坏 JSON | UNKNOWN，单次请求 |
| connect/pool/mint 错误、read/write timeout | 对应固定分类及 code |
| 取消、外部 client、自有 client 重复关闭 | 取消向上传播；外部池不关闭；自有池只关闭一次 |
| recording 两个 tenant 同 PK | 分别保存和返回，不串 Owner |

命令：`pt tests/community/plugins/community/test_bcs_ownership.py tests/community/plugins/local/test_bcs_ownership.py -q`。
- [x] 提交：`git commit -m "feat(backend): add trusted BCS ownership delivery plugins"`。

### Task 4: Owner 有限重试编排、诊断和消费者契约

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/bot_management/ownership_sync/service.py`
- Modify: `apps/backend/src/agentclaw/community/core/bot_collaborator/manager_sync/diagnostics.py`
- Test: `apps/backend/tests/community/core/bot_management/ownership_sync/test_service.py`
- Test: `apps/backend/tests/community/contracts/test_bcs_ownership.py`

- [x] 首先通过真实 consumer + recording Plugin 固定错误隔离和 fresh-read：

```python
from unittest.mock import Mock
import pytest
from agentclaw.community.core.bot_management.ownership_sync.service import BotOwnershipSyncService
from agentclaw.community.kernel.bot_manager_sync import SyncKey, SyncOutcome, DeliveryFailure
from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipError
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin

@pytest.mark.asyncio
async def test_retry_rereads_current_owner():
    key = SyncKey("t", "dev", 7)
    reader = Mock()
    reader.read_facts.side_effect = [OwnershipFacts(key, "bot", "stable", "B"),
                                   OwnershipFacts(key, "bot", "stable", "C")]
    plugin = RecordingBcsOwnershipPlugin()
    plugin.outcomes.append(BcsOwnershipError(DeliveryFailure.UNKNOWN, "read_timeout"))
    async def no_wait(seconds):
        return None
    service = BotOwnershipSyncService(reader, plugin, wait=no_wait, jitter=lambda value: 0)
    assert await service.sync_once(key) is SyncOutcome.CONFIRMED
    assert [call.owner_user_id for call in plugin.calls] == ["B", "C"]
    assert reader.read_facts.call_count == 2
```

- [x] RED：`pt tests/community/core/bot_management/ownership_sync/test_service.py -q`。
- [x] 扩展 diagnostics：增加 `SyncEvent.SEND_BOT_MISSING_NOOP`；`record` 新增 `work_kind=SyncWorkKind.MANAGERS`、`request_id=""`、`elapsed_ms=0`、`error_code=""`；固定 code 白名单/`http_NNN` 格式校验，不接受原始错误正文。保持旧 `counted[(event, outcome)]` 属性兼容；另提供有限标签 `counted_by_kind[(kind,event,outcome)]`，不要用 tenant/PK/request_id 作 counter key。

```python
# 替换 record() 原来的计数临界区，不能保留旧增量导致双计数。
# 新增 _counted_by_kind 同样在 __init__ 初始化 Counter。
kind_value = SyncWorkKind(work_kind).value
with self._counted_lock:
    self._counted[(event_value, outcome_value)] += 1
    self._counted_by_kind[(kind_value, event_value, outcome_value)] += 1
# 日志固定增加 kind、request_id、elapsed_ms、经过有限分类验证的 error_code；不写 payload。
```

- [x] 实现 `BotOwnershipSyncService`。构造参数为 reader、bcs，keyword-only `policy/clock/jitter/wait/diagnostics` 与现有 manager service 测试注入约定一致；复用 `SyncRunPolicy/backoff_delay/default_jitter`，不复用 manager reader/projector。

下面是 `sync_once` 的完整控制流（类字段按构造参数保存；`_record` 的定义紧随其后）：

```python
async def sync_once(self, key: SyncKey) -> SyncOutcome:
    started = self._clock()
    deadline = started + self._policy.budget_seconds
    for attempt in range(1, self._policy.max_attempts + 1):
        try:
            if attempt > 1:
                delay = backoff_delay(retry_index=attempt - 1,
                    remaining_seconds=deadline - self._clock(), jitter=self._jitter)
                if delay is None:
                    return self._record(SyncEvent.DEADLINE_EXPIRED, key, attempt, started)
                await self._wait(delay)
            if self._clock() >= deadline:
                return self._record(SyncEvent.DEADLINE_EXPIRED, key, attempt, started)
            try:
                facts = await asyncio.to_thread(self._reader.read_facts, key)
            except Exception:
                return self._record(SyncEvent.READ_FAILED, key, attempt, started)
            if facts.key != key:
                return self._record(SyncEvent.READ_FAILED, key, attempt, started)
            if self._clock() >= deadline:
                return self._record(SyncEvent.READ_LATE, key, attempt, started)
            try:
                snapshot = build_snapshot(facts)
            except (SnapshotUnavailable, ValueError):
                return self._record(SyncEvent.PROJECTION_FAILED, key, attempt, started)
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._record(SyncEvent.DEADLINE_EXPIRED, key, attempt, started)
            try:
                result = await asyncio.wait_for(self._bcs.replace_ownership(
                    snapshot, timeout_seconds=remaining), timeout=remaining)
            except BcsOwnershipError as exc:
                if exc.kind is DeliveryFailure.REJECTED:
                    return self._record(SyncEvent.SEND_REJECTED, key, attempt, started,
                                        error_code=exc.code)
                self._record(SyncEvent.SEND_RETRYABLE, key, attempt, started, error_code=exc.code)
                continue
            except Exception:
                self._record(SyncEvent.SEND_RETRYABLE, key, attempt, started,
                             error_code="unclassified_delivery")
                continue
            if result.bot_id != snapshot.bcs_bot_id:
                return self._record(SyncEvent.SEND_DRIFT, key, attempt, started,
                                    SyncOutcome.DRIFT, result.request_id)
            if result.owner_user_id is None:
                return self._record(SyncEvent.SEND_BOT_MISSING_NOOP, key, attempt, started,
                                    SyncOutcome.BOT_MISSING_NOOP, result.request_id)
            if result.owner_user_id != snapshot.owner_user_id:
                return self._record(SyncEvent.SEND_DRIFT, key, attempt, started,
                                    SyncOutcome.DRIFT, result.request_id)
            return self._record(SyncEvent.SEND_CONFIRMED, key, attempt, started,
                                SyncOutcome.CONFIRMED, result.request_id)
        except asyncio.CancelledError:
            self._record(SyncEvent.CANCELLED, key, attempt, started)
            raise
    return self._record(SyncEvent.RETRIES_EXHAUSTED, key,
                        self._policy.max_attempts, started)


def _record(self, event, key, attempt, started, outcome=SyncOutcome.FAILED,
            request_id="", error_code=""):
    self._diagnostics.record(event, key=key, outcome=outcome, attempt=attempt,
        work_kind=SyncWorkKind.OWNERSHIP, request_id=request_id,
        elapsed_ms=max(0, int((self._clock() - started) * 1000)), error_code=error_code)
    return outcome
```

HTTP Plugin 已验证结果组合，Core 消费者不重新解析 wire。未知本地 SQL 错误只记录固定原因。

- [x] 对每个分支增加虚拟 clock/Event 测试：三次上限、15 秒预算、读结果过期不发、NOT_SENT、UNKNOWN、REJECTED 不重试、noop/drift 不触发确认、CancelledError 传播、失败后可新开一轮。
- [x] 在 `test_bcs_ownership.py` 中使用 `world` 和测试 Injector provider，真实 consumer/SQL reader + recording Plugin，不直接测试 Plugin 当作 consumer suite：

```python
from injector import Module, provider, singleton
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipPlugin
from agentclaw.community.core.repository.implementations.bot.ownership_sync import SqlOwnershipSyncReadRepository
from tests.community.core.bot_collaborator.manager_sync.factories import seed_sync_facts

class ConsumerModule(Module):
    @singleton
    @provider
    def plugin(self) -> BcsOwnershipPlugin:
        return RecordingBcsOwnershipPlugin()

    @singleton
    @provider
    def consumer(self, db: DatabasePlugin, bcs: BcsOwnershipPlugin) -> BotOwnershipSyncService:
        return BotOwnershipSyncService(SqlOwnershipSyncReadRepository(db), bcs)

@pytest.mark.asyncio
async def test_world_consumer_calls_ownership_plugin(world):
    world.injector.binder.install(ConsumerModule())
    key = seed_sync_facts(world.get(DatabasePlugin), tenant="t", env="dev", bot_pk=7,
                         entity_id="stable", owner_id="B", rows=(), space_id=None)
    consumer, plugin = world.get(BotOwnershipSyncService), world.get(BcsOwnershipPlugin)
    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED
    assert plugin.calls[-1].owner_user_id == "B"
    assert plugin.calls[-1].key == key
    plugin.outcomes.append(BcsOwnershipError(DeliveryFailure.REJECTED, "http_403"))
    assert await consumer.sync_once(key) is SyncOutcome.FAILED
    assert len(plugin.calls) == 2
```

此文件显式导入 Task 4 首段使用的 pytest、service、SyncOutcome、DeliveryFailure、error、recording；不依赖另一个测试模块的隐式 globals。

- [x] GREEN：`pt tests/community/core/bot_management/ownership_sync tests/community/contracts/test_bcs_ownership.py tests/community/core/bot_collaborator/manager_sync/test_sync_service.py -q`。
- [x] 提交：`git commit -m "feat(backend): deliver current ownership with bounded retries"`。

### Task 5: 扩展共享 dispatcher，保留 managers-only 语义

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/bot_collaborator/manager_sync/dispatcher.py`
- Test: `apps/backend/tests/community/core/bot_collaborator/manager_sync/test_ownership_dispatcher.py`
- Reference: `apps/backend/tests/community/core/bot_collaborator/manager_sync/test_dispatcher.py`

- [x] 写明两类通知合并时 Owner 先执行，成功仅需一轮 managers：

```python
import asyncio
from unittest.mock import Mock
import pytest
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import BotManagerSyncDispatcher
from agentclaw.community.kernel.bot_manager_sync import SyncOutcome, SyncReason

@pytest.mark.asyncio
async def test_mixed_pending_runs_owner_then_one_manager_refresh():
    calls = []
    class Owner:
        async def sync_once(self, key):
            calls.append("owner")
            return SyncOutcome.CONFIRMED
    class Managers:
        async def sync_once(self, key):
            calls.append("managers")
            return SyncOutcome.CONFIRMED
    dispatcher = BotManagerSyncDispatcher(Managers(), Mock(), ownership_service=Owner())
    args = dict(bot_pk=7, env="dev", avernet_tenant="t", reason=SyncReason.OWNER_HANDOFF)
    dispatcher.request_sync(**args)
    dispatcher.request_ownership_sync(**args)
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert calls == ["owner", "managers"]
    finally:
        await dispatcher.stop()
```

- [x] RED：`pt tests/community/core/bot_collaborator/manager_sync/test_ownership_dispatcher.py -q`；预期缺少 `ownership_service`/Owner 方法。
- [x] 将 `QueueEntry.dirty` 替换为显式 pending 集合；`ownership_service: BotOwnershipSyncService | None = None` 只表示功能关闭这一合法状态，不用于隐藏缺失依赖：

```python
@dataclass
class QueueEntry:
    running: bool = False
    pending: set[SyncWorkKind] = field(default_factory=set)
```

修改 `_admit_locked(key, *, honor_closed, work_kind=SyncWorkKind.MANAGERS)` 的完整合并主体：

```python
if honor_closed and self._closed:
    return SubmissionResult.REJECTED
entry = self._entries.get(key)
if entry is not None:
    entry.pending.add(work_kind)
    return SubmissionResult.COALESCED
if self._capacity_used_locked() >= self._policy.max_keys:
    return SubmissionResult.REJECTED
self._entries[key] = QueueEntry(pending={work_kind})
self._ready.append(key)
self._schedule_wakeup_locked()
return SubmissionResult.QUEUED
```

- [x] 新增 `request_ownership_sync`；复用内存准入和 env/closed 判定，不调用 reader：

```python
def request_ownership_sync(self, *, bot_pk, env, avernet_tenant, reason):
    if self._ownership_service is None:
        return SubmissionResult.DISABLED
    key = SyncKey(avernet_tenant, env, bot_pk)
    with self._mutex:
        if not self._closed and self._env and env != self._env:
            result, rejection = SubmissionResult.REJECTED, SyncRejection.ENV_MISMATCH
        else:
            result = self._admit_locked(key, honor_closed=True, work_kind=SyncWorkKind.OWNERSHIP)
            rejection = self._rejection_for_locked(result)
    if rejection is None:
        self._note_admitted(key, result, reason, work_kind=SyncWorkKind.OWNERSHIP)
    else:
        self._note_rejected(key, rejection, "submission", work_kind=SyncWorkKind.OWNERSHIP)
    return result
```

`_note_admitted/_note_rejected` 增加 keyword `work_kind=MANAGERS` 并转交 diagnostics，旧调用不变。
关闭 Owner 通道的准入结果记录为 `DISABLED`，不得只沉默返回。

- [x] 在 `_run_key` 开始持锁复制并清空 pending，执行时用局部集合；完结只检查**新的** pending。保留 `_take_work` 原有 running 保护。

```python
async def _run_key(self, key):
    with self._mutex:
        entry = self._entries[key]
        pending = set(entry.pending)
        entry.pending.clear()
    try:
        with avernet_tenant_scope(key.avernet_tenant):
            run_managers = SyncWorkKind.MANAGERS in pending
            if SyncWorkKind.OWNERSHIP in pending:
                try:
                    assert self._ownership_service is not None
                    outcome = await self._ownership_service.sync_once(key)
                    run_managers |= outcome is SyncOutcome.CONFIRMED
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error("ownership_sync round_failed tenant=%s env=%s bot_pk=%s",
                                 key.avernet_tenant, key.env, key.bot_pk)
            if run_managers:
                await self._service.sync_once(key)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("manager_sync round_failed tenant=%s env=%s bot_pk=%s",
                     key.avernet_tenant, key.env, key.bot_pk)
    finally:
        with self._mutex:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.pending:
                    entry.running = False
                    self._ready.append(key)
                    self._schedule_wakeup_locked()
                else:
                    del self._entries[key]
```

Owner-only 成功在当前 slot 直接执行后续 managers，不申请新 slot。运行期间新增 managers
可导致下一轮再读一次，这是保守的 dirty 语义；不得为去重而清除运行中通知。

- [x] 逐个新增 Event 控制的回归：仅 managers 且 reason=OWNER_HANDOFF 不调用 Owner；Owner-only 成功跟随 managers；Owner FAILED/noop/drift 不增加原本没有的 managers；Owner 抛错仍跑已有 managers；运行中 Owner/manager 两种通知都保留；不同 tenant 分离；满队列现有 slot 后续工作；关闭已有 key 仍拒绝。
- [x] GREEN：`pt tests/community/core/bot_collaborator/manager_sync -q`；原 `test_owner_handoff_is_an_ordinary_roster_refresh` 必须保留并通过。
- [x] 提交：`git commit -m "feat(backend): coalesce explicit owner and manager work per bot"`。

### Task 6: 配置、共享资源生命周期与 DI

**Files:**
- Modify: `apps/backend/src/agentclaw/community/di/bot_manager_sync_config.py`
- Modify: `apps/backend/src/agentclaw/community/di/modules/bot_manager_sync_module.py`
- Create: `apps/backend/src/agentclaw/community/di/modules/bot_ownership_sync_module.py`
- Modify: `apps/backend/src/agentclaw/community/di/modules/infrastructure/test/bot_manager_sync.py`
- Modify: `apps/backend/src/agentclaw/community/di/container.py`
- Modify: `apps/backend/src/agentclaw/community/core/bot_collaborator/manager_sync/lifecycle.py`
- Create: `apps/backend/src/agentclaw/community/core/bot_management/ownership_sync/notifications.py`
- Test: `apps/backend/tests/community/di/test_bot_ownership_sync_config.py`
- Test: `apps/backend/tests/community/di/test_bot_ownership_sync_module.py`
- Test: `apps/backend/tests/community/core/bot_collaborator/manager_sync/test_ownership_lifecycle.py`

- [x] 写配置 RED 测试：

```python
import pytest
from agentclaw.community.di.bot_manager_sync_config import parse_manager_sync_config

def test_old_config_does_not_enable_owner():
    config = parse_manager_sync_config({"enabled": True, "env": "dev", "bcs_base_url": "http://bcs.test"})
    assert config.ownership_enabled is False

@pytest.mark.parametrize("block", [
    {"ownership_enabled": True}, {"ownership_enabled": "false"},
    {"enabled": False, "ownership_enabled": True},
])
def test_invalid_owner_config_is_rejected(block):
    with pytest.raises(ValueError):
        parse_manager_sync_config(block)
```

- [x] RED：`pt tests/community/di/test_bot_ownership_sync_config.py -q`。
- [x] `ManagerSyncConfig` 加 `ownership_enabled: bool = False`；扩展允许键，校验：

```python
ownership_enabled = block.get("ownership_enabled", False)
if type(ownership_enabled) is not bool:
    raise ValueError("bot_manager_sync.ownership_enabled must be a boolean")
if ownership_enabled and not enabled:
    raise ValueError("ownership sync requires bot_manager_sync.enabled")
return ManagerSyncConfig(enabled=enabled, ownership_enabled=ownership_enabled, **values)
```

- [x] 定义禁用通知实现，且记录固定 `DISABLED` 准入结果：

```python
class DisabledBotOwnershipSync:
    def __init__(self):
        self._diagnostics = SyncDiagnostics()

    def request_ownership_sync(self, *, bot_pk, env, avernet_tenant, reason):
        self._diagnostics.record(SyncEvent.SUBMIT_ADMITTED,
            key=SyncKey(avernet_tenant, env, bot_pk), outcome=SubmissionResult.DISABLED,
            reason=reason, work_kind=SyncWorkKind.OWNERSHIP)
        return SubmissionResult.DISABLED
```

测试断言重复禁用通知的 counter 累计且无数据库/HTTP。

- [x] 扩展已有 `ManagerSyncStack`，`ownership: BcsOwnershipPlugin | None = None` 表示禁用能力。
`available_with(bcs, *, ownership=None)` 保留旧调用有效；enabled 且 `ownership_enabled` 为真时，
由同一个 minter 创建 HTTP Owner Plugin。签名缺失仍返回原 unavailable，不创建网络资源。
`BotOwnershipSyncModule.configure` 绑定 `OwnershipSyncReadRepository → SqlOwnershipSyncReadRepository`。

在既有 `manager_sync` provider 中，构造可选 consumer 并传给原 dispatcher：

```python
owner_service = (
    BotOwnershipSyncService(self.__injector__.get(OwnershipSyncReadRepository), stack.ownership)
    if stack.ownership is not None else None
)
return BotManagerSyncDispatcher(
    BotManagerSyncService(reader=reader, bcs=stack.bcs), reader,
    env=config.env, ownership_service=owner_service)
```

Owner Service API provider 的明确同实例绑定：

```python
@singleton
@provider
def ownership_notifier(self) -> BotOwnershipSyncProtocol:
    config = self.__injector__.get(ManagerSyncConfig)
    if not config.ownership_enabled:
        return DisabledBotOwnershipSync()
    entry = self.__injector__.get(BotManagerSyncProtocol)
    if isinstance(entry, BotManagerSyncDispatcher):
        return entry
    return DisabledBotOwnershipSync()
```

Owner standalone consumer provider 只在需要该 consumer 时解析 `stack.ownership`；缺失明确报 unavailable，
不把必填 Plugin 变成 None；测试 module 直接提供 recording consumer。

- [x] 测试 module 增加 Owner recording singleton、consumer provider；stack 返回
`ManagerSyncStack.available_with(bcs, ownership=owner if config.ownership_enabled else None)`。
关闭时不得为了求 provider 参数而提前 resolve Owner HTTP Plugin；使用 injector 条件解析。
将 `BotOwnershipSyncModule()` 加入 `container.py` 的业务 module 列表，不新增 profile 分支。

- [x] lifecycle 构造函数增加 `ownership: BcsOwnershipPlugin | None = None`；composition root 从同一 stack 传入。
关闭时即使一个 Plugin 抛错，也尝试关闭另一个，错误保留为清理失败诊断：

```python
async def _shutdown_on_loop():
    try:
        await self._dispatcher.stop()
    finally:
        plugins = (self._bcs,) if self._ownership is None else (self._bcs, self._ownership)
        results = await asyncio.gather(*(plugin.aclose() for plugin in plugins), return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("sync_plugin_close_failed")
```

原超时、join、startup 单实例保护保持不变；不要新建 Owner 线程/loop。

- [x] 写 lifecycle/DI 断言：同实例 `world.get(BotOwnershipSyncProtocol) is world.get(BotManagerSyncProtocol)`；禁用不创建 Owner HTTP；关闭两个 client 在相同 loop；一方 close 异常不跳过另一方；startup race 仍单 loop；无签名降级；TEST/CORP_TEST recording、singlebox real。
- [x] GREEN：`pt tests/community/di/test_bot_ownership_sync_config.py tests/community/di/test_bot_ownership_sync_module.py tests/community/di/test_bot_manager_sync_config.py tests/community/di/test_profile_and_modules_for.py tests/community/core/bot_collaborator/manager_sync -q`。
- [x] 提交：`git commit -m "feat(backend): wire ownership sync behind a default-off flag"`。

### Task 7: 接入完整 handoff 成功边界

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/ownership_sync/notifications.py`
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/owner_handoff/service.py`
- Modify: `apps/backend/tests/community/core/bot_management/test_owner_handoff.py`（fixture 和显式构造参数）
- Modify: `apps/backend/tests/community/endpoints/test_owner_handoff_router.py`（显式构造参数）
- Create: `apps/backend/tests/community/core/bot_management/test_owner_handoff_ownership_sync.py`
- Create: `apps/backend/tests/community/core/bot_management/ownership_sync/test_notifications.py`

- [x] 审计当前基线所有实际 Owner 写入并记录到 `apps/backend/specs/2026-11-01-bcs-ownership-sync/validation.md`：

```bash
rg --pcre2 -n '(\.owner_id\s*=(?!=)|BotModel\.owner_id\s*:|SET\s+owner_id|set\s+owner_id)' \
  apps/backend/src --glob '*.py' --glob '*.sql'
rg -n 'OwnerHandoffService\(' apps/backend/src apps/backend/tests
```

预期正式替换仍是 `OwnerHandoffRepository.finish`。发现新增正式入口时，先补该入口完成点测试，
再接相同 helper；不得修改无关的 economy/task 模型 owner。记录所有直接构造的调用者并更新必填参数。

- [x] 在既有 handoff `world` fixture 中增加 `ownership_sync=MagicMock()`，factory 的 `service()` 始终传入它；将此 fixture 导入新测试文件，保留真实 DB/repository 和原 Passport/Runtime 测试替身。

```python
from tests.community.core.bot_management.test_owner_handoff import world, begin, drain  # noqa: F401

def test_notification_observes_committed_success(world):
    observed = []
    operation = begin(world)
    def on_submit(**kwargs):
        row = world.repo.get(operation)
        bot = world.bots.get_by_pk(world.bot["id"])
        observed.append((row["phase"], bot["owner_id"], kwargs["bot_pk"]))
    world.ownership_sync.request_ownership_sync.side_effect = on_submit
    drain(world, operation)
    assert observed == [("SUCCEEDED", "B", world.bot["id"])]
    assert len(world.passport.issues) == len(world.passport.retired) == 1
```

- [x] RED：`pt tests/community/core/bot_management/test_owner_handoff_ownership_sync.py -q`；预期成功后未收到通知。
- [x] 实现只隔离通知的 helper：

```python
def notify_bot_ownership_sync(sync: BotOwnershipSyncProtocol, *, bot_pk: int,
        env: str, avernet_tenant: str, reason: SyncReason) -> None:
    try:
        sync.request_ownership_sync(bot_pk=bot_pk, env=env,
                                   avernet_tenant=avernet_tenant, reason=reason)
    except Exception as exc:
        logger.error("bot ownership sync notification failed", extra={
            "bot_pk": bot_pk, "env": env, "avernet_tenant": avernet_tenant,
            "reason": reason.value, "exception_type": type(exc).__name__})
```

- [x] `OwnerHandoffService.__init__` 增加必填 `ownership_sync: BotOwnershipSyncProtocol` 并保存。
在 `COMMITTING_OWNER` 的 `_repo.finish()` **之后**，保留已有 manager helper，新增：

```python
notify_bot_ownership_sync(self._ownership_sync,
    bot_pk=row["bot_pk"], env=row["env"], avernet_tenant=row["avernet_tenant"],
    reason=SyncReason.OWNER_HANDOFF)
```

不要用 try 包住 `finish()` 然后返回 True；保留现有 handoff 异常/重试语义。
现有注释“不调用 BCS owner transfer”改为“通知权威 owner sync，不调用人工 transfer 协议”。

- [x] 添加取消、finish 抛错、等待授权、终态重放、recovery 完成通知测试；notification 抛错后仍 `SUCCEEDED` 且两个通知分别执行；断言原凭证调用次数不增加。新增参数使用 Disabled 或明确 recording，不设置生产默认 None。
- [x] GREEN：`pt tests/community/core/bot_management/test_owner_handoff.py tests/community/core/bot_management/test_owner_handoff_ownership_sync.py tests/community/core/bot_management/test_owner_handoff_spaces.py tests/community/core/bot_management/test_owner_handoff_delivery.py tests/community/endpoints/test_owner_handoff_router.py tests/community/core/bot_management/ownership_sync/test_notifications.py -q`。
- [x] 提交：`git commit -m "feat(backend): notify BCS ownership after committed owner handoff"`。

### Task 8: 人工重新提交，不新增任务持久化

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/bot_management/ownership_sync/repair.py`
- Modify: `apps/backend/src/agentclaw/community/di/modules/bot_ownership_sync_module.py`
- Test: `apps/backend/tests/community/core/bot_management/ownership_sync/test_repair.py`

- [x] 写分页准入结果测试；复用现有 `RepairPage` 数据类型，不继承依赖 managers notifier 的 service：

```python
from unittest.mock import Mock
from agentclaw.community.kernel.bot_manager_sync import SyncKey, SubmissionResult
from agentclaw.community.core.bot_management.ownership_sync.repair import OwnershipSyncRepairService

def test_page_records_every_non_admitted_key():
    keys = tuple(SyncKey("t", "dev", pk) for pk in (1, 2, 3))
    reader, notifier = Mock(), Mock()
    reader.page_keys.return_value = keys
    notifier.request_ownership_sync.side_effect = [
        SubmissionResult.QUEUED, SubmissionResult.REJECTED, SubmissionResult.DISABLED]
    result = OwnershipSyncRepairService(reader, notifier).request_page(
        avernet_tenant="t", env="dev", after_pk=0, limit=999)
    assert result.last_pk == 3 and result.queued == 1 and result.coalesced == 0
    assert result.rejected == 2 and result.rejected_keys == keys[1:]
    assert reader.page_keys.call_args.kwargs["limit"] == 100
```

- [x] RED：`pt tests/community/core/bot_management/ownership_sync/test_repair.py -q`。
- [x] 实现 `request_bot` 和 `request_page`：

```python
def request_bot(self, key: SyncKey) -> SubmissionResult:
    return self._notifier.request_ownership_sync(bot_pk=key.bot_pk, env=key.env,
        avernet_tenant=key.avernet_tenant, reason=SyncReason.REPAIR)


def request_page(self, *, avernet_tenant, env, after_pk, limit):
    bounded = max(0, min(limit, 100))
    if not bounded:
        return RepairPage(after_pk, 0, 0, 0, ())
    keys = self._reader.page_keys(avernet_tenant=avernet_tenant,
                                 env=env, after_pk=after_pk, limit=bounded)
    if any(key.avernet_tenant != avernet_tenant or key.env != env for key in keys):
        raise ValueError("ownership_repair_scope_mismatch")
    queued = coalesced = 0
    rejected = []
    for key in keys:
        result = self.request_bot(key)
        if result is SubmissionResult.QUEUED:
            queued += 1
        elif result is SubmissionResult.COALESCED:
            coalesced += 1
        else:
            rejected.append(key)
    return RepairPage(keys[-1].bot_pk if keys else after_pk,
                      queued, coalesced, len(rejected), tuple(rejected))
```

禁用实现 `request_bot → DISABLED`，`request_page → RuntimeError("ownership sync repair unavailable")`；
DI provider 使用配置和 stack 可用性选择，不能通过“全部拒绝的成功页”隐藏禁用。

- [x] 添加空页、limit=0、coalesced、跨 scope 拒绝、reader 错误传播、禁用和最新事实重读测试；不新增 HTTP router/cron/SQL 写入。
- [x] GREEN：`pt tests/community/core/bot_management/ownership_sync/test_repair.py tests/community/core/bot_collaborator/manager_sync/test_repair.py -q`。
- [x] 提交：`git commit -m "feat(backend): expose bounded manual ownership resubmission"`。

### Task 9: 架构声明、配置快照和运维文档

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/bot_management/ownership_sync/README.md`
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/README.md`、`apps/backend/src/agentclaw/community/core/bot_management/owner_handoff/README.md`
- Modify: `apps/backend/src/agentclaw/community/core/bot_collaborator/manager_sync/README.md`
- Modify: `apps/backend/src/agentclaw/community/core/repository/README.md`
- Modify: `apps/backend/src/agentclaw/community/api/README.md`、`apps/backend/src/agentclaw/community/plugin_api/README.md`、`apps/backend/src/agentclaw/community/plugins/local/README.md`
- Modify: `apps/backend/tests/community/architecture/test_module_boundaries.py`、`apps/backend/tests/community/architecture/test_architecture_compliance.py`
- Modify: `apps/backend/tests/community/config/effective_config.py`
- Modify: `apps/backend/tests/community/config/golden/community.json`、`apps/backend/tests/community/config/golden/singlebox.json`、`apps/backend/tests/community/config/golden/test.json`
- Modify: `apps/backend/src/agentclaw/community/configs/application-singlebox.yaml`
- Modify: `apps/backend/specs/2026-10-27-bcs-manager-sync/spec.md`
- Modify: `apps/backend/docs/bot-manager-sync-operations.md`
- Create: `apps/backend/docs/bot-ownership-sync-operations.md`

- [x] 运行 architecture RED/diagnosis，确认缺少的是本次声明而不是通过 broad allowlist 逃避：

```bash
pt tests/community/architecture/test_module_boundaries.py \
   tests/community/architecture/test_protocol_contracts.py \
   tests/community/architecture/test_architecture_compliance.py -q
```

- [x] 新目录 README 写入 Context Boundary；列出实际导入前缀，不引入 plugins/HTTP：

```yaml
purpose: Project committed Backend Bot ownership to BCS through bounded best-effort delivery.
provides:
  - BotOwnershipSyncService
  - OwnershipSyncRepairService
  - notify_bot_ownership_sync
consumes:
  - BotOwnershipSyncProtocol
  - OwnershipSyncReadRepository
  - BcsOwnershipPlugin
internal_dependencies:
  - agentclaw.community.api.bot_ownership_sync
  - agentclaw.community.kernel.bot_manager_sync
  - agentclaw.community.kernel.bot_ownership_sync
  - agentclaw.community.core.bot_management.ownership_sync
  - agentclaw.community.core.bot_collaborator.manager_sync
  - agentclaw.community.core.repository.protocols
  - agentclaw.community.plugin_api
  - agentclaw.community.log
```

- [x] `test_module_boundaries.py` 登记新模块；父模块和 handoff README 补精确 outgoing prefixes。
架构 AST 目前把 core→api 默认拦住，需要为 handoff service 和新 notifications 各添加一条
`api.bot_ownership_sync` 的窄例外，理由是 transport-free Service API 消费，与已有 manager 例外一致。
不放开全体 `core → api`，不新增 Plugin 契约豁免。

- [x] singlebox 的既有 `bot_manager_sync` 配置加 `ownership_enabled: true`；community/test 缺省 false。
扩展 `effective_config.py` 把该值包含在快照，然后运行现有生成器：

```bash
(cd apps/backend && DEPLOY_PROFILE=test uv run --frozen python -m tests.community.config.regen_golden)
pt tests/community/config/test_effective_config_snapshot.py -q
```

检查生成器支持的 profiles，不把本地路径/凭证写入 golden。只提交与此字段相关的快照差异。

- [x] 运维文档包含四态配置表、`ownership:sync` 授权、no-op/409/403/UNKNOWN 排查、关闭不回滚、旧版回滚必须删除新键，以及受控运维入口：

```python
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.core.bot_management.ownership_sync.repair import OwnershipSyncRepairService

# 在已启动的受控 Backend 进程中使用其现有 injector；不要另建一个未启动的队列。
# 运维独立进程须按平台正式 bootstrap 启动 lifecycle 后才可提交和有界 drain。
def resubmit_one(injector, *, tenant: str, env: str, bot_pk: int):
    return injector.get(OwnershipSyncRepairService).request_bot(SyncKey(tenant, env, bot_pk))
```

文档不得提供任意新 Owner override 参数；不宣称 `QUEUED` 等于成功。旧 managers 文档链接新文档，
保留仅 managers 的行为；spec 中历史验证结果不改写成此次验证。

- [x] GREEN：`pt tests/community/architecture tests/community/contracts/test_bcs_ownership.py tests/community/contracts/test_bcs_managers.py tests/community/config tests/community/di/test_bot_ownership_sync_module.py -q`。
- [x] 提交：`git commit -m "docs(backend): document ownership delivery boundaries and operations"`。

### Task 10: 真实 singlebox BCS 读回与受控 handoff 验收

**Files:**
- Create: `apps/backend/tests/community/acceptance/bot_ownership_sync/test_ownership_projection_live.py`
- Create: `apps/backend/tests/community/acceptance/bot_ownership_sync/conftest.py`
- Create: `apps/backend/tests/community/acceptance/bot_ownership_sync/fixture_module.py`
- Create: `apps/backend/tests/community/acceptance/bot_ownership_sync/fixture_passport.py`
- Create: `apps/backend/tests/community/acceptance/bot_ownership_sync/runtime/sitecustomize.py`
- Create: `apps/backend/tests/community/plugins/local/test_ownership_acceptance_fixture.py`
- Modify: `singlebox/modules/backend.sh`（仅显式验收启动增加 test runtime PYTHONPATH）
- Modify: `singlebox/ci/singlebox_coverage.sh`（受控 fixture 开关与 coverage 复用）
- Reference: `apps/backend/tests/community/acceptance/bot_collaborator/test_manager_sync_bcs_projection.py`
- Reference: `apps/backend/tests/community/acceptance/bot_collaborator/test_member_management_live.py`

**重要约束：** 当前 local Passport 的 `owner_handoff_supported()` 返回 false；直接向普通单盒
handoff 发 HTTP 不能完成流程。不能修改它假装换证成功，也不能把手动调用同步器当业务触发。
此 task 在测试目录安装明确的测试 composition module，仅替换隔离 Bot 的 Passport/运行时边界，
让共享单盒中的真实 HTTP、handoff、数据库、原 TaskQueue、dispatcher、signer、BCS 全部保持真实。
这证明受控交接 + 真实投递，不证明真实 AAC/凭证退役/设备更新。

- [x] 写 fixture 的隔离 RED 测试：`OWNERSHIP_ACCEPTANCE_BOT_PREFIX = "owner-sync-e2e-"`；普通 Bot 的
Passport/运行时行为必须仍委托原实现，fixture 仅在显式测试启动及 singlebox profile 生效。
缺少 `SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF=1` 或 `SINGLEBOX_COVERAGE=1` 时不安装。

- [x] 实现测试启动包装（只在 tests 路径，正常生产 PYTHONPATH 不包含它）：

```python
# runtime/sitecustomize.py
import os
if (os.environ.get("SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF") == "1"
        and os.environ.get("SINGLEBOX_COVERAGE") == "1"
        and os.environ.get("DEPLOY_PROFILE") == "singlebox"):
    try:
        import agentclaw.community.di as di
        from agentclaw.community.di import container
        from tests.community.acceptance.bot_ownership_sync.fixture_module import OwnershipAcceptanceModule
        original = container.build_injector
        def build_with_fixture(*, profile, extra_modules=None):
            return original(profile=profile,
                extra_modules=[*(extra_modules or ()), OwnershipAcceptanceModule()])
        container.build_injector = build_with_fixture
        di.build_injector = build_with_fixture
    except Exception:
        os.write(2, b"ownership acceptance bootstrap failed\n")
        os._exit(78)
```

基线 `adapters/http/boot.py` 在 finalize 时从 `agentclaw.community.di` 导入 build_injector，
因此必须同时替换 container 与 di 导出；增加“HTTP boot 最终 injector 含 fixture”的测试。
测试启动包装有失败即退出的自检，不捕获 ImportError 后继续启动一个没有 fixture 的服务。

- [x] `OwnershipAcceptanceModule` 通过 Injector 扩展点配置开启 handoff/recovery 和原 TaskQueue worker；
使用同一真实 `TaskQueueService`，不绑定单测 `Queue`。单盒启动总开关仍只在测试启动路径开启，
禁止写到普通 `application-singlebox.yaml` 的 handoff 开关。

fixture 使用委托对象，不能在 module.configure 中预先 resolve 全图，也不能在覆盖后
`injector.get` 同一 Protocol 导致递归：Passport wrapper 的 delegate 使用当前 singlebox 的
`LocalPassportPlugin`；Runtime wrapper provider 注入不同的现有 `BotServiceProtocol` 作为
真实 delegate（与 `ExecutionIdentityModule.runtime_updater` 的既有绑定相同）。
两个 wrapper provider 都按需解析，包装只有前缀匹配时使用下列状态，其他方法和其他 Bot 透传。
实现 fixture_module 时不替换 BotService/OwnerHandoffService/Repository/Sync Plugin。

另一个已核实的夹具前提：`LocalStaffDeptService.get_user_by_work_no` 缺省不返回姓名，
即使 ac_users 已存在，handoff 仍会拒绝 `NEW_OWNER_NOT_FOUND`。必须通过该 local Plugin
既有 `set_override` 测试 seam 提供**已登记测试用户**的目录数据，不关闭目录校验。
仅匹配测试用户前缀且 `UserService.get_user(user_id=work_no, user_type="staff")` 存在时，
返回 `UserIdentityInfo(work_no=work_no, display_name=work_no)`；其他工号返回原 local 结果。
这是验收身份数据准备的显式补充，不作为真实企业目录或在职状态证明；不改生产 local Plugin。
在 validation.md 明确记录此夹具与 spec §11.4 的凭证/运行时替身分别承担什么。

该目录 seam 的最小装配函数（只放 fixture_module.py）：

```python
from agentclaw.community.plugins.local.staff_dept import LocalStaffDeptService
from agentclaw.community.plugin_api.staff_dept import UserIdentityInfo

def directory_with_test_users(users):
    directory = LocalStaffDeptService()
    def get_identity(*, work_no):
        registered = users.get_user(user_id=work_no, user_type="staff")
        if work_no.startswith("owner-sync-e2e-user-") and registered is not None:
            return UserIdentityInfo(work_no=work_no, display_name=work_no)
        return UserIdentityInfo(work_no=work_no)
    directory.set_override("get_user_by_work_no", get_identity)
    return directory
```

TaskQueue worker 配置使用 `dataclasses.replace` 保留既有资源参数，仅启用原 worker；
OwnerHandoffPolicy 在本测试启动覆盖为 enabled/recovery_enabled=true，管理员仍沿用正式配置。
正常单盒未启用 fixture 时必须继续通过原 `test_owner_handoff_closed_live`。显式 fixture 部署
证明的是启用状态，不运行该默认关闭专用故事并谎称其已通过；在普通单盒另跑关闭故事。

| 测试替身方法 | 精确行为 |
| --- | --- |
| owner_handoff_supported | 匹配前缀 true，否则 delegate |
| query_agent_passport | 初始 credential 为 `fixture-old`；返回按稳定 bot/entity key 保存的当前副本 |
| reissue_agent_credentials | 检查 expected credential；记录 operation/execution；切换为 `fixture-selected`，同 operation 不再次生成 |
| query_auth_status | `ISSUED` + 非生产 fixture token；不记录 token |
| retire_agent_credentials | keep credential/operation 匹配才记录已退役；重复调用幂等 |
| hot_update_passport_token_to_device | 匹配前缀返回至少一条 device_count=1 的 binding ack，否则 delegate |

状态受线程锁保护；禁止按“所有 Bot 均返回成功”实现。来源 fixture ID 和受控 key 来自测试，
不使用真实密钥值写入代码。Passport 状态测试可复用 `test_owner_handoff.py` 的断言，但不能
把其只支持固定 `service/A/B` 的实例直接用于随机 live Bot。

- [x] 在 `singlebox/modules/backend.sh` 的 Backend 子进程 PYTHONPATH 组装点加入条件路径：

```bash
if [ "${SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF:-0}" = "1" ]; then
    [ "${SINGLEBOX_COVERAGE:-0}" = "1" ] || return 1
    export PYTHONPATH="${BACKEND_DIR}/tests/community/acceptance/bot_ownership_sync/runtime:${PYTHONPATH:-}"
fi
```

`singlebox_coverage.sh` 启动共享栈的现有 `env` 参数添加
`SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF="${SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF:-1}"`；
调用者可显式传 0 验证普通关闭部署。同一 Backend
进程继续由现有 coverage 命令启动，无第二套 stack/无直接 Core 测试调用拼进 runtime 分子。
运行共享全模块验收，证明该前缀隔离不会影响其他故事。

- [x] live 测试复用现有 BCS `_ensure_human/_ensure_projection_target/_initialize_fixture_ownership`
及 `_owner_principal/_read_manager_ids` helpers；Bootstrap 仅针对本次随机 Bot，结束清理。
`conftest.py` 提供 `projection_case`，在现有 `live_backend` 共享进程上准备：
  1. 随机 `owner-sync-e2e-user-` 前缀 old/new/member User IDs；在 Backend 注册这些测试用户、在 BCS ensure Human。
  2. 前缀隔离 service Bot、稳定 entity=old；通过已有 Backend backfill fixture 取得 bot_pk。
     `_seed_member_managed_bot` 原来创建 personal Bot；在**初始 fixture 装配阶段**经既有
     `_execute_local_sql` 参数化执行 `UPDATE ac_bots SET bot_type='service' WHERE bot_id=:bot_id AND owner_id=:owner_id`，
     再通过 Bot 读接口取得 PK 并断言类型。不能在交接过程中使用 SQL 改 Owner 或伪造 SUCCEEDED。
  3. BCS 正式 ensure + maintenance initialize，仅作初始装配；Backend HTTP 添加 new/member 协作者。
  4. 等待初始 managers 为 new/member，返回含 `backend` HTTP client、`bcs` client、
     `bot_pk/bcs_bot_id/old_owner/new_owner/member` 的 dataclass；teardown 在业务完成后使用有效身份清理。

- [x] 实现有界轮询 helper 和正式 HTTP 故事；不能在测试中直接 `finish()`/`request_ownership_sync()`/PUT ownership：

```python
import time
from urllib.parse import quote
import pytest
from tests.community.acceptance.bot_collaborator.test_manager_sync_bcs_projection import (
    _owner_principal, _read_manager_ids,
)

pytestmark = pytest.mark.acceptance

def wait_until(read, predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.25)
    raise AssertionError("ownership acceptance did not reach the required state")


def test_handoff_reaches_bcs_after_backend_completion(projection_case):
    case = projection_case
    response = case.backend.post("/api/bots/owner-handoffs", json={
        "bot_pk": case.bot_pk, "new_owner_id": case.new_owner, "request_key": "live-owner-1"})
    assert response.status_code == 200
    operation = response.json()["data"]["operation_id"]
    wait_until(lambda: case.backend.get(f"/api/bots/owner-handoffs/{operation}").json(),
               lambda body: body.get("data", {}).get("phase") == "SUCCEEDED")
    path = f"/api/v1/collaboration/bots/{quote(case.bcs_bot_id, safe='')}/ownership"
    response = wait_until(lambda: case.bcs.get(path,
        headers={"X-Avernet-Principal": _owner_principal(case.new_owner)}),
        lambda reply: reply.status_code == 200 and
            reply.json().get("data", {}).get("owner_user_id") == case.new_owner)
    assert response.json()["data"]["owner_user_id"] == case.new_owner
    wait_until(lambda: _read_manager_ids(case.bcs, bcs_bot_id=case.bcs_bot_id,
        owner_id=case.new_owner), lambda ids: ids == {case.member})
    assert case.bcs.get(path,
        headers={"X-Avernet-Principal": _owner_principal(case.old_owner)}).status_code == 403
```

- [x] 增加只对该隔离测试部署生效的网络故障代理故事：拒绝/断连时 Backend 保持 SUCCEEDED，
恢复后受控运维 repair 重新提交，再读回；代理只改变本测试出站目标，不停共享 BCS、
不修改生产权限规则。noop/未知 Human/version-0 另通过既有正式初始化/注册状态构造，
不能以 mock 响应声称 live 证据。
- [x] 先 GREEN fixture 单测，再通过统一 runner 执行目标故事：

```bash
pt tests/community/plugins/local/test_ownership_acceptance_fixture.py -q
bash singlebox/ci/singlebox_coverage.sh --module manager_sync \
  --acceptance-target tests/community/acceptance/bot_ownership_sync \
  --acceptance-target tests/community/acceptance/bot_collaborator
```

该 focused 命令同时包含原 managers 故事并保留 manager_sync 非回归门，
不得降低阈值。Task 11 正式登记 Owner 分母后使用新 module 名运行。
- [x] 提交：`git commit -m "test(backend): verify committed owner handoff against real BCS"`。

### Task 11: 覆盖登记、全量门禁和最终交付

**Files:**
- Modify: `singlebox/ci/singlebox_coverage_modules.yaml`
- Create: `singlebox/ci/tests/test_ownership_sync_coverage_registration.py`
- Modify: `singlebox/ci/tests/test_singlebox_coverage_report.py`（用户追加授权：仅修正 strict expected module list，包含 manager_sync 与 ownership_sync，其他断言不变）
- Modify: `singlebox/modules/backend.sh`; Create: `singlebox/verity/test_backend_coverage_shutdown.sh`（用户追加授权：仅覆盖模式保存真实 SIGTERM shutdown coverage，TDD/verity；不改变通用停止、生产 Plugin 或门槛）
- Create/Update: `apps/backend/specs/2026-11-01-bcs-ownership-sync/validation.md`
- Update: `apps/backend/specs/2026-11-01-bcs-ownership-sync/spec.md`（仅实施状态和证据）

- [x] 写 manifest 登记 RED 测试，要求新 `ownership_sync` module 的完整 Core 路径、
Owner Plugin replace/aclose 分母存在，旧 manager_sync 阈值仍为 54.03；不能将新 Core 放入 pending/exclude。
- [x] 为新模块登记实际分母。Router 无新增公开路由，标记 not_applicable 并解释 handoff 路由属于上游模块；
Plugin 列出两个方法，其中 aclose 由栈 shutdown 覆盖：

```yaml
ownership_sync:
  system: backend
  acceptance_targets:
    - tests/community/acceptance/bot_ownership_sync
  core_paths:
    - src/agentclaw/community/core/bot_management/ownership_sync/
  router_api:
    status: not_applicable
    reason: Ownership sync owns no HTTP routes; owner-handoffs is its upstream business trigger.
    items: []
  plugin_api:
    status: applicable
    reason: Real direct BCS ownership delivery and its lifecycle release.
    items:
      - key: BcsOwnershipPlugin.replace_ownership
        evidence:
          path: src/agentclaw/community/plugins/community/bcs_ownership.py
          symbol: HttpBcsOwnershipPlugin.replace_ownership
      - key: BcsOwnershipPlugin.aclose
        evidence:
          path: src/agentclaw/community/plugins/community/bcs_ownership.py
          symbol: HttpBcsOwnershipPlugin.aclose
  thresholds:
    core_min_percent: 65.90
    plugin_min_percent: 100.0
```

用户批准直接使用 Task 10 首次有效 completed fresh focused Backend report 建立初始值，
不必用人工 100 上界重跑：`owner-enabled-live4/reports/backend-coverage.json`，
SHA-256 `e8e970a9f3f8127598cf4d5f1e721c1119fafb91f13dc826e893fbc206c84931`。
完整 Core 为 87/132，`floor(87 / 132 * 10000) / 100 = 65.90`。
该报告的 17 个验收已通过；独立 BCS/BaaS baseline 失败不使 Backend 模块测量无效，
但不能声称全产品通过。保留来源、计算和新 focused 验证报告；不是降低既有门槛，
禁止填 0 或拿单测覆盖率代替。Plugin 两个真实方法保持 100；
不能排除人工 repair/错误路径的 Core 来抬高百分比。

- [ ] 运行首次实测、记录初始阈值、再次 focused GREEN：

```bash
bash singlebox/ci/singlebox_coverage.sh --module ownership_sync --module manager_sync
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

预期最终两 module 验收和门禁均 PASS；失败按真实错误修复，不弱化现有 Backend/BCS baseline。
当前新 focused 已执行：17 验收 PASS，Core/manager PASS，BCS 169/169 PASS；
初次 Owner Plugin 1/2 的 shutdown coverage 缺口已以用户授权的覆盖模式 sigterm 修正：
新 focused runner exit 0，Owner Core 65.91%、Plugin 2/2、manager 70.03%、BCS 169/169；
独立 artifact verifier 仍因 BaaS 44.50<45 失败，本完整 PASS 项保持未勾选。
最终 instrumentation 的默认 all-module runner 另行重跑，详见 validation.md。

- [x] 执行完整受影响 Python 回归：

```bash
pt tests/community/core/bot_management/ownership_sync \
   tests/community/core/bot_management/test_owner_handoff.py \
   tests/community/core/bot_management/test_owner_handoff_ownership_sync.py \
   tests/community/core/bot_management/test_owner_handoff_spaces.py \
   tests/community/core/bot_management/test_owner_handoff_delivery.py \
   tests/community/core/bot_collaborator \
   tests/community/core/spaces tests/community/core/work_orders \
   tests/community/repository/bot/test_ownership_sync_reader.py \
   tests/community/repository/bot/test_manager_sync_reader.py \
   tests/community/contracts/test_bcs_ownership.py tests/community/contracts/test_bcs_managers.py \
   tests/community/plugins/community/test_bcs_ownership.py \
   tests/community/plugins/local/test_bcs_ownership.py \
   tests/community/plugins/local/test_ownership_acceptance_fixture.py \
   tests/community/di tests/community/config tests/community/architecture -q
```

- [x] 全 Backend CI、变更范围 SAST（实际范围和后续独立检查见 validation.md；不扫描 venv）：

```bash
bash apps/backend/scripts/ci_test.sh --base "$OWNER_SYNC_BASE" --head HEAD
bash singlebox/ci/python_sast_local.sh apps/backend 1 --base "$OWNER_SYNC_BASE" --head HEAD
```

预期现有全模块/changed-line 阈值保持通过。`OWNER_SYNC_BASE` 仅用于本功能定向诊断；
最终 PR/pre-push 仍按其真实目标分支 fetch + merge-base 计算范围，不能拿此固定值替代远端基线。

- [ ] 运行共享全模块门禁，验证测试 fixture 未影响其他故事：

```bash
bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

保留 summary.json/summary.md/dashboard.html、acceptance JUnit、Backend/BCS 报告。
再用独立产物目录验证普通关闭部署，不覆盖默认 all-module 报告：

```bash
SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF=0 bash singlebox/ci/singlebox_coverage.sh \
  --coverage-root singlebox/.dependencies/coverage/ownership-default-off \
  --module manager_sync \
  --acceptance-target tests/community/acceptance/bot_collaborator \
  --acceptance-target tests/community/acceptance/bot_management/test_owner_handoff_closed.py
```

预期原始默认关闭故事仍 PASS，不能改断言为启用、跳过或放宽 HTTP 结果。
禁止以 focused PASS 替代默认 all-module gate，禁止忽略 artifact verifier。

- [x] 审计源文件行数、差异及敏感内容：

```bash
git diff --check "$OWNER_SYNC_BASE" HEAD
python3 - <<'PY'
import subprocess
from pathlib import Path
paths = subprocess.check_output(
    ['git', 'diff', '--name-only', '--diff-filter=AM',
     '9e0a36f1688df42db64596baf73c1b7d73059237', 'HEAD'], text=True).splitlines()
for name in paths:
    p = Path(name)
    if p.suffix in {'.py', '.sh', '.rs', '.ts', '.tsx', '.js'}:
        count = len(p.read_text().splitlines())
        print(count, name)
        assert count <= 1000, f'file exceeds 1000 lines: {name}'
PY
```

超限按责任拆分并更新调用者/测试；禁止扩 allowlist。不要提交日志、DB、fixture token 或运行态目录。

- [x] `validation.md` 记录每条实际命令、exit code、测试数、耗时、产物位置、未运行项和原因；
spec 状态只在真实实现验证后更新。真实外部 AAC/设备验收若未做，明确保留“未验证”，
不能从受控 singlebox fixture 推导生产凭证安全性。
- [x] 使用 requesting-code-review 与 verification-before-completion 技能完成执行阶段审阅（Task 11 双阶段及最终全范围独立审阅均通过；统一门禁失败不因此豁免）；
若实际审阅发现问题则修复、重跑对应 gate。本次按用户授权使用子代理，未合并或推送。
- [x] 提交：`066df82529` — `test(backend): gate ownership sync coverage and record validation`。
推送/PR 仅在用户授权后执行；PR 包含 Problem、Solution、Validation 和 Compatibility and risk。

## 3. Spec → Task 覆盖映射

| Spec 要求 | Task / 关键证据 |
| --- | --- |
| §1–3 完整业务边界、其他真实入口审计 | 7；真实 commit 可见性、失败/取消/恢复/终态重放 |
| §4–5 独立契约、transport-free、Owner 独立读 | 1–4、9；Plugin 消费者命中断言、架构门禁 |
| §6 两类工作合并与资源上界 | 5–6；Event 并发测试、旧 managers regression、两个 Plugin close |
| §7 稳定身份、app-only 签名、响应确认 | 1、3–4；MockTransport claims、noop/drift 精确判定 |
| §8 fresh-read/有限重试/失败隔离 | 4、7；虚拟时钟、取消、业务结果和凭证次数不变 |
| §9 配置与回滚 | 6、9；四态配置、profile/golden、旧版回滚删除键 |
| §10 人工 repair 与诊断 | 4、8–9；有限计数标签、拒绝 keys、无新增写入/公开入口 |
| §11.1–11.3 单测/consumer/架构 | 1–9、11；RED/GREEN、完整 affected suite |
| §11.4 真 BCS、测试替身证据边界 | 10–11；真实共享栈 HTTP/TaskQueue→BCS 读回、无生产假换证 |
| §12 实施证据与未验证项 | 11；validation.md 与实际 artifact，不借用参考 PR 结果 |

## 4. 本计划自审与交接

计划自审检查：spec 覆盖、路径存在性/新增声明、接口/方法名一致性、未完成占位内容、
命令根目录、manager-only 兼容、可选类型的真实禁用语义、验收不能靠直接 Core 调用刷覆盖率。

执行关键风险：共享 dispatcher/lifecycle 会影响旧 managers 路径；测试 DI 安装时机必须在
首个 singleton 解析前；真实单盒 handoff 需要 15 秒阶段调度轮询预算，不能用 <2 秒墙钟断言。

本轮文档静态检查通过：36 个 Python 代码块通过 AST 语法解析，全部 Bash 代码块通过
`bash -n`；修改/引用路径存在或已在前置任务声明新增，本地链接、围栏、占位内容和行尾空白检查通过。
这些检查不证明代码片段已在真实依赖中运行，也不替代实施阶段 RED/GREEN。

原计划编写时仅完成静态检查；当前 Tasks 1–10 已实现并分别通过 spec/quality 审阅。
Task 11 同样由子代理在原 worktree 实施并通过双阶段审阅；controller 完成最终集成验证，未新建分支或工作区。实际门禁及限制见 validation.md。
勾选表示已完成的步骤；要求 PASS 但尚未达到的统一门禁保持未勾选。

最终补充：focused runner 已通过，独立 focused artifact verifier 的 BaaS 分母仍低于全产品门槛，因此相应完整 PASS 步骤不勾选。最终默认全模块运行已执行：58 passed、7 failed；Owner Core 65.91%、Plugin 100% 均通过，但外部端口耗尽和既有 BCS queue_draining 故事使统一门禁未通过。代码完成不等于具备合并条件。
