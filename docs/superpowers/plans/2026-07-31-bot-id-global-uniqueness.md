# Bot 身份全局唯一化 · 解散 `default` 约定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 退役 `bot_id == "default"` 约定，新 bot 一律全局唯一 id；`is_first_bot` 改 `count` 派生；删除保护改"保留≥1"计数规则；发证按租户分流（默认租户首/非首，其他租户一律 `applyFirst`），从根上消除跨租户 `default` 碰撞（#556）。

**Architecture:** 改动全部落在 Avernet `src/backend/src/agentclaw/community/`。不新增 DB 列，tcauthmng wire 不带 tenant，ocb corp `ProdPassportPlugin` 不动。`default` 字符串承载的多种语义分别收敛到 `count` 派生（首/删保护）与 ownership/权限（collaborator/bot_chat）。设计依据见 `docs/superpowers/specs/2026-07-31-bot-id-global-uniqueness-design.md`。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy ORM / pytest（Avernet 社区测试套件，`DEPLOY_PROFILE=test`）。

**路径约定:** 本计划所有路径相对 Avernet 仓 `src/backend/`。执行前 `cd src/backend`。跑测试统一命令前缀：`DEPLOY_PROFILE=test uv run pytest <path> -v`。

---

## 前置 Gate（阻塞 Task 9）

**§7.1 tcauthmng `applyFirst` 语义确认**：Task 9（其他租户一律 `applyFirst`）依赖 tcauthmng 团队确认两点——(1) 同一工号在其他租户反复调 `applyFirst` 不会因"已有护照"报错/触发审批；(2) `applyFirst` 对外部租户始终同步返回 token。**确认前可执行 Task 1–8、10；Task 9 须 gate 通过后再做。** gate 不通过则走 spec §10 回退方案（不在本计划内）。

---

## File Structure

| 文件 | 责任 | 任务 |
|---|---|---|
| `src/agentclaw/community/core/bot_management/services/bot_service.py` | `generate_bot_id` 全局唯一；`is_first_bot`/`_is_first_bot` 改 count；删除保护改 count | T1, T2, T4 |
| `src/agentclaw/community/core/bot_management/create_flow.py` | `is_first_bot` 改 count 派生 | T2 |
| `src/agentclaw/community/core/bot_management/create_flow.py` `_apply_passport` | 发证 RPC 按租户分流 | T9 (gated) |
| `src/agentclaw/community/core/bot_collaborator/interceptor/collaborator.py` | `_resolve_owner_id` 去 `default` 短路 | T5 |
| `src/agentclaw/community/core/bot_chat/service.py` | `_check_bot_access` 统一 `has_bot_access` | T6 |
| `src/agentclaw/community/core/bot_management/services/create_bot_for_others_service.py` | 退役 `_DEFAULT_BOT_ID` | T7 |
| `src/agentclaw/community/adapters/http/resources/router.py` | 同步去 `_DEFAULT_BOT_ID` 引用 | T7 |
| `src/agentclaw/community/adapters/http/bot_management/router.py` | `refresh_bot_passport_token` 跨租户解析 | T3 |
| `src/agentclaw/community/adapters/http/openapi_v1/bots/router.py` | `sync_to_bcn` 重开 | T8 |
| `tests/community/core/bot_management/services/test_bot_service_misc.py` | `generate_bot_id` 新契约测试 | T1 |
| `tests/community/core/bot_management/services/test_bot_service_create_default_protection.py` | 删除保护新规则测试 | T4 |
| `tests/community/core/bot_management/test_create_flow_is_first.py` | 新建：is_first count 派生 + 租户分流 | T2, T9 |
| `tests/community/core/bot_collaborator/test_interceptor.py` | collaborator 短路移除 | T5 |
| `tests/community/core/bot_chat/test_bot_chat_service.py` | bot_chat 短路移除 | T6 |
| `tests/community/adapters/http/bot_management/test_refresh_token_callback.py` | 新建：回调跨租户解析 | T3 |
| `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py` | sync_to_bcn 重开断言 | T8 |

---

## Task 1: `generate_bot_id` 始终全局唯一（改动 #1）

