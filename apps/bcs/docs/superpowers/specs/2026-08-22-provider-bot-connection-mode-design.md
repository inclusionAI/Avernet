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

### 3.0 Admission gate (plugin only on allow-listed providers)

`connection_mode=plugin` is accepted **only** when the request's `provider_id` is in the server's `allowed_switch_provider_ids` configuration (`bootstrap/bcs/src/config.rs:749`, threaded as `HttpAppState.allowed_switch_provider_ids`, `state.rs:471`).

- **In the allow-list**: the handler already sets `bot_uuid = Some(provider_bot_ref.clone())` today (`providers.rs:225`), i.e. the deterministic-id invariant (IV-1) is already true for these providers. Plugin mode reuses that same precondition and same component path.
- **Outside the allow-list**: `connection_mode=plugin` is rejected. `connection_mode=gateway` is the only permitted value (and remains the default when the field is absent).

The admission check lives in the handler `register_provider_bot` (`routes/providers.rs`), before constructing `RegisterProviderBotCommand`, so the service layer never sees a plugin command from a non-allow-listed provider. Error responses are `400` (`connection_mode plugin requires an allow-listed provider`) — reusing `ProviderRouteError::bad_request`.

### 3.1 Invariants (plugin mode)

- **IV-1**: A plugin-mode bot's `bot_uuid` is exactly the request's `provider_bot_ref` (deterministic id). This is what lets a later plugin WS connect (which sends `bot_id = provider_bot_ref`) land on the same bot record. **Scope**: only true for providers in `allowed_switch_provider_ids` — which is exactly the set allowed to register plugin bots (§3.0), so the invariant is universally true for every plugin registration that BCS accepts.
- **IV-2**: The `session_token` is **not a connect credential** in plugin mode — it is a runtime token the plugin and BCS negotiate, returned via `BotConnectResponse.token` / `BCN_BOT_TOKEN`.
- **IV-3**: Provider registration of an **already-existing** plugin bot does **not** replace its `session_token` (so an already-connected plugin keeps its token and WS connection).
- **IV-4**: The reconcile that promotes a pre-registered (MOCK-token) plugin bot to a real-token connected bot happens **inside the store write lock**, not in the lock-free `connect_bot` existence check. The store lock is mutually exclusive, so two writers never truly overlap; the second always observes the first's final state.
- **IV-5**: Only providers in `allowed_switch_provider_ids` may register plugin bots (§3.0). Plugin mode is undefined for other providers and is rejected at the handler.

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

For **plugin** mode (only reached for allow-listed providers, §3.0):
- **Deterministic `bot_uuid == provider_bot_ref`** (IV-1). For allow-listed providers the handler already sets `bot_uuid = Some(provider_bot_ref.clone())` (`providers.rs:225`); plugin mode keeps that. The §3.0 admission gate guarantees plugin commands only arrive from those providers, so IV-1 holds.
- **Skip `bindings.insert_binding(...)`** entirely (the `provider_core.rs:202-222` block). → no provider_binding row → `resolve_delivery_target` ⇒ `WebSocket`, `is_provider_downlink_bot` ⇒ false ⇒ WS allowed.
- **Token rule (REVISED 2026-08-23)**: provider registration is the **only** path that writes `bcs_bots`/`session_token`. The rule is deliberately **token-agnostic** (it never asks "real or MOCK" and never needs to know what token the bot *actually holds* — BCS only reflects what is currently in the registry):
  ```text
  existing = BotRegistryCoreService::load_token(bot_uuid).await;   // memory → DB fallback (memory.rs:1337 / lib.rs:2265)
  session_token = match existing {
      Some(t) => t,            // bot exists (real OR MOCK, online OR DB-only): keep as-is, do NOT mint/replace
      None    => mock_token(), // bot truly absent (nothing in memory AND nothing in DB): mint a fresh MOCK placeholder
  };
  ```
  - **`Some(t)` (|\|one)**: pass `t` back into `register_with_owner_and_token`; the store's `existing.session_token.replace(t)` (same value) is a no-op, so the live WS connection, `token_to_bot`, and `created_by` are untouched. This branch covers W-before-P (real token), idempotent re-register of a not-yet-attached plugin bot (MOCK), and补注册 over any existing bot (real or MOCK) — uniformly: **补注册 never changes the token**.
  - **`None` (truly new)**: mint `MOCK_`. The real runtime token is minted later on the WS connect path by the `promote` branch (§3.5).
  - Why this is safer than "preserve real token specifically": the earlier draft required the registration to *detect* a real token, but after a BCS restart the bot's in-memory state is gone and BCS cannot know what token the plugin actually holds — `load_token` hitting `None` then meant "guess". This revision removes that guess: `load_token` already falls back to DB, so `Some` authoritatively means "the registry has this exact token now"; `None` authoritatively means "no record anywhere". No inference about the plugin's true token enters the decision.
