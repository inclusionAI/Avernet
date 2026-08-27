# Owner 级定时任务聚合列表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增公开面端点 `GET /openapi/v1/bots/routines/all`，返回认证用户名下所有 bot（含协作参与）的全部运行态定时任务，每条带 `bot_name`。

**Architecture:** 服务层 `list_all_crons`（`core/cron/services/cron_relay.py`）已完整具备 owner 级 fan-out、bot_name 装饰、按 (bot, stage, task) 去重、部分失败容忍——本计划只是公开面薄适配：扩展 `Routine` schema（纯加法可选字段）、新增一个不含 bot 寻址的小 router、在 admission/authorization 两张表声明、挂进 `_SUBGROUPS`（先于 legacy shim，避免 `/routines/{routine_id}` 吞掉字面量 `all`）、重新生成网关契约。

**Tech Stack:** FastAPI + Pydantic（openapi_v1 公开面）、pytest（handler 直调模式）、`scripts/dump_openapi.py`（契约再生成）。

**Spec:** `docs/superpowers/specs/2026-08-27-owner-routines-aggregate-openapi-design.md`

**设计修正（相对 spec §5/§7 的两处，实现以本计划为准）：**
1. **准入模式 = `USER_GATED`（非 spec 草案里的 GRANT_FILTERED）**：GRANT_FILTERED 承诺"结果按应用授权过滤"，但 `list_all_crons` 不按授权过滤，声明它即是撒谎。对齐最近似兄弟路由 `GET /bots/ceiling` 的 USER_GATED 模式：app-only 调用者须持有该用户至少一个实时 delegation（handler 内检查 `caller.granted_bot_ids()`），无 delegation 时以 `BotNotFoundError`（404 信封）拒答。人类调用者不受影响。gateway 侧无需任何改动（宽泛 `/openapi/v1/bots/**` 规则已覆盖，REFUSED 才需要双写 route_security）。
2. **三个新字段统一由 `_map_routine` 映射**（不做按路由的条件映射）：服务层 `_decorate_runtime_item` 对所有出参都装饰 `bot_id/bot_name/owner_id`（服务型 bot 另加 `runtime_stage`），per-bot 路由免费获得 bot_name 回填，无需分支。

**测试命令目录约定：** 所有 pytest 命令在 `/Users/rongzhi/PycharmProjects/Avernet/src/backend` 下执行，用 `.venv/bin/python -m pytest`（uv venv；不存在则先 `uv sync --frozen`）。

---

### Task 1: `Routine` schema 扩展 + `_map_routine` 映射三个新字段

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/schemas.py`（`Routine` 模型，约 52-97 行）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/router.py`（`_map_routine`，约 73-94 行）
- Test: `src/backend/tests/community/adapters/http/openapi_v1/routines/test_routines_handlers.py`（在 `_map_routine` 测试节追加）

- [ ] **Step 1: 写失败测试**（追加到 `test_routines_handlers.py` 的 `_map_routine` 测试节之后）

```python
def test_map_routine_carries_bot_metadata():
    """The aggregate listing decorates every adapter item with bot/owner/stage.

    ``cron_runtime_targets.py`` decorates ``bot_id``/``bot_name``/``owner_id``
    on every item and ``runtime_stage`` on a service bot's — the owner-level
    listing needs all of them mapped, and the per-bot route answers
    ``bot_name`` from the same dict for free.
    """
    adapter = _adapter_dict(
        bot_name="TicketBot",
        owner_id="209800",
        runtime_stage="online",
    )
    r = _map_routine(adapter)
    assert r.bot_name == "TicketBot"
    assert r.owner_id == "209800"
    assert r.runtime_stage == "online"


def test_map_routine_bot_metadata_defaults_to_none():
    """Absent or empty decoration maps to None, never to an empty string.

    The three metadata fields are optional additions; a producer that reports
    none (e.g. a draft-stage item has no ``runtime_stage``) must surface as
    null, which is what the schema documents.
    """
    r = _map_routine({})
    assert r.bot_name is None
    assert r.owner_id is None
    assert r.runtime_stage is None
    r_blank = _map_routine({"bot_name": "", "owner_id": "", "runtime_stage": ""})
    assert r_blank.bot_name is None
    assert r_blank.owner_id is None
    assert r_blank.runtime_stage is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/community/adapters/http/openapi_v1/routines/test_routines_handlers.py -v -k "carries_bot_metadata or bot_metadata_defaults"`
