# S04 Provider publishing density family

## Boundary and accounting

This family replays the two checked-in `stories.sh` functions in their actual
nested execution order. It does not run a shell, build Rust, call preprod,
refresh credentials, or edit the shared CLI/inventory seams.

The frozen map contains **98 receipt obligations**: 42 operation results and
56 field predicates. Each has a source function, line range, literal quote,
operation binding, typed predicate and expectation. The parent and helper
contain 100 explicit assertion calls. Two Provider registration credential
presence checks are uncounted admission prerequisites. The 42 status checks
are retained baseline operations, not new `require_status`/`|| return` padding.

The brief's input counts are not all consistent with this checkout:

- digest says 104 extracted clauses; provided JSON actually has 29+16=45 S04
  guards and omits the business field assertions;
- the old `local_provider_contract.py` has **32**, not 42, operations and its
  source hash predates the slug-discovery additions;
- of these 98 receipts, 32 preserve old operations, 44 expose existing inline
  field semantics as separately addressable receipts, and 22 cover previously
  unmapped slug behavior (10 operations + 12 fields). Therefore **56 additional
  receipts is not a claim of 56 semantically new tests**;
- no visibility enumeration or grey-release journey occurs in these two source
  functions. Visibility appears only as a denied attribute-write input. The
  real enumeration clauses cover candidates, members, bots, public projection
  fields and enabled/disabled values. No unsupported assertions were invented.

`build_contract.py` is a maintainer-only generator. Its output is frozen by
`CONTRACT_SHA256` in `contract.py`; source hashes and line quotes are checked
before execution. Do not refresh either silently to make a drift pass.

## Execution

The standalone entrypoint is `bcs_e2e.provider_publishes.dual_case_run`.
`register_provider_publishes_dual_case_commands` exposes the nine-argument parser
for `dual-provider-publishes-run`, but shared registration is mainline-owned.
`seam-demand.json` describes the exact pending row and counter changes.

The owned-local implementation uses the same runtime, workspace fixtures,
negative Provider sink and read-only SQLite projection primitives as
`local-provider-run`. It does **not** delegate to its stale 32-operation map or
patch that shared executor at runtime. One owned server performs all 42 HTTP
operations. Each of the 98 receipts is evaluated on its corresponding live
response; a case-level marker cannot manufacture field PASS results.

Both sheltered SHA256 pairs are mandatory before creating the state directory.
BCS runs the server; the CLI pair is admitted and fingerprinted but **not
executed** by this HTTP-only family. No cargo build is used. State must be fresh,
absolute and private. Process ownership comes only from the live child handle.
No remote or shared credentials are created or loaded. The local product mints
its normal ephemeral registration/runtime credentials; they remain in memory
and the retained product DB, never in source or published receipts. Memory
reference clearing is not secure erasure.

Preprod fail-closes honestly in three disclosed stages instead of statically
refusing. A sealed v1 profile carries no gateway origin, auth refs or driver
Bot, so no probe can be constructed: it blocks at entry with reason class
`PREPROD_PROVIDER_PUBLISHES_PROFILE_SEALED_V1`, zero network requests and its
v2 upgrade path disclosed. A v2 gateway profile first admits every declared
credential reference by NAME from the process environment (presence plus
bounded ASCII shape; values are never stored or echoed): a gap records
`PREPROD_PROVIDER_CREDENTIALS_REQUIRED` with verdict `NOT_RUN` and zero network
requests. With the named references loaded, the leg runs one live read-only
admission probe through the shared `ReadClient` (`environment.read`,
`identity.read`, driver Bot `bot.read`): a failure records `BLOCKED` with
reason class `PREPROD_PROVIDER_PUBLISHES_ADMISSION_PROBE_FAILED` and a
sanitized `error_detail` plus the sanitized evidence projections issued so far.
A passed probe still cannot execute the journey: no preprod
provider-admin write journey child is published in this wave, so the leg
fail-closes at the seam with reason class
`PREPROD_PROVIDER_PUBLISHES_JOURNEY_SEAM_PENDING`, all 98 obligations `NOT_RUN`
and the named gaps `PREPROD_PROVIDER_ADMIN_WRITE_FACE_ABSENT`,
`PREPROD_PROVIDER_REGISTRATION_PRECONDITIONS_PENDING` and
`PREPROD_PROVIDER_JOURNEY_CHILD_UNPUBLISHED` disclosed. Every branch keeps
`grants_release_credit=false`, `remote_validation=NOT_RUN` and
`resource_disposition=NO_REMOTE_RESOURCES_CREATED`; `observed_status` stays
null unless a probe read answered, and then it is the probe's own HTTP status.

## Disposition and scope of PASS

The downstream fixture deliberately rejects one delivery with 503 and starts
no engine. Local PASS proves the upstream API assertions, not positive engine
completion, remote validation, cleanup eligibility or release qualification.
The original member removal and bot retirement are business operations, not
cleanup. Their logical tombstones, Provider, organization, admin run and DB are
retained. Fixtures are compared before/after; the owned server and sink close.
On uncertain outcomes there is no retry in the same run or directory.

Live test opt-in is `BCS_E2E_S04_LIVE=1`; its fixed run ID is
`20261031-w3a-provider-publishes`. A fresh `BCS_E2E_S04_LEAF=attempt-N` preserves
all earlier evidence. Default focused tests skip the live case. Only the four
`test_provider_publishes_*` modules should run in this agent; full discovery
belongs to mainline.