- The plugin side keeps the bot's runtime token in `.bcs/session.json` (`{bot_uuid, token, bcs_url}`, written on every `bot.connect` success at `openclaw-channel-bcn/src/bcs-ws-client.ts:380-388`, reloaded via `_loadSession` at line 441-461), so a reconnect carries the previous real token T_old when the configured `bot_id` matches `session.bot_uuid` (`bcs-ws-client.ts:324-326`). This is what lets path-B (§3.5) self-heal a stale-MOCK DB: the plugin brings `bot_id = provider_bot_ref` (+ optionally T_old), BCS locates by bot_id, sees MOCK, promotes to a real token, writes it back to `bcs_bots`, and the plugin re-saves the new token to `session.json`.
- `register_with_owner_and_token(bot_uuid, capabilities, owner, &session_token)` is the write call; `session_token` computed above. The store's shared method is untouched.

For **gateway** mode: behave exactly as today (write binding; `bot_runtime_token` only for `static_bearer`/`provider_admin` auth modes, `provider_core.rs:185-189`).

### 3.3a Internal sentinel vs. external response (clarification)

Two distinct token roles in plugin mode, do not conflate:
- **External response** (`RegisterProviderBotResponse.bot_runtime_token`): for plugin mode, **always `None`** (DECIDED 2026-08-23, see Open-Risk #4 / Q2). Rationale: the real runtime token the plugin uses is returned to it from the WS handshake (`BotConnectResponse.token` / `BCN_BOT_TOKEN`), so the registration-side token is dead data; returning `None` also trivially guarantees the `MOCK_` placeholder is never exposed to the provider. (This refines locked decision 3's "no special handling" wording: plugin mode branches to `None`; gateway mode keeps the auth_mode rule.)
- **Internal `session_token` written to the registry** (§3.3): whatever `load_token` returned if the bot exists (preserved verbatim — real or MOCK, no change), else a freshly minted `MOCK_`. The `MOCK_` sentinel is keyed off only by the WS-connect promote branch (§3.5); it never leaves BCS.

### 3.4 Persistence stays provider-side only (DECIDED 2026-08-23)

Provider registration writes `bcs_bots` + `session_token` (per the §3.3 token-agnostic rule: `MOCK_` for truly-new, otherwise the existing value preserved verbatim). The **WS connect path does NOT persist on its own** (matches today's `register_streaming_connection`, `lib.rs:2405` "token in memory only, will persist on onboard"). This drops the earlier "persist token at first WS connect" plan (§3.4 of the prior draft) — the business confirmed plugin connect should not落库. The §3.5 `promote_mock` branch is the one WS-side path that writes a token back to `bcs_bots` (repairing a stale MOCK), but it is described in §3.5, not as general WS persistence.

Implication: a plugin-first bot (scenario 1) has no `bcs_bots` row until the provider later registers it; its session token lives in `token_to_bot` memory + (on onboard) disk. This is today's behavior and is unchanged. The promote branch (§3.5) still flips a MOCK placeholder to a real token **in memory** (and the provider's next registration, if any, preserves that real token write to `bcs_bots`); the WS path itself does not add a new persistence surface.

### 3.4b The three business scenarios (verified, 2026-08-23)

1. **存量 provider 没注册,bot 已 WS 连接生成 token 但未落库** — no MOCK record exists; the自助 bot operates on its memory token unaffected. Plugin connect does not persist (§3.4). ✅ no change.
2. **新 bot,provider 完成注册 + 写 MOCK token** — provider writes `bcs_bots` with MOCK. Plugin's first WS connect must carry `bot_id = provider_bot_ref` (empty token): `connect_bot` is_new arm calls §3.5's promote, locates the bot by id, detects MOCK, mints a real token, attaches ws, returns it to the plugin. ✅ (If the plugin connects without `bot_id`, the existing `new_bot_uuid()` builds an independent自助 bot that does not converge to the pre-registered ref — contract: to claim a plugin-mode pre-registered bot, connect with `bot_id = provider_bot_ref`.)
3. **存量 bot,provider 补注册(数据订正)** — under the §3.3 token-agnostic rule the补注册 **never changes the token**: if `load_token` returns `Some(t)` (whatever t is — real or MOCK), that exact `t` is written back as a no-op; only capabilities are merged. So the补救 cannot introduce a stale-MOCK-over-real situation: any token currently in the registry (memory or DB) is preserved as-is.
   - **Sub-case 3a: bot in registry with a real token** (plugin connected earlier; or present in DB) → preserved, DB stays real → reconnect-by-T_old resolves normally via `find_bot_by_token`. No MOCK is ever written. ✅
   - **Sub-case 3b: bot in registry with a MOCK token** (e.g. an earlier plugin started scenario-2 register but the plugin never connected yet; or a stored MOCK from some prior data state) → preserved as MOCK (补注册, again, does not mint or replace). Later when the plugin reconnects carrying `bot_id = provider_bot_ref` (+ optionally its T_old if `session.json` still has one), §3.5's `promote_mock` fires: locates the bot by `bot_id`, sees the MOCK, mints a real token, **writes the real token back to `bcs_bots`**, and returns it; the plugin re-saves the new token to `session.json`. ✅
   - The single "detect-MOCK → refresh" operator is §3.5 `promote_mock`; §3.3 (registration) and §3.5 (connect) never both rewrite a token, so their duties do not overlap. The plugin's persisted `session.json` token is what gives path-B its input even across BCS restarts.

Scope guardrails:
- This persistence enlargement applies when the connect mints a **new** bot (the WS-driven create path). It does not change onboard-driven persistence.
- Memory store keeps persisting on onboard exactly as now; the new persistence is additive (write the token at first connect, so a restart / a later provider registration can resolve the same `provider_bot_ref`).

### 3.5 Connect reconcile — the single connect-side change (in the store write lock)

Move the reconcile decision **out of `connect_bot`'s lock-free `repo.get`** (`bot_core.rs:712-714`) and **into `register_streaming_connection`'s write lock** (`lib.rs:2355` / `memory.rs:1478`). `connect_bot`, when given a `bot_id` that exists, no longer unconditionally returns `AlreadyRegistered`; instead it calls `register_streaming_connection`, which decides atomically under the write lock:

| Bot exists? | `session_token` state | `ws_connection` | Action (empty-token, `bot_id` provided path) |
|---|---|---|---|
| No | — | — | **Create** bot + new real token + attach ws. (plugin-first /自助) |
| Yes | `MOCK_…` placeholder | (any) | **Promote**: **persist the real token to `bcs_bots`/disk `FIRST`**; on failure return `ConnectStreamError::InternalError` (refuse the ws attach — no in-memory mutation). On success, swap in-memory `session_token`+`ws_connection` and reindex `token_to_bot` (drop the old MOCK). (provider-first, plugin arrives; scenario 2) |
| Yes | real token | none | `Err(AlreadyRegistered)`. (anti-hijack: empty-token claim of a real-token bot is refused) |
| Yes | real token | present | `Err(AlreadyConnected)`. (existing protection) |

Note: the "keep real token + attach ws" **Reconnect** path is NOT part of this empty-token table — it is the existing `reconnect_streaming(token)` path, reachable only when the connector **presents the real token** (`connect_bot`'s `is_new=false` arm via `find_bot_by_token` success, `bot_core.rs:690`). The empty-token `is_new=true` path (this table) refuses real-token bots to prevent hijack; promotion is exclusive to the MOCK sentinel. (Reconciles spec §3.5 table with §5.5 anti-hijack — Plan Open-Risk #2.)

**Promote ordering invariant (revised 2026-08-23)**: persist the promoted token to `bcs_bots`/disk **before** mutating the in-memory `bots`/`token_to_bot` view, and propagate a persist failure as `ConnectStreamError::InternalError` (resulting in a refused `bot.connect`) rather than logging+continuing. Rationale: the earlier "persist-after-mutate, log-on-failure" wording left a half-state (real token in memory, stale `MOCK_` in DB) that only surfaced as a dead old-token reconnect after a BCS restart — the promoted token, persisted in `session.json`, would no longer resolve from DB. Persist-first guarantees memory and storage either promote together or not at all; the plugin receives a connect failure and retries (DB still MOCK → eligible for the next promote). The lib `PersistentBotRepo` writes via `save_token_to_db`; the `MemoryBotRepo` writes the `[{data_dir}/{bot_id}/bot.json]` `PersistedCapabilities` via `save_token`. As a side effect, persisting before grabbing `self.bots.write()` also removes the prior deadlock (the in-memory `save_token` re-acquires the same write lock).

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

0. **Admission gate (§3.0)**: provider in `allowed_switch_provider_ids` with `connection_mode=plugin` ⇒ accepted; provider **not** in the list with `connection_mode=plugin` ⇒ `400` (`connection_mode plugin requires an allow-listed provider`); same non-allow-listed provider with `connection_mode=gateway` (or absent) ⇒ accepted (no regression).


1. **DTO**: absent ⇒ gateway; `"plugin"`/`"gateway"` parse; unknown value ⇒ 400.
2. **Gateway regression**: binding written; `bot_runtime_token` only for static_bearer/provider_admin; behavior unchanged.
3. **Plugin P-before-W**: register (plugin) creates bot with MOCK token, no binding; then WS connect with `bot_id=provider_bot_ref` empty-token ⇒ returns same `bot_uuid`, real token != MOCK, no new bot, `resolve_delivery_target == WebSocket`, `is_provider_downlink_bot == false`, **no** provider_binding row.
4. **Plugin W-before-P**: WS first connect creates bot with real token; then register (plugin, same `provider_bot_ref`) ⇒ same `bot_uuid`, capabilities merged, **token preserved** (unchanged), WS connection still present (`ws_connection.is_some()`), no binding.
5. **Anti-hijack**: attempt WS connect with `bot_id = <gateway provider_bot_ref>` (which has a binding) ⇒ `reject_provider_delivery_websocket` ⇒ rejected. Attempt WS connect with `bot_id = <random existing real-token bot>` empty token ⇒ `AlreadyRegistered`/`AlreadyConnected`, not promote.
6. **Idempotent re-register (plugin)**: register same `provider_bot_ref` twice ⇒ same `bot_uuid`, no duplicate bots.
7. **Observability**: each branch emits a log line carrying `provider_id`/`provider_bot_ref`/`bot_id`/`token_preview`/branch.

## 6. Open questions

0. **Admission of plugin mode (DECIDED)**: only `allowed_switch_provider_ids` providers may use `connection_mode=plugin`. Provider-id gate at the handler (`routes/providers.rs`), rejecting plugin-from-non-allow-listed with `400`. This reuses the same allow-list that already gives these providers `bot_uuid == provider_bot_ref` (IV-1). Confirmed 2026-08-22.
1. **DECIDED**: plugin-mode response `bot_runtime_token` gets **no special handling** — gateway's existing auth_mode rule applies unchanged; the internal `MOCK_` sentinel is never exposed to the provider. Confirmed 2026-08-22.
2. Whether to also record `connection_mode` as a `bot_info` audit-only field (not used by behavior). Default: **no** (YAGNI). Pending user confirmation.
3. **DECIDED (2026-08-22)**: do **not** touch delete/list/attributes this round. These endpoints key off the provider_binding row, so a plugin bot (no binding) is not manageable through them. Findings:
   - `delete_provider_bot` **already** has a no-binding fallback (`application/provider.rs:377-384`) gated on `allow_unbound_owner_suffixed_bot` (= provider in `allowed_switch_provider_ids`) **and** `is_owner_suffixed_bot_id(provider_bot_ref)` (must be `<prefix>:<owner>` form, `provider.rs:424`). For plugin bots whose `provider_bot_ref` is owner-suffixed, delete works today (same two preconditions plugin mode assumes). For non-owner-suffixed `provider_bot_ref`, delete returns `BotNotFound`.
   - `list_provider_bots` lists only bots with a binding (`list_bindings_by_provider`), so plugin bots do not appear.
   - `attributes` (get/patch) requires a binding (`providers.rs:587`), so plugin bots 404.
   This round ships as-is — owner-suffixed plugin bots remain deletable via the existing fallback; the rest are a documented limitation. Relaxing the fallback to cover any-form `provider_bot_ref` is deferred to a separate ask.
