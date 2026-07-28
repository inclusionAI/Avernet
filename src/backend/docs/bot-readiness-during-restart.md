# Bot readiness during an in-flight restart

**Status:** problem analysis + proposal. No code changes yet.
**Scope:** how the frontend decides a bot is ready, why that works for personal
bots and not for published service bots, and what we should build.

---

## 1. The symptom

Restart a published service bot from the console and, for the next five to six
minutes, every operation against it fails:

```
GET /api/v1/devices/1368782/connection?bot_id=20260728_vyjyj2s0&owner_id=272471

{ "success": false,
  "message": "设备未就绪，请重启 teamclaw 应用或切换其他 bot",
  "error_code": 40303, "data": null }
```

```
GET /api/service-bot/publish/6517/engine-config

查询失败: BaasConnInfoBuilder: get_ws_info failed for binding=1348396:
BaaS API error: 404 - {"detail":{"error":"NO_ACTIVE_DEVICES", ...}}
```

Then it recovers on its own.

The console *does* grey out those actions while a restart runs — but only in the
tab that started the restart. **Refresh the page mid-restart and every button
comes back enabled immediately**, because the disabled state lives in page
memory and nothing in the page-load response replaces it.

This document explains why, and what the fix should look like.

---

## 2. TL;DR

There are two restart paths in this system, and only one of them has a durable,
server-derived notion of "this bot is not ready right now."

| | Personal / desktop bot | Published service bot |
|---|---|---|
| Restart entry | `POST /api/bots/restart` | `POST /api/service-bot/publish/{id}/restart` |
| Server marks not-ready | **Yes** — `ac_bots.status = PENDING` | **No** — nothing is written |
| Readiness query | **Yes** — `GET /api/bots/{id}/status` → `is_ready` | **No such endpoint for a published instance** |
| Frontend polls | **Yes** — `pollBotUntilSettled` | **No** — service bots are explicitly skipped |
| Survives a page refresh | **Yes** | **No** |
| Duplicate-restart guard | **Yes** — `restart_in_progress` | **No** |

The service-bot path was never wired for this. It is a structural gap, not a
recent regression (see §7).

---

## 3. What "ready" actually means — four layers

"Ready" is not one flag. Four independent records must agree, and each is owned
by a different component:

| # | Record | Owner | Meaning | Written by |
|---|---|---|---|---|
| 1 | `ac_bots.status` | backend | the bot's lifecycle state (`PENDING`/`ACTIVE`/`FAILED`) | bot lifecycle service |
| 2 | `ac_entity_device_binding.status` | backend | this binding points at a usable container | device/publish flow |
| 3 | `baas_bot.status` + `baas_device.status` | BaaS | a container exists and is running | BaaS publish workflow |
| 4 | engine process | container | `openclaw` actually listening on `:20003` | `start_service.sh` |

A restart invalidates 3 and 4 for several minutes while leaving 1 and 2
untouched for a service bot. That mismatch is the whole bug.

Layer 3 is where the refusal is generated. BaaS resolves a bot to a device with
a strict filter:

```python
# src/baas/.../bot_runtime/dispatcher/_device_selector.py:108
active_devices = [d for d in devices if d.status == "ACTIVE"]
if not active_devices:
    return None
```

```python
# src/baas/.../bot_runtime/dispatcher/_base_dispatcher.py:118
device = select_active_device(devices, device_affinity=device_affinity)
if not device:
    raise NoActiveDevicesError(bot_uuid)
```

And a restart deliberately takes the device out of `ACTIVE`:

```
# src/baas/.../publish_manage/_publish_service.py:2195
│ UPDATE       │ ACTIVE → UPDATING → drain → destroy → create new → start       │
```

So for the entire restart window there is, correctly, no `ACTIVE` device. BaaS
is not wrong. The frontend is asking a question whose answer it already should
have known.

---

## 4. Path A — how a personal bot stays honest

This path works end to end. It is the reference design.

### 4.1 Click → optimistic local state

```ts
// src/frontend/src/hooks/useBot.ts:722
const restartBot = useCallback(async (botId: string) => {
  const res = await BotController.restartBot(
    { bot_id: botId, user_id: userId, owner_id: resolveBotOwnerId(botId) },
    { skipErrorHandler: true },
  );
  const errorMsg = handleApiError(res, { module: 'Bot', action: '重启 Bot' });
  if (errorMsg) return false;

  updateBot(botId, { status: BOT_STATUS.PENDING, binding_id: null });   // ← line 746
  toast.info('Bot 正在重启，请稍候...');

  pollingBotIds.add(botId);
  const didActivate = await pollBotStatus(botId, 'restart', { skipSyncMcps: true });
  ...
```

