# Bot List Event-Loop P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the owner-or-collaborator Bot list from blocking unrelated requests and remove BaaS fan-out from its first-paint path.

**Architecture:** The HTTP adapter will offload complete synchronous response-data construction with `asyncio.to_thread`. The core list service will return persisted desktop status without invoking BaaS; the existing single-bot live-status resolver remains unchanged for explicit freshness checks.

**Tech Stack:** Python 3.12, FastAPI, asyncio, pytest, pytest-asyncio

## Global Constraints

- Do not change HTTP request or response schemas.
- Do not change `resolve_desktop_live_status` semantics.
- Do not include P1 permission batching, path caching, frontend, or polling changes.
- Follow test-first red-green-refactor for each behavior.

---

### Task 1: Make the list use persisted desktop status

**Files:**
- Modify: `src/backend/tests/community/core/bot_management/services/test_list_desktop_live_status.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py:1837-1992`

**Interfaces:**
- Consumes: `BotService.list_bots_by_owner_or_collaborator(owner_id, page=1, page_size=100) -> dict`
- Preserves: `BotService.resolve_desktop_live_status(bot: dict) -> str | None`
- Produces: list results whose desktop `status` is the repository value and which perform no BaaS query

- [ ] **Step 1: Change the desktop-list test to require persisted status**

```python
def test_desktop_status_uses_persisted_value_without_baas_query(self):
    items = [_desktop("d1", status="OFFLINE")]
    client = _client_returning(bot_status="ACTIVE", device_status="ALL_ONLINE")
    svc = _make_bot_service(_repo_returning(items), client)
    result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
    assert result["items"][0]["status"] == "OFFLINE"
    client.query_device_status.assert_not_called()
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `uv run pytest tests/community/core/bot_management/services/test_list_desktop_live_status.py::TestDesktopLiveStatusMerge::test_desktop_status_uses_persisted_value_without_baas_query -q`

Expected: FAIL because the current bulk merge changes `OFFLINE` to `ACTIVE` and invokes BaaS.

- [ ] **Step 3: Remove bulk merge from list reads**

Delete the `self._merge_desktop_live_status(items)` call, `_merge_desktop_live_status`, and its list-only timeout/worker constants. Keep `resolve_desktop_live_status` and `_MERGE_SKIP_LOCAL_STATUSES` because the skill upload gate uses them.

- [ ] **Step 4: Run the service tests and confirm GREEN**

Run: `uv run pytest tests/community/core/bot_management/services/test_list_desktop_live_status.py -q`

Expected: all persisted-list and single-bot resolver tests pass.

### Task 2: Offload complete synchronous list assembly

**Files:**
- Modify: `src/backend/tests/community/endpoints/test_bot_management_router.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/bot_management/router.py:1-60,1344-1412`

**Interfaces:**
- Produces: `_build_bots_by_owner_or_collaborator_data(owner_id: str, bot_service: BotServiceProtocol) -> dict[str, Any]`
- Preserves: `list_bots_by_owner_or_collaborator(ctx, bot_service) -> ApiResponse`

- [ ] **Step 1: Add an async responsiveness regression test**

```python
@pytest.mark.asyncio
async def test_owner_or_collaborator_list_does_not_block_event_loop():
    class SlowBotService:
        def list_bots_by_owner_or_collaborator(self, **kwargs):
            time.sleep(0.1)
            return {
                "total": 1,
                "items": [{
                    "bot_id": "bot-1",
                    "entity_id": "entity-1",
                    "entity_type": "staff",
                    "engine_types": ["openclaw"],
                    "active_engine": "openclaw",
                }],
            }

        def get_engine_paths(self, *args):
            time.sleep(0.1)
            return {"openclaw": "/tmp/bot-1/openclaw"}

    loop = asyncio.get_running_loop()
    started = loop.time()
    task = asyncio.create_task(
        list_bots_by_owner_or_collaborator(
            ctx=SimpleNamespace(user_id="u"),
            bot_service=SlowBotService(),
        )
    )
    await asyncio.sleep(0.01)
    assert loop.time() - started < 0.1
    response = await task
    assert response.success is True
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `uv run pytest tests/community/endpoints/test_bot_management_router.py -k event_loop -q`

Expected: FAIL because the heartbeat is delayed by approximately `0.2` seconds.

- [ ] **Step 3: Extract and offload synchronous response assembly**

```python
def _build_bots_by_owner_or_collaborator_data(
    owner_id: str,
    bot_service: BotServiceProtocol,
) -> dict[str, Any]:
    engine_types = _get_engine_types()
    result = bot_service.list_bots_by_owner_or_collaborator(
        owner_id=owner_id,
        page=1,
        page_size=100,
    )
    items = result["items"]
    for bot in items:
        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        bot_id = str(bot.get("bot_id"))
        bot_engine_types = bot.get("engine_types", engine_types)
        bot["engine_paths"] = bot_service.get_engine_paths(
            entity_id, bot_id, bot_engine_types, entity_type
        )
        active = bot.get("active_engine", DEFAULT_ENGINE_TYPE)
        engine_paths = bot["engine_paths"]
        fallback = list(engine_paths.values())[0] if engine_paths else ""
        bot["bot_work_dir"] = engine_paths.get(active, fallback)

    default_bot = None
    if items:
        first_bot = items[0]
        first_engine = first_bot.get("active_engine", DEFAULT_ENGINE_TYPE)
        default_bot = {
            "entity_id": first_bot.get("entity_id"),
            "bot_id": first_bot.get("bot_id"),
            "entity_type": first_bot.get("entity_type", "staff"),
            "bot_work_dir": first_bot.get("engine_paths", {}).get(
                first_engine, first_bot.get("bot_work_dir", "")
            ),
        }
    return {"total": result["total"], "items": items, "default_bot": default_bot}


data = await asyncio.to_thread(
    _build_bots_by_owner_or_collaborator_data,
    owner_id,
    bot_service,
)
```

- [ ] **Step 4: Run endpoint and service tests and confirm GREEN**

Run: `uv run pytest tests/community/endpoints/test_bot_management_router.py tests/community/core/bot_management/services/test_list_desktop_live_status.py -q`

Expected: all tests pass.

### Task 3: Validate and publish

**Files:**
- Verify all modified source, test, design, and plan files

- [ ] **Step 1: Run formatting, lint, and architecture checks**

Run:

```bash
uv run ruff check \
  src/agentclaw/community/adapters/http/bot_management/router.py \
  src/agentclaw/community/core/bot_management/services/bot_service.py \
  tests/community/endpoints/test_bot_management_router.py \
  tests/community/core/bot_management/services/test_list_desktop_live_status.py
uv run pytest tests/community/architecture tests/community/endpoints/test_bot_management_router.py tests/community/core/bot_management/services/test_list_desktop_live_status.py -q
```

Expected: exit code 0 for both commands.

- [ ] **Step 2: Inspect the final diff**

Run: `git diff --check && git status -sb && git diff github/REL20260710...HEAD`

Expected: only the approved P0 source, regression tests, design, and plan are present.

- [ ] **Step 3: Commit and publish**

Commit the implementation and tests, push `agent/ceiling-event-loop-p0-rel20260710` to GitHub, then create a draft PR with base `REL20260710`.