Expected: FAIL —— `Routine` 无 `bot_name` 属性（`AttributeError` / pydantic 校验错）

- [ ] **Step 3: 扩展 `Routine` schema**

在 `schemas.py` `Routine` 的 `bot_id` 字段后插入（保持字段顺序：身份字段聚在一起）：

```python
    bot_name: str | None = Field(
        default=None,
        description="Name of the bot the routine belongs to; null when the "
        "engine reports none.",
    )
    owner_id: str | None = Field(
        default=None,
        description="The bot owner's staff id; null when the engine reports "
        "none.",
    )
    runtime_stage: str | None = Field(
        default=None,
        description="Which runtime holds this definition — 'draft', 'verify' "
        "or 'online' for a service bot, null otherwise. The same definition "
        "can exist in several runtimes; on a cross-bot list treat the pair "
        "(routine_id, runtime_stage) as the distinct row.",
    )
```

并在 `model_config` 的 `example` 里补三行（`"bot_id": ...` 之后）：

```python
                "bot_name": "TicketBot",
                "owner_id": "209800",
                "runtime_stage": "draft",
```

- [ ] **Step 4: 扩展 `_map_routine` 映射**

`router.py` `_map_routine` 的 `Routine(...)` 构造里，`bot_id=...` 行后追加：

```python
        bot_name=str(data.get("bot_name", "")) or None,
        owner_id=str(data.get("owner_id", "")) or None,
        runtime_stage=str(data.get("runtime_stage", "")) or None,
```

（`"" → None` 归一化：字段 schema 是 `str | None`，描述写的是 "null when the engine reports none"。`bot_id` 保持原有 `""` 约定不动——它是 required，语义是 "may be empty on the detail read"。）

- [ ] **Step 5: 跑全文件测试确认通过、无回归**

Run: `.venv/bin/python -m pytest tests/community/adapters/http/openapi_v1/routines/test_routines_handlers.py -v`
Expected: PASS（全绿；既有测试的 `_adapter_dict` 基字典无新键 → 映射为 None，不影响旧断言）

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/schemas.py \
        src/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/router.py \
        src/backend/tests/community/adapters/http/openapi_v1/routines/test_routines_handlers.py