**Files:**
- Modify: `src/agentclaw/community/core/bot_management/services/bot_service.py:243-267`
- Modify: `tests/community/core/bot_management/services/test_bot_service_misc.py:81-106`（`TestGenerateBotId` 整段替换）

- [ ] **Step 1: 写失败测试（替换 `TestGenerateBotId` 整段）**

把 `test_bot_service_misc.py` 中 `class TestGenerateBotId:` 整段（从 `class TestGenerateBotId:` 到下一个 `# ====` 分隔行之前）替换为：

```python
class TestGenerateBotId:
    """generate_bot_id now always returns a globally-unique id (never 'default')."""

    def test_never_returns_default(self):
        repo = MagicMock()
        bot_id = generate_bot_id("user001", repo)
        assert bot_id != "default"

    def test_format_is_date_underscore_random8(self):
        repo = MagicMock()
        bot_id = generate_bot_id("user001", repo)
        parts = bot_id.split("_")
        assert len(parts) == 2
        assert len(parts[0]) == 8 and parts[0].isdigit()  # yyyymmdd
        assert len(parts[1]) == 8  # 8 lowercase/digit chars

    def test_successive_calls_are_unique(self):
        repo = MagicMock()
        ids = {generate_bot_id("user001", repo) for _ in range(50)}
        assert len(ids) == 50  # 36^8 space → collision-improbable

    def test_ignores_owner_default_history(self):
        # 不再依赖 exists_by_owner_and_bot_id;owner 是否已有 default 不影响分配
        repo = MagicMock()
        repo.exists_by_owner_and_bot_id.return_value = True
        bot_id = generate_bot_id("user001", repo)
        assert bot_id != "default"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_bot_service_misc.py::TestGenerateBotId -v`
Expected: `test_never_returns_default` / `test_ignores_owner_default_history` FAIL（当前实现首 bot 返回 `"default"`）。

- [ ] **Step 3: 最小实现**

`bot_service.py:243` 的 `generate_bot_id` 整体替换为：

```python
def generate_bot_id(owner_id: str, bot_repository: BotRepository) -> str:
    """Return a globally-unique bot_id.

    Never returns 'default' — that convention is retired. ``bot_repository`` is
    retained in the signature for call-site compatibility; id allocation no
    longer depends on owner history.
    """
    del bot_repository  # unused; kept for call-site compatibility
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{date_part}_{random_part}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_bot_service_misc.py::TestGenerateBotId -v`
Expected: 4 passed。

- [ ] **Step 5: 扫描并修正断言旧 `default` 契约的测试**

Run: `grep -rn '== "default"\|"default"' tests/community --include='*.py' | grep -i 'bot_id\|generate_bot_id\|is_first'`
逐个人工判断：凡断言"首 bot 创建后 bot_id=='default'"的测试，改为断言 `bot_id != "default"` 且格式 `^\d{8}_[a-z0-9]{8}$`。重点关注 `test_create_flow_pending.py`、`e2e/test_bot_creation_flow.py`、`test_bot_service_create_default_protection.py`、`api/bot_management/test_router.py` 中创建后取 `bot_id` 的断言。**不要改断言"调用方传入 bot_id='default' 做某事"的语义测试**（如 `test_bot_service_create_default_protection` 的删除保护测试，T4 会改）。

