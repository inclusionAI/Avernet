# BCN Integration Plan — Group Context API

- **Date:** 2026-09-20
- **Status:** Draft (pending review)
- **BCS Side Ref:** `specs/2026-09-09-group-context-api/plan.md`

---

## 1. Decision: WS Frame Tool (not CLI)

### Why not CLI

BCS CLI (`bcs-cli`) calls BCS over HTTP. The bot's LLM would pass `group_id`,
`session_id`, `run_id` as CLI arguments. These values are **not trusted** —
the LLM could fabricate any group or session and read/write another group's
context. The `actor_id` is the only parameter that can be verified via token,
but even with a valid `actor_id`, a malicious or confused LLM could:

1. Pass a different `group_id` to read another group's private context.
2. Pass a stale `session_id` to read expired session state.
3. Pass a fabricated `run_id` to pollute audit lineage.

**CLI mode is rejected** because `origin.group_id` / `origin.session_id` /
`origin.run_id` must be filled by BCS infrastructure, never by the LLM.

### Why WS Frame Tool

In the WS frame (BCN plugin tool) model:

- BCN registers a tool (e.g. `bcs_group_context_status`) in `core.ts`.
- The LLM chooses *when* to call the tool and passes *business parameters*
  (`domain`, `key`, `content`, `query`) — these are safe because they are the
  LLM's own operational choices.
- `origin` parameters (`tenant_id`, `group_id`, `session_id`, `run_id`,
  `actor_id`) are **never** passed by the LLM. They are filled by BCN from
  in-process memory Maps populated by BCS `chat.send` frames.

This is the same pattern used by the existing 4 BCN tools (`bcs_route`,
`bcs_assign_task`, `bcs_send_task_message`, `bcs_task_complete`).

---

## 2. Origin Parameter Provenance

Every `origin` field has a single, auditable source. None come from the LLM.

| Origin field | Source | Populated by | Trust model |
|---|---|---|---|
| `tenant_id` | BCS config / deployment env | BCN config or fixed constant | Deployment-level; shared by all bots in tenant |
| `group_id` | `sessionKeyToGroupId` Map | BCN `inbound-handler.ts` at `chat.send` arrival | Filled when BCS sends `chat.send` with `group_id`; LLM never sees the `chat.send` frame |
| `session_id` | `sessionKeyToBcsSessionId` Map | BCN `inbound-handler.ts` at `chat.send` arrival | Filled from `GroupContext.session_id` in BCS frame |
| `run_id` | `resolveActiveRunId(sessionKey)` | BCN `inbound-handler.ts` tracks active run per session | Resolved from OpenClaw's run tracking; LLM can't fabricate |
| `actor_id` | `bot_uuid_from_headers()` | BCS HTTP route from `X-BCS-Bot-Token` / `Bearer` token | Token-authenticated; LLM can't impersonate another bot |

### How the Maps are populated

```
BCS WS Gateway                          BCN Plugin (inbound-handler.ts)
     │                                        │
     │  ── chat.send frame ──►                │
     │     { group_id, session_id,            │
     │       session_context, ... }           │
     │                                        │  rememberTaskToolSession()
     │                                        │    sessionKeyToGroupId.set(sk, groupId)
     │                                        │    sessionKeyToBcsSessionId.set(sk, sessionId)
     │                                        │
     │                                        │  (later, when LLM calls tool)
     │                                        │    const groupId = sessionKeyToGroupId.get(sessionKey)
     │                                        │    const sessionId = sessionKeyToBcsSessionId.get(sessionKey)
     │                                        │    const runId = resolveActiveRunId(sessionKey)
     │                                        │
     │  ◄── HTTP POST /groupcontext/status ── │
     │       { tenant_id, group_id,           │
     │         session_id, run_id, actor_id } │
```

---

## 3. BCN Side Changes

### 3.1 New files

| File | Purpose |
|---|---|
| `src/group-context-tools.ts` | Tool schemas + handler functions for 4 group context tools |

### 3.2 Modified files

| File | Change |
|---|---|
| `src/core.ts` | Register 4 new tools via `api.registerTool()` |
| `src/inbound-handler.ts` | Export `sessionKeyToGroupId`, `sessionKeyToBcsSessionId` (or add accessor functions) for use by tool handlers |

### 3.3 Four Tools

Each tool follows the existing pattern: `api.registerTool()` with a probe
function that checks `channel === 'bcs'` and `sessionKey` presence, then
returns a tool descriptor with `execute`.

#### 3.3.1 `bcs_group_context_status`

```yaml
Tool name: bcs_group_context_status
Label:     "BCS Group Context Status"
When:      Always active in BCS sessions (no routing-mode restriction)
Probe:     channel === 'bcs' && sessionKey present
Params:
  domain:   string (optional) — filter by domain
  limit:    number (optional, default 20)
Implementation:
  1. Resolve origin from Maps (group_id, session_id, run_id, actor_id)
  2. POST /groupcontext/status to BCS HTTP
  3. Return contexts[] + templates[]
```