git commit -m "feat(backend): extend Routine schema with bot_name/owner_id/runtime_stage"
```

---

### Task 2: owner 聚合路由 `owner_router.py`（handler TDD）

**Files:**
- Create: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/owner_router.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py`（NoCheck 表，约 378-381 行 "Operations that address no bot" 节）
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py`（ADMISSION 表，约 250 行 routines 节）
- Test: `src/backend/tests/community/adapters/http/openapi_v1/routines/test_owner_routines.py`（新建）

- [ ] **Step 1: 写失败测试**（新文件 `test_owner_routines.py`，handler 直调模式与 `test_routines_handlers.py` 一致）

```python
"""openapi_v1 owner-level routine listing handler unit tests."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_OK,
    Envelope,
    Page,
    PageParams,
)
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.routines.owner_router import (
    list_owner_routines,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)


def _human(user_id: str) -> ActingCaller:
    """A caller with a person on the wire — no grant governs the request."""
    return ActingCaller(user_id=user_id, app_id=None)


def _app_with_delegations(user_id: str, *bot_ids: str) -> ActingCaller:
    """An application caller holding one live delegation per named bot.

    ``granted_bot_ids()`` reads the grant protocol; a stub returning the
    records inline keeps the handler test off the database.
    """

    class _Grants:
        def list_for_app(self, *, app_id, user_id):
            return [
                SimpleNamespace(bot_id=b, owner_id=user_id) for b in bot_ids
            ]

    return ActingCaller(
        user_id=user_id, app_id=7, grants=_Grants()
    )


def _request_without_trace() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


class _StubCronService:
    """Minimal stub satisfying the CronRelayServiceProtocol list_all_crons seam."""

    def __init__(self, payload):
        self._payload = payload
        self.last_call_kwargs: dict = {}

    async def list_all_crons(self, *args, **kwargs):
        self.last_call_kwargs = dict(kwargs)
        return {"success": True, "data": self._payload, "total": len(self._payload)}


def _adapter_dict(**overrides):
    base = {
        "id": "t1",
        "bot_id": "bot-x",
        "bot_name": "Bot X",
        "owner_id": "u1",
        "runtime_stage": "online",
        "name": "cron1",
        "enabled": True,
        "schedule": {"expr": "0 9 * * *", "tz": "Asia/Shanghai"},
        "payload": {"message": "echo hi"},
        "created_at_ms": 1722165600000,
        "updated_at_ms": 1722165600000,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_returns_envelope_page_with_bot_metadata():
    service = _StubCronService([_adapter_dict()])

    env = await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        caller=_human("u1"),
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert isinstance(env.data, Page)
    assert env.data.total == 1
    item = env.data.items[0]
    assert item.bot_id == "bot-x"
    assert item.bot_name == "Bot X"
    assert item.owner_id == "u1"
    assert item.runtime_stage == "online"


@pytest.mark.asyncio
async def test_asks_for_the_user_whole_fleet_all_stages():
    """The aggregate names no bot and no runtime stage.

    ``bot_id=None`` is the service's all-bots mode and ``runtime_stage=None``
    aggregates draft, verify and online — the opposite of the per-bot draft
    workspace the rest of the routines group serves.
    """
    service = _StubCronService([])

    await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        caller=_human("u1"),
        factory=service,
        request=_request_without_trace(),
    )

    assert service.last_call_kwargs.get("user_id") == "u1"
    assert service.last_call_kwargs.get("nick_name") == "u1"
    assert service.last_call_kwargs.get("bot_id") is None
    assert service.last_call_kwargs.get("runtime_stage") is None


@pytest.mark.asyncio
async def test_paginates_items():
    service = _StubCronService(
        [_adapter_dict(id="t1"), _adapter_dict(id="t2"), _adapter_dict(id="t3")]
    )

    env = await list_owner_routines(
        page=PageParams(page=2, page_size=1),
        owner_id="u1",
        caller=_human("u1"),
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 3
    assert [i.routine_id for i in env.data.items] == ["t2"]


@pytest.mark.asyncio
async def test_empty_fleet_answers_an_empty_page():
    service = _StubCronService([])

    env = await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        caller=_human("u1"),
        factory=service,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_refuses_an_application_with_no_delegation():
    """An app naming a user it holds no delegation from learns nothing.

    Same gate and same answer as the ceiling: the refusal is a 404 shaped by
    ``envelope_errors`` from ``BotNotFoundError``, so it is indistinguishable
    from a user who does not exist.
    """
    service = _StubCronService([_adapter_dict()])
    # granted_bot_ids(): app_id set, grants None → frozenset() → refused.
    stranger = ActingCaller(user_id="u1", app_id=7)

    with pytest.raises(BotNotFoundError):
        await list_owner_routines(
            page=PageParams(page=1, page_size=20),
            owner_id="u1",
            caller=stranger,
            factory=service,
            request=_request_without_trace(),
        )
    assert service.last_call_kwargs == {}


@pytest.mark.asyncio
async def test_admits_an_application_with_a_delegation():
    service = _StubCronService([_adapter_dict()])

    env = await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        caller=_app_with_delegations("u1", "bot-x"),
        factory=service,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert env.data.total == 1


@pytest.mark.asyncio
async def test_partial_failure_returns_the_successes():
    """A dict-shaped ``data`` envelope unwraps to its ``items``.

    The service may answer ``{"data": {"items": [...]}}`` on partial failure
    paths; the listing keeps the succeeded rows like the per-bot route does,
    and never surfaces ``failed_targets`` on the public face.
    """
    service = _StubCronService({"items": [_adapter_dict(id="t1")]})

    env = await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        caller=_human("u1"),
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 1
    assert env.data.items[0].routine_id == "t1"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/community/adapters/http/openapi_v1/routines/test_owner_routines.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named '...routines.owner_router'`

- [ ] **Step 3: 创建 `owner_router.py`**

```python
"""Owner-level routine listing — ``GET /openapi/v1/bots/routines/all``.

