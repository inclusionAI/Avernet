# Flow run environment and tag markers

Owner: the public ClawWeb workflow module. This is an additive metadata contract
on existing run creation and read APIs, not a new execution purpose, identity,
authorization scope, routing selector, or filtering API.

## Stored and received values

| Field | Database definition | Omitted request field | Explicit JSON `null` |
| --- | --- | --- | --- |
| `env` | `VARCHAR(32) NULL DEFAULT 'prod'` | Store `prod` | Store SQL `NULL` |
| `tag` | `VARCHAR(255) NULL DEFAULT ''` | Store the empty string | Store SQL `NULL` |

`env` accepts any nonblank string up to 32 Unicode code points. It is not an enum:
`prod`, `test`, and caller-defined labels are valid. `tag` accepts any string up
to 255 Unicode code points, including an empty or whitespace-only string.
Booleans, numbers, arrays, objects, overlong strings, and a blank `env` are
rejected with HTTP 400. Accepted strings are not trimmed or truncated. Defaults
apply only to omission/`undefined`, never to explicit `null`.

Both top-level fields are accepted on the trusted engine's existing internal
`POST /runs`, including verified parent-related registration, and inside `run`
on the dedicated ordinary request-association registration endpoint. The
repository also applies the defaults and validation for direct callers. SQL
INSERTs name and bind both columns, including the separate parent-related path.

The existing legacy managed-fanout child start endpoint
`PUT /runs/:parentFlowId/fanout-children/:childFlowId` projects `env`/`tag` from
its existing `stateJson` object (or the JSON-object string accepted on the wire)
into the same SQL columns. No new top-level protocol fields are added. Missing
state markers default only in the row; explicit null is preserved. This path
never inserts default markers into `launchState` or alters `_fanoutIdentity`,
`startDigest`, admission, or parent/child matching. Exact replay retains the
stored row labels and advanced checkpoint. The pre-existing digest covers the
whole launch state, so a replay with changed state markers still conflicts;
the change does not exempt those fields from the legacy identity check.

These labels do **not** alter trial registration, `purpose`, request keys,
ordinary request fingerprints, or parent/child identity matching. An idempotent
retry returns/rejoins the existing winner without overwriting its labels. A
child is labelled by its supplied creation fields; the receiver does not infer
labels from its parent, current server environment, Bot configuration, or BaaS.

## State and read compatibility

For ordinary POST and parent-related registration, the receiver preserves the
submitted `state_json` **byte-for-byte**. It does not
append markers or reserialize old snapshots: deployed ClawMind clients verify
the insertion receipt against the exact state they sent. Marker-aware ClawMind
writers must make new state and row labels agree. Older state that lacks these
fields is still valid; runtime recovery applies its own compatibility handling,
not a ClawWeb database backfill. The receiver does not rewrite opaque state to
repair a caller mismatch.

Run list/detail reads return the stored nullable column values unchanged. This
includes the first public read router's authorized reader, the legacy
repository list, and scoped internal reads. Display fallbacks belong to the
frontend; they must not turn a raw `null` into an asserted historical fact.
The UI therefore renders an unrecorded `NULL` marker as `未记录`, while a
stored empty tag remains `—`; it never relabels either value as `prod`.
No new environment/tag filters or indexes are introduced.

Adding a defaulted column exposes `prod`/empty tag on old rows whose writers did
not record these fields. **Historical `prod` is a compatibility default, not
proof that a run was a real production/business execution.** Existing recorded
labels and explicit NULL values are preserved. Do not use this migration to
classify or rewrite historical runs.

## Release order and both hosts

1. **Schema first.** Provision both nullable columns on every database serving
   either host. The public migration is **v152**: `schema.ts` historically ended
   with repair v144, but database initialization additionally assembles
   snapshot/request migrations through v151. For managed databases without DDL
   privileges, an operator must review and apply the
   [MySQL provisioning script](../scripts/sql/flow-run-markers-v152.mysql.sql)
   before deploying code that reads/writes the columns. This work does not
   execute online DDL. Check actual column definitions and nullable defaults.
2. **Receivers second.** Upgrade both the Avernet/public host and the OCB host
   that consumes the public implementation. Updating only one repository or
   one deployment leaves a receiver that can discard new fields. Ensure all
   rolling instances support the fields before enabling senders. The database
   schema must remain expanded while older instances/writers still coexist.
3. **ClawMind third.** Deploy marker-aware persistence adapters and runtime state
   handling, preserving existing exact-state receipts and explicit NULL values.
   Use smoke tests against each upgraded receiver; a source revision alone is
   not evidence that a live host is upgraded.
4. **New commands last.** Release command options/launchers that send `env` and
   `tag`, then enable their UI display. Do not expose options while an old
   receiver can silently omit them. Rerun label selection is owned by the
   existing command path, not by global environment inference here.

The migration tolerates duplicate-column errors when ClawMind has already
created one or both columns or an interrupted upgrade is retried. Other DDL
errors propagate and v152 is not recorded. It intentionally does not inspect the definitions of already-present columns:
startup success after a duplicate-column signal is not schema conformance.
Existing incompatible columns need an operator-reviewed repair using the
provisioning preflight; the additive migration neither modifies nor validates
them. Do not advance `schema_version` to 152 by hand without verifying all
preceding migrations: its maximum value is the migration cursor. The generic
managed-database startup policy may skip ordinary migrations when DDL is denied;
that is not proof the required columns exist.

## Implementation and verification

- [Migration](../public/shared/server/schema-flow-run-markers.ts),
  [assembly](../public/shared/server/schema.ts), and
  [migration types](../public/shared/server/schema-types.ts).
- Historical declarations are split into `schema-v1-36.ts`, `schema-v37-74.ts`,
  `schema-v75-108.ts`, and `schema-v109-143.ts`. Their statement bytes and original
  declaration order (including existing repeated/nonascending version numbers)
  are unchanged; initialization retains its existing stable version sort.
- [Validation](../public/modules/workflow/server/services/flow-run-markers.ts),
  [repository](../public/modules/workflow/server/repositories/flow-run-repository.ts),
  [parent registration](../public/modules/workflow/server/services/parent-related-run-registration.ts),
  and [authorized read projection](../public/modules/workflow/server/repositories/run-subworkflows-repository.ts).
- [HTTP/SQL contract tests](../public/modules/workflow/server/repositories/__tests__/flow-run-markers.test.ts)
  exercise omission, NULL, validation, exact old state bytes, repository and
  public reads, and idempotent ordinary/parent registration.
- [Legacy managed-child marker tests](../public/modules/workflow/server/routes/internal/__tests__/fanout-run-markers.test.ts)
  exercise object/string state input, omission, NULL, nondefault labels, rejection
  before writes, unchanged launch digests and idempotent replay after advancement.
- [Migration tests](../public/shared/server/__tests__/flow-run-markers-migration.test.ts)
  exercise SQLite upgrades, shared/partially applied columns, replay, rendered
  MySQL/ZDAS definitions, strict error propagation and the real migration ledger.
  [Historical preservation test](../public/shared/server/__tests__/schema-history.test.ts)
  pins pre-split declarations and triggers from `d0cdd0234`.

These local tests do not establish live deployment readiness or validate DDL
locks/permissions on a target MySQL/ZDAS service. No live database is contacted.
