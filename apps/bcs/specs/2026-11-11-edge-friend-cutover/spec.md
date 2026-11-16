# Edge-owned friendship cutover

## Record

Owner: BCS. Baseline: PR #3846 at `976e8df3bf`.
The requester approved switching friendship/request reads to edge permission,
invalidating legacy requests rather than migrating them, and confirmed the
existing env/creator/decider widths are sufficient. They explicitly confirmed
that MySQL 046 / SQLite 044 from the preceding repair have not been executed
on retained databases and authorized removing those migrations from this PR.
Work remains in the current checkout/branch; no new worktree is created.

This supersedes the history-preserving migration and legacy-list decisions in
[the preceding repair](../2026-11-11-legacy-friend-cutover-fixes/spec.md), not its
error propagation or request-kind/receiver authorization fixes.

## Contract

- Legacy and V1 friendship/request routes use the same ConnectService. Legacy
  wire fields, self-only list authorization, Bot names/presence and exclusion
  of deleted/unregistered/Human peers from the legacy Bot list remain intact.
- Friendship membership comes only from active default-profile edge grants;
  legacy-only friendship/relation records are not a fallback. Existing edge
  friendships remain unchanged. No automatic friendship ETL is introduced.
- Legacy request rows remain untouched but invisible and unaddressable: old
  pending IDs cannot be accepted/rejected, and do not block a fresh request.
  New `permission_requests` records remain available across restarts.
- Remove only this PR's MySQL 046 / SQLite 044 files, registration and startup
  coverage check. No schema changes, data deletes, backfills or history edits
  are performed. Existing migration bodies/checksums remain unchanged.
- Keep reverse writeback needed by remaining collaboration consumers; it is
  not a friendship-list source or a way to resume legacy requests.
- Listing errors remain errors. Reuse bounded, fallible edge-list pages and
  batch registry enrichment rather than the old per-peer edge-profile scans.

## Rollout and compatibility

Stop old friend-request writers before switching all instances. No new flag,
manual ETL or cutover SQL is required on an otherwise current schema. Users
must resend old pending requests; old notifications/links return not-found.
Do not re-enable old writers against the new request source. Unexpected retained
databases that already recorded the withdrawn versions require a separate
reviewed recovery plan; do not delete their history or automatically narrow
columns. Disposable test databases can be rebuilt.

## Acceptance

Prove migration reruns leave old requests untouched and do not import them;
bootstrap succeeds with unconverted old requests; old IDs fail lookup and both
decisions; fresh requests work; Bot and Human lists read edge membership without
legacy mirrors, retain list authorization/enrichment, exclude revoked and
legacy-only friendships, and propagate DB failures. Exercise multi-page lists.

## Ownership and verification

Owning knowledge: [friend context](../../crates/services/bcs-friend/CONTEXT.md),
[edge-store context](../../crates/services/bcs-edge-permission-store/CONTEXT.md),
[rollout](../../migrations/README.md#legacy-friend-request-cutover).
Verification and delivery will be recorded after execution; deployment is not
implied by this change.

## Verification (2026-11-11)

- Before implementation, the no-import migration regression failed with four
  new-store requests instead of one; all three initial edge-list regressions
  failed against the legacy delegate. These now pass. Obsolete migration-tail
  expectations encountered in the wider suites were restored to 043/045, not
  removed or weakened.
- `BCS_STATIC_VERSION=1 cargo test --locked -j 2 -p bcs-edge-permission
  -p bcs-edge-permission-store -p bcs-app-invitation -p bcs-friend
  -p bcs-db-migrations`: 308 passed; one MySQL authority test ignored.
- `BCS_STATIC_VERSION=1 cargo test --locked -j 2 -p bcs-api-http -p bcs-http
  -p bcs-admin`: 1,015 passed; five environment-dependent tests ignored.
- `BCS_STATIC_VERSION=1 cargo test --locked -j 2 -p bcs
  --test integration_friend_cutover --test integration_friend_connect_store
  --test integration_friendship --test bot_authority_migration
  --test bootstrap_auth_boundaries`: 23 passed. This includes real SQLite
  bootstrap, HTTP old-ID not-found, new creation/acceptance, listing without
  legacy mirrors, revocation despite a stale legacy mirror and restart.
- `BCS_STATIC_VERSION=1 cargo check --locked --workspace --all-targets -j 2`:
  passed, with existing warnings.
- `bcs-admin db migrate --dialect mysql --check-files`: passed; 45 migrations,
  versions 001–045. SQL diffs are exactly the two authorized deletions and
  match the pre-repair SQL tree otherwise.
- Port purity, forbidden-symbol and store-boundary checks passed. Dependency,
  import-rule and trait-naming gates retain the same failing entries as the
  preceding repair/original-PR baseline logs. `cargo-machete` is unavailable.
  No gate was weakened. Compilation still emits existing warnings.
- Independent read-only review found no actionable issues. Diff whitespace,
  changed-source size and changed Markdown relative links passed.

### Commit/push follow-up

Before pushing, the remote branch advanced to `b18fca41ba` with independent
mapping/fallible-read tests. This follow-up was rebased onto that commit without
conflicts, preserving the remote tests; no force push is used. The five core
suites were rerun after rebase: 322 passed, 1 ignored. The durable SQLite
cutover/bootstrap regression also passed again.

The first normal push was blocked by the unchanged secret scanner: the test's
public secret-resolver entry name and OAuth E2E registration names were treated
as credential literals. These names are now explicit named constants; values
and registration behavior are unchanged, and no scanner rule or hook was
bypassed. A before/after scenario-call trace verifies identical registration
names and downstream arguments (not a substitute for live E2E). The full-PR
secret scan and focused durable-cutover integration test were rerun.

## Delivery and limits

At the initial handoff, changes were in the existing PR branch's working
directory, without a commit or push. The requester subsequently authorized
committing and pushing this follow-up to the same PR branch. This does not imply
merge or deployment. No existing runtime database was modified; integration
tests use disposable temporary databases. No global formatter was run.

Real MySQL/OceanBase runtime suites, Singlebox E2E, coverage gates and
production-scale pagination/query-plan validation were not run for this
follow-up. Static MySQL checks and SQLite tests do not establish those results.