#### 3.3.2 `bcs_group_context_create`

```yaml
Tool name: bcs_group_context_create
Label:     "BCS Group Context Create"
When:      Always active in BCS sessions
Probe:     channel === 'bcs' && sessionKey present
Params:
  template_id: string (required) — policy template id
  domain:      string (required)
  key:         string (required)
  content:     string (required) — free-form text or JSON
Implementation:
  1. Resolve origin from Maps
  2. POST /groupcontext/createByTemplate to BCS HTTP
  3. Return { entry_id, version, created_at_ms }
```

#### 3.3.3 `bcs_group_context_update`

```yaml
Tool name: bcs_group_context_update
Label:     "BCS Group Context Update"
When:      Always active in BCS sessions
Probe:     channel === 'bcs' && sessionKey present
Params:
  entry_id:          string (required)
  new_content:       string (required)
  expected_version:  number (optional) — CAS
Implementation:
  1. Resolve origin from Maps
  2. POST /groupcontext/updateContent to BCS HTTP
  3. Return { old_entry_id, new_entry_id, new_version }
```

#### 3.3.4 `bcs_group_context_retrieve`

```yaml
Tool name: bcs_group_context_retrieve
Label:     "BCS Group Context Retrieve"
When:      Always active in BCS sessions
Probe:     channel === 'bcs' && sessionKey present
Params:
  domain:  string (optional)
  query:   string (optional) — semantic search query
  limit:   number (optional, default 20)
Implementation:
  1. Resolve origin from Maps
  2. POST /groupcontext/retrieve to BCS HTTP
  3. Return { items: [{ entry, relevance_score }] }
```

### 3.4 HTTP client for BCS calls

The tool handlers call BCS HTTP endpoints. BCN already has an HTTP client
pattern in `api.ts` (`BcsClient`). The group context tools reuse this:
- Auth: `BCN_BOT_TOKEN` env var as `Bearer` token
- Base URL: from BCS channel config (`channels.bcs.bcsUrl`)

### 3.5 Exported accessors from inbound-handler.ts

Add three small exports so tool handlers can read the Maps:

```typescript
// In inbound-handler.ts — already exists:
export function resolveActiveRunId(sessionKey: string): string | undefined;

// Add:
export function resolveBcsGroupId(sessionKey: string): string | undefined {
  return sessionKeyToGroupId.get(sessionKey);
}
export function resolveBcsSessionId(sessionKey: string): string | undefined {
  return sessionKeyToBcsSessionId.get(sessionKey);
}
```

---

## 4. BCS Side Changes (complete in this PR)

All BCS-side changes are already done in this branch:

| Layer | File | Status |
|---|---|---|
| Domain types | `crates/contracts/bcs-domain/src/group_context.rs` | Done |
| Repo port | `crates/service-api/bcs-service-api/src/port/repo/group_context.rs` | Done |
| Core service trait | `crates/service-api/bcs-service-api/src/core/group_context.rs` | Done |
| Application service trait | `crates/service-api/bcs-service-api/src/application/group_context.rs` | Done |
| Noop impl | `crates/services/bcs-group-context/src/lib.rs` | Done |
| HTTP route | `crates/adapters/http/bcs-http/src/routes/group_contexts.rs` | Done (status only) |
| HTTP state injection | `crates/adapters/http/bcs-http/src/state.rs` | Done (field + setter) |
| Bootstrap wiring | `crates/bootstrap/bcs/src/main.rs` | Pending |

---

## 5. End-to-End Demo Path (after wiring)

```
1. Start BCS (with NoopGroupContextRepo → returns empty lists)
2. curl POST /groupcontext/status
   -H "Authorization: Bearer <valid-bot-token>"
   -d '{"tenant_id":"t1","group_id":"g1"}'
   → 200 { "contexts": [], "templates": [] }
```

This proves:
- The HTTP route is wired
- `actor_id` is resolved from the auth token (not from the body)
- The application → core → repo call chain works end to end
- Origin injection is correct

---

## 6. Open Questions

1. **Tenant ID resolution.** Where does BCN get `tenant_id`? Options:
   - BCS config (`channels.bcs.tenantId`)
   - Hardcoded default for single-tenant deployment
   - Inferred from bot's provider/org membership

2. **Store implementation.** The NoopGroupContextRepo is a placeholder.
   The real store (SQLite/MySQL-backed) will be built in a follow-up PR
   per `store-plan.md`.

3. **Policy template management.** First release has no CRUD API for
   templates. Templates must be inserted directly into the DB during dev.
   Admin API for templates is scoped to a later phase.