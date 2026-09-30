# V1 Create-Group: `originator` with caller↔originator authorization

Status: design, pending review
Date: 2026-08-19
Scope: OpenAPI V1 `POST /openapi/v1/collaboration/groups` (Normal collaboration variant)

## Goal / Problem

The V1 create-group handler (`crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs:67`
`create_group`) hardcodes `originator = caller principal`
(`crates/application/v1/bcs-app-group/src/lib.rs:670`) and authorizes the driver via
`ensure_collaboration_eligible(driver_bot_uuid)` — the "caller↔driver" check
(`crates/application/v1/bcs-app-group/src/lib.rs:628`).

Legacy `POST /groups` lets the client supply `originator` and authorizes caller↔originator
(legacy `authorize_originator`, `crates/services/bcs-group/src/application/management.rs:286`),
with per-participant reachability anchored on the originator. We want V1 to:

- accept an optional `originator` (default to caller principal when omitted);
- authorize caller↔originator (drop caller↔driver);
- keep the V1 facade as the single owner of this stricter-than-legacy gate (Approach A).

## Non-goals

- Do not change per-participant reachability (stays originator-anchored via the production
  legacy core branch).
- Do not enable `for_v1_openapi()` in production (test-only today; not in scope).
- Do not add legacy-only fields (`service_spec`, `routing_policy`, `visibility` override,
  `start_initial_run`, `auto_start_on_service_invocation`). Those parity gaps are tracked
  separately.
- DM groups (`Dm` variant / `create_dm`) unchanged. `originator` does not apply to DM.

## Decision

For the V1 `Normal` collaboration create-group request, add an optional `originator` field.
Facade-level authorization rule:

> The authenticated human caller may set `originator` to **itself** (`human_<staff_no>`)
> **or to a registered Bot it owns** (`bot.created_by == user.id`). Anything else → `403`.

This is **stricter than legacy** `authorize_originator` (which lets any human designate any
originator). It is a V1 gate only; legacy behavior is untouched.

When `originator` is omitted, fall back to the caller principal (`human_<staff_no>`) —
preserving current V1 behavior.

The `ensure_collaboration_eligible(driver)` call in `create_collaboration` is **removed**
(no caller↔driver check). The `create_dm` path keeps its
`ensure_collaboration_eligible(target)` call (lib.rs:1015).

## Per-participant reachability (unchanged, originator-anchored)

Production V1 runs the legacy core branch (`for_v1_openapi` is test-only; `server.rs:1870` and
`:2382` do not set it). Core per-participant reachability anchors on `originator`
(`crates/services/bcs-group/src/application/management.rs:965-978`):

| originator | gate on driver & other participants |
|---|---|
| human caller (default / `originator == caller`) | bot must be `public` OR `created_by == <human staff_no>` — no friendship |
| owned bot | `friend(originator_bot, participant)` via `ensure_reachable` (`management.rs:365`) |

Driver is treated as an ordinary participant in this loop (no driver-specific check). Matches
legacy.

## Changes

### 1. Wire DTO + service-contract field

`crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`:

- `CreateGroupRequest::Normal` (line 186): add `originator: Option<String>`. The struct keeps
  `#[serde(deny_unknown_fields)]`; a missing field deserializes to `None`.
- `From<CreateGroupRequest> for CreateGroupSpec` (line 200): pass
  `originator: value.originator` into `CreateCollaborationGroup`.

`crates/service-api/bcs-service-api/src/application/v1/group.rs`:

- `CreateCollaborationGroup` (line 266): add `pub originator: Option<String>`.

### 2. Facade: resolve + authorize originator, drop driver-eligibility

`crates/application/v1/bcs-app-group/src/lib.rs`, in `create_collaboration` (line 623):

- **Remove** the call at lib.rs:628:
  ```rust
  self.ensure_collaboration_eligible(&principal, &request.driver_bot_uuid, "driver_bot_uuid").await?;
  ```
- **Replace** the originator assignment at lib.rs:670
  (`let originator = principal_actor_id.clone();`) with:
  ```rust
  let originator = request.originator.take().unwrap_or_else(|| principal.actor_id());
  self.authorize_originator(&principal, &originator).await?;
  ```
- The existing `let principal_actor_id = principal.actor_id();` (lib.rs:656) stays; originator
  resolution may use `principal.actor_id()` or `principal_actor_id.clone()`.
- The `originator` local still feeds `GroupCreateCommand { originator: Some(originator), .. }`
  (lib.rs:782), unchanged.

### 3. New helper `authorize_originator`

