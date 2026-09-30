# BCN Integration Plan — Group Context API

- **Date:** 2026-09-20
- **Status:** Implemented & verified (singlebox E2E)
- **BCS Side Ref:** `specs/2026-09-09-group-context-api/plan.md`

---

## 1. Design Decision: WS Frame Tool (not CLI)

BCS CLI mode is rejected because `origin.group_id` / `session_id` / `run_id`
would be LLM-supplied CLI arguments — a malicious or confused LLM could
fabricate any group or session and read/write another group's context.

In the WS frame (BCN plugin tool) model, the LLM only passes **business
parameters** (`domain`, `key`, `content`, `query`, `limit`). Origin parameters
are filled by BCN infrastructure from in-process Maps populated by BCS
`chat.send` frames — the LLM never sees them.

This follows the same pattern as the existing 4 BCN tools (`bcs_route`,
`bcs_assign_task`, `bcs_send_task_message`, `bcs_task_complete`).

---

## 2. Origin Parameter Provenance

Every `origin` field has a single, auditable source. None come from the LLM.

| Origin field | Plugin-side source | BCS-side resolution | Trust model |
|---|---|---|---|
| `tenant_id` | Hardcoded constant `'default'` | From request body | Deployment-level; single-tenant Phase 1 |
| `group_id` | `resolveBcsGroupId(sessionKey)` — in-process Map | From request body | Filled at `chat.send` arrival; LLM never sees the frame |
| `session_id` | `resolveBcsSessionId(sessionKey)` — same Map | From request body | Same as above |
| `run_id` | `resolveActiveRunId(sessionKey)` — OpenClaw run tracking | From request body | LLM can't fabricate |
| `actor_id` | `getActiveBcsClient().botUuid` — active BCS WS session | **`bot_uuid_from_headers()` from Bearer token** | Token-authenticated; BCS ignores body value — non-forgeable |

**Security property:** BCS re-resolves `actor_id` from the Bearer token and
ignores any `actor_id` in the request body. This makes origin non-forgeable
even if the LLM attempts to pass a different value.

---

## 3. BCN Side Changes

### 3.1 Files

| File | Change |
|---|---|
| `src/group-context-handler.ts` | **New.** Shared `bcsApiCall()` + 4 tool handlers. Resolves origin, gets token, converts BCS URL, POSTs to BCS HTTP API. |
| `src/core.ts` | Register 4 new tools via `api.registerTool()` |
| `src/channel.ts` | Export `getActiveBcsClient()` for token & botUuid access |
| `src/inbound-handler.ts` | Export `resolveBcsGroupId()`, `resolveBcsSessionId()` (already had `resolveActiveRunId()`) |
| `openclaw.plugin.json` | Add 4 tools to `contracts.tools` array (gateway requires declaration) |

### 3.2 Four Tools

| Tool | Path | LLM params | Returns |
|---|---|---|---|
| `bcs_group_context_status` | `/groupcontext/status` | `domain?`, `limit?` | `{ contexts[], templates[] }` |
| `bcs_group_context_create` | `/groupcontext/createByTemplate` | `template_id`, `domain`, `key`, `content` | `{ entry_id, version, created_at_ms }` |
| `bcs_group_context_update` | `/groupcontext/updateContent` | `entry_id`, `new_content`, `expected_version?` | `{ old_entry_id, new_entry_id, new_version }` |
| `bcs_group_context_retrieve` | `/groupcontext/retrieve` | `domain?`, `query?`, `limit?` | `{ items: [{ entry, relevance_score }] }` |

All 4 share the same `bcsApiCall()` function for origin resolution and HTTP transport.

### 3.3 Shared `bcsApiCall()` — origin resolution & transport

```
1. Resolve group_id, session_id, run_id from in-process Maps (inbound-handler)
2. Get token + actor_id from active BCS WS client (getActiveBcsClient)
3. Resolve BCS HTTP URL: prefer BCS_API_BASE_URL env var,
   otherwise convert ws://host:port/ws/bot → http://host:port
4. POST with Bearer token + body { tenant_id, group_id, session_id, run_id, ...businessParams }
```

### 3.4 Configuration requirements

- **`openclaw.plugin.json`**: 4 tools must be listed in `contracts.tools` —
  the gateway rejects undeclared dynamically-registered tools.
- **`bots.sh`**: 4 tools must be in `tools.alsoAllow` — the `coding` profile
  strips tools not in the allowlist.
- **`bots.sh`**: `BCS_API_BASE_URL` env var (e.g. `http://127.0.0.1:21000`)
  injected at gateway startup for HTTP API calls.
- **`bots.sh`**: `BCN_BOT_TOKEN` / `BCN_BOT_UUID` injected from `session.json`
  as fallback when no active WS client is available.

---

## 4. BCS Side Changes (complete in this PR)

| Layer | File | Status |
|---|---|---|
| Domain types | `crates/contracts/bcs-domain/src/group_context.rs` | Done |
| Repo port | `crates/service-api/bcs-service-api/src/port/repo/group_context.rs` | Done |
| Core service | `crates/service-api/bcs-service-api/src/core/group_context.rs` | Done |
| Application service | `crates/service-api/bcs-service-api/src/application/group_context.rs` | Done |
| Noop impl | `crates/services/bcs-group-context/src/lib.rs` | Done |
| HTTP route | `crates/adapters/http/bcs-http/src/routes/group_contexts.rs` | Done (status) |
| HTTP state injection | `crates/adapters/http/bcs-http/src/state.rs` | Done |
| Bootstrap wiring | `crates/bootstrap/bcs/src/http_adapter.rs:97-100` | Done |

---

## 5. End-to-End Verification (singlebox)

Verified in singlebox mode with 2 bots (GC-Driver + GC-Member) in a group chat:

1. BCS starts with `NoopGroupContextRepo` → returns empty lists
2. Bot LLM calls `bcs_group_context_status` tool
3. Plugin resolves origin from in-process Maps + active WS client
4. Plugin POSTs to BCS HTTP `/groupcontext/status` with Bearer token
5. BCS resolves `actor_id` from token, calls application → core → noop repo
6. BCS returns `{ contexts: [], templates: [] }` → LLM processes result

Origin log confirmed:
```
[group-context] origin: {
  tenant_id: 'default',
  group_id: 'bcs_grp_92e73165b89a4f3f88d8569688d929f5:92381ba7',
  session_id: 'bcs_grp_92e73165b89a4f3f88d8569688d929f5',
  run_id: '408d3e42-1304-4e95-ad5b-713eca773bed',
  actor_id: '<bot-uuid>',
  path: '/groupcontext/status'
}
```

---

## 6. Open Questions

1. **Store implementation.** `NoopGroupContextRepo` is a placeholder.
   Real store (SQLite/MySQL-backed) per `store-plan.md` — follow-up PR.

2. **Policy template management.** No CRUD API for templates in first
   release. Admin API scoped to a later phase.

3. **Remaining 3 HTTP routes.** Only `/groupcontext/status` is wired in the
   HTTP route. `create`, `update`, `retrieve` routes need implementation
   (BCN plugin handlers already exist and will work once routes are added).
