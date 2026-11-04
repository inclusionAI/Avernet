# E2E cleanup and retained-data contracts

## Evidence boundary

Source audit: `codex/e2e-multi-env-spec` at
`bbf4e5298e2ef0de1d8ecf56e2fb3beda8996dfa` (2026-10-09). This is
source-level evidence, not the deployed preproduction/production contract.
No remote API mutation, business-row query or database cleanup was performed.

The retained, stopped local Run `observed-m2grw32h` independently illustrates the
boundary: read-only SQLite counts found zero Groups, Sessions and Files, but
11 messages belonging to its three ledger-recorded, deleted Groups. No message
content was queried. This is **one local observation**, not a universal count or
proof of any remote retention period.

`cleanup=PASS` means the current Run's registered resource operations converged
under the API/ownership contract. It does not certify that all dependent rows,
history, object-store replicas, backups or audit records have been erased.
The runner must never turn this gap into a database/table/bucket-wide delete.

## Retained admission generations

Retained remote execution admits exactly two Profile compositions
(`bcs_e2e.retained_remote.execution_generation`):

- `gateway-human-view` (G): gateway entrypoint, reviewed `authorized-human-view`
  identity binding, exactly the Cookie header reference - the historical shape,
  byte-compatible with every previously frozen review and Run.
- `bcs-direct-dual-header` (D): bcs-direct entrypoint with NO explicit identity
  binding (implicit native identity discovery; the legacy business adapters bind
  their own reviewed principals - an explicit NATIVE binding remains pilot-blocked)
  and both the driver-Bot `Authorization` and Human `Cookie` references.

The retained and retained-File review contracts accept an optional
`execution_generation` declaration; a declared value must equal the Profile's
judged generation, and undeclared reviews stay valid with the generation derived
from the Profile and reported in the frozen summary. Vetoed compositions include
gateway with a Bot token, gateway without the Human-view binding, bcs-direct with
only Cookie, bcs-direct with an explicit NATIVE or Human-view binding, and
singlebox/mock.

Changing generation changes nothing about quotas, journal targets or pool
bindings: retained-resource accounting, the raise-group-limit amendment and the
journal target binding apply identically per generation. Admitting generation D
also does not by itself close the journal-target rebinding or quota-headroom gaps.

## Audited resource matrix

Only normal Group, Session and File cleanup handlers are currently admitted.
The other rows below are migration constraints, **not new cleanup permissions**.
All paths are relative to the repository root.

| Resource | API / source behavior | Retention and migration constraint |
|---|---|---|
| Normal Group | Coordinator-authorized deletion; store deletes Group and participants. Runtime wrapper then cancels runs, deletes listed Sessions and runtime binding. | Parent deletion is not an atomic child/history purge. Track and clean Files and Sessions explicitly before Group deletion; propagate cleanup failures. Definitions, run history, audit and messages are not thereby proven erased. |
| DM Group | Create-or-reuse returns an explicit `created` flag. Delete, terminate and member removal reject DM. | Existing pair may be reused: never label it newly owned solely because an ID was returned. Use dedicated identities/pairs and an approved retention/quota plan. No fallback Group deletion or SQL purge. |
| Session | Public V1 selects Human/owned-Bot management authority and delegates to session deletion. Session wrapper cancels active collaboration runs first; store removes participants and Session row. | Public V1 does not invoke file cleanup. Legacy Session DELETE additionally invokes **best-effort** file cleanup after Session deletion and can return success despite a cleanup error. Neither path establishes message/history erasure. Delete known Files before their parent. |
| File / upload | For Ready/Pending/Failed rows, exact File deletion attempts backend cleanup before dropping metadata and retains the row on backend failure. Missing metadata and the Deleting branch can return without a backend deletion probe. Share URL expiration is a credential lifetime, not object retention. | Verify exact File API outcomes independently. The pending-upload sweeper handles expired Pending uploads and marks them Failed; it is not a general Ready-object/history reaper. Backend lifecycle, replicas and physical erasure remain environment-specific. |
| Message history | `MessageRepoPort` provides append/query/read operations, not deletion; inspected Session/Group store deletion does not delete `bcs_messages`. | Historical records can remain after API parents disappear. No message-history purge/TTL established in the inspected paths. A `Deleted` enum variant or delivery expiry is not proof of history reclamation. |
| Provider | Provider enable/disable updates `disabled`; no Provider DELETE in the inspected repository/route surface. | Provider row, credentials, bindings and child resources remain. A read-cache TTL is not retention. Do not disable a borrowed operational Provider. |
| Provider-backed Bot | Provider-admin deletion uses `provider_bot_ref` even where the legacy route placeholder says `bot_uuid`; it disables an existing binding and soft-deletes the Bot (the allowlisted legacy fallback has no binding). Channel cleanup is awaited later in the same delete request; failure propagates after earlier mutations. | Bot/binding history remains. Current store returns memory-removal OR DB-delete success, so the response alone cannot prove a durable tombstone. Require independent persisted-state verification before migrating this operation; no new handler is enabled here. |
| Organization / membership | Organization PATCH can disable the organization. Membership DELETE disables the member row; it does not delete the Bot. Managing Provider must remain active; member mutation also requires an enabled Organization. | Retained rows/roles are not physical deletion. For authorized newly owned resources, child operations must precede parent disabling; never use this as an automatic destructor for borrowed fixtures. No persistent-record reaper established. |
| Collaboration definition/version/blob | Patch persists a new version then rebinds the Group; failed rebinding can retain the candidate. No delete/archive operation in the inspected repository. | Old versions and blobs remain. Do not infer expiry from record status, and do not delete shared definitions by name/prefix. Require a retention owner and approved disposition before migration. |
| Collaboration Run/node/snapshot | Cancel makes eligible active runs aborted; terminal cleanup retires unfinished work/leases while retaining saved facts and payloads. | Cancelled/terminal is not deleted. Group cleanup removes its runtime binding, not definition/run/audit history. Human-input history can also become unreadable after its original Session is deleted; unreadability is not erasure. |
| Internal collaboration audit | `bcs_collaboration_events` is append/read history; no delete/reaper established. | Retained. It is not the public Eventing store and must not inherit that store's TTL assumptions. |
| Public Events/deliveries/attempts | Internal retention worker can delete eligible expired Events and dependencies, gated by unresolved targets/delivery states. Default event retention is 30 days; worker is wired hourly with Event/target selection limits of 100 when Eventing is enabled (not a 100-row total-delete bound). | Conditional eventual reclamation, not a hard deletion deadline or runner authority. Effective deployment configuration, worker health and eligibility must be verified separately. |
| Event-subscription audit/revisions | Inspected retention implementation removes public Events/targets/deliveries/attempts, not subscription audit/revision history. | Do not apply Event TTL to audits. An `audit_limit` input does not itself establish implemented audit deletion. |

