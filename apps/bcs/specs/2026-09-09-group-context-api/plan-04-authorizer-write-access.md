# Plan: Group Context Authorizer Write Access

## Approach

将 `add` 的 `collect_from` 从“必须包含创建者”的显式授权语义改为“可选的额外写者列表；创建者始终作为默认 owner”。应用契约、HTTP 请求和 CLI 参数统一改为 `Option<GroupContextPolicy>`，缺省或空列表在 add 路径落库为空策略，所有权由 `owners` 表达。`get` 对 owner 额外输出 `owners`，非 owner 继续省略；`update`、成员准入 guard、`systemset` 权限语义保持不变。

## Affected Components

- `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group_context.rs` — inbound Service API contract; make add `collect_from` optional and get item expose owner-only `owners`.
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs` — apply add creation/validation semantics and owner-only get projection.
- `apps/bcs/crates/contracts/bcs-domain/src/group_context.rs` — persisted policy semantics; no struct change, but validation interpretation shifts from unconditional “collect_from must be non-empty” to “add may persist empty, update must still provide valid non-empty policy when set”.
- `apps/bcs/crates/adapters/http/bcs-http/src/routes/group_context.rs` — HTTP add request shape.
- `apps/bcs/crates/tools/bcs-cli/src/main.rs` — CLI `add --collect-from` and JSON body construction.
- `apps/bcs/crates/tools/bcs-cli/bcs-coordination/SKILL.md` and `apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/group-context.md` — user-facing command guidance.
- Application, service API, HTTP adapter, CLI, session guard, and bootstrap integration tests — update contract expectations and add missing acceptance coverage.

## Data Model Changes

None. `GroupContextEntry.collect_from`, `owners`, and SQL columns remain unchanged. A missing/empty add policy is persisted as the existing empty `GroupContextPolicy` (`userIds: []`, no extras); no actor id is synthesized and no migration is required.

## API / Interface Changes

- `AddGroupContext.collect_from`: `GroupContextPolicy` → `Option<GroupContextPolicy>` at `application/v1/group_context.rs:59`.
- HTTP `AddRequest.collect_from`: required policy → optional deserialized policy at `adapters/http/bcs-http/src/routes/group_context.rs:50`.
- CLI `GroupContextCommands::Add.collect_from`: required `String` → `Option<String>` at `tools/bcs-cli/src/main.rs:1551`; `group_context_add_body` omits `collect_from` when `None` at `tools/bcs-cli/src/main.rs:2364`.
- `GroupContextGetItem.owners`: add `Option<GroupContextOwners>`; owner receives `Some(entry.owners.clone())`, non-owner receives `None` and the field is omitted on the wire.
- Backward compatibility: old requests that still send a policy continue to work. New requests that omit it are accepted. Existing persisted rows require no migration.

## Key Files & Functions

- `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group_context.rs:52` — change `AddGroupContext.collect_from`.
- `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group_context.rs:173` — add `GroupContextGetItem.owners`.
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:41` — `create` accepts optional external policy; `None` resolves to empty policy but `Some` passes through unchanged.
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:129` — remove actor-membership hard check; validate visible policy and only validate `collect_from` when present.
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:266` — add `owners = is_owner.then(|| entry.owners.clone())`.
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:467` — keep strict non-empty validation for update-provided policies; add path must not call it for an omitted policy.
- `apps/bcs/crates/application/v1/bcs-app-session/src/group_context_access.rs:39` — preserve group/session/round membership guards; only compile-time test fixture/command shapes follow the new optional field.
- `apps/bcs/crates/adapters/http/bcs-http/src/routes/group_context.rs:44` — optional request field and direct contract pass-through.
- `apps/bcs/crates/tools/bcs-cli/src/main.rs:1532` — optional CLI flag.
- `apps/bcs/crates/tools/bcs-cli/src/main.rs:2364` — omit key when absent.
- Tests:
  - `apps/bcs/crates/application/v1/bcs-app-group-context/tests/group_context_service.rs`
  - `apps/bcs/crates/service-api/bcs-service-api/tests/v1_group_context_application_contracts.rs`
  - `apps/bcs/crates/adapters/http/bcs-http/tests/group_context_routes.rs`
  - `apps/bcs/crates/tools/bcs-cli/src/main.rs`
  - `apps/bcs/crates/tools/bcs-cli/tests/e2e/group_context_test.rs`
  - `apps/bcs/crates/application/v1/bcs-app-session/tests/group_context_access_guard.rs`

## Dependencies

No new crate or package dependency. Existing `bcs_domain`, `serde`, CLI, HTTP, and test crates are sufficient.

## Risks & Mitigations

- **Risk:** Removing the add hard check could be perceived as unrelated broad write permission expansion.
  **Mitigation:** Only the authenticated creator gains immediate write authority through `owners=[actor_id]`; other writers still require explicit membership. Keep update authorization and membership guards unchanged, and pin these semantics in tests.
- **Risk:** Empty `collect_from` was previously invalid, so callers or tests may assume `permission_denied` / `invalid_param`.
  **Mitigation:** Update only tests tied to the specified add behavior; retain `validate_collect_from` for update-supplied policies so non-owner update semantics are unchanged.
- **Risk:** Optional transport fields can deserialize `null` as `None` and change body validation.
  **Mitigation:** Add wire/contract tests for omitted fields and explicit null; ensure explicit `userIds: []` persists as empty rather than being silently rewritten.
- **Risk:** Existing `main.rs` is near the repository file-size threshold.
  **Mitigation:** Make minimal targeted edits; do not reorder code or run global formatting.

## Alternatives Considered

- **Default collect_from to `[actor_id]`.** Rejected: conflicts with the confirmed decision that absent/empty add policy stays empty and ownership is expressed only by `owners`.
- **Make the field optional but reject explicit empty list.** Rejected: the spec requires both omitted and empty-list add requests to represent “仅 owner 可写”.
- **Expose owners to all get callers.** Rejected: owners are management metadata and should follow owner-only disclosure, like `visible_to` and `collect_from`.
- **Change the persisted entry type to `Option<GroupContextPolicy>`.** Rejected: the row already has a concrete policy representation; empty list can represent “no additional writers” without a schema migration.

## Rollout

No feature flag or database migration. Server and CLI are safe to release independently:

1. Server accepts omitted/empty add policies first.
2. CLI release makes `--collect-from` optional and omits the JSON key when not supplied.
3. Old CLI requests that still send the policy continue to work.

Users are documented to use `get` as an owner to view `owners`; `list` remains unchanged.

## Test Strategy

- Service-level tests:
  - omitted `collect_from` succeeds; owner is persisted as sole owner and `collect_from` is empty;
  - explicit empty policy succeeds and persists empty;
  - policy containing another bot grants that bot `W` access;
  - update keeps requiring old `collect_from` membership for non-owners;
  - owner `get` sees `owners`; non-owner `get` does not.
- Service API contract tests:
  - omitted/null and supplied add fields deserialize correctly;
  - get item serializes `owners` only for owner and omits it for non-owner.
- HTTP route tests:
  - add without `collect_from` succeeds;
  - add with empty list succeeds and later write semantics follow the persisted policy;
  - owner get response contains `owners`; non-owner get omits it.
- CLI tests:
  - `--collect-from` is optional;
  - absent field is omitted from the request body;
  - supplied field remains unchanged.
- Existing guard and bootstrap integration tests continue passing unchanged semantically.
- Local gates: BCS unit tests and the existing Singlebox BCS coverage path without reducing thresholds.