Note what this local write mirrors: the server is about to persist the *same*
`PENDING`. The client state is an optimization, not the source of truth.

### 4.2 The server refuses to double-restart

```python
# src/backend/.../bot_management/services/bot_service.py:3439 (restart_bot)
bot_status = str(bot.get("status") or "").upper()
if bot_status in {"REACTIVATING", "PENDING"}:
    logger.info("[bot_service.restart_bot] skip restart while activation in progress: ...")
    return self._activation_in_progress_result(bot)      # restart_in_progress = True

if bot_status not in {"ACTIVE", "FAILED"}:
    raise BotInvalidLifecycleStateError(bot_id=bot_id, current_status=bot_status or "UNKNOWN")
```

```python
# bot_service.py:3302
current["restart_in_progress"] = True
current["message"] = "Bot activation is in progress"
```

A second restart is a no-op that reports "already restarting" — the UI cannot
cause harm even if it forgets to grey out the button.

### 4.3 The server persists PENDING

Along the non-BaaS path the record is published as `PENDING` *before* the async
allocation is spawned, precisely so polling reports the truth:

```python
# bot_service.py, inside restart_bot
# There is no current binding to release, but the asynchronous
# allocation can take time. Publish PENDING before spawning it
# so status polling does not continue to report the old FAILED
self._repository.update_by_owner(..., {"status": "PENDING", ...})
```

### 4.4 The frontend polls a server-derived answer

```ts
// src/frontend/src/utils/botPolling.ts:65
export async function pollBotUntilSettled(botId, options): Promise<PollBotResult> {
  while (Date.now() < deadline) {
    const res = await BotController.getBotStatus({ bot_id: botId, owner_id }, ...);
    const { bot_status, error_message } = res.data;
    const { start_status, start_message } = res.data.ext ?? {};

    if (start_status === 'FAILED') {                       // line 112 — container start failed
      return { outcome: 'failed', message: start_message || error_message };
    }
    if (bot_status === 'ACTIVE') {                         // line 120 — ready
      const detail = await BotController.getBotDetail({ bot_id: botId, owner_id });
      if (detail.success && detail.data) return { outcome: 'active', bot: detail.data };
    }
    if (bot_status === 'FAILED') return { outcome: 'failed', ... };
    await sleep(interval);                                 // PENDING → keep polling
  }
  return { outcome: 'timeout' };
}
```

3 s interval, 5 min timeout.

### 4.5 The backend owns the readiness predicate

```python
# src/backend/.../adapters/http/bot_management/router.py:2472
@router.get("/{bot_id}/status", response_model=ApiResponse)
async def get_bot_status(bot_id: str, owner_id: Optional[str] = Query(None), ...):
    bot = bot_service.get_bot(bot_id, resolved_owner_id)
    bot_status     = bot.get("status", "UNKNOWN")
    binding_info   = bot.get("device_binding", {})
    binding_status = binding_info.get("status", "UNKNOWN") if binding_info else "UNKNOWN"
    ...
    result = {
        "bot_id": bot_id,
        "bot_status": bot_status,
        "binding_status": binding_status,
        "device_id": binding_info.get("device_id") if binding_info else None,
        "device_provider": binding_info.get("device_provider") if binding_info else None,
        "error_message": error_message,
        "is_ready": bot_status == "ACTIVE" and repos_ready,     # ← line 2594
        "ext": response_ext,                                    # start_status / start_message
    }
```