## Primary source index

- Normal Group/DM: `apps/bcs/crates/services/bcs-group/src/application/management.rs`
  (`create_dm`, `delete_group`, `terminate_group`, `GroupManagementWithRuntimeCleanup`);
  `apps/bcs/crates/services/bcs-group-store/src/lib.rs` (`delete_group_from_mysql`).
- Public Session delete: `apps/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
  (`SessionService::delete`); legacy route:
  `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs` (`delete_session`).
  Runtime wrapper: `apps/bcs/crates/services/bcs-session/src/application.rs`;
  row removal: `apps/bcs/crates/services/bcs-session-store/src/mysql.rs` (`delete`).
- Files: `apps/bcs/crates/services/bcs-session-file/src/service.rs`
  (`delete_all_for_session`, `cleanup_backend_for_row`, `sweep_expired_pending`).
- Messages: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/message.rs`;
  `apps/bcs/crates/services/bcs-message-store/src/mysql.rs`.
- Providers: `apps/bcs/crates/adapters/http/bcs-http/src/router.rs`;
  `apps/bcs/crates/adapters/http/bcs-http/src/routes/providers.rs`;
  `apps/bcs/crates/services/bcs-bot/src/core/provider_core.rs`;
  `apps/bcs/crates/services/bcs-bot/src/application/provider.rs` (`delete_provider_bot`);
  `apps/bcs/crates/services/bcs-bot-store/src/provider.rs`;
  `apps/bcs/crates/services/bcs-bot-store/src/lib.rs` (`soft_delete_in_db`, `soft_delete`).
- Organizations: `apps/bcs/crates/services/bcs-organization/src/application.rs`;
  `apps/bcs/crates/services/bcs-organization/src/core.rs`;
  `apps/bcs/crates/services/bcs-organization-store/src/lib.rs`;
  `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/organization.rs`.
- Definitions/runs/audit: `apps/bcs/api-contracts/v1/openapi/state-machine-runs.yaml`;
  `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/collaboration.rs`;
  `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs`
  (`patch_group_collaboration_definition`, `delete_group_runtime_state`, cancellation);
  `apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs`;
  `apps/bcs/crates/services/bcs-collaboration-store/src/terminal_cleanup.rs`.
- Eventing: `apps/bcs/crates/services/bcs-event-store/src/db/retention.rs`;
  `apps/bcs/crates/services/bcs-event-store/src/recorder.rs`;
  `apps/bcs/crates/bootstrap/bcs/src/eventing_wiring.rs`;
  `apps/bcs/crates/service-api/bcs-config-api/src/eventing.rs`.

## Required disposition before expanding a continuously running suite

For each retained kind, record the exact Run-owned ID or verified parent scope,
environment, origin (`created` versus `borrowed`/`reused`/`unknown`), responsible
owner, retention reason, review/expiry time, expected volume/quota and a real
reclamation procedure or explicit manual review action. An expiry label alone
is not a reaper. If no safe procedure exists, leave the new Case blocked; do not
invent one or silently classify retained data as removed.

Known implementation constraints needing separate review include Provider-Bot
persistence failure handling, legacy versus public Session cleanup differences,
Group deletion preceding runtime cleanup, and differences between documented
Human cancellation requirements and Bot-only runtime authorization. None of
these grants additional runner permissions or changes the product in this audit.
