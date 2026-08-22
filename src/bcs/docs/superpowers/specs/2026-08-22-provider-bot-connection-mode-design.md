# Provider Bot Connection Mode (gateway / plugin) — Design

- Status: Draft (pending user review)
- Date: 2026-08-22
- Branch: `provider_bot_register_with_type`
- Scope: `POST /providers/{provider_id}/bots` gains a `connection_mode` field that selects how the registered bot connects to BCS. `plugin` mode skips the provider_binding write; `gateway` keeps current behavior.

## 1. Problem

`POST /providers/{provider_id}/bots` always writes a `provider_bot_binding` row. That row is exactly what makes a bot "gateway/downlink" (HTTP webhook delivery): `resolve_delivery_target` (`services/bcs-bot/src/core/bcs-bot_core.rs:196-283`) returns `HttpProvider` iff a live binding exists, else `WebSocket`. There is no way to register a provider-owned bot that connects via **WebSocket through a BCN plugin** instead of through the provider webhook.

A BCN plugin connects its bot to BCS over **WebSocket with an empty token** (`adapters/ws/bcs-ws/src/bot/dispatcher.rs:341`, `connect_bot` is_new branch `bot_core.rs:696-705`), and BCS allocates a token and returns it to the plugin. The provider may register the bot before or after the plugin's first connection. The registration record and the WS connection must resolve to the **same bot** — without reintroducing duplicate/orphaned bots, and without opening a "any `bot_uuid` + empty token can hijack an arbitrary bot" hole.

## 2. Verified code facts (Phase 1)

1. **WS frames carry no provider credential.** `handle_bot_connect` uses `params.token` / `params.bot_id` / `params.client_kind` only (`dispatcher.rs:331-345`). Nothing ties a WS connect to the provider that pre-registered the bot.
2. **`register_streaming_connection` deliberately does NOT persist the token.** `services/bcs-bot-store/src/lib.rs:2405-2406`: "Token is NOT persisted to DB here. It will be saved during onboard when `save_to_db` is called with the session_token." First-connect tokens live only in the in-memory `token_to_bot` index until onboard.
3. **`register_with_owner_and_token` is a soft-merge on existing bots** (`memory.rs:626-689`, `lib.rs:1385-1450`): it merges capability fields and **replaces** `session_token`, but does **not** clear `ws_connection` or sessions. So a provider registration that lands after a plugin is already connected does **not** kick the WS connection.
4. **`connect_bot` is_new branch rejects a provided `bot_id` that already exists** with `AlreadyRegistered` (`bot_core.rs:712-714`). There is no path today for "empty token + known `bot_id` → take over an existing pre-registered bot."
5. **`is_provider_downlink_bot` = "has a live binding"** (`application/bot.rs:828-829`). `reject_provider_delivery_websocket` (`dispatcher.rs:387-418`) calls this and only rejects HTTP-provider (downlink) bots. A plugin-mode bot with **no binding** passes cleanly → WS connect is allowed. **No WS-adapter change needed for the guard.**
6. **`reconnect_streaming`** (`lib.rs:2414-...`) resolves the bot **by token** (`find_bot_by_token`), then takes the bots write lock. It does not allocate a new id.
7. **Store write paths hold the `bots` write lock for their own read-decide-write**, but the *existence check* in `connect_bot` (`repo.get`) happens **outside** that lock (`bot_core.rs:713`), so `connect_bot`'s view of "exists" can be stale relative to a concurrent write. Putting the reconcile inside the store's write lock removes that window.

## 3. Design

### 3.1 Invariants (plugin mode)

- **IV-1**: A plugin-mode bot's `bot_uuid` is exactly the request's `provider_bot_ref` (deterministic id). This is what lets a later plugin WS connect (which sends `bot_id = provider_bot_ref`) land on the same bot record.
- **IV-2**: The `session_token` is **not a connect credential** in plugin mode — it is a runtime token the plugin and BCS negotiate, returned via `BotConnectResponse.token` / `BCN_BOT_TOKEN`.
- **IV-3**: Provider registration of an **already-existing** plugin bot does **not** replace its `session_token` (so an already-connected plugin keeps its token and WS connection).
- **IV-4**: The reconcile that promotes a pre-registered (MOCK-token) plugin bot to a real-token connected bot happens **inside the store write lock**, not in the lock-free `connect_bot` existence check. The store lock is mutually exclusive, so two writers never truly overlap; the second always observes the first's final state.

### 3.2 Wire contract

`crates/contracts/bcs-protocol/src/http/provider.rs`, `RegisterProviderBotRequest`:

```rust
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProviderBotConnectionModeDto {
    Gateway,
    Plugin,
}

// inside RegisterProviderBotRequest:
#[serde(default)]
pub connection_mode: Option<ProviderBotConnectionModeDto>,
```

