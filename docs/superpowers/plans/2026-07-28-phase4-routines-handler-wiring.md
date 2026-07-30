# Routines openapi_v1 7 handler 接通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Subagent-driven(额度已恢复,继续该范式)。Steps use checkbox (`- [ ]`)。**不要 git commit**(用户自己收尾),只改代码报告。

**Goal:** 把 `openapi_v1/routines/router.py` 的 7 个 stub handler(`raise NotImplementedError`)接通真实 `CronRelayServiceProtocol`,返回标准 `Envelope[T]`,**严格沿用 resources device_fs handler 的 fallback 范式**(owner_id/owner_name 从 `bot_repo.get_by_id(bot_id)` 取,`principal` 作为 seam 占位)。

**Architecture:** 架构宪法 Rule 7(薄 adapter) + Rule 6/8(不 import legacy helper) + Rule 24(增量)。openapi_v1 handler 只做协议转换 + service 调 + Envelope。cron service 是纯 HTTP 中继到 engine(无 device_fs),routines 无表靠 `ac_bots` guard(Session 0 + Phase 0)间接隔离。

**Caballer 身份来源(架构裁定):** bot_id 从 query/path 取;user_id/nick_name **本期从 `bot_repo.get_by_by_id(bot_id)` 取 owner_id/owner_name**(personal bot owner=调用者);**gateway 主体验签链路是 `require_principal` / `resolve_avernet_tenant` 两个 seam 的事,快通时统一改 seam,handler 零改**。与 resources device_fs handler 完全同套(注释锚点 "When principal lands, swap to principal.subject")。

**Tech Stack:** Python / FastAPI / pydantic v2 / fastapi_injector(`Injected`) / SQLAlchemy。`openapi_v1/contracts.py` 已有 `Envelope`/`Page`/`PageParams`/`Deleted`。`openapi_v1/dependencies.py` 有 `require_principal`(stub)/`resolve_avernet_tenant`(stub)。

**前置依赖:** Phase 0(ac_bots guard)+ resources phase1/phase3 已验证 `Injected` 不进 served schema + fallback 范式可行。

**范围:** routines 7 endpoint 全部接通。upload/download/preview/update/delete 等不在本 plan(resources 已完工)。

---

## File Structure

- **Modify** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/routines/router.py` — 替换 7 stub body,新增模块级 `_owner_from_bot()` helper + `_request_id_from()`(或 import resources 的)
- **Create** `src/backend/tests/community/adapters/http/openapi_v1/routines/test_routines_handlers.py` — handler 单测(`tests/community/adapters/http/openapi_v1/__init__.py` 已有,需加 `routines/__init__.py`)

---

## 关键事实(已代码级核实)

- 注入点:`CronRelayServiceProtocol`(`api/cron_relay_service.py:8`,10 方法,loose `*args`)。用 `Injected(CronRelayServiceProtocol)`
- owner fallback 范式(沿用 resources):`bot = bot_repo.get_by_id(bot_id); owner_id = bot.get("owner_id") or bot_id; owner_name = bot.get("owner_name", "")`,需注入 `BotRepository`
- `CronRelayServiceProtocol` 7 个核心方法(`core/cron/services/cron_runtime_operations.py`),全 async,全要 `bot_id`/`user_id`/`nick_name`:
  - `get_cron_status(bot_id, user_id, nick_name) -> dict`(运行态总览,用于 list)
  - `get_cron_detail(bot_id, user_id, nick_name, task_id) -> dict`
  - `create_cron(bot_id, user_id, nick_name, body: dict) -> dict`
  - `update_cron(bot_id, user_id, nick_name, task_id, body, runtime_stage=DRAFT) -> dict`
  - `delete_cron(bot_id, user_id, nick_name, task_id) -> dict`
  - `run_cron(bot_id, user_id, nick_name, task_id) -> dict`
  - `get_cron_runs(bot_id, user_id, nick_name, task_id) -> dict`
- cron service 返回的是 `dict`(含 `success`/`data` 等,中继 engine adapter 的响应)。**handler 要把 dict 映射到 openapi `Routine`/`RoutineRun` schema**(`openapi_v1/routines/schemas.py`)
- openapi `Routine`(routines/schemas.py:18-29):`routine_id`/`bot_id`/`name`/`trigger`/`command`/`enabled`/`timezone`/`gmt_create`/`gmt_modified`;`RoutineCreate`(`:32`)含 `bot_id`;`RoutineRun`(`:53`)
- **C3(已定方案 b)**:`GET/PATCH/DELETE/{routine_id}` 等 path 只含 routine_id,但 service 要 bot_id——**加必填 query `bot_id`**;openapi router 未上线不断 online 契约。`RoutineCreate.bot_id` 已含(C2 非缺口)
- legacy cron router(`adapters/http/cron/router.py`)用的是 `get_request_context`(ctx.user_id/ctx.bot_id)拿身份,openapi 改用 fallback(owner from bot_repo)

---

## Task 1: `_owner_from_bot` helper + `_request_id_from` + `_map_routine`/`_map_run`(纯函数,TDD RED→GREEN)

**Files:** Modify `openapi_v1/routines/router.py`(模块级加 helper);Create test 文件

- [ ] **Step 1: 写红测试**(`_owner_from_bot`/`_map_routine`)

```python
# tests/community/adapters/http/openapi_v1/routines/test_routines_handlers.py
"""openapi_v1 routines handler unit tests."""
@pytest.mark.asyncio
async def test_owner_from_bot_returns_owner_id_and_name():
    # stub bot_repo.get_by_id returns {owner_id:"u1", owner_name:"Alice"}
    oid, name = _owner_from_bot(bot_id="bot-x", bot_repo=stub_repo)
    assert oid == "u1" and name == "Alice"