每改一个文件跑一次该文件确认绿：
Run: `DEPLOY_PROFILE=test uv run pytest <改的文件> -v`

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/bot_management/services/bot_service.py tests/community/core/bot_management/services/test_bot_service_misc.py <其它改动的测试>
git commit -m "feat(bot): generate_bot_id always returns globally-unique id (retire 'default')"
```

---

## Task 2: `is_first_bot` 改 `count` 派生（改动 #2、#10）

**Files:**
- Modify: `src/agentclaw/community/core/bot_management/services/bot_service.py:795-798`（`_is_first_bot`）+ 新增 public `is_first_bot`
- Modify: `src/agentclaw/community/core/bot_management/create_flow.py:329`
- Create: `tests/community/core/bot_management/test_create_flow_is_first.py`

- [ ] **Step 1: 写失败测试（新建文件）**

`tests/community/core/bot_management/test_create_flow_is_first.py`：

```python
"""is_first_bot 现按 owner 当前 bot 数==0 派生,不再依赖 bot_id=='default'。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService


def _svc_with_count(count: int) -> BotService:
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._repository.count_by_owner.return_value = count
    return svc


class TestIsFirstBot:
    def test_zero_bots_is_first(self):
        assert _svc_with_count(0).is_first_bot("user001") is True

    def test_one_bot_not_first(self):
        assert _svc_with_count(1).is_first_bot("user001") is False

    def test_many_bots_not_first(self):
        assert _svc_with_count(5).is_first_bot("user001") is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/test_create_flow_is_first.py -v`
Expected: FAIL（`BotService` 无 `is_first_bot` 属性 / `_is_first_bot` 仍走 `exists_by_owner_and_bot_id`）。

- [ ] **Step 3: 实现**

`bot_service.py:795` 的 `_is_first_bot` 改为：

```python
    def _is_first_bot(self, user_id: str) -> bool:
        """First bot iff the owner has zero bots (current tenant+env)."""
        return self._repository.count_by_owner(user_id) == 0

    def is_first_bot(self, user_id: str) -> bool:
        """Public alias (used by create_flow) for :meth:`_is_first_bot`."""
        return self._is_first_bot(user_id)
```

`create_flow.py:329` 的 `is_first_bot = bot_id == "default"` 改为：

```python
    is_first_bot = bot_service.is_first_bot(user_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/test_create_flow_is_first.py tests/community/core/bot_management/test_create_flow_pending.py -v`
Expected: passed（`test_create_flow_pending.py` 内部路径行为等价：首 bot count==0 → is_first=True）。

- [ ] **Step 5: 跑 bot_management 套件确认无回归**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management tests/community/api/bot_management -v`
Expected: 全绿。若 `test_create_flow_pending.py` 因 `is_first_bot` 来源变化断言失败，更新断言为基于 count 的预期。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/bot_management/services/bot_service.py src/agentclaw/community/core/bot_management/create_flow.py tests/community/core/bot_management/test_create_flow_is_first.py
git commit -m "refactor(bot): derive is_first_bot from owner bot count (not bot_id=='default')"
```

---

## Task 3: refresh-token 回调跨租户解析（改动 #8）

**Files:**
- Modify: `src/agentclaw/community/core/bot_management/services/bot_service.py:4564`（`hot_update_passport_token_to_device`，按 `(bot_id, user_id)` 跨租户查）
- Modify: `src/agentclaw/community/core/bot_management/repository/protocol.py` 的 `get_by_id_and_owner` 签名（透传 `execution_options`）+ `plugins/bot_repository.py:146` 实现
- Create: `tests/community/adapters/http/bot_management/test_refresh_token_callback.py`

> 说明：回调 `POST /api/bots/passport/refresh-token`（`router.py:1196`）走 `/api` 前缀，`AvernetTenantMiddleware` 置默认租户 `teamclaw`，外部租户 bot 被 guard 挡掉。`(bot_id, owner_workno)` 全局唯一，故跨租户直查安全。

- [ ] **Step 1: 写失败测试**

`tests/community/adapters/http/bot_management/test_refresh_token_callback.py`：

```python
"""refresh-token 回调按 (bot_id, owner_workno) 跨租户解析,不被 tenant guard 挡。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
)


def _svc_with_bot(bot: dict | None) -> BotService:
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._repository.get_by_id_and_owner.return_value = bot
    svc._passport_plugin = MagicMock()
    return svc


def test_callback_passes_cross_tenant_option():
    # 关键契约:hot_update_passport_token_to_device 的 bot 查询带 skip_avernet_tenant_guard
    # (回调走 /api→默认租户,外部租户 bot 需跨租户直查)。下游链路可能需更多 mock,
    # 这里只断言 lookup 调用契约,允许下游抛错。
    bot = {"bot_id": "20260731_abcd1234", "owner_id": "user001", "bot_type": "personal",
           "binding_id": None, "ext": {}}
    svc = _svc_with_bot(bot)
    try:
        svc.hot_update_passport_token_to_device(
            bot_id="20260731_abcd1234", user_id="user001", token="tok_xyz"
        )
    except Exception:
        pass  # 下游 device 热更新链路可能需更多 mock;本测试只锁定 lookup 契约
    call = svc._repository.get_by_id_and_owner.call_args
    assert call.kwargs["execution_options"]["skip_avernet_tenant_guard"] is True