- Field `connection_mode`; values `"gateway"` / `"plugin"`; absent / `None` ⇒ `gateway`.
- Unknown value ⇒ serde enum deserialization error ⇒ 400 (matches `ProviderCoordinationModeDto` behavior).
- **Not persisted** — behavior is fully determined by "binding written or not", so no `connection_mode` column.

### 3.3 Provider registration (`register_provider_bot_internal`, `provider_core.rs`)

Add a `connection_mode: ProviderBotConnectionMode` to `RegisterProviderBotCommand` (`service-api/.../application/provider.rs:77`) and `RegisterProviderBotParams` (`service-api/.../core/provider.rs:27`), threaded through `register_provider_bot` (`routes/providers.rs:217` and `application/provider.rs:244`).

For **plugin** mode:
- **Force deterministic `bot_uuid == provider_bot_ref`** (IV-1). Do not gate on `allowed_switch_provider_ids` for plugin mode (today `bot_uuid = Some(provider_bot_ref.clone())` only happens under that gate, `providers.rs:225`).
- **Skip `bindings.insert_binding(...)`** entirely (the `provider_core.rs:202-222` block). → no provider_binding row → `resolve_delivery_target` ⇒ `WebSocket`, `is_provider_downlink_bot` ⇒ false ⇒ WS allowed.
- `register_with_owner_and_token(bot_uuid, capabilities, owner, token)` is still called, but the `token` passed to it is **not** a usable runtime token — it is a `MOCK_`-prefixed placeholder (so the store's reconcile branch can recognize "pre-registered, no plugin attached yet"; see §3.5). The real token is minted later from the WS connect path.
- If `repo.get(provider_bot_ref)` already exists (plugin connected first): `register_with_owner_and_token` already soft-merges capabilities. **Plugin-mode further requires: do not replace `session_token`** (IV-3). This is achieved by a new plugin-mode merge path (or a flag) that preserves the existing real token instead of `existing.session_token.replace(...)` (`memory.rs:660`, `lib.rs:1417`).
- The response `bot_runtime_token` is returned for audit/observability but is **not** the token the plugin connects with. (Open: return the MOCK placeholder or `None`; see §6 Open question 1.)

For **gateway** mode: behave exactly as today (write binding; `bot_runtime_token` only for `static_bearer`/`provider_admin` auth modes, `provider_core.rs:185-189`).

### 3.4 First-connect persistence (`register_streaming_connection`, `bot-store`)

Today the first-connect token is memory-only (fact 2). To let a plugin-first bot survive and be reconciled by a later provider registration, **persist the token at first connect** in plugin mode: `save_to_db(bot_id, caps, Some(token), None)` inside `register_streaming_connection` when the bot is created by a WS connect.

Scope guardrails:
- This persistence enlargement applies when the connect mints a **new** bot (the WS-driven create path). It does not change onboard-driven persistence.
- Memory store keeps persisting on onboard exactly as now; the new persistence is additive (write the token at first connect, so a restart / a later provider registration can resolve the same `provider_bot_ref`).

### 3.5 Connect reconcile — the single connect-side change (in the store write lock)

Move the reconcile decision **out of `connect_bot`'s lock-free `repo.get`** (`bot_core.rs:712-714`) and **into `register_streaming_connection`'s write lock** (`lib.rs:2355` / `memory.rs:1478`). `connect_bot`, when given a `bot_id` that exists, no longer unconditionally returns `AlreadyRegistered`; instead it calls `register_streaming_connection`, which decides atomically under the write lock:

| Bot exists? | `session_token` state | `ws_connection` | Action |
|---|---|---|---|
| No | — | — | **Create** bot + new real token + attach ws. (plugin-first) |
| Yes | `MOCK_…` placeholder | (any) | **Promote**: replace token with real token + attach ws. (provider-first, plugin arrives) |
| Yes | real token, not connected | none | **Reconnect**: keep token + attach ws. (same plugin instance re Connecting) |
| Yes | real token, already connected | present | `Err(AlreadyConnected)`. (existing protection) |

`MOCK_` placeholder is a BCS-internal sentinel (prefix on the `session_token`), not a credential. The branch is only reachable for a bot whose token is exactly that sentinel — i.e. a plugin-mode pre-registered bot with no plugin attached yet. Any attempt to connect with a real-token/existing bot guarded by `AlreadyConnected`; any attempt that supplies no `bot_id` mints a brand-new uuid as today (does not collide with a deterministic `provider_bot_ref`).

### 3.6 Anti-hijack analysis

- The only connect path that reuses an existing bot's identity without a token is the **MOCK-promote** branch, and only for a bot whose token is the MOCK sentinel.
- To wrongly trigger it, a caller would need to connect with `bot_id = <some provider_bot_ref>` whose bot record holds the MOCK sentinel. That bot exists only because **the owning provider created it in plugin mode**; taking it over is the intended "a plugin attaches to its pre-registered bot" action,-owner-aligned (the provider chose `provider_bot_ref` and that the bot connect via plugin). Window is limited to "pre-registration not yet claimed"; once promoted to a real token, the standard `AlreadyConnected`/token reconnect protections apply.
- Without `connection_mode=plugin`, gateway-mode providers **do** write a binding ⇒ `is_provider_downlink_bot == true` ⇒ `reject_provider_delivery_websocket` rejects WS. So a gateway bot cannot be hijacked via this path.

## 4. Timeline handling

Three orderings of (provider register P) vs (plugin first connect W), plus the true-overlap case the business will avoid.

### 4.1 P before W
P creates bot with MOCK token (no binding). W connects with `bot_id = provider_bot_ref`, empty token → `register_streaming_connection` sees existing + MOCK → **promote** to real token + attach ws. ✅ Same bot, real token, plugin holds connection.

### 4.2 W before P
W first-connect creates bot with real token (now persisted, §3.4). P registers (plugin mode) → `register_with_owner_and_token` sees existing → soft-merge capabilities, **preserve real token** (IV-3), no binding. The plugin's WS connection is **not** kicked (soft-merge keeps `ws_connection`). ✅

### 4.3 W and P both observe "not exists", then write
Business guarantees the two are temporally staggered, so this case is **out of scope** (documented limitation). Even if it occurred, the deterministic id (IV-1) plus the store's mutually-exclusive write lock means the second writer observes the first's final state and reconciles per §3.5 — at worst a single token replacement, no duplicate bots, no hijack. A pathological simultaneous `bots.insert` under different ids is prevented by IV-1 (both use `provider_bot_ref`).

### 4.4 Observability requirement
Business asks for link-level logs to aid triage. Each reconcile branch and each provider registration emits a structured log with enough context to reconstruct the ordering. Fields (existing style: `bot_id`, `provider_id`, `provider_bot_ref`, `token_preview`, action phrase):

- Provider register (plugin): `info!(provider_id, bot_id, provider_bot_ref, connection_mode="plugin", created=..., "register_provider_bot: plugin bot registered (no binding)";)` and, when existing: `info!(..., token_replaced=false, "register_provider_bot: plugin bot already connected; capabilities merged, token preserved")`.
- `register_streaming_connection` reconcile: tag the branch — `info!(bot_id, provider_bot_ref?, branch="create"|"promote_mock"|"reconnect", token_preview, "register_streaming_connection: <branch> ...")`; on promote include `previous_token_kind="mock"`.
- All site/branch logs use `provider_bot_ref` where known so a triager can grep the single identity across both the provider-registration and WS-connect paths.

## 5. Test plan

1. **DTO**: absent ⇒ gateway; `"plugin"`/`"gateway"` parse; unknown value ⇒ 400.
2. **Gateway regression**: binding written; `bot_runtime_token` only for static_bearer/provider_admin; behavior unchanged.
3. **Plugin P-before-W**: register (plugin) creates bot with MOCK token, no binding; then WS connect with `bot_id=provider_bot_ref` empty-token ⇒ returns same `bot_uuid`, real token != MOCK, no new bot, `resolve_delivery_target == WebSocket`, `is_provider_downlink_bot == false`, **no** provider_binding row.
4. **Plugin W-before-P**: WS first connect creates bot with real token; then register (plugin, same `provider_bot_ref`) ⇒ same `bot_uuid`, capabilities merged, **token preserved** (unchanged), WS connection still present (`ws_connection.is_some()`), no binding.
5. **Anti-hijack**: attempt WS connect with `bot_id = <gateway provider_bot_ref>` (which has a binding) ⇒ `reject_provider_delivery_websocket` ⇒ rejected. Attempt WS connect with `bot_id = <random existing real-token bot>` empty token ⇒ `AlreadyRegistered`/`AlreadyConnected`, not promote.
6. **Idempotent re-register (plugin)**: register same `provider_bot_ref` twice ⇒ same `bot_uuid`, no duplicate bots.
7. **Observability**: each branch emits a log line carrying `provider_id`/`provider_bot_ref`/`bot_id`/`token_preview`/branch.

## 6. Open questions

1. Plugin-mode response `bot_runtime_token`: return the `MOCK_` placeholder (for provider audit) or `None`? Default: return `None` with a `message` explaining the plugin obtains its token via WS connect. Pending user confirmation.
2. Whether to also record `connection_mode` as a `bot_info` audit-only field (not used by behavior). Default: **no** (YAGNI). Pending user confirmation.
3. Whether delete/list/attributes provider endpoints (which key off the binding) should gain a registry-fallback for plugin bots (which have no binding). Default: **no this round** — documented limitation; plugin bots are managed via WS/onboard lifecycle, not the binding-keyed provider-admin endpoints. Pending user confirmation.