**`is_ready` is the contract.** It is computed server-side from layers 1, 2 and
4 (`ext.start_status` is reported by the container's `starting_watchdog`), so
any client — a fresh page load included — gets the same answer.

### 4.6 The property that matters

Nothing in this loop depends on the client remembering anything. Reload the
page mid-restart and the very next `GET /api/bots/{id}/status` returns
`bot_status: PENDING`, `is_ready: false`. The UI re-derives "restarting" from
the server. **That is the behavior people remember, and it is real — it just
belongs to personal bots.**

---

## 5. Path B — why a published service bot cannot answer the question

Four independent reasons, any one of which would be sufficient.

### 5.1 The restart writes no state at all

```python
# src/backend/.../service_bot/services/publish_flow/restart_mixin.py:28
def restart_bot(self, publish_id: int, operator: str = "system") -> dict:
    """Submit a Bot restart (durable, crash-safe).
    ...this method returns as soon as the task is submitted (it does not wait
    for the re-deploy)."""
    error, stage, bot_uuid = self._resolve_restart_request(publish_id)
    if error is not None:
        return error

    enqueue_restart(                                        # ← line 72
        self._task_queue_service,
        publish_id=publish_id, stage=stage.value, operator=operator,
    )
    return {"success": True, "message": f"Restart task submitted, stage: {stage.value}", ...}
```

Validate, enqueue, return. No write to `ac_bot_publish.status`, none to
`ac_entity_device_binding.status`, none to `ac_bots.status`. Observed in
production: binding `1348396` kept `status = ACTIVE` with `gmt_modified`
equal to the restart moment — we touched the row and left it marked ready
while its container was being destroyed.

### 5.2 The publish record's status deliberately does not move

Even the progress sync refuses to advance it for an already-stable record:

```python
# src/backend/.../publish_flow/progress_sync_mixin.py:604
# Step 5: Advance the publish record status based on the BaaS status
# VALIDATING and SUCCESS are completed, stable states that do not need advancing
if current_status in (PublishStatus.VALIDATING, PublishStatus.SUCCESS):
    logger.info(f"... Current status is {current_status}, skip status update: publish_id={publish_id}")
```

This is intentional — a restart is not a new publish and must not rewrite the
publish lifecycle. But it means `status` can never serve as a readiness signal.

### 5.3 The frontend explicitly opts service bots out of polling

```ts
// src/frontend/src/hooks/useBot.ts:222
const pollBotStatus = useCallback(async (botId, reason = 'activate', options?) => {
  // 检查是否是服务 Bot，如果是则不轮询
  const bot = useBotStore.getState().getBotById(botId);
  if (bot?.bot_type === 'service') {
    console.log('[useBot] 服务 Bot 跳过轮询:', botId);   // ← line 232
    return false;
  }
```

The entire machinery of §4 bails out on the first line for service bots.

### 5.4 …and the readiness endpoint could not answer anyway

This is the subtle one. `GET /api/bots/{id}/status` resolves its binding like
this:

```python
# src/backend/.../bot_management/services/bot_service.py:1644
# Also fetch binding info from ac_entity_device_binding if exists
binding_id = bot.get("binding_id")          # ← ac_bots.binding_id
if binding_id:
    binding = service.get_device(binding_id=binding_id)
    bot["device_binding"] = binding.to_dict()
```

For a service bot, `ac_bots.binding_id` is the **owner's draft container**, not
the published verify/online instance. From the incident:

```
ac_bots.binding_id                     = 1368773    ← draft container
ac_bot_publish(6517).ext.binding.verify = 1348396   ← the instance that was restarting
```

So even if the console called `/api/bots/{id}/status` for a service bot, it
would describe a completely different container. The published instance is
addressed by *publish record + stage*, and there is no endpoint that answers
"is the verify-stage instance of publish 6517 ready?"

### 5.5 What exists instead, and why it is not enough

`POST /api/service-bot/publish/{id}/restart_status` (`router_publish.py:915`)
does report BaaS restart progress. But:

- it is a *progress* query, not a readiness predicate — the caller must
  interpret BaaS workflow status itself;
- it resolves the workflow via the operation ledger, falling back to
  `ext.restart[stage]`, and returns "Restart publish record ID not found" when
  neither is set — so a page that has just loaded cannot distinguish "no
  restart running" from "a restart is running but I looked too early";
- `ext.restart[stage]` is never cleared (since #197 removed the clear as a
  crash hazard), so its presence means "a restart happened once", not "one is
  running";
- crucially, **the page has to already suspect a restart to call it.** There is
  nothing in the page-load payload that says "ask about a restart."

`GET /api/service-bot/publish/{id}` (`router_publish.py:222`) returns
`record.to_dict()` and nothing else — no readiness, no in-flight marker.

---

## 6. What happens when the UI acts on a stale "ready"

The connect path never consults our own records for readiness; it goes straight
to the provider and lets BaaS answer:

```python
# src/backend/.../adapters/http/devices/router.py:452
except DeviceServiceError as e:
    # BaaS/provider failure ... The most common case is the device not yet being
    # ready — BaaS replies 503 NO_ACTIVE_DEVICES while the container/process is
    # still coming up. That is an expected, self-healing state, NOT a server fault
    detail = str(e)
    if "NO_ACTIVE_DEVICES" in detail:
        return ApiResponse(success=False,
                           message="设备未就绪，请重启 teamclaw 应用或切换其他 bot",
                           error_code=40303, data=None)
```

Two problems compound here:

1. **The advice is actively harmful.** It tells the user to restart — while a
   restart is already running. A second restart opens a new UPDATE publish and
   re-arms the whole window. In the incident, one bot took **four UPDATE
   publishes in 48 minutes** (22:20, 22:28, 22:51, ~23:03), i.e. roughly 20 of
   those 48 minutes were self-inflicted failure windows.
2. **The publish engine-config path does not even degrade.** It has a bare
   catch-all that stringifies the provider error into the toast
   (`router_publish.py:272`, `get_publish_engine_config`), which is where the nested
   BaaS JSON in §1 comes from.

---

## 7. This is a structural gap, not a recent regression

Checked across four release branches:

| Surface | REL20260717 | REL20260723 | REL20260724 | REL20260728 |
|---|---|---|---|---|
| `PublishFlowResult` has an in-flight field | no | no | no | no |
| `GET /publish/{id}` body | `data=record.to_dict()` | same | same | same |
| service-bot restart writes a status | never | never | never | never |
| "skip status advance" for stable records | present | present | present | present |
| `pollBotStatus` skips `bot_type === 'service'` | present | present | present | present |

`adapters/http/service_bot` has a zero-byte diff between REL20260724 and
REL20260728. The only nearby semantic change in that window landed on
**REL20260723** (#197: `ext.restart` is no longer cleared at submission, and the
`ac_publish_operation` ledger was introduced).

---

## 8. Incident timeline (evidence)

Bot `20260602_icousaig`, publish record 6517, `BOT-b9c01670…`, env `pre`:

```
22:20:25 → 22:26:33   BaaS publish 27447  UPDATE  SUCCESS   bot 21035 → 21226
22:28:34 → 22:34:54   BaaS publish 27456  UPDATE  SUCCESS   bot 21226 → 21232
22:51:17 → 22:56:16   BaaS publish 27469  UPDATE  SUCCESS   bot 21232 → 21244
~23:0x   → 23:08:27   (a fourth)                            bot 21244 → …
```

Every generation handed off cleanly (old row `RELEASED` + soft-deleted at its
publish's completion timestamp; device `1732` /
`ARCA-SANDBOX-f8719b1b-…@0` carried across all four and is `ACTIVE` now).
**No data was corrupted at any point.** The 404s land exactly inside those
windows.

A second bot, `20260728_owrvovnu` (publish 6539), was traced end to end through
its container: the bootstrap guard passed, `install_engine.log` completed at
23:41:49, `start_service.sh` finished at 23:47:19, the hook's callback returned
`HTTP 200 {"status":"processed"}`, and the publish completed. The bot was simply
unreachable for the ~6 minutes in between — of which ~5.5 min was
`start_service.sh` itself (engine install took 17 s; `openclaw` came up at
23:46).

That last number is worth its own follow-up: shrinking the window shrinks the
blast radius of every proposal below.

---

## 9. Proposals

Ordered; each stands alone, but the recommendation is B + C + D.

### A. Expose the operation ledger as an in-flight flag *(small)*

`ac_publish_operation` already persists an operation's intent **before** the
BaaS call and only leaves `pending`/`id_recorded` at a terminal state — its own
index comment reads `"any in-flight op for this record?" scans`. Surface it:

```
GET /api/service-bot/publish/{id}   → data.in_flight = { kind, stage, state, started_at, baas_publish_id } | null
POST /api/service-bot/publish/{id}/sync → same field on PublishFlowResult
```

- **Pro:** no schema change, no new table, one query, page-load-visible.
- **Con:** answers "an operation is running", not "is this instance usable".
  A bot can also be unusable with no operation running (crashed container).

### B. A readiness endpoint for a *published instance* *(the real parity fix)*

Mirror `GET /api/bots/{id}/status`, keyed on publish record + stage:

```
GET /api/service-bot/publish/{id}/readiness?stage=verify|online

{ "ready": false,
  "phase": "RESTARTING",            // READY | RESTARTING | PUBLISHING | FAILED | UNBOUND
  "since": "2026-07-28T23:41:32",
  "binding_id": 1348396,
  "bot_uuid": "BOT-…",
  "device_status": "UPDATING",      // from BaaS
  "detail": "Bot 正在重启，通常需要几分钟" }
```

Composed from: the operation ledger (A) + the stage binding from
`ext.binding[stage]` + BaaS device status. Note BaaS's `start-progress`
endpoint already works *during* this window — it uses the relaxed
`select_available_device` (`ACTIVE`/`PENDING`/`UPDATING`/`OFFLINE`), unlike
`ws-info` — so real progress can be surfaced rather than a boolean.

- **Pro:** gives service bots exactly what `is_ready` gives personal bots;
  answers the question the console actually has; covers non-restart causes.
- **Con:** new endpoint + a BaaS call on the read path (cache/short-TTL it).

### C. Frontend: consume it and drop the carve-out

1. On mount, call the readiness endpoint for each visible publish record and
   derive the disabled state from `ready`/`phase` — this is the part that fixes
   the refresh.
2. Remove or narrow the `bot_type === 'service'` skip in `pollBotStatus`
   (`useBot.ts:229`) so service bots poll the endpoint from B, reusing the
   `pollBotUntilSettled` shape.
3. Render `phase` + `since` ("重启中，已用时 2 分钟") instead of a dead button.

**Open question — where this lands.** `src/frontend` is a micro-frontend shell:
`components/UmdLoader/loadUmd.ts` fetches remote UMD bundles by CDN URL at
runtime, and the publish console is one of those panels. In this repo only the
SDK exists (`services/backend-api/ServiceBotController.ts` — `restartPublish`,
`getRestartPublishStatus`), with no caller. The console panel's source must be
located before C can be implemented.

### D. Server-side guard *(defense in depth)*

Personal bots already refuse a concurrent restart (`restart_in_progress`,
§4.2). Service bots do not. Reject `restart` / `upgrade` / `offline` /
`scale` while a non-terminal operation exists for that publish record, with a
distinct business code the UI can render.

- **Pro:** greying out is UX; this makes it *true*. Kills the retry storm even
  with a stale tab open.
- **Con:** must not break legitimate retry-after-crash — the guard has to treat
  a stuck non-terminal op as retryable (bounded by age, or explicitly via the
  existing `abandon` + fresh-attempt path).

### E. Fix the two error surfaces *(independent, cheap)*

- `devices/router.py:452` — when an operation is in flight, say "正在重启，请稍候"
  and **stop advising a restart**.
- `router_publish.py` `get_publish_engine_config` — map the provider error to a
  business response instead of stringifying the BaaS payload into a toast.

### Recommendation

**B + C** is the actual fix: it gives published instances the same
server-derived readiness contract personal bots have had all along, and it is
the only option that survives a page refresh, a second browser, or a different
user opening the console. **D** should ship with it so correctness does not
depend on the UI. **E** is worth doing immediately and independently — it is
small, and it stops the failure mode from amplifying itself. **A** is a strict
subset of B; only build it standalone if B needs to be staged.

Separately, investigate the **5.5 minutes in `start_service.sh`**. Every
proposal here makes the window *visible*; that one makes it *smaller*.

---

## 10. Code index

| Concern | Location |
|---|---|
| Personal restart (frontend) | `src/frontend/src/hooks/useBot.ts:722` |
| Service-bot polling carve-out | `src/frontend/src/hooks/useBot.ts:229` |
| Poll loop / terminal states | `src/frontend/src/utils/botPolling.ts:65` |
| `getBotStatus` client | `src/frontend/src/services/backend-api/BotController.ts:941` |
| Publish restart client | `src/frontend/src/services/backend-api/ServiceBotController.ts:289` |
| `is_ready` predicate | `src/backend/.../adapters/http/bot_management/router.py:2594` |
| Personal restart + guard | `src/backend/.../bot_management/services/bot_service.py:3439` |
| `get_bot` binding resolution | `src/backend/.../bot_management/services/bot_service.py:1644` |
| Service-bot restart (no writes) | `src/backend/.../publish_flow/restart_mixin.py:28` |
| Stable-record status skip | `src/backend/.../publish_flow/progress_sync_mixin.py:604` |
| `GET /publish/{id}` | `src/backend/.../service_bot/router_publish.py:222` |
| `restart_status` | `src/backend/.../service_bot/router_publish.py:915` (handler at `:917`) |
| `NO_ACTIVE_DEVICES` → 40303 | `src/backend/.../adapters/http/devices/router.py:452` |
| Strict ACTIVE device filter | `src/baas/.../bot_runtime/dispatcher/_device_selector.py:108` |
| Relaxed filter (start-progress) | `src/baas/.../bot_runtime/dispatcher/_device_selector.py:117` |
| `NoActiveDevicesError` raise | `src/baas/.../bot_runtime/dispatcher/_base_dispatcher.py:118` |
| UPDATE device lifecycle | `src/baas/.../publish_manage/_publish_service.py:2195` |
| Operation ledger model | `src/backend/.../service_bot/repository/models.py` (`ac_publish_operation`) |