def test_callback_missing_bot_raises_not_found():
    svc = _svc_with_bot(None)
    with pytest.raises(BotNotFoundError):
        svc.hot_update_passport_token_to_device(
            bot_id="missing", user_id="user001", token="tok"
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/bot_management/test_refresh_token_callback.py -v`
Expected: FAIL（当前 `get_by_id_and_owner` 不透传 `execution_options` / 调用未带 `skip_avernet_tenant_guard`）。

- [ ] **Step 3: 实现**

`plugins/bot_repository.py:146` 的 `get_by_id_and_owner` 加 `execution_options` 透传到 `orm_session()` 查询。原方法签名加可选参数：

```python
    def get_by_id_and_owner(
        self, bot_id: str, owner_id: str,
        *, execution_options: dict | None = None,
    ) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            q = (
                db.query(self.Model)
                .filter(
                    self.Model.is_delete == 0,
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self._env(),
                )
            )
            if execution_options:
                q = q.execution_options(**execution_options)
            bot = q.first()
            return self._to_dict(bot) if bot else None
```

`protocol.py` 的 `get_by_id_and_owner` Protocol 签名同步加 `*, execution_options: dict | None = None`。

`bot_service.py:4564` 的 `hot_update_passport_token_to_device` 内 `get_by_id_and_owner` 调用改为：

```python
        bot = self._repository.get_by_id_and_owner(
            bot_id, user_id,
            execution_options={"skip_avernet_tenant_guard": True},
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/bot_management/test_refresh_token_callback.py -v`
Expected: 2 passed。

- [ ] **Step 5: 跑 router 套件确认无回归**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/api/bot_management/test_router.py -v`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/bot_management/services/bot_service.py src/agentclaw/community/core/bot_management/repository/protocol.py src/agentclaw/community/plugins/bot_repository.py tests/community/adapters/http/bot_management/test_refresh_token_callback.py
git commit -m "fix(passport): refresh-token callback resolves bot cross-tenant (bot_id+workno unique)"
```

---

## Task 4: 删除保护改"保留≥1"计数规则（改动 #4）

**Files:**
- Modify: `src/agentclaw/community/core/bot_management/services/bot_service.py:3159-3160`
- Modify: `tests/community/core/bot_management/services/test_bot_service_misc.py`（delete_bot 测试）
- Modify: `tests/community/core/bot_management/services/test_bot_service_create_default_protection.py`

- [ ] **Step 1: 写失败测试**

在 `test_bot_service_misc.py` 的 `delete_bot` 相关测试类中新增（若该类不存在则新建 `class TestDeleteBotProtection:`）：

```python
class TestDeleteBotProtection:
    """delete_bot 拒绝删除 owner 最后一只 bot (保留≥1),不再按 bot_id=='default'。"""

    def _svc(self, count: int, bot: dict | None):
        svc = _make_service()
        svc._repository.count_by_owner.return_value = count
        svc._repository.get_by_id_and_owner.return_value = bot or _make_bot()
        svc._repository.soft_delete_by_owner = MagicMock()
        return svc

    def test_only_bot_rejected(self):
        svc = self._svc(1, _make_bot(bot_id="20260731_abcd1234"))
        with pytest.raises(BotServiceError):
            svc.delete_bot(bot_id="20260731_abcd1234", user_id="user001")

    def test_last_of_many_rejected(self):
        svc = self._svc(1, _make_bot())  # 删后归零
        with pytest.raises(BotServiceError):
            svc.delete_bot(bot_id="x", user_id="user001")

    def test_non_last_allowed(self):
        svc = self._svc(3, _make_bot(binding_id=None))  # binding_id=None 避开 device 释放分支
        svc.delete_bot(bot_id="x", user_id="user001")  # 不抛
        svc._repository.soft_delete_by_owner.assert_called_once()

    def test_default_bot_deletable_when_not_last(self):
        # 行为变化:default 字面值不再受特殊保护;只要删完还剩≥1 即可
        svc = self._svc(2, _make_bot(bot_id="default", binding_id=None))
        svc.delete_bot(bot_id="default", user_id="user001")
        svc._repository.soft_delete_by_owner.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_bot_service_misc.py::TestDeleteBotProtection -v`
Expected: `test_only_bot_rejected` / `test_last_of_many_rejected` FAIL（当前只拦 `bot_id=="default"`，count 不判）。

- [ ] **Step 3: 实现**

`bot_service.py:3159-3160` 的：

```python
            if bot_id == "default":
                raise BotOperationNotAllowedError("default bot 不允许删除")
```

替换为：

```python
            # 至少保留一个 Bot:删掉这只后归零则拒。
            if self._repository.count_by_owner(user_id) <= 1:
                raise BotOperationNotAllowedError("至少保留一个 Bot，不能全部删除")
```

- [ ] **Step 4: 更新 `test_bot_service_create_default_protection.py`**

该文件原测"device 分配失败回滚时 `bot_id=='default'` 不 soft_delete"——其 `bot_id="default"` 现仅作为入参字面值，回滚逻辑不变。检查其中是否断言"删除 default 抛错"，若有改为"count==1 时抛错"。运行确认：

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_bot_service_create_default_protection.py -v`
Expected: 全绿（若断言旧规则则按上更新）。

- [ ] **Step 5: 跑 delete 相关套件确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_bot_service_misc.py::TestDeleteBotProtection tests/community/api/bot_management/test_router.py -v`
Expected: 全绿。

> 注意：`delete_bot` 现在会调 `count_by_owner`。`test_bot_service_misc.py` / `test_router.py`
> 里其它既有 delete 相关测试若用 `_make_service()` 却没设 `_repository.count_by_owner`
> 返回值（MagicMock 默认返回非 int → `<= 1` 报 TypeError），需补
> `svc._repository.count_by_owner.return_value = <N>`（N>1 表示非最后一只）。逐文件跑、
> 见到 TypeError 即补。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/bot_management/services/bot_service.py tests/community/core/bot_management/services/test_bot_service_misc.py tests/community/core/bot_management/services/test_bot_service_create_default_protection.py
git commit -m "refactor(bot): delete protection = keep >=1 bot (count-based, not default-string)"
```

---

## Task 5: collaborator `_resolve_owner_id` 去 `default` 短路（改动 #5）

**Files:**
- Modify: `src/agentclaw/community/core/bot_collaborator/interceptor/collaborator.py:436`
- Modify: `tests/community/core/bot_collaborator/test_interceptor.py`

- [ ] **Step 1: 写失败测试**

在 `test_interceptor.py` 中找到 `_resolve_owner_id` 的测试，新增/改为：

```python
class TestResolveOwnerIdNoDefaultShortcut:
    def test_missing_bot_id_returns_user(self, ...):
        # bot_id 缺失 → user_id(语义=我的 bot),保持
        ...
    def test_historical_default_bot_id_resolves_via_repo(self, ...):
        # bot_id="default"(历史值)不再短路;走 repo.get_by_id → bot.owner_id
        repo = MagicMock()
        repo.get_by_id.return_value = {"owner_id": "ownerA"}
        ctx = SimpleNamespace(injector=MagicMock())
        ctx.injector.get.return_value = repo
        owner = interceptor._resolve_owner_id(ctx, "default", "user001")
        assert owner == "ownerA"
        repo.get_by_id.assert_called_once_with("default")
```

（按文件现有 fixture 风格补全参数；核心断言：`bot_id=="default"` 走 `get_by_id`，不再直接返回 `user_id`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_collaborator/test_interceptor.py -v`
Expected: `test_historical_default_bot_id_resolves_via_repo` FAIL（当前 `default` 短路返回 user_id）。

- [ ] **Step 3: 实现**

`collaborator.py:436` 的：

```python
        if not bot_id or bot_id == "default":
            return user_id
```

改为：

```python
        if not bot_id:
            return user_id
        # 历史的 bot_id=="default" 不再短路;统一走 repo 解析 owner
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_collaborator/ -v`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_collaborator/interceptor/collaborator.py tests/community/core/bot_collaborator/test_interceptor.py
git commit -m "refactor(collaborator): resolve owner via repo for all bot_ids (drop default shortcut)"
```

---

## Task 6: `bot_chat._check_bot_access` 统一 `has_bot_access`（改动 #6）

**Files:**
- Modify: `src/agentclaw/community/core/bot_chat/service.py:359-364`
- Modify: `tests/community/core/bot_chat/test_bot_chat_service.py`

- [ ] **Step 1: 写失败测试**

在 `test_bot_chat_service.py` 中找 `_check_bot_access` 测试，新增：

```python
class TestCheckBotAccessNoDefaultShortcut:
    def test_historical_default_still_goes_through_has_bot_access(self):
        svc = _make_service()  # 按文件现有 helper
        svc._db_repo.has_bot_access.return_value = True
        assert svc._check_bot_access("user001", "default") is True
        svc._db_repo.has_bot_access.assert_called_once_with("user001", "default")

    def test_user_default_form_also_uses_has_bot_access(self):
        svc = _make_service()
        svc._db_repo.has_bot_access.return_value = False
        assert svc._check_bot_access("user001", "user001_default") is False
        svc._db_repo.has_bot_access.assert_called_once_with("user001", "user001_default")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_chat/test_bot_chat_service.py -v`
Expected: FAIL（`default` / `{user_id}_default` 现在短路返回 True，不走 `has_bot_access`）。

- [ ] **Step 3: 实现**

`service.py:359-364` 的 `_check_bot_access` 改为：

```python
    def _check_bot_access(self, user_id: str, bot_id: str) -> bool:
        """Check access: owner (ac_bots) or collaborator (ac_bot_collaborator)."""
        return self._db_repo.has_bot_access(user_id, bot_id)
```

（删除 `if bot_id == "default" or bot_id == f"{user_id}_default": return True` 短路。）

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_chat/ -v`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_chat/service.py tests/community/core/bot_chat/test_bot_chat_service.py
git commit -m "refactor(bot_chat): unified access check via has_bot_access (drop default shortcut)"
```

---

## Task 7: 退役 `_DEFAULT_BOT_ID`（改动 #7）

**Files:**
- Modify: `src/agentclaw/community/core/bot_management/services/create_bot_for_others_service.py`（`_DEFAULT_BOT_ID` 常量 + ~20 处引用）
- Modify: `src/agentclaw/community/adapters/http/resources/router.py`（`_DEFAULT_BOT_ID` 引用）
- Modify: 对应测试

- [ ] **Step 1: 摸引用面**

Run: `grep -rn "_DEFAULT_BOT_ID\|\"default\"\|'default'" src/agentclaw/community/core/bot_management/services/create_bot_for_others_service.py src/agentclaw/community/adapters/http/resources/router.py`
记录全部命中行。

- [ ] **Step 2: 写失败测试**

在 `create_bot_for_others` 的测试文件中（`tests/community/core/bot_management/services/`，若无则新建 `test_create_bot_for_others_service.py`）新增：

```python
def test_target_user_gets_globally_unique_bot_id(monkeypatch):
    # 为他人开 bot 不再使用字面 "default";走 generate_bot_id
    from agentclaw.community.core.bot_management.services import create_bot_for_others_service as mod
    monkeypatch.setattr(mod, "generate_bot_id", lambda owner, repo: "20260731_zzzz9999")
    svc = _make_service()  # 按文件 helper
    svc.create_default_bot_for(target_user_id="user002", ...)  # 主入口名按现状
    created = svc._repository.create.call_args.kwargs
    assert created["bot_id"] == "20260731_zzzz9999"
```

（入口方法名与参数按文件现状填全；核心断言：bot_id 来自 `generate_bot_id`，非 `"default"`。）

- [ ] **Step 3: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_create_bot_for_others_service.py -v`
Expected: FAIL（当前用 `_DEFAULT_BOT_ID="default"`）。

- [ ] **Step 4: 实现**

在 `create_bot_for_others_service.py`：
- 删除 `_DEFAULT_BOT_ID = "default"` 常量。
- import `generate_bot_id` from `bot_service`。
- 所有原 `_DEFAULT_BOT_ID` 使用处改为 `generate_bot_id(target_user_id, self._repository)`（首次创建时；若服务语义是"已存在则修复",保留查重逻辑，仅创建分支用 `generate_bot_id`）。
- 服务顶部 docstring 把"为他人开 default bot"改为"为目标用户创建一只 bot（全局唯一 id）"。

在 `adapters/http/resources/router.py`：同步把对 `_DEFAULT_BOT_ID` 的引用改为调用 `generate_bot_id` 或直接透传（按上下文）。

- [ ] **Step 5: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/services/test_create_bot_for_others_service.py tests/community/adapters/http/resources -v`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/bot_management/services/create_bot_for_others_service.py src/agentclaw/community/adapters/http/resources/router.py tests/community/core/bot_management/services/test_create_bot_for_others_service.py
git commit -m "refactor(bot): retire _DEFAULT_BOT_ID in create_bot_for_others (use generate_bot_id)"
```

---

## Task 8: openapi update 重开 `sync_to_bcn`（改动 #9）

**Files:**
- Modify: `src/agentclaw/community/adapters/http/openapi_v1/bots/router.py:401`
- Modify: `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py`

- [ ] **Step 1: 写失败测试**

在 `test_bots_endpoints.py` 的 update 端点测试中新增/改：

```python
def test_openapi_update_re_enables_bcn_sync(...):
    # openapi PUT /bots/{id} 不再带 sync_to_bcn=False
    # 断言 bot_service.update_bot 收到的 sync_to_bcn=True(默认)
    ...
    call = bot_service.update_bot.call_args
    assert call.kwargs.get("sync_to_bcn", True) is True
```

（按文件现有 fixture 风格补全；核心：openapi update 透传 `sync_to_bcn` 不再被强制 False。）

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py -v`
Expected: 该断言 FAIL（当前 `sync_to_bcn=False`）。

- [ ] **Step 3: 实现**

`openapi_v1/bots/router.py:401` 删除 `sync_to_bcn=False,` 参数透传（让 `update_bot` 默认 `True` 生效）。

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py -v`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/adapters/http/openapi_v1/bots/router.py tests/community/adapters/http/openapi_v1/test_bots_endpoints.py
git commit -m "fix(openapi): re-enable BCN sync on public bot update (bot_id+workno globally unique)"
```

---

## Task 9（GATED）: 发证 RPC 按租户分流（改动 #3）

> **前置：§7.1 gate 已通过**（tcauthmng 确认 `applyFirst` 对外部租户始终同步发证、反复调用不报错）。未通过则跳过本任务、走 spec §10 回退。

**Files:**
- Modify: `src/agentclaw/community/core/bot_management/create_flow.py:187-215`（`_apply_passport`）
- Modify: `tests/community/core/bot_management/test_create_flow_is_first.py`

- [ ] **Step 1: 写失败测试（在 T2 文件追加）**

```python
class TestApplyRpcTenantBranch:
    @pytest.mark.parametrize("tenant,is_first,expect_first_rpc", [
        ("teamclaw", True, True),    # 默认租户首 bot → applyFirst
        ("teamclaw", False, False),  # 默认租户非首 → applyAgent
        ("tenantB", True, True),     # 其他租户 → 一律 applyFirst
        ("tenantB", False, True),    # 其他租户非首 → 仍 applyFirst
    ])
    def test_rpc_selection(self, tenant, is_first, expect_first_rpc, monkeypatch):
        from agentclaw.community.core.bot_management import create_flow
        from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
        monkeypatch.setattr(create_flow, "get_current_avernet_tenant", lambda: tenant)
        plugin = MagicMock()
        plugin.apply_first_agent_passport.return_value = {"token": "t"}
        plugin.apply_agent_passport.return_value = {"token": "t"}
        create_flow._apply_passport(
            plugin, bot_id="20260731_abcd1234", user_id="user001",
            bot_name=None, spec=MagicMock(), mcp_codes=[], cli_items=[],
            is_first_bot=is_first,
        )
        if expect_first_rpc:
            plugin.apply_first_agent_passport.assert_called_once()
            plugin.apply_agent_passport.assert_not_called()
        else:
            plugin.apply_agent_passport.assert_called_once()
            plugin.apply_first_agent_passport.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/test_create_flow_is_first.py::TestApplyRpcTenantBranch -v`
Expected: 默认租户非首 / 其他租户分支 FAIL（当前只按 `is_first_bot` 选）。

- [ ] **Step 3: 实现**

`create_flow.py` 顶部 import：

```python
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    get_current_avernet_tenant,
)
```

`_apply_passport`（187-215）内，原：

```python
    apply = (
        passport_plugin.apply_first_agent_passport
        if is_first_bot
        else passport_plugin.apply_agent_passport
    )
```

改为：

```python
    # 默认租户:首 bot → applyFirst,非首 → applyAgent
    # 其他租户(openapi):一律 applyFirst(跳过审批),不依赖 is_first_bot
    if get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT and not is_first_bot:
        apply = passport_plugin.apply_agent_passport
    else:
        apply = passport_plugin.apply_first_agent_passport
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_management/test_create_flow_is_first.py -v`
Expected: 全绿。

- [ ] **Step 5: 跑 openapi + 内部 router 套件确认无回归**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py tests/community/api/bot_management/test_router.py tests/community/services/test_bot_passport.py -v`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/core/bot_management/create_flow.py tests/community/core/bot_management/test_create_flow_is_first.py
git commit -m "feat(passport): tenant-aware apply RPC — default tenant first/non-first, others always applyFirst"
```

---

## Task 10: 架构守卫 + 全套件绿

**Files:**
- 可能 Modify: `src/agentclaw/community/core/bot_management/README.md`（Context Boundary 声明新依赖）
- 可能 Modify: `tests/community/architecture/test_http_adapter_layer_is_http_only.py`

- [ ] **Step 1: 声明新 context-boundary 依赖**

Run: `grep -n "avernet_tenant\|## Context Boundary" src/agentclaw/community/core/bot_management/README.md`
若 `create_flow` 新引入 `utils.avernet_tenant` 未在 `bot_management` 的 Context Boundary 声明，补一行声明（照该 README 既有格式）。

- [ ] **Step 2: 跑架构守卫**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/architecture/ -v`
Expected: 全绿。若 `test_http_adapter_layer_is_http_only.py` 因新非端点 helper 报错，按 `#494` 既有做法将新 helper stem 加入 allowlist。

- [ ] **Step 3: 跑社区全套件**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community -v`
Expected: 全绿（含 openapi_v1 46、bot_management router、unified repo、architecture）。

- [ ] **Step 4: 桌面旁支评估（§7.3）**

Run: `grep -n "apply_first_agent_passport\|apply_agent_passport\|is_first_bot" src/agentclaw/community/core/desktop_bot/services/desktop_bot_service.py`
判断桌面链路是否涉及外部租户。若不涉及（大概率）→ 记录结论、不改。若涉及 → 单独补一个与 T9 同形状的分支任务（超出本计划，另起 spec）。

- [ ] **Step 5: Commit**

```bash
git add src/agentclaw/community/core/bot_management/README.md tests/community/architecture/test_http_adapter_layer_is_http_only.py
git commit -m "chore(arch): declare avernet_tenant dependency in bot_management context boundary"
```

---

## 完成判定（对应 spec §0/§3）

- [ ] 新 bot 一律全局唯一 `bot_id`，无 `"default"` 产生（`generate_bot_id` + `_DEFAULT_BOT_ID` 退役）
- [ ] `is_first_bot` 由 `count_by_owner==0` 派生，不依赖字符串
- [ ] 删除保护 = `count<=1` 拒，不再按 `default`
- [ ] collaborator / bot_chat `default` 短路移除，走 ownership/权限
- [ ] refresh-token 回调跨租户解析（`skip_avernet_tenant_guard`）
- [ ] openapi update `sync_to_bcn` 重开
- [ ] 发证按租户分流（默认租户首/非首，其他一律 `applyFirst`）—— **Task 9，gate 通过后**
- [ ] ocb corp `ProdPassportPlugin` / DTO 全程不动
- [ ] `tests/community` 全绿、`tests/community/architecture/` 全绿