In `bcs-app-group/src/lib.rs`, alongside `ensure_collaboration_eligible`. Mirrors
`authorize_bot_resource` (`crates/application/v1/bcs-app-invitation/src/lib.rs:115`) and the
load-and-convert-not-found pattern from `resolve_view_actor`
(`crates/application/v1/bcs-app-group/src/lib.rs:118-123`):

```rust
/// The authenticated human caller may act as itself or as an owned Bot originator.
async fn authorize_originator(
    &self,
    principal: &Principal,
    originator: &str,
) -> Result<(), ApplicationError> {
    if principal.actor_id() == originator {
        return Ok(());
    }
    let bot = self.load_bot(originator).await.map_err(|error| match error {
        ApplicationError::NotFound { .. } => ApplicationError::forbidden(format!(
            "Authenticated User cannot act as originator '{originator}'"
        )),
        other => other,
    })?;
    if bot.actor_kind != ActorKind::Bot {
        return Err(ApplicationError::invalid(
            "invalid_originator",
            "originator must be a Bot Actor",
        ));
    }
    if let Principal::Human(human) = principal {
        if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
            return Ok(());
        }
    }
    Err(ApplicationError::forbidden(format!(
        "Authenticated User cannot act as originator '{originator}'"
    )))
}
```

Semantics:

1. `originator == caller` → OK.
2. originator must resolve to a registered Bot (else `403 forbidden` — "cannot act as
   originator"). This covers unregistered ids and other humans (humans are not in the bot
   registry, so they fall through to the not-found→forbidden map).
3. `actor_kind != Bot` → `400 invalid_originator` (defensive; reachable only if the registry
   resolves a non-bot actor).
4. ownership `created_by == user.id` → OK; else `403 forbidden`.

### 4. Out of scope / unchanged

- `create_dm` (lib.rs:1010) and its `ensure_collaboration_eligible(target)` (lib.rs:1015):
  unchanged.
- Core `GroupManagement::authorize_originator` (`management.rs:286`): unchanged. It still
  rubber-stamps human callers (a no-op for humans); the facade gate runs first and is the
  binding constraint.
- Per-participant reachability in core (`management.rs:951-998`): unchanged.

## Error codes

| Case | Status | `code` |
|---|---|---|
| `originator` resolves to a bot not owned by caller | 403 | `forbidden` ("cannot act as originator") |
| `originator` is an unregistered id / a human that is not the caller | 403 | `forbidden` ("cannot act as originator") |
| `originator` resolves to a non-Bot actor | 400 | `invalid_originator` |
| `originator == caller` (or omitted) | — | OK |

Consistent with `authorize_bot_resource` / `resolve_view_actor` patterns.

## Test matrix

`crates/application/v1/bcs-app-group/tests/v1_group_service.rs`:

1. `originator` omitted → group created; persisted `group.originator == human_<caller>`.
2. `originator == human_<caller>` (explicit self) → OK.
3. `originator == owned bot` (`created_by == caller.id`) → OK; `group.originator == bot`.
4. `originator == bot not owned by caller` → 403 forbidden.
5. `originator == unregistered bot id` → 403 forbidden.
6. `originator == another human` (`human_<other>`) → 403 forbidden (not caller, not owned bot).
7. `originator == owned bot`, with a non-friend/non-public participant (including the driver)
   → rejected by `ensure_reachable(originator_bot, participant)` — asserts the bot-originator
   friendship gate fires in production.

`crates/adapters/http/bcs-api-http/tests/group_routes.rs`:

8. `POST /openapi/v1/collaboration/groups` body without `originator` → 200/201, behavior
   unchanged.
9. body with `originator` field → accepted (no `unknown field` error).

## Compatibility / migration

- Wire: `originator` optional. Existing clients omitting it keep current behavior.
- Service-contract: adding `originator: Option<String>` to `CreateCollaborationGroup` breaks
  the `CreateCollaborationGroup { .. }` struct literals in
  `crates/application/v1/bcs-app-group/tests/v1_group_service.rs` (≈20 sites). Add
  `originator: None` to each (semantically = current "fallback to caller"). Mechanical; same PR.
- The DTO `From` at `dto/group.rs:209` is the only production construction site to update
  beyond the struct definition.

## Rollback boundary

The change is isolated to:

- 1 DTO field + its `From` (bcs-api-http)
- 1 struct field on a service-api type
- 1 method replacement + 1 new helper in `bcs-app-group/src/lib.rs`
- test updates

No DB migration (`group.originator` already persists in `bcs_groups`). No core changes. No
legacy-route changes.