async def test_owner_from_bot_falls_back_to_bot_id_when_no_owner():
    # stub returns {owner_id: None}
    oid, name = _owner_from_bot("bot-x", stub_repo)
    assert oid == "bot-x"  # fallback to bot_id (personal owner)

def test_map_routine_flattens_service_dict():
    service_dict = {"task_id":"t1","bot_id":"bot-x","name":"cron1","command":"echo",...}  # 待核 service 真实返回键
    r = _map_routine(service_dict, bot_id="bot-x")
    assert r.routine_id == "t1"
    assert r.bot_id == "bot-x"
```

> **`_map_routine`/`_map_run` 待核实**:cron service 返回的 dict 键名(task_id?cron_id?enabled?)跟 openapi schema 字段映射,**实现时先 `grep -rn "task_id\|cron_id\|return.*data\|\"name\"\|\"command\"" src/agentclaw/community/core/cron/services/cron_runtime_operations.py` 看 forward_request 返回的 dict 结构**,或卷 legacy cron router 怎么 extract。这步是 core 事实,implementer 必须先核。

- [ ] **Step 2: 跑确认红**(`_owner_from_bot` 没定义)
- [ ] **Step 3: 实现 helper**(在 router.py 顶部 import 之后)
- [ ] **Step 4: 跑绿**
- [ ] **Step 5: 不 commit**

---

## Task 2: 接 `GET /openapi/v1/bots/routines`(list)

service 调 `get_cron_status(bot_id, user_id=owner_id, nick_name=owner_name)`,返回 dict(运行态),映射成 `Page[Routine]`。

- [ ] Step 1-5: 红测试→接通→绿→不 commit。handler signature 加 `bot_id` query(必填,List 没意义)+ `factory`/`request` Injected。
- [ ] **关键**:`get_cron_status` 返回的 dict 结构要核(`forward_request` 返回 engine adapter 的 cron 列表),**实现前先核 service 真实返回**。

---

## Task 3: 接 `POST /openapi/v1/bots/routines`(create)

- `create_cron(bot_id, user_id, nick_name, body: dict)`。body 从 `RoutineCreate` 转 dict(`{"name":..,"trigger":{"type":"schedule","cron":..},"command":..,"timezone":..,"enabled":..}`)。返回映射成 `Routine`。
- [ ] 红测试→接通→绿→不 commit。

---

## Task 4-7: 接 `GET/PATCH/DELETE/{routine_id}` + `POST/{id}/run` + `GET/{id}/runs`

全用 C3 方案 b:` 增加必填 query `bot_id: str`。service 调用传 `task_id=routine_id`。
- `get_cron_detail`/`update_cron`/`delete_cron`/`run_cron`/`get_cron_runs`,各映射到 `Routine`/`Deleted`/`RoutineRun`/`Page[RoutineRun]`
- [ ] 每个 task 红测试→接通→绿→不 commit

---

## Task 8: 全量验证

- [ ] `uv run pytest tests/community/adapters/http/openapi_v1/routines/test_routines_handlers.py tests/community/contracts/gateway/test_public_namespace.py -v`
- [ ] `UV_DEFAULT_INDEX=https://pypi.org/simple uv run pytest tests/community -q`(只增不减 0 fail;resources 后基线 ~9071+)
- [ ] `uv run ruff check .../routines/router.py`;`uv run mypy .../routines/router.py`
- [ ] 剩余 stub 数 = 0

---

## Self-Review

- spec §3.2 routines handler 表 7 个全覆盖 ✓
- C3 方案 b(query bot_id)✓
- fallback 范式 = resources device_fs(handler 零改 seam)✓
- gateway 透传 = `require_principal`/`resolve_avernet_tenant` seam 的事,不 handler 管 ✓
- 不 import legacy helper ✓(架构 Rule 7/8)
- 不 commit ✓

## 待核实(implementer 动笔前)

1. cron service 返回 dict 真实结构(`forward_request` → engine adapter 响应的键名)——**决定 `_map_routine` 映射写法**
2. `get_cron_status` 是否返回某 bot 全部 cron(list 语义),还是单条——决定 list 是否够用