The per-bot group lists one bot's draft workspace; this route aggregates the
named user's whole fleet — bots owned or collaborated on — across every
runtime stage, the way the legacy ``/api/cron`` listing always did. The
service layer owns the fan-out, the bot_name decoration, the per-stage dedup
and the partial-failure tolerance; this is the public-face adapter over it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    PublicAPIRoute,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.api.cron_relay_service import (
    CronRelayServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .router import _map_routine
from .schemas import Routine

router = APIRouter(
    prefix="/openapi/v1/bots/routines/all",
    tags=["routines"],
    route_class=PublicAPIRoute,
)

logger = get_logger()


@router.get("", response_model=Envelope[Page[Routine]])
@envelope_errors
async def list_owner_routines(
    page: PageParamsDep,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Page[Routine]]:
    """List the named user's routines across all their bots (paginated).

    Every runtime stage is aggregated — draft, verify and online — so a
    service bot's published runtimes appear alongside its draft workspace,
    and one definition can answer more than one row, differing by
    ``runtime_stage``. Bots the user collaborates on are included, matching
    the listing the internal console has always shown.
    """
    # Names no bot, so there is no grant to check against one — but the
    # answer is still about a person's fleet, and a stranger application must
    # not read it by naming a user id. Gated like the ceiling: an application
    # needs at least one live delegation from the named user; without one the
    # user is answered as if they did not exist.
    granted = caller.granted_bot_ids()
    if granted is not None and not granted:
        logger.warning(
            "[owner routines] app holds no delegation from user=%s; "
            "refusing the listing",
            for_log(owner_id),
        )
        raise BotNotFoundError("no authorization from the named user")
    result = await factory.list_all_crons(
        user_id=owner_id,
        nick_name=owner_id,
        bot_id=None,
        runtime_stage=None,
    )
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, list):
        items_list = data
    elif isinstance(data, dict):
        items_list = data.get("items", [])
    else:
        items_list = []
    mapped = [_map_routine(d) for d in items_list if isinstance(d, dict)]
    start = (page.page - 1) * page.page_size
    end = start + page.page_size
    page_items = mapped[start:end]
    return page_envelope(len(mapped), page_items, request)
```

- [ ] **Step 4: 声明 authorization NoCheck 条目**

`authorization.py` 的 "── Operations that address no bot ──" 节（`("GET", "/openapi/v1/bots/all")` 行附近）追加：

```python
    ("GET", "/openapi/v1/bots/routines/all"):
        NoCheck("a collection, not one addressed bot"),
```

- [ ] **Step 5: 声明 admission 条目**

`admission.py` ADMISSION 表 routines 节（`("GET", "/openapi/v1/bots/{bot_id}/routines")` 行附近，250 行区域）追加：

```python
    # The owner-level aggregate lists the named user's fleet, not one bot —
    # gated on a live delegation like the ceiling (see owner_router).
    ("GET", "/openapi/v1/bots/routines/all"): AdmissionMode.USER_GATED,
```

- [ ] **Step 6: 跑 Task 2 测试确认通过**

Run: `.venv/bin/python -m pytest tests/community/adapters/http/openapi_v1/routines/test_owner_routines.py -v`
Expected: PASS（7 条全绿）

- [ ] **Step 7: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/owner_router.py \
        src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py \
        src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py \
        src/backend/tests/community/adapters/http/openapi_v1/routines/test_owner_routines.py
git commit -m "feat(backend): add GET /openapi/v1/bots/routines/all owner aggregate"
```

---

### Task 3: 挂载进 `_SUBGROUPS` + 库存/次序测试验证

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`（import 区约 220 行 + `_SUBGROUPS` 约 285 行）

- [ ] **Step 1: 加 import**

`from .routines import router as routines_router` 后追加：

```python
from .routines.owner_router import router as routines_owner_router
```

- [ ] **Step 2: 挂进 `_SUBGROUPS`**

`_SUBGROUPS = [` 列表追加（带次序注释）：

```python
    # The owner-level routine aggregate is a literal under `bots/routines` —
    # it must mount before the *legacy* routines shim's `/{routine_id}`
    # wildcard (which the legacy groups, mounted later, contribute) or that
    # route captures a literal "all" as a routine id.
    routines_owner_router,
```

（`_SUBGROUPS` 挂载点只有 `_PUBLIC_AUTH` + USER_SCOPED 错误表——正对该路由"集合、无被寻址 bot"的形状；grant 检查不需要。）

- [ ] **Step 3: 跑库存与次序测试**

Run: `.venv/bin/python -m pytest tests/community/adapters/http/openapi_v1/test_admission_inventory.py tests/community/adapters/http/openapi_v1/test_authorization_inventory.py -v`
Expected: PASS（两表与路由面完全一致；这说明新路由的两条声明被看见了，且挂载的依赖形状与声明的模式相符）

Run: `.venv/bin/python -m pytest tests/community/adapters/http/openapi_v1/ tests/community/endpoints/test_openapi_routines.py tests/community/endpoints/test_openapi_legacy_routines_resources.py -v`
Expected: PASS（路由包全量 + 既有 routines endpoint 测试 + legacy parity 测试全部不回归）

- [ ] **Step 4: 手动冒烟确认路由次序（防 `all` 被吞）**

Run: `.venv/bin/python -c "
from agentclaw.community.adapters.http.openapi_v1 import build_public_router
r = build_public_router()
paths = [route.path for route in r.routes if getattr(route, 'path', '')]
idx_all = paths.index('/openapi/v1/bots/routines/all')
idx_legacy = paths.index('/openapi/v1/bots/routines/{routine_id}')
assert idx_all < idx_legacy, (idx_all, idx_legacy)
print('ordering ok: routines/all at', idx_all, 'before legacy {routine_id} at', idx_legacy)
"`
Expected: 输出 `ordering ok: ...`（若 `paths.index` 抛 ValueError，说明路由没挂上）

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py
git commit -m "feat(backend): mount owner routines aggregate before legacy shim"
```

---

### Task 4: 网关契约再生成 + README 记录

**Files:**
- Regenerate: `src/gateway/configs/schemas/bots.openapi.json`
- Modify: `src/backend/docs/openapi-v1/README.zh-CN.md`（变更记录节）

- [ ] **Step 1: 重新生成契约**

Run（在 `src/backend` 下）:
```bash
.venv/bin/python scripts/dump_openapi.py ../gateway/configs/schemas/bots.openapi.json
```
Expected: `wrote public OpenAPI to ../gateway/configs/schemas/bots.openapi.json`

- [ ] **Step 2: 核对 diff**

Run: `cd /Users/rongzhi/PycharmProjects/Avernet && git diff --stat src/gateway/configs/schemas/bots.openapi.json && git diff src/gateway/configs/schemas/bots.openapi.json | grep -c '"bot_name"'`
Expected: diff 只含新 path `/openapi/v1/bots/routines/all`、`Routine` 三新字段及关联 required/example 变化；`"bot_name"` 计数 ≥5（schema 字段定义 + example）

- [ ] **Step 3: 跑 gateway 侧 schema 相关测试**

Run: `cd /Users/rongzhi/PycharmProjects/Avernet/src/gateway && .venv/bin/python -m pytest tests/unit/core/forwarding/ tests/integration/baseline/test_startup.py -v 2>/dev/null || uv run --frozen python -m pytest tests/unit/core/forwarding/ tests/integration/baseline/test_startup.py -v`
Expected: PASS（pinned 契约与转发目录测试均绿；若 gateway venv 结构不同，按其 README/CI 惯例命令跑）

- [ ] **Step 4: README 变更记录**

`src/backend/docs/openapi-v1/README.zh-CN.md` 变更记录节（倒序，最新在上）加条目：

```markdown
- **2026-08-27** —— **Owner 级定时任务聚合列表。** 新增
  `GET /openapi/v1/bots/routines/all`：按认证用户聚合其名下（含协作参与）所有 Bot
  的定时任务，全部运行态（draft/verify/online）平铺，同一配置跨运行态可多行并以
  `runtime_stage` 区分；`Routine` 纯加法扩展可选字段
  `bot_name`/`owner_id`/`runtime_stage`（per-bot 列表同享 `bot_name` 回填）。
  机器调用者按 `USER_GATED` 接纳（同 `/bots/ceiling`：须持有该用户至少一个实时
  delegation，否则 404 掩蔽）；服务层复用 `list_all_crons` 的 fan-out/去重/部分失败
  容忍，`failed_targets` 不出公开面。地址为字面量 `all`，挂载先于 legacy
  `/{routine_id}` shim 以免被通配捕获。Gateway `bots.openapi.json` 已重新生成，需
  同步独立维护的 OCB/Sofapy 副本；转发与鉴权由既有宽泛 `/openapi/v1/bots/**` 规则
  覆盖。
```

- [ ] **Step 5: Commit**

```bash
git add src/gateway/configs/schemas/bots.openapi.json src/backend/docs/openapi-v1/README.zh-CN.md
git commit -m "feat(gateway): publish owner routines aggregate in bots.openapi.json"
```

---

### Task 5: 覆盖率门禁 + 收尾验证

- [ ] **Step 1: 本地跑 CI 门禁**（changed-line coverage ≥80% 只能本地复现）

Run: `cd /Users/rongzhi/PycharmProjects/Avernet/src/backend && bash scripts/ci_test.sh`
Expected: 通过（pre-push hook 只有 lint，看不到 coverage；这个才有）

- [ ] **Step 2: 若 coverage 不足**，回到 Task 1/2 补测试（优先补：`_map_routine` 新字段分支、owner_router 的 dict/data None 分支、app caller 两个分支），再跑

- [ ] **Step 3: 最终确认提交链**

Run: `git log --oneline origin/dev..HEAD`
Expected: 4 个提交（schema 扩展、owner 路由、挂载、契约+README）+ 已有 spec 提交

---

### Task 6: OCB 双写同步

**Files:**
- Modify: `~/IdeaProjects/ocb` 仓库内的 gateway `bots.openapi.json` 副本（执行时先 `find ~/IdeaProjects/ocb -name "bots.openapi.json"` 定位实际路径）

- [ ] **Step 1: 定位 ocb 侧契约文件**

Run: `find ~/IdeaProjects/ocb -name "bots.openapi.json" -not -path "*/node_modules/*"`
Expected: 找到网关侧副本路径（记忆约定：avernet/ocb 两侧 `bots.openapi.json` 要同步）

- [ ] **Step 2: 用 avernet 再生成的文件覆盖 ocb 副本**（若两侧文件此前完全一致则直接 `cp`；若历史上已有差异，先 `diff` 确认差异仅本次新增内容再覆盖——发现不一致要向用户报告，不要盲目覆盖）

- [ ] **Step 3: 检查 ocb gateway `application.yaml` 的 route_security**——本路由为 USER_GATED（非 REFUSED），预期**无需**任何 route_security 变更（宽泛 `/openapi/v1/bots/**` user/app optional 规则已覆盖）；若发现 ocb 侧有独立 REFUSED 清单需同步，停下来向用户确认

- [ ] **Step 4: ocb 侧提交由用户决定**（oen 仓库的提交节奏遵循 ocb 侧团队约定；完成后向用户报告 diff 摘要）

---

## Self-Review 记录

- **Spec 覆盖**：契约(§4)→Task 1/2；实现与挂载(§5)→Task 2/3；错误处理(§6)→Task 2 代码+测试；契约同步(§7)→Task 4/6；测试(§8)→各 Task + Task 5。准入模式两处修正已在计划头部声明理由。
- **占位符扫描**：无 TBD/TODO；所有代码步骤带完整代码。
- **类型一致性**：`list_owner_routines(page, owner_id, caller, factory, request)` 签名与测试直调一致；`_map_routine` 新字段名与 `Routine` schema 字段一致（bot_name/owner_id/runtime_stage）；`ActingCallerDep`/`UserIdDep`/`PageParamsDep` 的 import 路径均已按现行源码核实（principal.py:339 / contracts / admission）。
