# BCS E2E pilot runner — incremental implementation

This opt-in runner lives alongside the existing local E2E/coverage scripts.
Implemented framework paths include offline planning, read-only HTTP discovery,
Run/ledger preparation, single-machine pool reservation and command exclusion,
LocalMock and real owned-local execution of PRE-01/02/03, exact-ID cleanup and
JSON/JUnit/Markdown reports. None of these results qualify the full release suite.

The foreground `local-run` provisions a fresh, exclusively owned local BCS.
Detached `runtime start/stop/check/status` remain disabled: saved metadata is not
a process-ownership capability. Remote HTTP/WS/file transports exist but are not deployment-qualified; the implemented byte-transfer paths still require live deployment validation. **All remote pilot runs currently stop at required
observer admission before business writes**, because no live environment-bound
MCP provider is wired. Generic HTTP `check` remains available independently.

Creation intent and exact IDs are journaled. Unknown resources and failed runs
remain reserved for reconciliation. These client controls are not server-side
authorization: independent resource ownership verification, live integration and
full-release validation remain outstanding.

## Local usage

Requires Python 3.10+ (standard library only for this increment).
Run from this directory:

```bash
python3 runner.py plan --profile profiles/preprod.example.json
python3 runner.py plan --profile profiles/local.mock.json
python3 -m unittest discover -s tests -v
```

`plan` does not read credentials, contact a service, create resources or write
reports. It freezes three pilot case IDs and exposes their effects and assertion IDs;
All three shared Stories have opt-in real local regression; remote validation
remains NOT_RUN. `manifest.json` separately inventories all 29
legacy release entries. Inventory tests detect source registration drift.

`prepare` is now a local-only lifecycle step. It creates a private SQLite run
ledger and an exclusive fixture-pool lock; it does not contact BCS, create a
remote resource, or claim that stories are executable. An existing lock is never
stolen automatically, even if its PID is dead. Inspect the run state and release
it explicitly:

```bash
python3 runner.py prepare --profile /path/to/private-profile.json \
  --run-id e2e-20260922-001 --state-dir /private/path/e2e-20260922-001
python3 runner.py release --state-dir /private/path/e2e-20260922-001
```

The state directory and lease are local implementation details. Do not use a
shared filesystem or run two machines against the same pool until a durable
cross-runner lease service is implemented. `run` supports PRE-01, PRE-02 and PRE-03 with a prepared mock or approved remote
profile. Use `local-run` for the complete fresh real-local workflow. A real
profile requires explicit write confirmation. Preparing a lease is not running
a Story.

Copy the example to a private location outside Git, replace the target and IDs,
and arrange legitimate authentication through your approved credential manager.
The example Cookie reference is illustrative, not proof of the deployment's
supported automation authentication. An Authorization environment reference is
also supported; mock identity and client-signed Principal headers are forbidden
for remote profiles. The separate owned-local capability uses freshly generated
local test keys only.
Do not paste credentials into the profile, command line, source or test report.

```bash
python3 runner.py check --profile /path/to/private-profile.json --allow-read
```

`check` performs at most four authenticated GETs to ONE configured origin:
manifest, current Human, and the two explicitly bound Bots. Wrong environment or
Human stops subsequent reads. It never follows redirects (including SSO), retries
via direct BCS, discovers arbitrary Bots, approves requests, ensures identities,
changes friendships, or tests write permissions with a write. HTTPS verification
remains enabled; plain HTTP is limited to explicitly local loopback profiles.
Standard environment proxy configuration is respected.

Exit 0 from `check` means **DISCOVERY_PASSED only**, not that stories can run.
The JSON always contains `ready_to_execute: false` and the unverified checks.
Configuration/HTTP/binding failures exit 2. No response bodies, Cookie,
Authorization, Set-Cookie or server exception bodies enter reports.

Task 7.2's Aliyun/Docker target can be reviewed offline with:

```bash
python3 runner.py docker-binding-review --binding /private/path/aliyun-docker-binding.json
```

`docker-binding-review` validates the target form, target and runner image
digests, profile/secret/report mounts, network endpoints, TLS policy, observer
binding, and signal/timeouts. It never starts a container, accesses a registry,
reads credentials, or grants release credit. A READY result still means the next
step is live environment validation.

Current delivery priority is **preprod-first, 7.2 deferred**.  Task 7.2 is only for
the future Aliyun Docker target and is not a prerequisite for preprod closure.
A preprod validation effort can therefore stop after the existing preprod gate;
7.2 should be restored only when a real Aliyun target environment and image
digests are available.

`aliyun-docker-binding.example.json` is a placeholder-only intake contract. It
contains the required top-level keys, but remains intentionally `BLOCKED` until
real target, image, network, TLS, observer and runtime values replace the
placeholders; this prevents the template itself from being mistaken for a
validated binding.  Any `REPLACE_WITH_` value is rejected by the offline
reviewer, so a partially filled binding remains BLOCKED rather than silently
passing.

The current remote profile supports public-v1 Bot/auth routes, and either
internal-v1 or legacy manifest. Direct BCS still needs legitimate authentication:
a public face that trusts only Gateway Principal cannot be bypassed by this tool.
Other route/auth adapters will need explicit contracts; do not change the base
URL to silently work around a denied Gateway.

For local framework development, `profiles/local.mock.json` selects the
`LocalMockAdapter`. It supplies one deterministic Human and two dedicated mock
Bots, keeps friendship unmodified, uses no credential and opens no socket. This
validates Runner/Story/ledger/cleanup behavior only; it is not a BCS or Gateway
integration test. `OwnedLocalPilotAdapter` connects the same Story contract to a
live owned BCS with real HTTP/WS/file operations and read-only SQLite evidence.

## Real local pilot — no manual profile

Build the intended BCS binary first and approve its SHA256. The runner does not
build, download, select another checkout's binary, or stop an existing service.
Python 3.10+ and `lsof` are required. From this runner directory:

```bash
# Set these from your own verified build artifact:
BCS_BIN=/absolute/path/to/bcs
BCS_BIN_SHA256=<approved-sha256>
RUN_ID=local-pilot-001
STATE_DIR=/private/path/new-local-pilot-001

python3 runner.py local-run \
  --binary "$BCS_BIN" --binary-sha256 "$BCS_BIN_SHA256" \
  --run-id "$RUN_ID" --state-dir "$STATE_DIR" \
  --allow-local-processes --allow-fixture-write --allow-write \
  --confirm-run-id "$RUN_ID"
```

Use a **fresh** state directory. No `--profile`, Human account, existing Bot,
Cookie or company-network dependency is required. The command creates local mock
identity and two real registered test Bots, resolves their profile, reserves a
unique fixture pool and runs the same PRE-01/02/03 Stories. Each Case retains
its normal observer/ledger/lease checks. On the first non-PASS, remaining Cases
are not started; they stay unexecuted in the report denominator.

The foreground process always attempts bounded finalization of its own live
child, including after fixture/preparation/Story/report failures. Ctrl-C or
SIGTERM latches cancellation once; subsequent SIGINT/SIGTERM do not interrupt
reaping. A first cancellation during startup cleanup, normal shutdown or report
publication is also recorded without interrupting the bounded finalizer. Earlier
failures remain primary; cancellation prevents an otherwise successful command
from being reported as green. TERM/KILL waits total at most twice the configured stop
timeout (default 15 seconds each); this is not a whole-command time limit.
SIGKILL of the runner cannot be handled and still requires manual reconciliation.
No saved PID, port-based process kill, implicit restart, recursive data removal,
second cleanup sweep or unconditional failed-pool release is performed.

Outputs retained with private permissions:

- `profile.json`, frozen `manifest.json`, `run.json`, `run.sqlite`;
- per-Case reports and `reports/summary.json`, `junit.xml`, `summary.md`;
- `runtime/`: BCS config/database/files, fixture/process receipts and console log;
- `local-run.json`: primary outcome, separate shutdown/report failures, runner
  commit/dirty state, binary digest and report paths.

Exit **0** means `LOCAL_PILOT_PASS`: all three Cases and report integrity passed,
with graceful owned-child shutdown. Exit **2** means non-success; **130** means
cancellation. Forced termination is `FAILED_STOP`, never success. If receipt
writing fails, the non-success JSON on stdout preserves the primary outcome and
reports the secondary write error; it is not retried blindly. A failed/cancelled
Run retains its reservation where required, even when the child has stopped.

This does not expose a persistent runtime or enable generic post-stop cleanup
against an adopted process. Preserve failures for inspection. It remains a pilot:
full-release qualification is `NOT_QUALIFIED`, and Gateway/SSO, Bot inference,
BaaS/OB and production/Alibaba Cloud deployment behavior are not tested here.

## Initial pilot, not the full regression suite

- PRE-01: Human-owned normal group CRUD and persistence (local SQLite / remote OB).
- PRE-02: Human Session CRUD, issued token and real WS connection, OB state.
  LocalMock simulates token/WS; the owned-local pilot uses real WS and SQLite.
  The remote implementation is not deployment-validated.
- PRE-03: small file API lifecycle; BaaS + signed object URLs in preproduction,
  local backend in local execution. It does NOT count as CLI coverage.

The LocalMockAdapter currently executes all three pilot Stories. PRE-02
simulates the Session token and WebSocket lifecycle. PRE-03 simulates the local
file backend, byte/hash round trips, share-token download and exact deletion.
All remote pilots remain blocked before business mutations until a live,
environment-bound required observer is supplied and admitted. Local mocks preserve business
assertions without implying that real WS/BaaS/OB services were exercised.

One Human owns two dedicated Bots. No friendship is required or forbidden; it is
not changed. Bot model/runtime replies, friends mutation, BCSFuse, provider/Judge,
service-global settings and cross-owner negative authorization are out of scope
for this first pilot, not removed from the full migration backlog.

The observer profile is a declaration only in this increment. It does not contact
alisddbquery or assert a DB/environment mapping is correct. Sharing a fixture pool
across machines is not permitted until a real shared lease implementation exists.

The existing singlebox and Rust unit-test gates remain unchanged. Do not point the
legacy local E2E runner at a remote service: it still has broad cleanup behavior.

## Inspecting a blocked fixture pool

A pool reservation is deliberately not reclaimed just because its saved PID is
no longer alive. Inspect it without modifying anything:

```bash
python3 runner.py pool inspect \
  --profile /private/path/local-mock-profile.json
```

`LEASED` plus `process_alive: false` means the reservation is stale-looking,
not automatically safe to delete: inspect the matching Run ledger and use the
normal `release --state-dir` path only after confirming no resources or
processes remain. The command never exposes the lease token and never unlinks
the lock. Use a unique `policy.fixture_pool` for an independent local smoke
run rather than bypassing an unknown reservation.

## LocalMock three-case smoke

The complete first pilot can be exercised without credentials or product
processes. Use a private profile copy and a unique local fixture-pool name so a
stale reservation from another Run is not reused:

```bash
python3 runner.py prepare \
  --profile /private/path/local-mock-profile.json \
  --state-dir /private/path/e2e-local-001 \
  --run-id e2e-local-001
for case in PRE-01 PRE-02 PRE-03; do
  python3 runner.py run \
    --profile /private/path/local-mock-profile.json \
    --state-dir /private/path/e2e-local-001 \
    --run-id e2e-local-001 --case "$case" \
    --allow-write --confirm-run-id e2e-local-001
done
python3 runner.py report \
  --profile /private/path/local-mock-profile.json \
  --state-dir /private/path/e2e-local-001 --format all
python3 runner.py gate \
  --profile /private/path/local-mock-profile.json \
  --state-dir /private/path/e2e-local-001
```

Expected pilot result is `PILOT_PASS` with all three cases `PASS`,
`LOCAL_SURROGATE` evidence, and `cleanup=PASS`. The qualification command
still returns non-zero with `NOT_QUALIFIED`: local surrogate evidence is not
real BCS/Gateway/WS/BaaS/OB evidence and does not cover the frozen reviewed
release inventory. The repository integration test
`test_local_pilot_integration.py` runs this lifecycle with a temporary pool
registry and verifies that distinction.

## PRE-01 execution increment

After a private profile is prepared, PRE-01 can be exercised with the explicit
write guard and exact run-ID confirmation:

```bash
python3 runner.py run --profile /path/to/private-profile.json \
  --state-dir /private/path/e2e-20260922-001 --run-id e2e-20260922-001 \
  --case PRE-01 --allow-write --confirm-run-id e2e-20260922-001
```

The executor first performs read-only preflight, then creates exactly one normal
group with the two bound Bots, reads and patches it, and finally deletes and
verifies that exact group ID. It records intent before creation and will not sweep
by name, Driver, tenant or group list. If the create response is lost, the
resource remains unresolved and the run is non-success; recovery is explicit.
The shared executor is validated against an owned local BCS; do not point the
placeholder profile at preproduction until its actual endpoint, Human credential,
Bot IDs, environment binding and cleanup policy have been reviewed.

## Local Singlebox lifecycle — foreground execution only

`local.singlebox.example.json` reserves the profile shape, but the new runner
currently refuses `runtime start/stop` and reports `BLOCKED` for `check/status`.
Those four reserved actions perform no shell commands, port probes, process
signals or runtime directory creation. A saved `RUNNING` or `STOPPED` marker is
not process evidence. The separately authorized foreground `runtime smoke`
command below now verifies one directly owned BCS child.
The Singlebox profile is also rejected by the adapter factory rather than
silently routed through the remote adapter.

The prior implementation delegated to legacy scripts that can kill by port or
process path; private directories did **not** establish per-Run process ownership.
Those calls have been removed, not hidden behind a force option. A genuine local
adapter now uses isolated configuration/storage, port conflict blocking, a live
child handle and verified finalization through `local-run`. It cannot attach to
an existing process based on a saved receipt.

If an old `runtime.json` exists, new Story execution, destructive cleanup and
lease release are blocked even if its saved status says `STOPPED` or `FAILED`.
Preserve the marker, ledger and lease for explicit process recovery; do not delete
them or invoke the legacy stop script to bypass this guard. Offline cleanup
`--dry-run` is still available to inspect exact ledger resources. This increment
does not automatically stop or establish ownership of previously started processes.

LocalMock remains available without profiles containing real credentials.
The legacy singlebox coverage scripts and their default behavior are unchanged;
this runner disablement does not certify the safety of those older entrypoints.

## Exact cleanup command

The generic cleanup path uses the same durable ledger as the Stories. It never
lists by Driver, name, tenant or bucket. A dry run is read-only:

```bash
python3 runner.py cleanup \
  --profile /private/path/private-profile.json \
  --state-dir /private/path/e2e-local-001 \
  --dry-run
```

An actual cleanup requires the explicit write guard and exact Run ID. It deletes
only `created/live` resources in inverse dependency order (`file -> session ->
group`), verifies each exact resource, skips deletions already confirmed in the
ledger on retries, and blocks if the ledger contains an unknown or unresolved
object. Delete exceptions (including 401/403, timeouts and unclassified 404s)
remain BLOCKED; a later not-found read must not erase that error. Normalizing an
already-absent response requires an explicit endpoint adapter contract, not a
catch-all exception handler. Borrowed/reused resources are retained. A failed child
cleanup prevents its parent cleanup; the original failure remains visible in
`cleanup.json`. After partial success, the remaining-resource list is refreshed
from the ledger, so successfully deleted children are not reported as live.
Independent ownership/parent verification and durable mock recovery still need
implementation; these checks alone do not certify remote cleanup safety.

## Semantic observation boundary

`bcs_e2e.evidence.SemanticEvidenceProvider` is the stable contract for
persistence assertions. `AlisdDbEvidenceProvider` performs only approved schema
and bounded row queries after environment binding; `LocalSemanticEvidenceProvider`
uses the same assertion IDs, exact environment/resource filters and result
validation without contacting OB or an MCP. A local semantic observation is
reported as local evidence only and cannot be promoted to preproduction DB
proof.


## Report generation

After case reports exist, render the three report forms without claiming that
the full reviewed release inventory passed:

```bash
python3 runner.py report \
  --profile profiles/local.mock.json \
  --state-dir /private/path/e2e-local-001 \
  --format all
```

The output directory contains `summary.json`, `junit.xml` and `summary.md`.
The summary records the frozen inventory, case denominator, actual routes,
mock/evidence boundaries, cleanup status and diagnostic timings. A successful
three-case pilot is reported as `PILOT_PASS` while `full_release_status` remains
`NOT_QUALIFIED`; functional E2E timings are explicitly not a performance
benchmark.

## PRE-02 remote WebSocket transport

The Remote adapter now has a dependency-free WebSocket Upgrade client for the
V1 session route. It derives `ws://`/`wss://` from the configured HTTP(S)
origin, sends the short-lived session token only in the approved query
parameter, validates the `101` response and `Sec-WebSocket-Accept`, and closes
with a protocol close frame. Evidence redacts the token and does not carry the
Human Cookie/Authorization header into the WebSocket URL. This is transport
coverage only; a real preproduction run remains `NOT_RUN` until a resolved
profile, route binding and authorized session are supplied.


## PRE-03 remote file transport

`RemoteFileClient` implements the internal V1 file API and bounded consumption of
returned single/multipart upload targets. It distinguishes authenticated exact
proxy routes from credential-free storage PUTs, then completes through the product
API. Upload acceptance is not synthesized as a Ready metadata response.

Optional private profile configuration authorizes exact storage origins:

```json
"file_transfer": {
  "allowed_storage_origins": ["https://storage.example.test"]
}
```

Omit it (or use an empty list) to forbid direct storage requests. No wildcard
hosts, URL credentials, paths, query strings or duplicate origins are accepted.
The allowlist never authorizes forwarding Human auth to storage, even if its
origin is also the product origin. All upload-part URLs are checked before the
first PUT; returned URLs, signed queries and part numbers are not rewritten.
The small-file budget uses `max_response_bytes` for request/response bodies,
allows at most 64 parts, and follows at most three explicit download 302 hops.
Every hop must be approved; redirects never forward Cookie/Authorization.

MIME and per-part byte lengths are preserved. Direct PUTs use HTTP success, not
BCS envelope assumptions. Protected/shared downloads accept raw bytes or bounded
storage redirects; a returned share URL must match the issued token and product
route. Storage URL paths/queries and signatures are excluded from evidence.
Real BaaS/object-storage behavior, physical deletion and live OB are not verified
by fake-transport tests. Remote Story admission remains blocked without the
required environment-bound observer.

The PRE-02 remote transport now treats `101` as only the protocol upgrade. It
immediately sends the bounded Workbench `connect` request with the created
`group_id` and `session_id`, waits for the matching response ID, requires
`ok=true` and the same group binding, responds to server Ping frames, and only
then reports `websocket_connected`. A close record uses `protocol` and
`close_code=1000`, not an HTTP status.


## Database response completeness

Reviewed MCP catalogue, schema and exact-resource reads revalidate the pinned
mapping/receipt after a successful tool response as well as before dispatch.
Expiry, revocation, Run rebinding or mapping drift during the call cannot be
credited as current evidence. There is no automatic renewal or retry. A raw
transport failure remains the original failure; these local checks add no MCP
calls. API functional and cleanup outcomes remain separate if required database
evidence is rejected after an in-flight approval expiry.

Semantic resource reads and normalized metadata discovery reject a present
`truncated` flag unless it is exactly boolean `false`. Boolean `true`, numeric,
string, null and container values cannot establish complete evidence. Omission
remains supported by the observed tool contract; the exact-resource assertion
still independently requires exactly one matching row and its full projection.
Truncated or malformed schema/catalogue results block admission before business
row queries, including when a replacement client bypasses the mcporter wire
normalizer. This does not add retries or widen SQL, row, table or resource scope.

The three existing persisted-resource assertions use a two-row limit to detect
non-unique results; they do not need pagination. Collection observations retain
their existing versioned caps and reject incomplete pages. Database keyset
pagination uses a separately selected contract described below; neither this
completeness check nor remote-instance discovery implicitly enables it.

### Opt-in delivery keyset traversal

A fresh `retained-recheck` review or persistent-mapping source read plan may
explicitly add this field alongside the existing wait-v2 observation budget:

```json
"delivery_pagination": {
  "contract": "bcs-session-deliveries-keyset-v1",
  "page_size": 100,
  "max_pages": 4,
  "max_rows": 300
}
```

This is a configuration fragment, not authorization. Existing command consent,
exact Run confirmation, source/mapping binding, held lease and new observation
ID are still required. Original business-run reviews reject the new field;
old read plans retain their one-page semantics. The parser permits page sizes
1–100, page-attempt limits 1–8 and aggregate row limits 1–500 per Session per
round; these maxima never enlarge the shared 32-call phase budget or the
original absolute observation deadline.

The query retains exact env/Group/Session/two-Bot filters and adds only `id`
to the existing finite projection. MySQL migration 021 defines `id` as the
unsigned integer primary key; schema admission requires that column and each
returned value must be a strictly advancing unsigned integer. Queries use
`id > last_id ORDER BY id ASC`, not offsets or textual UUID collation. No
storage blobs, prompts, tokens or arbitrary WHERE/order expressions are read.
The cursor column is discarded from semantic delivery rows.

Every page attempt, including an empty end probe, goes through current scope,
mapping and authority checks and consumes one shared call before dispatch.
Reports distinguish page attempts from actual query attempts. Full pages need
a subsequent short/empty page; row/page/call exhaustion, malformed truncation,
duplicates, out-of-order cursors, scope drift, late responses and cancellation
cannot yield complete evidence. Partial traversal counts/reasons remain visible;
partial rows do not authorize downstream alias queries. Completed traversal
still uses the existing four-Run downstream cap and producer-v1's 20-row cap.

`COMPLETE` means **NON_ATOMIC_TRAVERSAL**, not a transaction snapshot: concurrent
inserts/updates/deletes or commits below a cursor can change the collection.
It does not prove no future input, whole-scope completion, resource deletion or
pool reuse. An empty scan still leaves execution evidence incomplete. Original
Run artifacts and the quarantined pool are unchanged.

Validation uses real SQLite-backed keyset queries and the CLI/review/lease/MCP
composition with synthetic subprocess replies. It is **not live MySQL/OB
pagination acceptance**. At most 32 MCP calls are possible in one phase,
including metadata; each row page returns at most 100 rows, with one sentinel
row beyond the aggregate cap causing rejection. SQL rows examined and sort
cost are not bounded by returned-row limits: the deployed optimizer/index plan
and latency need scoped validation before operational use. No index, migration,
production data or existing remote review is changed by enabling the code.

## Evidence provenance in Case reports

Each pilot Case now has a separate `evidence_status` and
`semantic_evidence` section. The LocalMock adapter observes its own exact
Group/Session/File state through `LocalSemanticEvidenceProvider` and reports
`LOCAL_SURROGATE`; it is not labeled as an OB observation. Remote adapters
are `BLOCKED` before mutation until a live, environment-bound alisddbquery provider is
configured. Functional API PASS and required evidence completeness are kept
separate, and the suite report will not produce `PILOT_PASS` when required
semantic evidence is unavailable.

## Fixture plan/apply boundary

Fixture initialization is separate from `check` and `run`:

```bash
python3 runner.py fixture plan \\
  --profile profiles/local.mock.json \\
  --state-dir /private/path/e2e-local-001

python3 runner.py fixture apply \\
  --profile profiles/local.mock.json \\
  --state-dir /private/path/e2e-local-001 \\
  --run-id fixture-20260923 \\
  --allow-write --confirm-run-id fixture-20260923
```

The LocalMock apply only writes a private `fixture.json` declaration; it does
not call `ensure-human`, register Bots, alter friendship, open a socket or
contact a product service. `local.singlebox` and remote profiles remain
`BLOCKED` until a separately reviewed environment-specific fixture handler is
implemented. Read-only preflight never repairs missing fixtures.

## Explicit read-only HTTP probe

After resolving a real profile, a single preflight GET can be exercised without
running a Story:

```bash
python3 runner.py probe http \
  --profile /private/path/profile.json \
  --operation identity.read \
  --allow-read
```

The operation is limited to `environment.read`, `identity.read`, and a bound
`bot.read`; `--allow-read` is mandatory. The output contains request/response
correlation metadata only, rejects redirects, and never prints the response
body or authentication headers. A transport or envelope failure is reported
as `BLOCKED`, not as a successful empty response. The probe does not issue
writes or session tokens. WebSocket probing remains coupled to PRE-02 until an
explicit run-owned session/token input contract is approved.

## Run-owned WebSocket probe

A standalone WS probe is available only for a `PREPARED` or `RUNNING` Run that
already owns a live group and Session in its Run-local ledger:

```bash
BCS_E2E_SESSION_TOKEN='(provided by the approved short-lived token handoff)' \
python3 runner.py probe ws \
  --profile /private/path/profile.json \
  --state-dir /private/path/e2e-run-001 \
  --run-id e2e-run-001 \
  --token-env BCS_E2E_SESSION_TOKEN \
  --group-id <run-owned-group-id> \
  --session-id <run-owned-session-id> \
  --allow-read
```

The CLI accepts only an environment-variable **name**, never a token literal.
Before opening the socket it verifies the profile fingerprint, Run ID, and
created/live group→Session parent chain. It performs the existing bounded
Upgrade/connect handshake, closes the connection, and reports only redacted
correlation metadata. It does not mint a token, create/delete resources, use a
second origin, or permit a borrowed/reused/unknown resource. A session that
has already been cleaned up cannot be probed; use the PRE-02 execution while
its resources are live instead.

Quarantined, completed or recovery-marked Runs are rejected before token lookup
or network construction. A foreign or symlinked ledger is not accepted. The
probe snapshots the Run and owned parent chain and rechecks them after the
handshake; a changed scope blocks the result and still closes its acquired
connection. These checks detect drift, not a server-side execution fence.

The result is finalized only after local connection closure. Close failures
block an otherwise successful handshake; interruption reports `CANCELLED` with
exit code 130. `failure_phases` preserves primary and close failures without
printing exception payloads. `socket_close_status=CLOSED` means only that the
local handle's close call completed (`close_verification=LOCAL_HANDLE_ONLY`).
`NOT_ACQUIRED` means no handle was returned to the probe; it does not attest
transport-internal cleanup after a failed handshake. Neither value proves that
the peer acknowledged closure or Bot execution stopped. Execution remains
`UNCONFIRMED`; a probe never authorizes pool release or a new Story.

## Ephemeral CLI context

The runner can create a private, temporary context outside the user's home
configuration tree:

```bash
python3 runner.py context create \
  --profile /private/path/profile.json \
  --context-dir /private/path/e2e-context-001
python3 runner.py context remove \
  --context-dir /private/path/e2e-context-001
```

`context create` validates the profile without resolving credentials or making a
network request, then writes only a canonical `profile.json` copy and a private
ownership marker. The destination must be an empty regular directory outside
`$HOME`; existing content, symlink roots, tampered markers, and profile digest
mismatches are rejected. `context remove` requires a path-bound marker, matching validated profile,
and an inventory containing exactly `context.json` and `profile.json`. It
unlinks only these two regular files, then removes the empty directory; it
never recursively deletes a tree. Extra files or directories (especially
Run ledgers/reports), linked metadata and copied markers block removal before
unlinking. Old markers without the path binding are preserved for manual
inspection, not silently upgraded. A concurrent extra file makes final removal
fail and is retained. These checks reduce accidental deletion; a local marker
is not cryptographic proof against another process running as the same user. This context boundary is
local configuration hygiene; it is not a substitute for a Run ledger, fixture
pool lease, or remote resource isolation.

The standalone WebSocket/CLI probe and live preproduction qualification remain
separate follow-up work because a valid probe also needs an authorized,
run-owned session/token binding. No credential or token is accepted in this
context metadata.

## Persisted diagnostic summaries

Optional log collection can be run by an approved embedding runner through
`collect_log_diagnostics`. It consumes only unique request IDs already recorded
in the Run ledger, enforces a finite query budget and time window, and persists
only status, correlation selector, bounds, record count and truncation. Raw log
records are never stored in the ledger or report. Budget exhaustion is
`INCOMPLETE`, a backend failure is `BLOCKED`, and no correlations means
`NOT_RUN`; the functional/required evidence result remains separate.

## Required versus diagnostic observation

The pilot manifest declares `required_evidence` separately from
`diagnostic_evidence`. API/bytes/WebSocket and semantic persistence checks in
the first list remain gating; request correlation and log lookup are currently
`NOT_RUN`/diagnostic until a deployment-specific backend is authorized. A
missing or truncated diagnostic result is rendered as `INCOMPLETE` and remains
visible in the report, but it cannot upgrade a failed required assertion or
silently turn a blocked required observer into a pass.

New execution checks these declarations against the finite executor contracts
in `bcs_e2e.evidence_policy` before freezing a manifest or acquiring the pool.
PRE-01/RETAIN-GROUP-01 require API and persistence; PRE-02/RETAIN-SESSION-01
also require WebSocket; PRE-03 also requires bytes. Removing/demoting any of
these sources, declaring unsupported mandatory trace/log checks, or malformed,
duplicate or unknown diagnostics blocks admission. Diagnostic declarations may
select only `request_correlation` and `logs` (including neither). Adding a
mandatory source requires implementing and testing its preflight and execution
gate, not merely editing the manifest. The shared `ob` requirement is explicitly
reported as SQLite for the owned-local SQLite adapter, not as remote OB evidence.

This is a new-execution admission rule, not proof that evidence was observed.
Historical snapshots retain their original declarations and remain readable;
they are not rewritten or re-admitted using current execution policy. Required
runtime evidence and report integrity remain independent qualification gates.

Offline reporting compares each Case report's `evidence_required` with its own
frozen manifest, using only the explicit owned-local `ob` to `sqlite` mapping.
Missing, malformed, duplicate or different declarations block qualification in
JSON, Markdown and JUnit even if the original report says PASS; the recorded
functional result and original files are preserved. A recorded failure remains
a failure. This comparison never consults the current manifest or current
admission policy, and declaration equality alone does not prove collection.

## Bounded diagnostic log observer

`bcs_e2e.log_observer.BoundedLogObserver` defines a replaceable, read-only log
query seam. A deployment adapter must provide a client for the normalized
`LogQuery`; the query is restricted to the exact bound environment, exactly one
request ID or W3C traceparent, a finite time window, and at most 100 records.
`success: false` is an observation error, while a valid `truncated: true`
response is reported as `INCOMPLETE` rather than silently treated as “no log”.
No AntLogs/Observ/MCP package is imported by the public runner, and this
contract alone does not prove that a physical logging backend is configured.

## Correlation journal

Each prepared Run ledger now contains a bounded `correlation` table. Transport
evidence is recorded as it is appended to a pilot report, retaining only
`run/case/attempt`, operation, method/path, submitted request ID, echoed
response request ID, and opt-in W3C traceparent values. Headers, response
bodies, cookies, tokens, and signed URLs are not persisted. When a create
response is accepted, the actual wire request ID is linked to the exact
ledger resource ID; a planned request ID is not treated as the server's
correlation ID. Report summaries expose these rows as diagnostic evidence.
Malformed IDs or traceparent values fail closed instead of being guessed.

## alisddbquery adapter boundary

`bcs_e2e.alisddbquery.AlisdDbQueryObserver` defines the integration seam for a
future `alisddbquery` MCP client. It performs database/table discovery, checks
the declared environment binding, requires all approved semantic tables, and
then delegates only bounded `EvidencePlan` queries to the stable evidence
provider. It does not accept SQL, execute DML, or import a private MCP package;
the live client binding and credentials remain deployment-specific.

The normalized client contract has fake tests plus an explicit bounded
`McporterAlisdDbQueryClient` transport and metadata-wire parser. Metadata-only
probing does not establish a deployed database environment. The declaration that a binding is confirmed is an
input, not independent proof of the physical database environment. A remote
adapter can now receive an explicitly injected approved client and expose the
same semantic provider; the runner never starts `mcporter` implicitly from a
profile. The explicit reviewed command path is documented in `remote-run.md`.
Task 3.4 remains incomplete until the actual environment is reviewed and the
preproduction integration has been exercised.

The Python composition API also accepts an explicit `observer_client` and
`adapter_factory` when an approved deployment runner embeds this package. The
CLI now supports explicit `--mcporter-binary` and `--allow-observer-read` for a
Run prepared with `--observer-binding`; it does not import arbitrary commands
or private plugins. Without this reviewed composition or approved embedding
injection, a remote Run remains blocked before business mutation. A profile's
server name alone never triggers an implicit credentialed side effect.

For a non-local target, the injected `AlisdDbQueryObserver` also requires an
independent `physical_binding_probe`. The profile boolean
`environment_binding_confirmed` is only an operator declaration and cannot by
itself authorize metadata or row discovery. The binding verifier must return
the exact reviewed database/environment pair; missing or mismatched evidence
blocks before `db_list`. The first CLI verifier checks a separately frozen owner
review receipt, not live deployment state; provenance is `OPERATOR_ATTESTED`,
with `live_verification=NOT_PERFORMED`. See `remote-run.md` for its trust limits.

The same pilot Story can therefore be exercised in a remote-shaped contract
 test with a fake/injected observer and fake transport, producing `PASS`
semantic evidence rather than `LOCAL_SURROGATE`. That test still does not
prove a deployed Gateway, MCP server, database, or BaaS; it only verifies that
the composition boundary does not require a second Story implementation.

Embedding code should call the shared `run_case(...)` dispatcher with the
approved observer/physical-binding/adapter dependencies. It selects only the
frozen `PRE-01`/`PRE-02`/`PRE-03` IDs and rejects unknown cases, so a remote
runner cannot silently substitute a different Story while retaining the same
report label.

The contract regression also executes all three frozen Story IDs against a
remote-shaped fake Gateway/WS/BaaS adapter. It verifies that PRE-01 group CRUD,
PRE-02 Session/WS, and PRE-03 file bytes/share/cleanup retain the same business
steps and use `PASS` semantic evidence under an injected remote observer. This
is still a composition test, not a live preproduction qualification: no
Gateway, MCP, database, object storage, or BaaS endpoint is contacted.

### Run binding during execution and recovery

All three pilot entrypoints and cleanup compare the supplied profile fingerprint
with the prepared Run **before** obtaining a lease, opening the ledger, resolving
credentials or creating an adapter. A changed target, actor, pool or credential
reference requires a new Run; reusing the old ledger against it is blocked.
Cleanup dry-run reads the matching ledger offline without constructing a remote
adapter, and therefore does not need live credentials. This client-side binding
check is not server-side resource authorization or protection against a tampered
Run directory; use a private directory and restricted test credentials.

### Single-machine pool registry

New Runs store their durable pool reservation under the current OS account's
`~/.local/state/bcs-e2e/pools/`, not under an individual Run directory. The key is
`environment.tier + environment.id + policy.fixture_pool`; changing a credential
reference, route or timeout cannot acquire a second reservation for that scope.
All worktrees/runners using these fixtures must use the same stable environment
and pool IDs and the same OS account. Different IDs must represent genuinely
disjoint fixtures, not aliases for overlapping Bots. This is not cross-account,
cross-host, or server-enforced isolation.

The Run state records the pool scope and canonical reservation path. Older
per-Run lock records are refused for execution/release rather than silently
migrated: inspect the old ledger and recover any activity before preparing a new
Run. Offline cleanup dry-run remains available. A process-held `flock` on a persistent `.guard` inode additionally prevents two
commands from adopting the same reservation. The guard file is never unlinked;
its existence alone does not mean a command is running. This implementation
requires a POSIX local filesystem (the supported macOS/Linux Runner hosts), not
NFS or a cross-host shared directory.


### Command ownership, failure quarantine and recovery

Prepare persists the reservation and closes only its process-held guard. Each
Story holds that guard through ledger operations, case report persistence and
Run state updates. Repeating a Case in one Run is refused instead of overwriting
its report or reusing its request IDs. A successful case releases its reservation;
the next distinct case re-acquires it and rechecks its preconditions.

Failed cases retain the reservation. Cancellation or unexpected exceptions leave
`QUARANTINED` state; abrupt process death leaves `RUNNING`/unknown quiescence.
Process exit releases the OS guard but **does not** release the durable pool
reservation. These unknown states require explicit reconciliation; there is no
PID-death/timeout-based reclaim or force-release option.

Cleanup can resume a matching failed Run when its activity is confirmed quiescent.
A cleanup failure retains the reservation, and `release` refuses live, unknown or
retained ledger resources, unverified activity and unresolved cleanup failures.
Successful cleanup keeps the original functional failure in Run state. Dry-run
writes `cleanup-plan.json`, not `cleanup.json` or the Run cleanup status, so a
plan cannot overwrite a real failure. Run-scoped LocalMock now persists its surrogate resources across commands;
see the snapshot boundary below. It does not recover real product resources or
relax quarantine for unknown in-flight activity.


### Required observer admission and evidence qualification

The selected pilot resolves its semantic provider exactly once and admits its
required assertion IDs **before any resource intent or business mutation**.
LocalMock admits only `local-semantic` provenance; remote profiles cannot use
that surrogate as OB proof. An unavailable provider, failed metadata call,
unconfirmed binding or schema mismatch yields a persisted `BLOCKED` case with
`functional=NOT_RUN` and `cleanup=NOT_ATTEMPTED`. Error payloads are not echoed;
remediation identifies only the configured observer reference.

The normalized alisddbquery provider validates selected schemas using metadata
calls, not fabricated resource queries. This tests the adapter contract; actual
preproduction mapping and qualification are not established by these tests. The
reviewed CLI path and its manual-versus-live provenance are in `remote-run.md`.
Subsequent observations still revalidate schema and exact resource/environment,
require exactly one complete projected row, and discard undeclared columns.
Malformed/error responses and missing evidence cannot become PASS. A post-write
observation failure preserves the functional outcome, performs cleanup, and makes
the case and CLI non-success. Failure/incompleteness dominates local-success
provenance when aggregating PRE-02's two observations. Report/JUnit rendering also
refuses older case PASS records with non-qualifying evidence.

### File API routing and negative evidence

File operations use the internal V1 paths declared by
`apps/bcs/api-contracts/v1/internal.yaml`, independently of the public group/session
API face. They are not implemented by substituting an `/openapi/v1` prefix. JSON
responses require the V1 envelope; raw content remains bytes. Share-content GETs
and deletion verification send the issued share token without Human credentials.
Each product request records its operation, submitted request ID, echoed-ID match
and elapsed time; query values never enter records.

Metadata deletion verification requires HTTP 404 with `session_file_not_found`.
A missing parent session/group, a proxy HTML 404 or a denial does not prove the
file outcome. Shared-content verification separately requires the product
`shared_file_not_found` error. Neither is proof that all physical storage copies
or previously issued storage signatures have been revoked.

### PRE-03 assertion and cleanup boundaries

Completion, listing and metadata read-back validate the same expected file:
exact file/session IDs, name, MIME, integer size, Ready state, owner, backend and
required timestamps. A nullable server SHA is accepted by the API contract, but
protected/shared downloads must still equal the original bytes. Page totals must
be integers and the owned file may appear exactly once in the returned page.

`ob_file_matches` now compares the admitted observer's persisted projection with
the expected session, size, hash, state and backend; it is not an alias for API
metadata equality. A persistence mismatch blocks evidence qualification while
preserving the independent functional and cleanup outcomes. Local observation
is still `LOCAL_SURROGATE`, not independent OB proof.

Only a successfully journaled returned file ID can authorize file cleanup.
DELETE must explicitly return `deleted: true`; otherwise verification and parent
Session/Group deletion stop. Share invalidation is probed only after a nonempty
share token was issued. An upload failure before sharing therefore still attempts
exact file abort/delete and parent cleanup without an invented share probe.

### Atomic private state files

Run-state writes encode and size-check JSON before creating a file, write through
a unique private temporary file, flush/fsync it, atomically replace the target,
and fsync the directory. A failure before replacement leaves the previous state
intact; a directory-sync failure after replacement is surfaced, not represented
as a successful durable write. Only the current writer's temporary file is removed.
Reads reject symlink/nonregular targets, duplicate keys, malformed schemas and
oversized data. These safeguards assume a private, trusted local Run directory;
they do not make user-editable files a server authorization boundary.


### Run-scoped LocalMock persistence

`prepare` explicitly initializes a private `local-mock.json` for mock profiles.
Run and cleanup commands load that exact Run/Profile-bound snapshot; missing,
corrupt, foreign or symlink snapshots block rather than becoming an empty world.
Read-only `check` can still use an ephemeral mock because it has no Run context.

Each simulated resource mutation is atomically saved **before** the adapter
returns success. The snapshot holds exact resources, small-file bytes, synthetic
share tokens, connection markers, deletion markers and independent creation
provenance (case/attempt/origin/parent). A snapshot-level lock plus generation
check rejects stale adapter writes. Persistence failures fail the operation and
poison that adapter instance until it is reloaded; they are never swallowed.
Recovery-mode adapters cannot create resources or reset existing snapshots.

The local semantic observer now reads the saved snapshot rather than its caller's
mutable Python dictionaries. This is stronger framework recovery evidence, but
remains `LOCAL_SURROGATE`—not BCS/OB or independently deployed service evidence.
Read-only operations do not rewrite the snapshot. A subprocess regression proves
that a case with failed cleanup can be cleaned by a separate, fresh CLI process
without credentials, sockets, or the original in-memory adapter instance.

Snapshots are private test artifacts and are not included in reports or Git.
Treat them as trusted local input, not a tamper-proof authority. Independent
cleanup inventory reconciliation and deletion/ledger crash-gap convergence are
the next safety increment; persistence alone does not prove those properties.

### Cleanup inventory reconciliation

Destructive cleanup is now fail-closed for LocalMock. Before the first delete and
after the last delete, the runner compares every ledger row—including tombstones
and retained rows—with the independent Run-bound mock snapshot: resource kind and
ID, creation case/attempt/origin, exact parent, deletion evidence and child
inventory must agree. Omitted live resources, forged ledger deletion markers,
borrowed/reused roots, cross-parent IDs, live files under deleted sessions,
open sessions and stale share mappings block cleanup before any delete.

A successful file delete must return `deleted: true`; file deletion is still
verified with the exact parent session. Already-persisted matching deletion
markers can converge on a fresh cleanup process; unknown or mismatched IDs cannot.
Unsupported adapters have no destructive-cleanup authority and are blocked. A
retained/unresolved resource keeps the pool reservation and remains visible in
cleanup output; it is never silently deleted or treated as a successful release.
This is an independent local-snapshot check, not server-side authorization or a
substitute for remote scoped-delete contracts.

### Report integrity and assertion denominators

Report generation now binds every case report to the prepared Run ID, Profile
fingerprint, schema version, exact filename and frozen manifest case ID. Unknown,
foreign, malformed or mismatched report files block the summary instead of being
silently ignored. A PASS case must contain unique assertion records for every
manifest-declared assertion; missing/duplicate IDs, invalid statuses and
contradictory functional/evidence/cleanup states are exposed in `integrity` and
cannot produce `PILOT_PASS` or a successful JUnit case.

Generated case and cleanup artifacts carry the Profile fingerprint. JSON,
Markdown and JUnit share the same normalized case status and denominator counts.
The summary remains a pilot report and always exposes `full_release_status` as
`NOT_QUALIFIED`; timings are diagnostic only, not benchmark or coverage evidence.

### WebSocket transport safety

PRE-02's WebSocket adapter preserves bytes coalesced with the HTTP Upgrade
headers, uses one bounded deadline for the connect handshake, caps unrelated
frames, answers server Ping with Pong, and closes the raw socket on every
handshake/protocol failure. Server frames must be unmasked, final, use supported
opcodes and obey control-frame limits; reserved bits and oversized frames block.

The connection token remains query-redacted in evidence. A successful `101` is
only the protocol upgrade; the runner still requires the matching Workbench
`connect` response with the expected request ID and group binding. A local close
frame is best-effort transport cleanup, not proof that the peer acknowledged an
HTTP/WebSocket close code.

### Report artifact and redaction safety

Report JSON/JUnit/Markdown files are written through private atomic replacement:
existing symlinks/nonregular targets are rejected, temporary files are unique and
private, data is fsynced before replacement, and the containing directory is
fsynced afterward. A failed write does not silently produce a successful artifact.

Redaction applies both to sensitive mapping keys and to strings: Bearer values,
URL query values such as `token`, `signature`, `X-Amz-*`, credentials and session
parameters are removed before report aggregation. Diagnostic timing ignores
negative, NaN and infinite values. These controls reduce accidental leakage but
do not make arbitrary free-form case text safe; case authors must not put secrets
or real user content into evidence.

### Remote write transport admission

Pilot remote writes use only the profile-selected public API operation bindings;
there is no 401/403 retry through a direct BCS route, Principal header invention,
or authentication redirect following. Every request carries a bounded generated
`X-Request-ID`; evidence records both the HTTP header echo result and the
contract envelope's response `request_id` when the public V1 envelope supplies it.

Public V1 success envelopes must contain integer `code=20000`, a message, a
nonempty response request ID and a `data` member. Denials, redirects, malformed
or missing envelopes are errors, not empty data or success. Pilot group/session
DELETE bindings follow the contract's HTTP 200 delete result; 204 is not accepted
as an accidental alternate. These transport checks still do not prove a live
Gateway identity or deployment route until an authorized remote profile is used.

### One-time fixture apply safety

`fixture apply` is intentionally create-once for the local mock declaration. It
rejects an existing or symlinked `fixture.json` instead of overwriting it, and,
when `run.json` already exists, requires the exact Run ID and Profile fingerprint.
A malformed or foreign Run state blocks the apply. The command remains a local
metadata declaration only; it does not register Bots, repair friendship, call a
service or grant authority to a later destructive action.

### Explicit remote route bindings

Non-mock profiles must declare the complete finite pilot `route_bindings` map.
Each binding fixes method, exact path template, API face, identity policy and
response contract. Validation rejects missing/unknown operations, path-prefix
substitutions, internal/public face mismatches, app-vs-human identity changes and
raw-response declarations. The write transport consumes the validated binding
instead of carrying an independent route table.

This is intentionally a small contract surface for PRE-01/02/03, not a generic
arbitrary URL feature. Adding a route requires changing the finite contract,
profile schema and tests; a 401/403 still terminates the selected operation and
never authorizes fallback to another binding.

### Optional Edge read binding

Non-mock Singlebox/preprod profiles may opt into `edge_case_bindings.friend_requests.read`; the generic example profiles intentionally leave it absent so unrelated Pilot Stories do not require Edge-specific authority. The
declared dual-environment route is exactly
`GET /openapi/v1/collaboration/friend-connections/requests` with only the
`direction=received|sent|all` query. The binding must identify the dedicated
driver Bot as caller and require an `Authorization` environment reference; a
Human Cookie cannot substitute for the Bot identity. `RemoteEdgeReadAdapter`
consumes only that validated binding through `ReadClient`, executes each finite
read once, and projects `data.items/page/page_size/total` while rejecting
denials, redirects, malformed public envelopes and oversized responses.

This declaration is route admission, not completed Singlebox/preprod execution.
No live environment has been exercised with this adapter, and the Case still
grants neither remote validation nor release credit.

### Read-only response correlation

Public-v1 preflight responses must carry the complete success envelope, including
a nonempty response `request_id`. Read evidence records that ID separately from
whether the HTTP `X-Request-ID` header echoed the submitted value. Legacy manifest
responses remain explicitly non-envelope and do not fabricate an envelope ID.
Malformed or missing public response IDs block discovery; they do not become an
empty environment/identity result.

### Opt-in traceparent propagation

Each finite remote route binding now declares `traceparent: disabled|w3c`.
`disabled` is the default used by the checked-in examples and sends no trace
header. With an explicitly approved `w3c` binding, the transport generates a
valid W3C traceparent, records the submitted value, and validates/records an
effective response traceparent when one is returned. Invalid effective values
block the operation. This avoids claiming universal trace support while still
providing a replaceable correlation contract for deployments that document it.

### Initial pilot actor/relationship admission

The local and remote pilot profiles explicitly declare
`relationships.friendship = unchanged`. Admission requires exactly the configured
Human owner plus two distinct dedicated Bot roles (`driver` and `member`), with
both owner IDs matching that Human. It does not query, create, accept, reject or
repair friendship; existing friendship state is deliberately outside this pilot.

The admission result records `cross_owner: NOT_TESTED`, so Human-owned group
success cannot be reported as cross-owner or friendship authorization coverage.
Wrong owner, duplicate Bot or any requested friendship mutation policy blocks
before the required observer/business write path.

### Migrated Case static guard

`scripts/ci/e2e_runner_case_guard.py` is a CI/review guard for Story executor
files. It rejects direct HTTP/client libraries, raw sockets, SQL drivers,
subprocess/shell/process-kill calls, hardcoded Principal/Bearer credentials,
`curl`, destructive SQL and similar bypasses. Cases must call the reviewed
adapter, ledger, cleanup and evidence contracts instead.

The guard deliberately does not scan approved transport implementations,
LocalMock internals or tests; those modules are the controlled boundary and have
separate contract tests. This is not a security sandbox—runtime credentials and
execution permissions remain the real safety boundary.

### Explicit qualification gate

`runner.py gate --profile ... --state-dir ...` evaluates the rendered report as a
release gate without releasing a pool. It returns `QUALIFIED` only when the
pilot status, report integrity, every required case/evidence result and the
frozen release inventory all qualify. The initial pilot deliberately reports
`PILOT_PASS` with `full_release_status=NOT_QUALIFIED`, so `gate` returns exit
code 2 and explains the missing qualification rather than silently treating a
pilot as a release pass.

### Read-only recovery inspection

`report`, `gate`, the WS probe's ledger lookup, and `cleanup --dry-run` open
existing journals with SQLite `mode=ro` and `query_only`. They do not create
missing journals or add correlation/diagnostic tables to historical journals.
A missing/unreadable report ledger or unsupported observation schema is an
integrity blocker, not evidence of successful observation. Dry-run can still
inspect resource cleanup in an older journal without upgrading its schema.
Report output files and the dry-run cleanup plan are still intentionally
written; the input ledger's rows and schema are read-only.

Writable recovery validates the Run/Profile binding before additive schema
initialization. It cannot create a new database in place of a missing one.
This does not establish server-side ownership of remote business resources.

### Diagnostic log adapter contract

A normalized log record must include the exact `environment`, integer epoch
`timestamp` inside the query window, and the matching `request_id` (or the
same trace ID for a traceparent query; child span IDs may differ). A successful
empty response is `INCOMPLETE`, not proof of a logged request. Raw text and
other arbitrary fields are dropped. Deployment clients implement
`query(request, *, timeout_seconds)` and must enforce that transport timeout.

The collector uses server response request IDs when recorded, otherwise the
submitted ID, without inventing propagation success. It validates result/query
bindings again, caps queries, passes at most five seconds per query, and stops
starting queries when its total monotonic time budget is exhausted. This is a
cooperative client contract, not preemption of arbitrary injected Python code.
Backend failures become sanitized diagnostic `BLOCKED` results; process
interrupts are not swallowed. No live logging client is enabled by default.

### Optional diagnostics in the Case lifecycle

Approved embedding runners may pass `log_observer` to `run_case(...)` (or an
individual pilot executor). After business execution and case cleanup, the
framework checkpoints the primary result, then admits the log observer for the
exact configured environment and collects that Case/attempt's correlations
within its recorded start/end timestamps. The command holds the pool guard
until the final report/state is persisted. No new CLI flag or implicit live
log integration is enabled.

No observer means `NOT_RUN` plus an explicit warning. Admission/backend/storage
failures mean diagnostic `BLOCKED`; empty, truncated or budget-limited evidence
is `INCOMPLETE`. These optional results never replace functional, required DB
evidence or cleanup outcomes. JSON retains the collection summary and sanitized
ledger counts; Markdown and JUnit expose diagnostic status separately. In
particular, successful individual rows cannot mask an exhausted collection
budget. An interrupt still quarantines the Run; the prior checkpoint remains
available for recovery.

### Owned local BCS configuration (runtime foundation)

`prepare_local_bcs_inputs` creates a fresh private runtime root and an explicit
JSON BCS configuration. SQLite, Bot files, session files, template storage,
HOME, temporary files and console logs belong to that root. It selects memory
cache/local storage, disables BCSFuse, LLM, external user-directory/login and
event dispatch, and issues fresh test secrets via an allowlisted child
environment. It does not inherit `.env.local`, proxies or developer secrets.

The explicit config-file path avoids the directory loader's legacy fallback.
The minimal configuration intentionally has no frontend bundles or seeded
collaboration templates, and does not claim those features were tested.
Runtime-local Human identity is mock authentication, not SSO validation.
This configuration is not a prepared pilot profile: real Bot fixture creation,
resolved actor IDs and public API identity binding remain separate steps.

### Foreground owned-runtime smoke

`runtime smoke` is a local-only boot/health/shutdown check. Provide the absolute
path and approved SHA256 of a BCS binary built from the intended source; it
never automatically builds or uses another checkout's binary. `lsof` is required
on the runner to verify that the directly spawned child owns the assigned IPv4
loopback listener before and after a bounded `/health` request. HTTP proxy
inheritance and redirects are disabled.

From this runner directory, with `BCS_BIN` and `BCS_BIN_SHA256` set to those
verified artifact values:

```bash
python3 runner.py runtime smoke \
  --profile profiles/local.singlebox.example.json \
  --state-dir /private/path/new-local-runtime-smoke \
  --run-id local-runtime-smoke-001 \
  --binary "$BCS_BIN" --binary-sha256 "$BCS_BIN_SHA256" \
  --allow-local-processes
```

For this boot check, the example profile's actor placeholders are allowed: it generates its own private local Human configuration and creates no
Bot fixture, prepared Run, or suite qualification. Use a fresh state directory;
legacy runtime markers or a prepared Run in that directory block this command.
The runtime subdirectory is create-once and is never recursively removed.

Port zero selects an ephemeral unprivileged port; an explicit occupied `--port`
blocks before spawning. Because BCS binds its own socket, a competing process
could win the reservation handoff; PID-specific listener checks prevent its
health response from being accepted as ours. The runner only terminates/reaps
its live `Popen` child, never a PID read from metadata and never a process found
by port. SIGTERM/interrupt triggers owned-child finalization. Configuration,
console logs and the private `owned-bcs.json` receipt remain for inspection.
A forced kill is `FAILED_STOP`, not clean success.

This is not a detached supervisor. If the controlling process is killed with
SIGKILL, the receipt is evidence for manual recovery, not authorization for a
later blind PID-based stop. The original `runtime start/stop/status/check` and
cross-command runtime attachment remain blocked pending persistent supervision.
`local-run` instead keeps the live child handle for the entire three-Story flow.

Live regression is opt-in with `BCS_E2E_TEST_BINARY` and
`BCS_E2E_TEST_BINARY_SHA256`; `test_owned_bcs_live.py` boots the actual BCS
binary, verifies graceful shutdown and two-instance isolation. The ordinary
process tests use small real loopback children to exercise failure paths.
Neither set is the three-story pilot or the full Singlebox release suite.

### Real fixtures inside an owned local BCS

`OwnedLocalFixtures` accepts only a live `OwnedBcsProcess`, not a profile URL.
Explicit `apply(allow_write=True)` verifies the generated local identity,
initializes that Human and registers two distinct Bots via the **legacy local
provisioning** APIs. Registration already performs onboarding; its success is
not assumed sufficient, because the registration endpoint can return after an
onboarding error. Exact Bot read-back must confirm owner, kind, environment,
name and online registration state. This does not prove Bot runtime reachability.

The create-once private fixture declaration records pending writes before each
mutation and exact returned Bot IDs immediately afterward. Lost responses or
mismatched read-back block; retries do not create more Bots. Registration and
Bot tokens are never persisted or returned in summaries. The generated Human
staff ID is ASCII alphanumeric, matching the actual ensure-human contract.

`check()` performs identity/Bot GETs only, never registration, repair, visibility
or friendship changes. Fixtures remain in this runtime's private data directory
after its process is stopped, not in any continuously operating environment.
This is initialization evidence, **not** public-v1 Story or CLI feature coverage;
a resolved pilot profile and local public-API auth adapter still need integration.


### Foreground local fixture smoke (no private actor profile needed)

Use `runtime fixture-smoke` to exercise local initialization through the actual
BCS binary. The example profile is sufficient: this flow generates a fresh local
Human and two owned Bots instead of requiring pre-existing actor bindings.
Both process startup and fixture mutation require explicit permission:

```bash
python3 runner.py runtime fixture-smoke \
  --profile profiles/local.singlebox.example.json \
  --state-dir /private/path/new-local-fixture-smoke \
  --run-id local-fixture-smoke-001 \
  --binary "$BCS_BIN" --binary-sha256 "$BCS_BIN_SHA256" \
  --allow-local-processes --allow-fixture-write
```

As with boot smoke, set the binary path and approved digest from your own build
and use a fresh state directory. Remote profiles and prepared E2E Run directories
are rejected before startup. The command starts its owned BCS child, applies the
create-once fixtures, independently rechecks them with GET requests, and stops
that child before returning. It does not change Bot friendship.

`LOCAL_FIXTURE_SMOKE_PASS` means initialization/read-back and graceful shutdown
succeeded; `ready_to_execute` remains false. It is **not** a persistent runtime,
a prepared pilot profile, three-story E2E, or release qualification; use
`local-run` for the three-Story pilot. Local mock
identity is not Gateway/SSO authentication, and registered-online Bots do not
prove model/runtime reachability. Public-v1, WS, files, BaaS and OB still require
their own integration and tests.

On normal completion, `smoke-report.json` is retained alongside `owned-bcs.json`,
`local-fixtures.json`, the database and console log in the private runtime
subdirectory. Fixture failure still triggers owned-child shutdown and returns
non-success; inspect the runtime/fixture receipts for partial progress or pending
writes. No retry reuses the root or blindly recreates uncertain resources, and
no recursive cleanup removes the retained data. The opt-in live tests also
exercise this CLI flow against a real locally built BCS binary.


### Owned local SQLite observation

`OwnedSqliteEvidenceProvider` reads the actual SQLite database of a live
`OwnedBcsProcess`; no arbitrary database path, remote target or SQL is accepted.
It uses the existing `group.persisted`, `session.persisted` and `file.persisted`
projections, with provenance `local-sqlite` (not `local-semantic` or OB).
Admission checks the physical local configuration and required real tables and
columns. A missing/replaced/symlinked/shared DB or a stopped/drifted runtime
blocks observation rather than creating a database or adopting saved metadata.

Each observation requires the exact resource and its parents to be live, created
entries of the current Run, Case and attempt in the journal. Connections use
SQLite `mode=ro` and `query_only`; no migrations, DML, schema repair or copying
of a live database without its WAL occurs. Queries use fixed columns, parameterized
local environment/exact ID filters and `LIMIT 2`, requiring exactly one row.
SQLite work is capped at 100,000 VM instructions and a two-second execution
budget, with a one-second lock timeout. Parent rows are checked against the
journal; no product parent-list scan is needed. At most three bounded journal
lookups are made per ownership check, before and after each observation.

File state has an explicit `Pending/Ready/Deleting/Failed` to lowercase semantic
mapping; unknown states block. Actual nullable `sha256` and group `originator`
values are preserved, never supplied from mock/profile/upload data. This module
is a persistence adapter foundation; shared local Story transport/auth and
observer admission integration remain separate work. Synthetic database tests
exercise row contracts; an opt-in live BCS test checks the actual migrated
schema and write refusal, not yet a complete Story persistence round trip.

### Public success envelope compatibility

Group and Session creation use `HTTP 201` with public envelope `code=20100`;
`HTTP 200` pairs with `code=20000`. The shared write transport validates these
pairs rather than accepting a success code independently of the HTTP status.
Mismatches, authentication failures and redirects remain failures. This prevents
an actually successful creation from being rejected before its returned ID can
be journaled merely because the test fake used the wrong success envelope.

### Initial Session side effects of normal group creation

All three pilot Cases now journal the normal-group API's implicit initial
Session, not just the explicit Session later created by PRE-02/PRE-03. Group
and initial-Session intents are committed atomically **before** group creation;
the exact IDs from its response then resolve each intent. Missing/invalid child
IDs and lost responses keep the reservation unresolved; no group-name search,
blind retry or presumed cascade deletion is permitted.

Finalization reads the initial Session by its exact ID, checks its group parent,
requires confirmed deletion and a subsequent not-found response, and only then
deletes the group. An initial-Session failure preserves the group and pool lease.
The mock now models this product side effect and persists both resources'
provenance together. PRE-01 has two resources; successful PRE-02 has three and
PRE-03 four. The manifest's Case version 2 declares the extra effects/steps and
requires `owned_initial_session_deleted`; older reports cannot silently satisfy
this newly required assertion. These are API deletion semantics, not a claim
that all audit history or storage objects have been physically erased.

### Live owned-local pilot composition

`resolve_owned_profile(runtime)` resolves the generated Human/Bots into a strict
profile **before** the Run fingerprint and fixture-pool lease are created.
The profile uses `auth: {"kind": "owned-local"}` without credential references,
a unique runtime-specific pool, and the `local-sqlite` observer. This profile
alone cannot authorize requests: shared clients require an injected
`OwnedLocalAuth` capability tied to the actual live runtime and verified fixtures.
The ordinary `adapter_for(singlebox)` remains blocked without that composition.

`OwnedLocalPilotAdapter(runtime, profile, state_dir=..., case_id=...)` reuses the
shared HTTP, WebSocket and file operations and the same `run_case` Story entry.
Each authorized request revalidates runtime/config/fixture/profile binding and
signs a new 60-second test Principal with this child's freshly generated key.
No ambient signing key, developer Cookie, subprocess key argument or remote
signer is used. Owned loopback transports disable proxies and redirects. Local
identity discovery explicitly reads bare `/auth/user`; protected public Bot
reads independently exercise Principal verification. No 401/403 route fallback
is permitted. This is **mock local authentication, not Gateway/SSO evidence**.

The opt-in live PRE-01 regression executes the shared group Story, including
real public creation (`201/20100`), independent read/update/read, SQLite
persistence, exact initial-Session cleanup and group deletion, under the normal
Run journal and pool guard. Required local persistence is labeled `sqlite`, not
`ob`. The test verifies the ledger and released lease and stops the owned child.
This is the programmatic integration seam used by `local-run`. A persistent
`runtime start/stop` is not enabled. Full-release gates remain separate.

### Real three-Story local pilot regression

The opt-in live suite now runs PRE-01, PRE-02 and PRE-03 sequentially in each of
two fresh owned BCS runtimes. It checks real HTTP, authenticated Session
WebSocket Upgrade/connect/close, upload/complete/list/metadata/download/share,
shared bytes, real SQLite projections, all nine journaled resources' API
cleanup, lease release and graceful child shutdown. JSON/JUnit/Markdown summary
integrity must pass; full release qualification must **remain NOT_QUALIFIED**.
No Gateway, SSO, Bot inference, BaaS or OB is exercised by this local test.

Contract corrections discovered during live execution apply to both environments:

- Session creation declares `acting_bot_id: human_<authenticated user ID>` up
  front. A Human who can manage a Session is not automatically a message
  participant; this documented explicit-Human creation path adds that participant.
  It is not a different account, Bot impersonation or retry after denial.
- Public message history returns an array. The operation adapter validates it
  and projects it to the Story's `items` view, recording that projection.
- Internal file prepare/share return 201/20100; proxy upload returns 202/20000.
  File `owner.actor_id` is the Human Actor ID, not the raw login User ID.
- Exact same-origin upload proxy matching compares decoded URL path segments,
  so the Rust serializer's raw `:` and Python's `%3A` are equivalent. Encoded
  separators, traversal, double encoding, different file IDs and unexpected
  query parameters do not acquire product credentials. Returned URLs remain
  unchanged; external storage targets still require an allowlist and receive no
  product auth.
- PRE-01 asserts returned originator, driver and roles, not profile declarations;
  DB owner/driver/status and PRE-02 persisted title/status must also match.

The Case versions are now 3. These tests validate the shared programmatic
composition and the `local-run` foreground command; the legacy full release
suite and coverage gates are unchanged.

### Frozen inventory and historical report replay

Every new `prepare` (including `local-run`) stores `manifest.json` and binds its
SHA256 plus the runner Python artifact digest in `run.json`. Each Case report
records those digests and the declared Case version. Before acquiring the Case
lease—and again after holding its guard—the executor requires the frozen
inventory and current executor artifact to match. Changing Case definitions or
runner source after preparation blocks execution; start a new Run instead.
Cleanup does not execute new Stories and remains governed by its existing
ownership/lease recovery checks.

`report` and `gate` read the Run snapshot, not the checkout's current manifest.
They do not require the historical runner source to be installed merely to
render reports. JSON, JUnit and Markdown expose Case versions/inventory binding.
Missing, corrupt or mismatched snapshots, report digests or Case versions prevent
qualification; the original denominator cannot silently shrink after a checkout
update. Reports from older Runs without these bindings remain historical
artifacts, but the new runner will not backfill bindings or retroactively qualify
them. Preserve their original reports and recorded runner commit.

These checks detect accidental drift and mixed-version reports, not tampering by
a process that can rewrite every Run artifact. They do not provide server-side
authorization or substitute for an immutable CI artifact/signature.

The executor snapshot is captured at package import, before implementation
modules load, and checked before local startup/preparation as well as Case
execution. A source edit during runtime startup cannot be newly adopted as the
identity of already-loaded Python code. Preparation also rejects existing Run,
journal or report markers before writing inventory, preserving historical Run
contents even when preparation is refused.

### Failed HTTP attempts remain observable

Shared JSON and file transports carry finite request metadata when a sent attempt
fails: operation/path projection, submitted request ID, HTTP status (or unknown
on a network failure), supported trace metadata and elapsed time. The Case records
this evidence with `outcome=ERROR` before its ordinary failure/cleanup handling,
so the optional log collector can correlate a denied request instead of seeing
only preceding successes. A captured attempt is not proof the server received it.

No response body, authentication header, raw exception URL or storage signature
is retained. External storage paths stay `[storage-target]`; they do not acquire
a fictional product request ID. Failures before request construction have no
invented request evidence. Invalid file JSON/not-found contracts preserve the
actual HTTP attempt but remain failures. This does not add retries, route or
identity fallback, or deletion authority for unresolved creates. WebSocket and
hard-interruption evidence retain their separately documented limitations; no
live logging backend is enabled by this change.

Composite file failures also retain completed redirect hops/upload parts before
the failed attempt, labeled `COMPLETED_BEFORE_FAILURE`, with their actual HTTP
statuses. A rejected next URL creates no fictional request. Optional malformed
server request IDs are rejected without discarding the submitted client ID.
If correlation persistence fails, the Case report keeps the original request
failure plus an explicit secondary `correlation_status=BLOCKED`; diagnostics stay
incomplete rather than replacing the primary failure or hiding missing evidence.

### alisddbquery metadata wire compatibility

The mcporter client now normalizes the observed tool responses: database names,
`SHOW TABLES` rows bound to the requested database, and `SHOW CREATE TABLE` rows
bound to the requested table. Column extraction is bounded and quote-aware;
unsupported syntax, failed/truncated responses, duplicate identifiers and wrong
DB/table responses block discovery. It never executes DDL and is not a general
SQL validator. Custom clients still implement the normalized client protocol.

On 2026-10-09, read-only metadata discovery and normalization succeeded for the
three pilot tables; their required projection columns were present. No business
row query or environment mutation was performed. Metadata compatibility does
**not** verify physical preproduction binding, deployed version, authorization,
or Story success: `ready_to_execute=false` and physical binding remains
`UNVERIFIED`. Private discovery captures stay outside Git. Actual row-query
integration and approved target/identity/Bot bindings are still required.

### Stored rows versus semantic evidence

Both actual DB providers (owned SQLite and alisddbquery) share the exact persisted
file-status mapping `Pending/Ready/Deleting/Failed` to
`pending/ready/deleting/failed`, after validating the projection and exact
resource/environment. Unknown, non-string, or already-lowercase stored statuses
fail closed. The local semantic/mock provider already supplies semantic rows and
does not apply this conversion.

No other stored value is repaired: NULL SHA256 remains NULL, a string size is not
converted to an integer, and actual parent IDs, owner/driver, title, environment
and storage backend stay observed values. Shared Story assertions still reject
mismatches. Conversion copies rows and performs no additional DB query or write;
SQLite retains its current-Run ownership and parent checks. Remote coverage for
this conversion is synthetic contract validation, not live business-row evidence.

### WebSocket attempt evidence

PRE-02 now retains bounded metadata when connection setup, HTTP Upgrade or the
WebSocket `connect` exchange fails. Upgrade carries a generated `x-request-id`;
the separately generated frame request ID is retained only once that frame is
attempted. Reports distinguish connection/upgrade/connect phases, actual HTTP
status (unknown before a valid response), echo match and elapsed time. Neither an
attempt nor HTTP 101 alone proves the application handshake succeeded.

The adapter preserves this failure evidence for the Case checkpoint and ledger.
The Upgrade ID is the HTTP correlation key; the frame ID remains a separate
protocol identifier in the report, not an invented distributed trace. Failures
before sending Upgrade have no submitted HTTP request ID. Tokens, response
bodies, redirect URLs, raw network errors and arbitrary server error fields are
not recorded. Denial/redirect causes no retry or alternate route. Held sockets
are also closed on interruption before ownership is handed to the adapter.
This does not validate a live log backend or promise evidence after process kill.

### Initial product CLI integration (local programmatic slice)

`OwnedLocalSessionCli` in `bcs_e2e/local_cli.py` executes the **real product
bcs-cli**, not this runner's CLI. Its initial fixed operation is
`--json --log-level error session get <owned-session-id>`. It requires an explicit
executable/SHA256, a live owned local runtime, matching Run/Profile, and a live
created Session plus its exact Group belonging to the same Case attempt. It
checks runtime/resource ownership before and after execution and requires exact
session IDs, parent, title and running status in one JSON object. Exit zero alone
cannot pass an authentication-error object or malformed/foreign response.

Each invocation creates a retained private context beneath the Run directory:
separate `HOME`, `BOT_DATA_DIR`, `TMPDIR`, and a mode-0600 product session file with
only the exact loopback URL and an empty token. The CLI reads this real context;
no URL or token override flags are supplied. The environment is constructed from
an allowlist, never copied from the user or server. `SERVER_ENV=local` selects
the data environment; the product's `tc_sdb_nenv=production` selects its
**no-Office-OAuth network mode**, not a production endpoint. Product logs stay in
the private HOME. Execution has one deadline, a combined stdout/stderr budget,
and only its held child is killed/reaped on timeout or interruption. Reports do
not contain arbitrary stdout/stderr or response fields.

This credential-free legacy GET does **not** verify authentication or remote
redirect safety. It reports `LOCAL_CLI_SLICE_PASS`, with full CLI coverage
`NOT_QUALIFIED`. It has a programmatic integration and an explicit opt-in on `local-run`
(described below), both exercised by live contract tests. It is not an addition
to the three shared pilot Cases. The additional local authenticated check below
does not qualify remote CLI authentication or migrate the file CLI Story.

To enable that additional live contract test, build the product `bcs-cli` in the
same worktree and supply `BCS_E2E_TEST_CLI_BINARY` and
`BCS_E2E_TEST_CLI_BINARY_SHA256` alongside the existing BCS test binary variables.
Do not silently use a binary from another checkout or the user's PATH. Synthetic
subprocess tests only verify the harness and are not counted as product coverage.

### Run the optional product CLI check from the foreground command

`local-run` accepts the explicit pair `--cli-binary` and `--cli-binary-sha256`.
Both the server and CLI must be approved artifacts from the intended checkout:

```bash
python3 runner.py local-run \
  --binary "$BCS_BIN" --binary-sha256 "$BCS_SHA256" \
  --cli-binary "$BCS_CLI_BIN" --cli-binary-sha256 "$BCS_CLI_SHA256" \
  --state-dir "$FRESH_PRIVATE_STATE_DIR" --run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write \
  --confirm-run-id "$RUN_ID"
```

The optional pair is validated before runtime startup or fixture writes. Omitting
both retains the normal three-Story pilot and reports `product_cli: NOT_SELECTED`.
When selected, the real product CLI reads the current Run's PRE-02 Session after
its title update, then performs the authenticated permission read described below,
while its owned Group and Session are still live. The wrapper
preserves the original API result/correlation, and the same PRE-02 cleanup closes
and deletes its resources. Shared Case IDs, versions and assertion denominators
are unchanged; this is not additional shared Story or release CLI coverage.

`product-cli-check.json` records the selection and bounded result, bound to Run,
Profile, frozen manifest, runner artifact and CLI binary digest. `local-run.json`
contains the final in-memory check result, including any checkpoint error. A
failed/interrupted checkpoint may retain its earlier non-success state; it is
never inferred to have passed. Exactly one successful side-check attempt (both
fixed CLI reads), plus successful checkpoint persistence is required for the selected check to pass. A CLI failure
leaves the API Case's actual outcome intact, completes PRE-02 cleanup, and stops
before PRE-03. Consequently the three-Case API summary is incomplete/BLOCKED,
not a falsely shortened two-Case PASS. CLI cancellation propagates through the
existing foreground cancellation/cleanup/shutdown path.

The operator command returns 0 only when the selected check, all pilot Cases and
shutdown succeed; cancellation returns 130 and other unsuccessful results return
2. Full-release and full-CLI qualification remain unchanged. Private CLI contexts
and logs are retained with the Run, never recursively removed or adopted later.

### Authenticated local product CLI context

Selecting the CLI check also opts into an in-memory handoff of the newly
registered **driver** Bot token, only after its identity/owner read-back.
Default Human-only runs still discard all registration tokens. The fixture
receipt, Profile, ledger and reports never contain this token. No existing or
remote Bot credential is acquired and no extra Bot is created.

The second fixed product command is:

```text
bcs-cli --json --log-level error collaborate permission --session <owned-session-id>
```

It queries one exact Session's permission endpoint, which requires Bot auth;
unlike `list-groups`, it cannot fall back to the mock Human. It does not start a
state machine. The existing non-home context resolver supplies the token through
its private `session.json`, never argv or inherited environment. The harness
asserts the caller/owner driver, exact Group/Session, chat strategy, allowed
policy and absence of an active run. A zero exit or unrelated permission result
is insufficient. Source baseline: this task worktree's product CLI and legacy
collaboration permission contract, not the deployed preproduction version.

`product-cli-check.json` retains the unauthenticated `cli.session.get` outcome
and a separate required `bot_permission` result (`LOCAL_CLI_AUTH_PASS`). Both
must pass before PRE-03. The temporary token is cleared on success, error or
cancellation; scrub failure remains `credential_cleanup=UNCONFIRMED` with the
exact private context path for inspection, and cannot qualify as PASS. Context
files/logs remain private for diagnostics. This is normal-process cleanup, not
secure erasure or a guarantee after hard-kill; the enclosing owned runtime is
still stopped on exit. In-memory handoff is discarded after the check or early
lifecycle failure and is never reconstructed from a retained Run.

Live tests use the real local server and CLI: missing and invalid context tokens
produce nonzero CLI exits; the generated driver token returns exact permission
identity, and the cleared context/log artifacts contain no generated credential.
These results do not establish a preproduction Cookie/OAuth CLI adapter, remote
redirect safety or full CLI/file coverage. Task 3.2 framework acceptance is
recorded separately in [the requirement matrix](task-3.2-acceptance.md); remote
acceptance and full migration retain their own unfinished gates.

### Bounded command transport

The actual MCP client and product CLI share a pipe-capture primitive with a
single process deadline and a combined stdout/stderr byte budget. Both pipes are
drained concurrently; a flood aborts while the child is running rather than
being checked only after unlimited capture. Valid JSON followed by a hung child
is not success. Stderr counts against the budget but is never returned in the
normalized response. Timeout/cancellation attempts to reap the directly held child within a separate
two-second cleanup budget and always closes the pipes. If shutdown cannot be
confirmed, the original failure/cancellation retains an explicit `UNCONFIRMED`
secondary status; it is not reported as a successful reap. This is
not a claim to manage arbitrary external MCP daemons or detached descendants.

Product CLI execution still supplies its private allowlisted environment. MCP
execution deliberately uses the operator's configured tool environment and does
not copy or publish its credentials. MCP output must be strict UTF-8 and one
JSON object with no duplicate keys; nonzero status, output overflow or ambiguous
JSON cannot be treated as successful metadata. Injected process fakes remain
contract-test seams, not evidence of a live MCP call.

### Metadata-only preparation without a remote Profile

The optional company-tool integration has an explicit read-only command:

```bash
python3 runner.py probe db-metadata \
  --server "$ALISD_SERVER" --database "$EVIDENCE_DB" \
  --mcporter-binary "$MCPORTER_BIN" --timeout-seconds 30 \
  --allow-read
```

Supply the configured server name and an absolute path to the approved local
mcporter executable (a package-manager symlink is resolved explicitly). The
command does not discover an executable via PATH, configure/login to the tool,
load a remote Profile, acquire a pool, provision fixtures, or build/start services.
Public local execution still needs no MCP installation.

At most five tool calls are made, in order: `db_list`, `table_list` for the
selected database, then `table_describe` for the three fixed pilot tables. Missing
DB/table/field or malformed/failed/truncated metadata stops discovery; there are
no automatic retries, fallback targets or business-row queries. Each actual tool
process has the selected 1..60-second execution deadline, the shared 2 MiB output
budget and at most two seconds of cleanup waiting. The normalized injected-client
seam is for contract tests, not proof of transport preemption.

Exit 0 means **METADATA_COMPATIBLE for required-column presence only**. The report
always retains `ready_to_execute=false`, `physical_environment_binding=UNVERIFIED`
and `business_queries=0`. It does not instantiate/admit an observer or bypass
its existing independent physical-binding gate. Type/nullability compatibility,
permissions, stored values, deployed version and preproduction identity are not
verified by this probe.

Only the selected target, fixed required fields, column counts, bounded statuses
and DDL capture digests are reported. Raw DDL, other discovered database/table or
column names, tool trace IDs, messages and stderr are excluded. A DDL digest is
a capture fingerprint (including options such as AUTO_INCREMENT), **not** a
semantic-schema version or deployment identity. Partial results remain BLOCKED;
ordinary failure exits 2 and cancellation exits 130.

### Owned local access-log evidence

Fresh owned BCS runtimes now explicitly use `TZ=UTC` and a private JSON output at
`runtime/logs/e2e-access.jsonl`, filtered to `bcs_http_access`. This uses the
existing product logging configuration; no Rust logger or remote deployment is
changed. Automatic retention for this added output is disabled, so the Run's
artifacts are not swept by a log reaper. The current file can still rotate daily;
rotation/replacement is detected and blocks this observer rather than silently
adopting another file. Console output remains available separately.

`OwnedLocalLogObserver` implements the existing optional log-observer contract.
It requires the live held runtime, its unchanged config, explicit local/UTC
binding and the access-log file identity captured at startup. Queries require an
exact request ID from the current Case/attempt ledger and a bounded time window.
It does not search other Runs, accept arbitrary paths, infer trace IDs, or parse
free-text substring matches.

The observer takes one bounded snapshot per Case (default 2 MiB, lines at most
64 KiB), parses the product's three access-event schemas, and exports only
request-ID/timestamp/environment projections. Environment comes from verified
runtime ownership because the access event has no env field; timezone-less
product timestamps are interpreted as UTC only under the explicit child TZ
binding. Byte/record limits and incomplete trailing lines remain incomplete;
malformed JSON, duplicate keys, schema mismatch, source drift and unsupported
trace queries fail closed. Empty results are not evidence of business absence.

A matching start/response-ready/cancel event proves only correlated log presence,
not response-body delivery, WebSocket completion or business success. API and
required persistence assertions remain authoritative. Raw routes, message text,
headers and other JSON fields never enter diagnostic reports. This local provider
is not validation of a company log service or of preproduction trace propagation.

### Collect local logs in the foreground workflow

Add `--local-logs` to `local-run` to collect the owned JSON access-log evidence for
each Case before stopping its runtime. It can be combined with the separate
`--cli-binary` / `--cli-binary-sha256` check. Without the flag, diagnostic collection
remains disabled and the lifecycle receipt reports `local_logs: NOT_SELECTED`.
The private access log is still retained for inspection.

The usual Case JSON/JUnit/Markdown results retain independent diagnostic statuses
and warnings. `local-run.json` additionally records the selected local log source
and all three Case diagnostic outcomes; unexecuted Cases remain NOT_RUN, so a
partial execution cannot become an all-log PASS. The selected source is optional:
missing/unavailable/incomplete logs are visible but cannot overwrite API,
required persistence or cleanup outcomes. Accordingly, an otherwise successful
local pilot can exit 0 with `local_logs.status=INCOMPLETE` and explicit warnings.

Cancellation is different from an optional outage: direct or chained interruption
propagates through the observer/collector, prevents later Cases, and invokes the
existing owned cleanup/shutdown path. An ordinary log-platform error is never
printed verbatim. This flag does not query company services or qualify remote
logging/trace propagation, code coverage or performance.

Optional log-source construction is deferred to diagnostic admission as well:
ordinary initialization failures become explicit diagnostic warnings rather than
preventing the API Case from executing. This does not swallow cancellation.

### Full legacy-gate validation boundary

Runner tests and owned-local pilots are not the unified all-module singlebox
coverage gate. The existing legacy orchestration still contains port-based
termination and checkout-wide cleanup; `--skip-start` and a separate coverage
output directory do not make that lifecycle Run-owned. Do not launch it on a
shared host merely because this new runner has safe ownership checks. Full
compatibility validation requires an exclusive disposable VM/CI runner with its
own process/network namespace and checkout, retaining every existing module and
coverage threshold plus the artifact verifier. No legacy gate is replaced or
weakened by the optional CLI or log checks above.

### Cleanup is not complete data erasure

JSON, JUnit and Markdown summaries now state that cleanup verifies registered
resource API convergence, with a separate mock-versus-product verification mode.
Physical data erasure and associated-history purge remain **NOT_VERIFIED**; a
successful Case cannot promote these to PASS. This is a report boundary, not a
new assertion that all data should have been erased or permission to do so.

The source-backed [retention contract audit](retention-contracts.md) records the
current DM, message/history, file, Provider, Organization, definition/run and
Eventing constraints. In one retained local pilot, the registered Group/Session/
File rows were gone while 11 message rows remained under its exact ledger Group
IDs. A parent DELETE, disable flag, cancelled run, expired credential or cache TTL
must therefore not be treated as universal retention enforcement.

Continuously running environments still need an approved owner, quota, review
period and actual reclamation/manual-review procedure for retained data before
expanding the suite. The runner never compensates for missing delete APIs with
SQL, bucket-wide deletion or cleanup of borrowed operational fixtures.

### Governed effect declarations for new Runs

`effect_policy.py` validates the current pilot manifest during planning and
freezing, before a new Run obtains its lease/resource journal or executes Case
operations. Only the reviewed normal Group, Session and File effects are
admitted. The declarations must be internally complete:

- Group creation includes its implicit initial Session and cleanup of both.
- Session creation includes its parent Group and exact Session cleanup.
- File upload includes an owned Session and exact File cleanup.
- Update/delete/share/token declarations require their corresponding current-Run
  creation/upload declarations; borrowed-asset mutation is not silently admitted.

Unknown effects such as DM creation, Provider/Organization mutation, history
purge, bucket-wide deletion or a new `retain:*` label remain blocked until the
framework's authority, ownership, retention and cleanup contracts are reviewed
and implemented. A label alone does not add a cleanup handler.

`plan` exposes this as `DECLARATION_CHECK_ONLY`: it grants no deletion authority
and proves neither action ordering nor actual Case behavior, physical erasure or
server isolation. Runtime ledgers/API checks and code review remain necessary.
This is an accidental-misuse guard, not a sandbox for hostile Python code.

Historical report parsing remains structural and reads the frozen inventory;
new execution policy does not rewrite old snapshots or retroactively qualify
them. Executing/preparing with unreviewed effects fails closed, while the existing
three Case versions and all legacy inventory entries remain unchanged.

### Runtime Case resource scope

The pilot composition root now wraps the selected adapter in `CaseScopedAdapter`
inside the live Run ledger and pool lease. The same guard applies to local mocks,
owned local BCS and remote adapters; it does not change Story IDs or assertions.
There is no generic attribute forwarding to an adapter's raw HTTP/file clients.

- Before targeted API operations, the guard checks the exact registered ID,
  current Case and attempt, `created` origin, `live` state and the entire
  File → Session → Group parent chain. Run/profile binding is checked again;
  another Case's resources or old tombstones do not confer access.
- Mutations additionally require their frozen effect declaration. Creation needs
  one fresh write-ahead intent; normal Group creation needs both Group and
  implicit initial Session intents. An already-submitted intent cannot silently
  retry a lost creation response. The guard never journals a guessed ID.
- Parent deletion is refused **before calling the product API** while any child
  is unresolved, retained or not yet marked deleted. Previously, the ledger also
  checked this when recording a tombstone; that post-call check remains.
- Initial Session cleanup uses its own declared effect. A pending upload is still
  a live owned File eligible for exact cleanup; Ready status is not required.
- WS connections and shared downloads require a token returned through this
  Case's issuance call, bound to the exact resource. Tokens stay in memory, never
  in guard error messages or the ledger. Closing a held WS does not require fresh
  server/auth/ledger validation, so teardown can run after runtime failure.
  Socket ownership is recorded before response/evidence validation, and cleared
  before recording a successful close, so evidence failures neither leak a held
  socket nor cause a duplicate close.
- Product deletion and ledger deletion are deliberately separate: the guard
  preserves response/evidence, permits absence/share-invalidation verification
  while the ledger is live, and leaves `mark_deleted` to the Story. After an
  explicit successful delete response, ordinary reads/writes through this guard
  are refused. Share provenance survives until its invalidation check.

The guard delegates to the existing adapter instance, including local CLI
side-check overrides, without translating exceptions, retrying requests or
replacing transport evidence. It only reads the local ledger: fixed-depth parent
lookups, at most two candidate intent rows and one unresolved-child existence
result. It adds no remote metadata/DB calls or product schema changes. The
three-Story pilot remains serial within the held fixture-pool lease; this is not
cross-process fencing or protection against concurrent hostile ledger edits.

This layer is **not** a security sandbox, server-side authorization, full request
payload policy, physical erasure proof or authorization for new resource kinds.
Semantic providers keep their independent admission/query contracts; admitted
Story observations additionally receive the scope check described below. Raw
provider calls are not sandboxed by the API wrapper. Standalone cleanup is
not wrapped: it retains its independent recovery verifier and existing refusal
to destructively recover a real remote/local-product Run. Case scope checks must
not be used as a shortcut around those recovery restrictions.


### Case-scoped persistence observations

`AdmittedObserver` requires a `resource_scope` callback from the pilot's existing
`CaseScopedAdapter`. There is no optional unscoped mode in the Story observation
gate. Provider construction, metadata/schema discovery and physical-environment
admission still happen before the first resource intent; they do not require a
business object to exist and are not evidence of its persistence.

For each of the declared `group.persisted`, `session.persisted` and
`file.persisted` observations, the gate:

1. Checks the Case's allowed assertion and validates the exact-ID semantic plan.
2. Reads a detached snapshot of the entire validated current-Case resource chain
   from the Run ledger **before invoking the provider**. Missing IDs, other
   Cases/attempts, borrowed/reused/unknown origins, tombstones, and API-deleted
   resources awaiting a tombstone are refused without provider calls.
3. Invokes the existing read-only provider once, without retries or query changes.
4. Revalidates and compares the full chain after the call, then checks the source,
   exact plan, bounded row projection and persisted immediate parent. A changed
   ancestor or a Session/File associated with a different parent cannot become
   accepted evidence.

Scope, parent and ordinary provider errors produce sanitized `BLOCKED` evidence
without returning rows; they do not rewrite an otherwise successful API result
or skip exact cleanup. Direct cancellation and cancellation masked by a secondary
ordinary exception propagate through the existing Run quarantine/runtime shutdown
path, including during physical-binding probes and provider admission.

The extra work is bounded **local ledger reads**, not remote metadata queries:
File observations add at most eight SELECT statements for the before/after chain
checks, returning at most one Run row and three resource rows per check. No ledger
transaction is held across the provider call. Existing SQLite runtime/file/schema
checks remain independently enforced. DB status normalization and the remote
query's exact environment/ID filters and row limit are unchanged.

This is an admitted-Story boundary, not access control on raw provider objects or
protection against hostile Python/local-journal mutation. Raw provider contract
tests still exercise their independent API. Remote physical DB mapping, legitimate
credentials and resource isolation remain separate admission requirements; fake
remote-shaped conformance tests do not establish a real preproduction binding.

### Explicit reviewed preproduction command

See `remote-run.md` for the strict environment-owner review receipt, offline
`prepare --observer-binding`, explicit `run --mcporter-binary --allow-observer-read`,
report provenance and failure/recovery limits. This closes the command-to-client
wiring gap; it does not assert that any actual preproduction approval or Run has
been completed.


### Combined reviewed preflight before Run preparation

`check --allow-read --observer-binding <private-review.json>
--mcporter-binary <absolute-executable> --allow-observer-read` checks HTTP
identity/actors, then the three required MCP schemas with metadata calls only.
It creates no Run/lease and cannot call `db_query`. A failed HTTP discovery skips
MCP completely; a receipt changed or expired during checking cannot yield a
successful discovery. Without observer options, `check` remains HTTP-only.
Results keep `ready_to_execute=false` and do not qualify writes, BaaS, cleanup or
a release. The full procedure and the legacy-only file-capability route gap are
documented in `remote-run.md`.


### One-command remote pilot lifecycle

`remote-run` now composes the existing reviewed `check`, offline Run preparation,
three serial shared Stories and JSON/JUnit/Markdown reporting. It requires a fresh
state directory and explicit HTTP/MCP-read plus write confirmation. The private
`remote-run.json` receipt always retains the three-Case denominator once its
output directory is owned, distinguishes uncertain preparation/checkpoints, and
stops after the first non-PASS or cancellation. No target lifecycle, fixture repair,
blind retry, force-unlock or remote recovery is added. Existing per-Case pool locks
remain per-Case—not a new cross-Run/full-suite fence. See `remote-run.md` for the
command and failure/cancellation semantics.

### Explicit native identity discovery

For approved direct BCS deployments, the optional `identity_binding` selects
`GET /auth/user` with `plain-user-info`; it is allowed only for `bcs-direct`.
Unlike the legacy default remote V1 identity path, this response has a top-level
`user_id`. Both preflight and identity probes verify the expected Human. This
binding is immutable under the Run/Profile fingerprint and does not change any
business API route or confer permission to bypass its authentication. See
`remote-run.md`; company-specific origins and live identity details stay in
private operator inputs rather than checked-in examples.


The current native binding is discovery-only: offline plan exposes a business
adapter blocker, and prepare/run/remote-run refuse it before Run/pool/Story
activity. Preflight stops after identity before V1 Bot/MCP discovery; the
identity probe remains usable. A separately reviewed native business/WS adapter
is needed to lift this restriction. Existing V1 and owned-local behavior is
unchanged; identity success alone is never preproduction pilot qualification.

### Gateway profiles matching the deployed frontend

For browser-equivalent V1 Stories, select the gateway based on the matching
frontend controllers **and** deployment overlay, not the direct-service address
shown in Bot onboarding instructions. Optional `browser_origin` carries the
explicit frontend Origin/Referer context for Cookie SSO on authenticated product
requests only; it is never forwarded to storage or token-only shared downloads.
The identity-only HTTP probe can run before unrelated Bot/DB bindings are
resolved; full preflight and execution remain strict. See `remote-run.md`.

### Read-only internal Human admission after bootstrap

An internal frontend's `bots/mine` initialization is not a read-only identity
probe: it may create/backfill the Human actor. After separately authorized
bootstrap, a gateway may opt into the reviewed `authorized-human-view` identity
binding. It proves authorization of the expected existing Human perspective via
a finite bounded candidates GET, without another initialization. Evidence labels
this as a derived authorization proof, not a returned user profile; candidate
records are discarded. No gate, fixture lease or observer requirement is waived.
See `remote-run.md` for the precise server contract and profile.

### Remote execution hold (implicit bootstrap)

Remote group setup can dispatch Bot bootstrap messages and create additional
relation edges. Unreviewed remote PRE-01/02/03 adapters block before creation;
HTTP/MCP preflight is still allowed.

For a specifically reviewed preprod full pilot, the Profile may add
`policy.remote_effect_review` with `isolated_test_runtime`,
`no_operational_channels` and `implicit_bot_execution_allowed` all set to `true`.
This is an explicit local authorization record, not server-derived permission.
A valid review admits the Group effect and the reviewed remote Session/WS
cleanup callbacks; it never turns local PASS into release qualification.
See `remote-run.md`.

### Independent native sessionless experiment

`sessionless-run` runs only **NATIVE-GROUP-01** under an explicitly reviewed
native deployment binding. It does not unblock or replace the V1 pilot. See
[remote-run.md](remote-run.md#independent-native-sessionless-group-slice) for the
source/build admission, private inputs, legacy DELETE authorization limitation,
shared-database scope, separate report denominator and quarantine behavior.

### Persisted execution evidence before cleanup or pool release

Runs using the controlled execution ledger persist an
`execution_contract_required` marker before effect-producing operations. The
same read-only evidence gate is used by Case finalization, standalone cleanup,
manual release, and JSON/JUnit/Markdown reporting. It inspects **all** registered
Sessions, including deletion tombstones, and all effects, including effects
without a valid resource binding. Resource API deletion alone cannot establish
execution convergence.

Missing contract/schema, unresolved creation, missing or mismatched termination
proof, unbound effects, pending cancellation, unknown state, and incomplete
terminal evidence block cleanup/release. A stale PASS report cannot override
this check. A missing required ledger cannot silently downgrade a new Run to
legacy behavior. Resource-only legacy Runs are labelled `NOT_TRACKED`, not
credited with verified execution safety; existing independent resource scope
checks remain mandatory.

The gate performs five bounded local SQLite reads for a tracked Run, each
inventory capped at 1,000 rows (plus one overflow sentinel). Exceeding this pilot
budget blocks rather than truncating evidence. It does not query or modify the
product database, reconcile effects, retry operations, or authorize recovery.
This is a local framework contract, not proof of remote Bot termination. The
remote Session/WS execution hold remains in place.

### Operation authority during a Run

The composition root now fences pilot adapter calls and standalone mock cleanup
with the currently held single-host pool lease. Before and after each adapter
operation it verifies the guard file's inode, the live command handle, and the
reservation's Run/profile/token binding. A detected loss is sticky for that
adapter: restoring a file does not resume requests. Structured transport
401/403 failures also invalidate authority, preserving the original failure and
request evidence. Subsequent target operations, including resource deletion,
are refused, and the Run is quarantined for explicit reconciliation.

Closing an already held Session WebSocket remains possible without acquiring
new authority; the existing Case scope guard still checks socket ownership.
This exception does not grant broad Bot stop, remote cancellation, or socket
creation permission. A late lease loss during finalization also prevents a
stale PASS checkpoint from qualifying the aggregate report.

These checks are local operation-boundary detection, **not** a distributed or
server-side fencing token. They cannot undo a request already accepted by a
service, guarantee atomicity across the network, or recreate a reservation
removed externally. Multi-runner shared leases and authorized recovery after
credential/lease loss remain separate work. The checks add local
filesystem metadata and reservation reads, not product-database or network probes.

### Producer shutdown before the PRE-02 observation socket closes

The shared PRE-02 executor now calls the scoped `session_quiesce` before closing
its WebSocket. This fences new Session actions, stops the controlled producer
subscription, and observes terminal effects while the exact owned socket remains
open. Resource cleanup later confirms that the control socket has closed and
rechecks effect convergence. Cancellation acknowledgement alone is not terminal
proof. An unknown or late effect retains resources and the pool reservation.

If termination fails, the executor still closes its held socket in a `finally`
path; this releases a local handle, not a claim that remote work has stopped.
The controlled mock explicitly distinguishes the producer subscription from the
observation socket and refuses to infer quiescence after an early socket close.
Its subscription intent and connect evidence now share the same request ID.
A remote termination mode with missing callbacks is rejected rather than treated
as a no-op.

This change exercises the runner ordering and deterministic local effect model.
Owned-local resource tests without an execution ledger do not become Bot-runtime
termination tests, and no remote stop/observe API is introduced. Remote pilot
execution remains blocked in the current implementation. The approved next
increment audits existing log/DB evidence and adds a serial default-retain subset;
see `retained-pilot-plan.md`. It is not an implemented command or a profile bypass.

### Termination deadlines and late responses

The pilot composition supplies `policy.timeout_seconds` as the Session
termination budget. The budget starts when the scoped termination state is
created, not at Group creation, and is shared across producer shutdown, effect
observation and control-channel completion. Its original timeout and absolute
deadline are persisted in the first termination-history entry. Reopening that
state retains the deadline instead of allocating a fresh timeout; missing or
invalid deadline metadata fails closed.

Both wall-clock and monotonic checks apply to a live command. Callback entry,
callback return and cleanup admission check the remaining budget, so a late
success cannot grant deletion or pool release. Expiry records `RETAINED`;
PRE-02 still releases its owned socket without claiming that external work has
stopped. Tests use deterministic clocks, not sleeps or background workers.

This is a deadline admission and late-result rule, **not** hard preemption of a
blocked synchronous callback. Adapters must still bound their own I/O, and a
future remote termination adapter must honor the approved stop/observation
budget. No worker is abandoned and no remote process is killed. Clock changes
across process restarts are not a distributed time guarantee; reopening state
alone never authorizes automatic recovery. Completed cleanup evidence is not
retroactively invalidated merely because its old deadline later passes.

### Explicit recovery of interrupted local Mock Runs

`recover-local --profile <original-local-mock-profile.json>
--state-dir <existing-private-run-directory> --allow-write
--confirm-run-id <exact-original-run-id>` is a separate cleanup-only command.
It can finish an interrupted/failed local Mock Run **only when its persisted
execution effects and Session termination proofs already confirm convergence**.
It does not execute Stories, settle pending effects, renew deadlines, or repair
missing evidence.

Recovery requires the original reservation to still exist and its command guard
to be available. It never calls `PoolLease.acquire`, steals another Run's lock,
or infers ownership from names. The original profile, manifest, journal, and
independent Mock resource snapshot must agree; open connections, unknown effects,
borrowed resources, missing proof, active commands, or inventory drift block it.
Remote profiles and owned live runtimes are not admitted by this command.

Each attempt writes a private `recovery-<id>.json` receipt recording the original
Run status and original/recovery executor digests. Resource cleanup is exact and
child-first. Original `report-*.json` and `cleanup.json` artifacts are not
rewritten. A successful recovery has `status=RECOVERED`, not a functional PASS;
the old Run cannot continue business Cases or qualify the suite. Start a new Run
to perform another regression.

A receipt marked `CLEANUP_VERIFIED / RELEASE_PENDING` is not proof that recovery
finished. It may remain after a crash or finalization failure. Only a completed
receipt records `RECOVERED / RELEASED`. A failed or partial attempt keeps the Run
quarantined and does not automatically retry. This local-only route intentionally
cannot reconcile unknown external work or authorize remote cleanup.

### Cancellation is not permission for ordinary target cleanup

An adapter cancellation, or cancellation still unwinding through a Story's
`finally` block after observer/evidence failure, now invalidates ordinary target
operations. Detection follows the visible exception chain, including cancellation
between earlier and later non-cancellation failures, without changing the
original-error reporting contract. Previously held sockets can still be released.
No new resource deletion, implicit completion, retry, or fixture-pool release is
allowed merely because the runner is exiting.

This leaves unknown external work isolated rather than pretending it has been
cancelled. A reviewed exact stop/reconciliation contract would be required to
handle that work. Owned local process shutdown remains the supervisor's separate
responsibility. Normal assertion failures without cancellation still use the
existing exact, proven cleanup path.

See `execution-safety-status.md` for the boundary between local framework evidence
and the still-blocked remote Session/WS release gate.


### Execution-state metadata preparation (retained pilot)

The existing metadata-only command now accepts `--scope execution` (default:
`pilot`). It discovers the configured database, lists its tables and describes
six fixed candidate projections: Group Sessions, messages, message deliveries,
Chat runs, downstream runs and downstream queue entries. It performs at most
eight metadata calls, **zero business-row queries**, and does not load a Profile,
Cookie, fixture pool or Run. It does not admit new remote writes.

```bash
python3 runner.py probe db-metadata --scope execution \
  --server configured-observer --database reviewed-database \
  --mcporter-binary /absolute/path/to/mcporter --allow-read
```

`METADATA_COMPATIBLE` means required columns exist, not that executions ended.
The report keeps `execution_status=UNCONFIRMED`, `ready_to_execute=false` and no
business-query/write authority. Tables lacking `env` are exposed explicitly;
row access needs separately reviewed exact downstream-ID provenance and parent/
owner correlation. Missing tables/fields, truncation or observer failures block
the probe. No arbitrary tables, SQL, content, tokens or query filters are accepted.
See `execution-observation-audit.md` for state meanings and remaining live checks.


### Local contract entrypoint for retained control slices

`retained-run` executes `RETAIN-GROUP-01` or `RETAIN-SESSION-01` through the same
business steps as PRE-01/02. It accepts a resolved local/mock
Profile or the separately reviewed supervised gateway attachment described in
`retained-remote.md`; terminal observation and production use remain pending.
The command requires `--profile`, a fresh `--state-dir`, `--run-id`, `--case`,
`--allow-write` and an exact `--confirm-run-id`.

The separate frozen manifest has no DELETE effects. Success, failure, lost-create
responses and cancellation preserve Group/Session resources and intents. It closes
only an owned WS, never calls remote cancel/quiesce, and never auto-releases the
pool. `retention.json` records exact IDs/parents or unresolved intents, an owner,
review deadline (not automatic TTL) and manual instructions; `report-<case>.json`
separates the functional result from `execution_status=UNCONFIRMED`.

Until terminal observation/reuse is implemented, even functional PASS returns
exit **2** and keeps the reservation quarantined; cancellation returns **130**.
Do not repeatedly invoke this experimental local command using the same pool,
clear its lock, or treat its mock records as remote completion evidence. Normal
PRE-01/02/03 local cleanup and their existing commands are unchanged. The existing
report command can render the retained frozen inventory as non-qualified; full
retention-aware qualification/reporting is a later increment.

### Native effective-policy discovery

See `policy-check.md` for the separate, explicitly reviewed `policy-check` command.
It reads only the native BCN manifest, identity and effective delivery policy,
projects the two test actors, and does not change gateway routing, global policy,
Run state, fixture ownership or E2E qualification.

### Reusable environment mapping and one-shot readonly plans

`environment-mapping.md` describes the new retained-recheck v2 path: persistent,
revocable target facts plus an exact source-bound read plan and explicit CLI
invocation. It avoids conflating a runner-local hourly approval with platform
permissions, without changing legacy receipts or enabling writes/pool release.


### Retained WS logs with dynamically discovered instances

The optional `retained-observ-ws` command acquires exact retained-Send WS ACKs via
an explicitly selected Observ CLI and private application/environment binding.
It rediscovers instances for every invocation, rejects production fallback, and
keeps bounded private acquisition receipts outside the original Run. It does not
load the native Cookie, modify resources or authorize pool reuse. See
[transport log evidence](transport-log-evidence.md#optional-live-ws-collector-retained-observ-ws)
for the binding contract, command, bounds and distinction between transport
acceptance and full execution completion. Existing `retained-transport` is still
offline-only; no existing Story or local-runtime path starts Observ automatically.

### Dedicated fixture cron diagnostic

`fixture-cron-check` performs explicit gateway identity/ownership preflight and
at most two exact-Bot cron configuration reads via the configured frontend.
Partial runtime failures and HTML responses are not empty success; empty lists
never authorize scope completion or pool release. See
[fixture cron check](fixture-cron-check.md) for the source/route contract, invocation,
side-effect limits and recorded live-route blocker. The native/legacy release
suites do not start this diagnostic automatically.

### Standalone WS probe product validation

The opt-in live runner regression now exercises `ws_probe` against the owned
local BCS, using the same PRE-02 Session and its actual issued token. An invalid
token is rejected; the valid token completes Upgrade/connect and local close.
The test checks that all original Run files remain byte-identical across the
probes, then lets the existing Story finish its assertions and scoped cleanup.
This validates the real local transport rather than only a fake socket client;
it does not qualify remote execution completion or release the retained pool.

### File credential boundaries on actual HTTP requests

`test_storage_wire.py` exercises the real urllib opener and redirect policy
against two independently owned loopback HTTP fixtures, not a mocked opener.
It verifies that only the initial authenticated product request carries Human
headers; allowed signed-storage downloads and PUTs receive no Cookie,
Authorization, browser context or request/trace identity. Token-only shared
content carries no Human credentials. Unapproved redirects and authentication
denial send no request to the storage fixture, while a redirect loop terminates
within the existing four-hop budget. Success and failure metadata omit share
credentials and signed storage paths. These six wire-contract tests complement
the existing file/storage unit tests and actual owned-BCS file Story; they are
not deployed BaaS, cloud storage or remote CLI qualification.

### Actual response request-ID provenance

Read, write, file-product and WS Upgrade transports now capture a bounded
`x-request-id` response header **before** HTTP status/envelope handling. A
rewritten ID therefore survives 401/403/5xx, expected negative verification and
empty/header-only responses. Failure projection carries it into the existing
Run correlation ledger; diagnostic queries use this returned ID rather than
assuming that the server accepted the submitted one. No redirects or retries
are introduced.

Metadata distinguishes `response_header_request_id` from
`response_envelope_request_id`. The established public-envelope ID retains
precedence for BCS correlation; header/body disagreement is explicitly flagged
by `response_request_id_conflict`, not treated as an exact echo or proof that a
gateway and BCS used the same ID. Header-only/error evidence selects `HEADER`;
validated public envelopes select `ENVELOPE`. Wire responses whose protocol
carries no envelope request ID at all (the station legacy exact not-found
dicts) record `response_envelope_request_id_absent` instead of fabricating an
ID or silently skipping the correlation assessment. The ledger's selected-ID column
is unchanged and source provenance remains in the Case evidence/report.

IDs must satisfy the existing bounded identifier syntax. Invalid optional
headers are omitted with a rejection marker; invalid envelope IDs fail their
response contract. Known supplied credentials (including parsed Cookie values
and Bearer/Basic values), the actual local Principal and shared-download token
are excluded before metadata publication. File request secrets travel only in
a private per-request context, including proxy uploads and expected-404
envelopes; they never become evidence or a client-wide mutable cache. Storage IDs
are not reclassified as BCS request IDs, and no response body or full header set
is retained. Tests cover safe source precedence, conflicting/invalid IDs,
credential echoes, and the error → failure evidence → SQLite ledger → exact
bounded log-query path. This does not prove universal trace export or deployed
log completeness.


### Nonzero trace correlation identifiers

The runner's supported version-00 `traceparent` contract requires both trace and
parent identifiers to be nonzero, not merely correctly sized hexadecimal text.
Generated identifiers sample the nonzero range directly. Invalid returned
trace context never becomes `effective_traceparent`; supplied/effective ledger
values and log selectors use the same validator. A malformed log selector is
rejected before a backend query, and a returned zero parent ID cannot qualify
as same-trace evidence. Existing trace opt-in, request-ID correlation, and
separate diagnostic status remain unchanged; this is not a claim of deployed
end-to-end tracing.


## Next parent-Story migration plan

The ordinary offline `plan` command includes `next_story_migrations`, backed by
`migration-contracts.json` and checked against the original Shell source hashes.
It preserves the full workspace, trusted-team and CLI-file parent boundaries,
required roles, business obligations, known soft-pass corrections and capability
gaps. It never executes Shell or repairs fixtures. All three remain NOT_MIGRATED;
33 declared obligations are not 33 passing tests, and the reviewed release
inventory remains unchanged. See `next-story-migrations.md` for the source audit
and implementation prerequisites. A changed legacy source requires explicit
review and map update rather than automatic rebaselining.

### Owned-local actor sets for upcoming migrations

`runtime fixture-smoke` now accepts an explicit `--fixture-set`:

| Set | Generated logical roles | Current scope |
| --- | --- | --- |
| `pilot` (default) | driver, member | Existing two-actor pilot fixture contract, unchanged |
| `workspace` | CEO, PM, ENG, QA | Four-role initialization only |
| `trusted-team` | CEO, ENG, PM, QA, CS | Five-role initialization only |
| `cli-file` | PM, ENG | Bot-originated file Story prerequisites only |

For example, add `--fixture-set workspace` to the existing authorized
`runtime fixture-smoke` command, using the local-singlebox example profile, an
explicit approved binary/SHA256 and a **fresh** state directory. Expanded selections are
not accepted by runtime check/start/stop or the ordinary smoke command; the option does
not affect `local-run` or select new executable Stories. Both
`--allow-local-processes` and `--allow-fixture-write` remain necessary.

Provisioning uses one generated local Human and separately registered Bot IDs.
Every Bot is independently read back for ID, owner, kind, environment, registry
status and exact generated name. Registration is create-once; the shared fixture
receipt records the selected set, known IDs and any unresolved registration.
Another set cannot overwrite it or register extra actors in the same runtime.
`check()` performs only identity/Bot reads, never repairs or initializes.

Expanded receipts retain the generic storage `schema_version: 1` and add the
versioned `fixture_contract: owned-local-role-fixtures-v1` plus `fixture_set`.
Pilot profile resolution and Principal signing reject these receipts, including
relabelled driver/member roles. This does not widen the old two-role profile.

`LOCAL_FIXTURE_SMOKE_PASS` proves local provisioning/read-back and child shutdown,
not a parent Story pass, model/runtime connectivity, legitimate per-Bot CLI
contexts, friendship/visibility/request baselines, preproduction configuration or
release qualification. Expanded receipts explicitly retain
`relationship_baseline=NOT_VERIFIED`, `story_admission=NOT_EVALUATED` and
`ready_to_execute=false`. No friendship/visibility repair operations are invoked. Registration defaults
and creator edges do not constitute verified relationship baselines.

Expanded sets discard returned Bot tokens by default and refuse the old pilot's
driver-token sink. They cannot load credentials from a saved receipt. The explicit
managed role identity lifecycle below may capture selected fresh credentials,
but does not expose a general callback or token accessor. The existing pilot CLI
handoff and its cleanup are unchanged. Runner fixture/smoke receipts contain no
returned tokens; this is not a secure-erasure assertion about product storage.

A full five-role apply plus check performs at most 19 fixture HTTP calls
(3 initialization calls + 2 per Bot during apply + 1 identity and 5 Bot reads).
Registration failure stops later roles without retry. Cancellation/failure can
leave a BLOCKED fixture receipt and a stopped runtime without a smoke report;
absence of that report is not success. There is no automatic restart, actor
adoption, remote binding, cleanup sweep or reuse of the printed local IDs in
preproduction. Use a fresh owned runtime when beginning a future Story run.


### Bound local Bot identity verification

Add `--verify-bot-identities` only to expanded `runtime fixture-smoke` selections.
The command captures fresh registration credentials inside an
`OwnedLocalRoleIdentity` context, verifies all selected roles, clears its retained
credential references, then stops the owned service and records the result. No
existing profile, remote URL, ambient Cookie or saved token can activate it.
This does not extend the pilot profile or offer executable parent Stories.

Capture is bound to the exact fixture-handler instance, runtime/Run/root/origin,
configuration and role set. Each selected credential is accepted only once after
Bot owner/state read-back; the complete initialization is checked before use.
Receipt/config/runtime drift, duplicate/replayed capture, unselected role,
initialization failure, observation error or cancellation closes the holder.
Context exit is idempotent and clears retained token references even if the
caller catches an exception inside the context. The holder never serializes or
returns raw credentials. Python copies/tracebacks and product persistence are
outside reference clearing; this is **not secure erase or token revocation**.

The fixed legacy-local probe sends only GET plus one Bearer credential to the
owned loopback service, with proxies/redirects disabled, 5-second request timeouts
and 64-KiB response limits. Its three operations use the same credential/target:

1. Bot visibility read rejects a supplied invalid token rather than falling back.
2. Bot friends self-read rejects another valid Bot. It returns no direct caller
   echo, and an invalid token here alone can fall back to the local mock Human.
3. Visibility is checked again to reject token invalidation persisting across
   the self-read interval.

The result is labelled `CREATED_BINDING_AND_BRACKETED_SELF_READS`,
`atomic_identity_attestation=false`, with the explicit assumption of no concurrent
credential mutation in the runner-owned test runtime. Bracketing does not solve
an arbitrary invalidate/revalidate race. This is a local fixture credential check,
not a universal WhoAmI endpoint or proof of per-operation business authorization.
Friend-list contents are discarded, not used to infer the caller or assert an
initial positive/negative relationship baseline. There are no user identity,
Principal or Cookie headers and no retry or alternate route on denial.

Actual owned-BCS regression covers correct identities for all three sets, a
nonempty invalid token (friends alone succeeds but visibility returns 401), a
valid foreign token (visibility succeeds but self-read returns 403), and cancelled
partial capture with cleared references and child shutdown. The normal pilot
and full release denominators remain unchanged; Gateway/SSO, real per-Bot CLI
contexts, friendship mutations and parent Story execution are still unqualified.

With the flag enabled, a five-role apply performs an extra six identity/Bot
revalidation reads during sealing, and fifteen fixed credential probe GETs.
Together with the final fixture check that is at most **40 fixture/probe HTTP
calls** on success, plus runtime health/ownership checks; each failed phase stops
later probes. The default no-identity five-role smoke remains nineteen calls.

### Read-only trusted-team baseline

For a fresh five-role local initialization, explicitly combine
`--fixture-set trusted-team --verify-bot-identities --check-trust-baseline` with
the existing fixture-smoke process/write permissions. This creates local actors,
then checks preconditions; it does not execute the friendship Story. No preprod
profile or previously initialized fixture can be attached to this command.

After all role identities verify, the baseline reads each Bot's visibility,
friend list, pending sent requests, pending received requests and visibility
again. Pending-request URLs deliberately omit `bot_uuid`: the caller must resolve
from the selected Bot token, not a Human ownership query parameter. Exact targets,
role/receipt/runtime binding, strict envelopes, directional request fields and
finite result bounds are checked. Nonempty, malformed or full 100-row lists,
visibility drift, authorization failure and cancellation stop collection and
clear the credential holder; nothing is repaired or accepted/rejected implicitly.

**Empty API output is insufficient.** The inspected legacy stores can suppress
read errors, malformed request rows or deleted peers. The baseline therefore also
uses the verified owned process's actual SQLite file, reusing the existing
runtime/config/path/inode/link checks and read-only, timed/VM-bounded connection.
A single fixed statement in a read transaction performs three existence checks
for each exact role ID under `env=local`: incident legacy friendships, sent
requests and received requests. No live-peer join hides deleted/missing peers;
all request statuses are included because these are fresh fixtures. Missing
schema, views, query errors, database replacement or any stored incident row
block the baseline. Queries do not inspect tokens, names, message content or the
separate edge-permission graph, and never mutate the database.

A successful result is `BASELINE_OBSERVED`, with original visibilities, four
required absent role pairs and `visibility_changes_required`. The pairs preserve
CEO→ENG public auto-accept, QA→PM protected acceptance, ENG→CS protected rejection
and CEO→PM CLI acceptance. Desired ENG=public, PM/CS=protected values are **plans,
not writes**; later mutation/restoration needs its own ownership and drift checks.
The immutable provisioning receipt stays `relationship_baseline=NOT_VERIFIED`;
this later observation is a separate smoke-report result, not a receipt rewrite.

`ready_to_execute` remains false and the complete Story remains NOT_MIGRATED.
API reads are non-atomic; the SQLite read transaction is a later scoped snapshot,
not a transaction spanning API and DB or a guarantee against subsequent changes.
Storage completeness covers only legacy incident rows in this owned local DB;
`edge_permission_graph=NOT_CHECKED` and remote baseline semantics remain separate.
No absence claim about unrelated actors/environments or all graph state is made.

Baseline selection adds 25 API reads and two six-read fixture revalidations to
the five-role identity smoke: **77 fixture/identity/baseline HTTP calls** on success,
plus owned-runtime health/listener checks. Persistence uses four schema reads and
one fixed statement returning five rows with fifteen indexed existence predicates;
there is no per-result fanout or unbounded row materialization. Existing SQLite
VM/time limits bound query work even if indexes/schema differ; any budget failure
is a blocker, not zero evidence. Full API scans have byte/response limits but are
not remote load-qualified and are used only for newly created isolated fixtures.

### Actual local legacy friendship API regression

`local-trust-run` creates a fresh owned local BCS and the five trusted-team actors,
verifies their credentials and API/SQLite baseline, then executes the finite
`LOCAL-TRUST-API-01` slice:

```bash
python3 apps/bcs/scripts/e2e-runner/runner.py local-trust-run \
  --state-dir "$NEW_LOCAL_RUN_DIRECTORY" \
  --binary "$APPROVED_BCS_BINARY" --binary-sha256 "$APPROVED_BCS_SHA256" \
  --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
```

All three permissions and exact Run confirmation are required before startup;
existing directories cannot be adopted or overwritten. The command accepts no
remote Profile, Cookie, arbitrary origin or pre-existing Bot IDs. This is a
separate local migration slice, not an alternative preproduction entrypoint.

The business sequence lives in `trust_api_steps.py`, checked by the existing
Case AST guard. `local_trust_api.py` supplies the finite controlled transport,
private journal, exact scope and owned SQLite assertions. It covers:

- ENG public visibility, then CEO→ENG automatic acceptance (**HTTP 200**).
- PM protected visibility, QA→PM fresh pending request (**HTTP 201**), exact
  sender/receiver pending read-back, and PM acceptance.
- CS protected visibility, ENG→CS fresh pending request (**HTTP 201**), exact
  sender/receiver pending read-back, and CS rejection.
- Exact terminal request ID/from/to/status from both API directions; API friends
  membership/absence from both sides; persisted request and friendship checks.
- Final owned incident inventory: exactly three legacy requests and two legacy
  friendship pairs. This is not a global-table or edge-permission-graph assertion.

“Already friends”, missing IDs and duplicate-pending HTTP 200 cannot pass a fresh
protected-create assertion. No first-request selection, warning-only acceptance
or skipped decision is allowed. API empty results do not replace positive SQL
rejected-state evidence and exact absence of that pair's friendship.

Each mutation, including restoration, first rechecks the bound Bot credential
with the bracketed self-read contract, then durably records an intent and its
actual submitted X-Request-ID in `local-trust-api.json`. Safe returned request IDs
and HTTP outcomes are recorded; created request IDs must also bind to the intended
pair. Lost, denied or malformed responses remain UNKNOWN/unresolved; no HTTP
mutation retry, list-based adoption or compensating relationship deletion occurs.
The product may internally retry edge-store work; the no-retry statement concerns
the runner's HTTP mutations, not every server-side DB attempt.

Original visibility is preserved. Before writing either an initial change or a
restoration, API and SQLite must agree with the expected current state. Restoration
only touches roles actually changed by this slice; an already-original value is
read-verified, a third value blocks. Every role—including untouched ones—must match
its original API/SQLite visibility before restoration PASS. This is **not CAS**;
it assumes exclusive use of the newly owned runtime, not protection against an
adversarial concurrent writer. On failure/cancellation, no further restoration
writes are attempted and the journal reports UNCONFIRMED.

Journal integrity is rechecked before dispatch and before each publication. A
changed/unreadable journal is preserved, not overwritten by stale memory. A
separate create-once `local-trust-api-integrity.json` reports that blocker; if it
cannot be published, execution still fails. Cancellation remains cancellation
when error-journal publication also fails. No outcome grants resume/replay rights.
Required assertion identities, uniqueness, statuses and bound creation outcomes
are checked before slice PASS; a missing business executor cannot report success.

Successful output is `LOCAL_TRUST_API_SLICE_PASS` only after normal child shutdown
and credential-holder closure. The intermediate journal explicitly records
`runtime_shutdown=NOT_YET_OBSERVED`; it is not by itself lifecycle qualification.
A failed run may retain journal/runtime receipts without `smoke-report.json`.
Inspect the returned exit status and owned-process shutdown evidence together.

All relationships, requests and any product-created relation/profile/grant history
remain in the stopped, isolated local database. Only visibility is restored.
No Bot/request/friendship/graph deletion, DB repair, pool release or path sweep is
performed. Legacy API+storage success does not verify edge dual-write outcomes,
visibility-sync delivery, work-order notifications, real Bot engines, Gateway,
OB, or production behavior. `cli_friendship=NOT_TESTED`, parent
`full_story_status=NOT_MIGRATED`, and `ready_to_execute=false` remain explicit.
The original parent still requires CEO→PM actual CLI request/accept/list, and
other full-suite migration obligations remain open.

The finite mutation budget is eleven calls maximum (three visibility changes,
three creates, two decisions, three restores); the usual default-protected case
uses seven. Including initialization/baseline and per-write authorization reads,
success requires at most 156 fixture/business HTTP calls, excluding health and
listener checks, with five-second and 64-KiB per-response bounds. SQL is confined
to exact owned IDs/env, bounded row projections or pair counts with the existing
VM/time limits; final inventory uses one read transaction. These are safety
bounds for small fresh fixtures, not load/latency benchmark qualification.

### Owned-local trusted-team API and actual CLI sequence

`local-trust-run` can opt into the product CLI portion of the trusted-team parent
by adding both `--cli-binary /absolute/approved/bcs-cli` and
`--cli-binary-sha256 <approved-sha256>` to the existing explicit owned-local
command. A partial selection or invalid binary is rejected before runtime startup.
The default API-only command and old release/coverage entrypoints are unchanged.
This remains a fresh-runtime command, not an attachment to preproduction or an
existing Bot pool; it accepts no remote Profile, Cookie or caller-supplied Bot ID.

The opt-in contract is `owned-local-trust-api-cli-v1`, Case
`LOCAL-TRUST-API-CLI-01`. After the three existing API flows, the same five-role
runtime executes actual product `friend request` as CEO, `friend requests` and
`friend accept` as PM, and `friend list` as CEO. Each subprocess has a separate
0700 HOME/BOT_DATA_DIR/TMPDIR and a 0600 session context. Tokens are freshly issued
owned-local credentials, never command-line arguments or inherited operator
credentials. The final list resolves CEO through the product context and must
contain exactly ENG and PM. Exact pending/accepted IDs, both directions of request
and friendship reads, and the four-request/three-friendship SQLite inventory must
agree before qualification. Original visibility is restored with the existing
API+SQL drift guards and checked for all five roles.

#### Why a local wire capture exists

The current product CLI discards successful HTTP status/response correlation and
some envelope fields. A zero exit status cannot distinguish a fresh protected
request (201) from an existing pending request (200), and list output alone does
not retain the success envelope. The runner therefore supplies a per-invocation
loopback capture endpoint in the isolated CLI context. It forwards **one exact
admitted request** to the live owned BCS, preserving its business body and Bot
credential and adding the journaled `X-Request-ID`. It does not synthesize success,
substitute an API call for the CLI, use remote targets, retry, follow redirects or
fall back to another identity. Both actual upstream status/envelope and actual CLI
JSON must match the operation contract. This is instrumented local CLI coverage,
not direct-gateway, remote-authentication or network-topology equivalence.

The capture admits only the fixed role/method/path/body for that step. Wrong
credentials, extra/unexpected methods, replay, duplicate pending 200, lost output,
nonzero exit or malformed/mismatched output prevent later writes and restoration.
Writes retain durable intent and unbound returned candidates rather than adopting
history after failure. Four CLI operations have a finite total, each subprocess
has a 12-second/64-KiB budget; capture reads also have an absolute deadline.
Cancellation fences undispatched work, interrupts active sockets and joins the
owned worker. In-flight dispatch remains uncertain rather than automatically
retried. Unknown child shutdown is surfaced independently.

Each credential file is cleared on ordinary completion/failure/cancellation.
Directory or file drift prevents overwriting another writer's state and leaves
`credential_cleanup=UNCONFIRMED`; failed clearing is not PASS. This is removal of
managed references/files, **not secure erasure or server token revocation**.
Relationships, request history and side-effect graph artifacts remain in the
isolated local DB. There is no SQL delete or relationship cleanup.

Success is `LOCAL_TRUST_API_CLI_PASS`, with separate functional, visibility and
CLI evidence plus an observed owned-server shutdown in the outer smoke report.
It covers the trusted-team parent's local legacy business operations, but leaves
`full_story_status=NOT_MIGRATED` and `ready_to_execute=false`: remote adapter/effect
admission, release manifest integration, edge permission graph/notification
qualification and the old all-module coverage gate are not supplied by this
command. The reviewed migration denominator is not reduced.

#### Versioned trusted-team registration

The CLI opt-in now loads `trusted-team-manifest.json` as suite
`owned-local-trusted-team-v1`. It registers the full parent against all five
`FRIEND.*` obligations while retaining the unchanged legacy release denominator
and zero full-migration/release credit. Ordinary offline `plan` exposes this as
`local_registered_executors`; it grants no execution or remote authority.

There are **58 required assertion IDs**. Three already-executed target-visibility
SQLite checks are now explicitly mandatory, and final stored-visibility assertion
IDs are stable (the expected original value stays in the bound baseline, not in
the ID). The default API-only contract/denominator is unchanged.

Before starting the owned runtime, the runner validates the current executor and
source-bound manifest. After runtime readiness and **before creating any fixture**,
it freezes `manifest.json` and `trust-registration.json` in the fresh runtime.
The receipt binds Run/root, Case version, executor and migration-contract digests,
plus the selected BCS/CLI binary digests. Existing fixture and business-operation
checks reject receipt, manifest, legacy source, runner source or binary-binding
drift rather than refreshing the registration. The journal records this binding
and five requirement results computed from unique, actual assertion outcomes.
The same registration is rechecked after server shutdown before smoke success.

Registration and the matching trusted-team lifecycle now feed canonical local
Case reports and the shared JSON/JUnit/Markdown renderers described below. The
generic pilot `prepare` lifecycle still cannot accept this suite with a two-Bot
pilot Profile. A smoke file alone is not a canonical Case report, and the pilot's
unchanged-friendship rule is unchanged. Remote attachment remains unavailable.

### Canonical report consistency checks

A saved Case claiming `PASS` must contain only passing executed assertions,
including required and additional business assertions. A contradictory `FAIL`,
`BLOCKED`, `SKIPPED`, `ERROR` or `CANCELLED` assertion makes report integrity and
the projected Case `BLOCKED`; the original evidence file is preserved. JSON,
Markdown and JUnit reflect the same nonpassing outcome, and `report` exits
nonzero. Optional diagnostics belong in the diagnostic fields, not in a falsely
passing business-assertion list.

An authentic Case `ERROR` remains `ERROR` and is represented by both JUnit's
`errors` counter and a testcase `<error>` element. It is not rendered as a plain
successful testcase. Existing `FAIL` and skipped/blocked/cancelled encodings are
preserved. None of these report checks authorizes replay, cleanup or pool release.

#### Five-role Profile and RunContext prerequisite

For the **registered API+CLI parent only**, verified fixture identities now produce
three private, create-once artifacts before any friendship business step:

- `profile.json`: schema 2 / `owned-local-trusted-profile-v1`, with the five exact
  created Bot/owner bindings, owned loopback endpoint, SQLite observer and fixed
  restore-visibility/retain-history policy. No Cookie, token or signing key.
- `trust-run-context.json`: binds the Profile, fixture receipt, registration,
  runtime configuration, Case version and source digests.
- `run.json`: a frozen `CONTEXT_BOUND` descriptor, not a completed canonical Run.

The context is checked through existing fixture/identity/operation guards and
again after shutdown. Changed or removed in-memory/disk bindings stop later
operations; unexpected files are preserved rather than adopted or repaired.
Partial publication and cancellation prevent business execution and retain the
partial artifacts for inspection. Registration cannot be retried to replace a
failed or completed context binding.

Schema 1 and the default API-only slice are unchanged. Schema 2 is intentionally
not a broader pilot Profile: old execution commands, shared pilot preparation and
resolution still reject it. Only the offline `report` command has a matching
adapter. New context-v2 artifacts require `canonical_reporting=COMPLETION_REQUIRED`
and retain `ready_to_execute=false`; completion is a separate sealed receipt.
Old context-v1/NOT_INTEGRATED artifacts are not automatically upgraded. Supplying
a schema-2 file is not permission to attach to another runtime or to mutate
preproduction relationships.


#### Canonical local trusted-team reports

The registered API+CLI lifecycle now publishes `report-LOCAL-TRUST-API-CLI-01.json`
after observing the owned runtime shutdown. It projects the bound actual journal,
58-assertion denominator, operation acknowledgements and credential cleanup,
original-visibility restoration, and independent process shutdown evidence.
The shared renderers write `reports/summary.json`, `reports/junit.xml` and
`reports/summary.md`. The `trust-completion.json` receipt binds the exact source/Case bytes. A durable
`trust-publication-pending.json` fence remains until all outputs and completion
verification succeed; removal of that fence is the final commit step. Completion
bytes alone, loose smoke or provisional summary files do not establish success. Checksums detect drift, not malicious rewriting
of all artifacts by their filesystem owner.

Successful local completion is `LOCAL_SUITE_PASS`, with 5 requirement results,
70 observed assertions on the default fresh fixture, and explicit
`RETAINED_LOCAL_DATABASE`. Cleanup here means visibility restoration and owned
runtime shutdown, not deletion of friendship/request history. CLI/network
instrumentation, local mock Human identity and SQLite remain distinct from
Gateway/SSO, Bot model behavior, edge/notification validation and remote OB.
The reviewed parent inventory and full release `NOT_QUALIFIED` remain visible.

Failures and cancellation after context binding also attempt a canonical Case
report, without overwriting the original journal or hiding the primary outcome.
CANCELLED remains CANCELLED even if no assertions were reached; evidence/integrity
and the overall suite can still be BLOCKED. A functional PASS can coexist with
restoration failure or uncertain shutdown. Publication errors prevent a successful
CLI result. A separate `trust-publication-failure.json`, when written, vetoes a
late or partial completion; failed publication is never automatically retried.
If writing that negative notice also fails, the already-persisted pending fence
still blocks offline success. No fallible I/O follows the commit-point removal;
a crash may conservatively resurrect the pending fence, requiring inspection
rather than claiming a newly completed run.
A failure before context binding has no invented canonical identity or report.

Use the ordinary `report --profile <runtime>/profile.json --state-dir <runtime>
--format all` command to recheck a completed Run offline. It revalidates source
hashes/bindings and rejects missing/changed evidence; it does not start a process,
query SQLite or resolve credentials. Before completion, default viewing returns a
BLOCKED summary with no output files, so it cannot occupy the active publisher's
report directory. Explicit diagnostic exports must be wholly outside the owned
Run tree. Historical context-v1 receipts are refused rather than adopted.


Publication commit is not delivery of the CLI acknowledgment: interruption after
successful pending removal can prevent the caller receiving success even though
the report was committed. Conversely, a crash that restores the pending directory
entry yields conservative BLOCKED rather than automatic recovery or replay.

### CLI-file migration: native file assertions

The complete CLI-file parent remains NOT_MIGRATED. Its reusable assertion layer
now distinguishes native `Ready`/`Bot`/`Human` DTOs from public-v1 semantic values,
explicitly handles Bot ownership, and checks anonymous metadata privacy without
supplying a synthetic session ID. Fresh-page/capability and bytes/hash checks are
separate. Existing PRE-03 still expects a Human owner; selecting a native or Bot
expectation does not change an API face or authorize a request.

The native-read integration regression uses an actual V1-created, Human-owned
file in a fresh owned local runtime. It verifies native metadata/list/capabilities,
content, and anonymous share metadata/content, then lets PRE-03 perform its normal
cleanup. It provides real wire-format evidence, **not** six CLI command coverage
credits, Bot-originated setup coverage, or remote file-service acceptance.

### Preproduction serial retained policy

`retained-run --serial-preflight` holds the same local pool guard through fresh
dependency checks and execution. For a subsequent fresh Run, add
`--previous-state-dir /private/path/previous-run`. This hands off scheduling only:
old resources/reports and quota usage are retained; complete historical remote
termination is not a prerequisite. Existing remote profile, review, observer,
read/write and exact Run confirmation arguments remain mandatory.

New-policy selected-Case success is `RETAINED_CASE_PASS` (exit 0), not full pilot
or release success. Remote execution may remain `UNCONFIRMED`, while local
connections must close and all business assertions/required evidence must pass.
Each next Run still needs fresh checks. Old/default-policy results are unchanged.
See [retained-serial-admission.md](retained-serial-admission.md) for the contract,
interrupted-handoff behavior and validation boundary. Two live preproduction Group rounds and two retained Session/WS rounds have
passed under this revised policy. The original full pilot, deletion and file
acceptance remain unqualified.

### Retained File slice rollout

The File rollout uses the separate `preprod-retained-file-v1` inventory and
`RETAIN-FILE-01`, not a change to the existing two-Case retained-v1 denominator.
See [retained-file.md](retained-file.md) for the implementation milestones,
no-delete boundary and remote storage/quota prerequisites. Shared PRE-03 business
steps are reused; original PRE-03 cleanup and release qualification stay intact.
Local/mock retained success is not BaaS/OSS or notification-delivery acceptance.

### Owned-local CLI-file setup

`local-file-setup` accepts approved BCS/CLI binary paths and SHA256 digests,
a fresh state directory/Run ID and explicit process/fixture/business permissions.
It runs the actual PM CLI create-group and session-create against one owned BCS,
verifies the Group and both implicit/explicit Session identities through API and
SQLite, clears CLI credentials, then stops only its owned child. The reusable
`OwnedLocalFileSetup` class leaves runtime ownership with its caller for the
subsequent full CLI-file implementation. See
[next-story-migrations.md](next-story-migrations.md) for the exact command, local
ENG visibility preparation and failure evidence. Setup success is not File
upload/list/capabilities/download/share/delete or complete parent-Story coverage.

### Actual owned-local CLI File Story

`local-file-run` executes the complete ten-obligation local CLI File parent using
actual approved BCS/CLI binaries, not the PRE-03 HTTP substitute. Its six CLI
leaves are upload/list/capabilities/download/share/delete, with independent native
metadata, anonymous privacy/content, SQLite and deletion assertions. It requires
the same explicit local-process/fixture/business permissions as setup and a fresh
Run directory. `local-file-report --state-dir ...` reads the source-bound completed
Run offline. See [next-story-migrations.md](next-story-migrations.md) for commands,
transport capture limits, retained history and qualification boundaries. Neither
command contacts PRE or claims legacy coverage/full-release qualification.


## Owned-local Workspace API increment

`local-workspace-api` is the first implementation increment of
`story_user_operates_group_workspace`, not a substitute for the full parent.
It starts one private owned BCS process, provisions fresh CEO/PM/ENG/QA roles,
and executes create/detail/patch/list/member/label/visibility business checks.
The frozen parent mapping retains all 17 obligations: seven selected API
obligations and ten CLI/incident/messaging/deletion obligations not run here. The
reviewed release inventory and all remote admission gates remain unchanged.

```bash
python3 runner.py local-workspace-api \
  --state-dir /private/path/new-workspace-api-run \
  --run-id workspace-api-001 --confirm-run-id workspace-api-001 \
  --binary /absolute/path/bcs --binary-sha256 '<approved-bcs-sha256>' \
  --allow-local-processes --allow-fixture-write --allow-write
```

This command accepts no remote Profile, Cookie or borrowed fixture IDs.
Permission, exact Run confirmation and fresh-directory checks occur before
process creation. A local binding freezes the source and complete parent
inventory before fixture creation. Writes are journaled before dispatch;
unknown creation outcomes are retained, never retried or adopted from a list.
The final `workspace-api-report.json` records the actual process shutdown,
credential-holder clearing and unexecuted parent obligations. JSON reporting is
currently specific to this API increment; canonical full-parent JSON/JUnit/Markdown
integration remains part of the subsequent complete Workspace executor.

No automatic Group/Session deletion occurs; generated fixtures and business
state remain in the private local database. Fresh owned CEO/PM/ENG Bots are
explicitly retained public to satisfy the product public-Group prerequisite;
QA stays protected. The case report names these fixture mutations. Stopping the owned local process
does not prove remote execution convergence or physical data erasure.
`LOCAL_WORKSPACE_API_PASS` means only the seven named API obligations passed,
never that the full Workspace Story or release suite passed.

### Workspace API plus actual CLI increment

`local-workspace-run` continues the same owned runtime after the API steps and
executes actual product CLI creation/get, member addition/get, fusion, completion
and termination/readback. It requires the approved CLI binary as well as BCS:

```bash
python3 runner.py local-workspace-run \
  --state-dir /private/path/new-workspace-api-cli-run \
  --run-id workspace-cli-001 --confirm-run-id workspace-cli-001 \
  --binary /absolute/path/bcs --binary-sha256 '<approved-bcs-sha256>' \
  --cli-binary /absolute/path/bcs-cli --cli-binary-sha256 '<approved-cli-sha256>' \
  --allow-local-processes --allow-fixture-write --allow-write
```

The complete parent denominator is still 17: this increment selects eleven
API/CLI obligations, while six incident/messaging/deletion obligations remain NOT_RUN.
The final `workspace-api-cli-report.json` does not qualify the full parent,
remote environment or release gate. The existing `local-workspace-api` command
retains its API-only scope and does not require a CLI binary or fusion fixtures.

Fusion uses the product's **local simple-context merger**, with four explicit
PM/ENG fixture text files in this fresh runtime's configured `bots_base_dir`.
It must return both exact Bot perspectives containing the expected original
texts and the product's recommendation, with a successful CLI exit. Neither
an empty default response nor JSON from a failed process counts as success.
These files do not represent engine execution, remote BaaS or LLM inference.

The inspected product CLI's legacy `fuse` and `get-group` operations do not send Bot auth.
Their local capture therefore declares separate, narrowly bound no-auth contracts
in advance and preserves that exact wire behavior; it does not inject a token,
strip a token, retry anonymously after rejection, or grant remote access. Other
CLI operations retain mandatory Bot bearer authentication. This is a known
remote-authentication gap, not proof of gateway authorization.

CLI membership adds the newly provisioned QA Bot after its own authenticated
public-visibility setup. All four generated Bot visibility changes are retained
locally and explicitly reported. Two additional CLI Groups and their initial
Sessions are retained. One Group is completed via `group-status`; a separate
initially active Group is terminated and read back. Group completion is not a
claim that initial Sessions or engine executions have ended. No Group/Session
DELETE or broad Bot stop is exposed by this increment.

### Incident configuration within the Workspace lifecycle

Add `--with-incident-config` to `local-workspace-run` to continue after the API
and CLI stages with a new CEO/PM/ENG incident Group. The selected requirements
become fifteen of the original seventeen: exact incident creation, ENG self-mute,
Workspace replacement/readback and structured routing/service settings join the
eleven API/CLI requirements. `WORKSPACE.messages` and `WORKSPACE.incident-delete`
remain NOT_RUN. The outcome is written separately to
`workspace-config-report.json`; `LOCAL_WORKSPACE_CONFIG_PASS` is not full-parent
or release qualification. The two existing commands without this option retain
their earlier scopes.

Only the exact fresh Group and default initial Session enter the incident ledger.
The CEO Bot credential creates the Group and sets routing; ENG's credential
mutes ENG, not another participant. Workspace/settings use the inspected native
local routes, whose handlers do not establish remote caller authorization.
Independent API and owned SQLite reads verify the persisted business state;
none of these observations proves gateway authorization or message delivery.
Resources remain in the private local database, without Group/Session deletion.

### Workspace message ingress and transport observations

`local-workspace-run --with-messages` includes the API, actual CLI and incident
configuration stages, then runs the original persistent message, Bot chat and
agent callback requests against the same owned server. It enables message
storage **before startup**, not by modifying a running instance. Existing
Workspace command defaults remain unchanged.

CEO/PM/ENG use freshly provisioned local Bot tokens on `/ws/bot`. The runner
checks the exact target and delivery type, successful delivery results, received
Bot frames and bounded SQLite content/sender projections. The PM display-name
mention is bound to the generated PM fixture rather than a fixed human-readable
name. Receiver evidence means transport reception only: no LLM/engine reasoning,
no synthetic Bot reply or execution-terminal event, and no remote authorization
proof. Group/Session resources remain local and retained.

The selection contains 16 of 17 Workspace requirements; Incident deletion
remains NOT_RUN. This is not a full migrated Story or release gate.
The original approved binary exposed a legacy persistence defect returning HTTP
500 when eventing was disabled. The separately tested product repair resolves
the default Session for ingress. Use a binary built with that repair; the runner
does not hide HTTP failures or substitute a different API. A legacy API message
ID can differ from its durable repository ID; both are retained and not conflated.

### Workspace incident deletion round trip

`local-workspace-run --with-incident-delete` includes messages and deletes only
the newly created Incident Group through the original product API, selecting
all seventeen Workspace obligations of the frozen report-v1 inventory. Its
private execution report is `workspace-incident-roundtrip-report.json`;
complete runs now publish bound JSON/JUnit/Markdown reports. This is local
executor integration only; the multi-environment release inventory remains unqualified.

Deletion admission is **owned-local foreground execution closed**, not remote
execution quiescence. It requires the live completed message executor, its three
actual closed receiver objects, matching runtime/fixtures/source/ledger, disabled
external execution features and no matching Provider/queued/state-machine work.
The receiver never starts an engine or emits Bot replies. A saved report, closed
socket alone, generic success response or timeout cannot authorize this deletion.
Business Session and legacy run-context terminal state are explicitly not claimed.
After exact product deletion, independent API/SQLite checks verify Group and
initial Session cascades before child-first ledger acknowledgement. Existing
message history and the other four Groups/Sessions remain in the isolated local
DB. No SQL delete, prefix delete, retry after unknown response or remote cleanup
is available through this path.

Retirement note (2026-10-26): upstream dev `72f9dff36` (Avernet #3114) removed
the group proposal/confirmation product surface end to end. The local Proposal
executor, `dual-workspace-proposal-profile-v1` contract and
`DUAL-WORKSPACE-PROPOSAL-01` case are retired from this runner with it; the
complete Workspace round trip now ends at Incident deletion with exactly
seventeen obligations. The 2026-10-15 flow record and the 2026-10-20 proposal
slice section above remain as history and are not executable claims.

### Complete Workspace canonical reports

A new `local-workspace-run --with-incident-delete` run freezes the report-v1 inventory
before fixtures or business effects. After owned process stop it publishes:

- `report-LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01.json`: the canonical complete Story projection;
- `reports/summary.json`, `reports/junit.xml`, `reports/summary.md`;
- `workspace-completion.json`: exact source and output digests.

The completion protocol uses an exclusive pending marker. Failed writes,
missing obligations, changed journals/ledgers/fusion inputs or changed output
files cannot qualify as PASS. The five stage journals remain original evidence; the retired
upstream Proposal confirmation surface is not retrospectively rewritten as completed cleanup.
All seventeen obligations, four retained Group/default Session pairs, retained
history and public local fixture visibility are represented without claiming
remote auth, engine reasoning, business Session terminality or release coverage.

Offline verification, with no credentials/network/runtime startup:

```bash
python3 runner.py local-workspace-report --state-dir /absolute/private/run
python3 runner.py local-workspace-report --state-dir /absolute/private/run \
  --output-dir /absolute/private/separate-export
```

The export directory must be outside the original Run tree. Reading does not
modify original artifacts. Output-file digests are also checked, so edits to
JUnit/Markdown/JSON cannot leave a green integrity result. Historical new-format
runs use their frozen report-v1 inventory, not subsequently changed executable
step definitions. Old pre-integration execution reports have no completion
record and cannot be silently promoted to a canonical PASS. Integrity hashes
detect drift; they are not an anti-forgery boundary against the filesystem owner.

`local_registered_executors` lists Workspace as `INTEGRATED_LOCAL` alongside
trusted-team and CLI File. The reviewed migration denominator and
remote release gaps remain unchanged.

### Optional real panel asset in the owned Local Adapter

The owned runtime can accept a real built panel file through
`LocalPanelAsset(path, sha256)`. It validates a bounded regular `.js`/`.mjs`
artifact and declared SHA256, then copies it exclusively into the new Run as
`panel/index.umd.js` before BCS starts. A private `panel-asset.json` receipt binds
Run, bytes and digest. Existing/partial files are preserved and never adopted or
overwritten; symbolic links, hard links, FIFOs, wrong hashes and oversized input
are rejected. This does not build code or fetch artifacts during runtime startup.

The existing runtime smoke supports paired flags:

```bash
python3 runner.py runtime smoke \
  --profile profiles/local.singlebox.example.json \
  --state-dir /absolute/private/new-run \
  --binary /absolute/path/to/approved/bcs --binary-sha256 APPROVED_BCS_SHA256 \
  --run-id unique-local-run --allow-local-processes \
  --panel-asset /absolute/path/to/built/index.umd.js --panel-sha256 APPROVED_PANEL_SHA256
```

When selected, smoke independently reads the owned `/manifest`, follows only the
exact expected local asset path, and checks environment/name/content type/bytes/
SHA256. It rechecks bound inputs and records `panel_asset.status` only after real
readback. With no flags, existing manifest/runtime defaults remain unchanged.
The new optional result is runtime prerequisite evidence, **not** Agent-network
Story, frontend browser functionality, preproduction behavior or release PASS.

For opt-in real asset tests, supply `BCS_E2E_TEST_PANEL_ASSET` and
`BCS_E2E_TEST_PANEL_SHA256` alongside the approved BCS test binary variables.
Synthetic byte fixtures in unit tests validate guardrails only; actual asset
serving acceptance uses the built repository panel artifact.

### Complete owned-local Agent-network Story

`local-network-run` executes the original Agent-network parent's eight ordered
helpers: platform/manifest/identity, actor list/search/status, owned/paged/bulk
Bot queries, and temporary Bot registration → token-auth onboarding → browser
URL → Human re-onboarding → capability readback → exact owner deletion/404.
It requires the approved **real built panel artifact**, not generated filler.

```bash
python3 runner.py local-network-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --panel-asset "$PANEL_ASSET" --panel-sha256 "$PANEL_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-network-report --state-dir "$NEW_RUN_DIR"
```

The runtime and four Bot fixtures are fresh and locally owned. The CEO's own
status is restored to online; only the newly registered temporary Bot can be
deleted. Ambiguous registration, mutation or deletion is not retried or recovered
by name matching. Source map, write intents, exact ownership and readbacks remain
in private Run evidence. Credentials stay in memory and references are cleared;
this is not secure erasure or remote token revocation. Owned fixtures and local
history remain for inspection, and only the owned child process is stopped.

JSON, JUnit and Markdown reports preserve all eight obligations and the original reviewed
release entries, with independent functional/evidence/cleanup results. Offline
verification binds the original outputs and journals and refuses incomplete or
changed publication; optional export must be outside the original Run tree.
Local built-in Human authentication and Bot tokens are **not** evidence of remote
gateway/SSO permissions, remote onboarding, model reasoning or full release parity.
The old singlebox and coverage entrypoints are unchanged.

### Session parent: public API/JWT/WebSocket helper

`session-migration-contract.json` freezes the **entire nine-helper** original
Session Story, including CLI, invitation, lifecycle, completion/service and
collection obligations. The first executable increment selects only the complete
`_story_connect_with_group_session_jwt` helper; it is **not** the full Session
parent or remote PRE-02 qualification.

```bash
python3 runner.py local-session-public-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-session-public-report --state-dir "$NEW_RUN_DIR"
```

This creates a fresh isolated local runtime/four-role fixture, exercises public
identity/error envelopes, Group and Session CRUD, mints a non-cacheable connection
JWT, performs a real WebSocket Upgrade and closes the owned connection before
exact local resource deletion. Public Principal signing is test-only and bound to
the live local runtime; this does not distribute remote signing keys or bypass a
real gateway. Unknown responses are retained without replay/adoption or cleanup.

Reports use `LOCAL_SLICE_PASS` for the selected helper and explicitly retain the
other eight parent requirements as `NOT_RUN`. Neither this result nor local
producer closure asserts that deployed Bot execution has stopped. The old
Singlebox coverage gate and remote execution/cleanup permissions are unchanged.
JSON/JUnit/Markdown evidence can be verified offline; export must not overwrite
any file in the original Run tree.

The public Session list without a View Actor remains Human-relative and may be
empty despite Bot ownership. This helper preserves that request, then explicitly
checks the authorized PM view. PM history must match its exact persisted system
bootstrap context. Two Sessions with two Bots create four per-recipient bootstrap
rows; deletion verifies Group/Session/participant removal **and retention of the
same four history rows**. It does not assert empty storage or erase history.

### Session public + actual CLI/sharing sequence

`local-session-sharing-run` executes the first six original Session helpers in
one owned runtime: the complete public helper above followed by actual CLI
Session create/list/get, Group invitation/join, CLI Session invite-link, Bot Group
listing, and Session invitation/join. The final lifecycle, completion/service and
collection helpers remain `NOT_RUN`; no full-parent or remote credit is granted.

```bash
python3 runner.py local-session-sharing-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --cli-binary "$BCS_CLI_BINARY" --cli-binary-sha256 "$BCS_CLI_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-session-sharing-report --state-dir "$NEW_RUN_DIR"
```

All six selected obligations are planned before fixtures or public actions.
The original public report remains under `reports/`; the composed six-helper
report is under `sharing-reports/`, and binds both stages without overwriting
that first report. Actual CLI requests use only private temporary contexts and
bounded one-attempt forwarding to the owned local runtime. Invite capabilities
are held in memory; raw output and credential-bearing join URLs are not persisted.

Each sharing helper now deletes and verifies its own exact Group/Session resources
**before the next helper creates a Group**, preserving the original sequence and
the default active-Group quota. ENG is temporarily public only inside the fresh
owned fixture and is restored to protected. Helper PASS is assigned only after
its exact delete/404/SQLite/child-before-parent ledger checks. Invite signatures,
targets and expiry are verified locally before join; a foreign capability is not
followed. Bootstrap and membership history remain retained, not silently purged.

### Complete owned-local Session parent

`local-session-run` selects all nine original Session helpers in one fresh
owned runtime. It extends the public and sharing stages with Session lifecycle,
manual chat completion/service invocation guardrails, and per-Bot collection.
The lifecycle chat must return200 and match actual history;403/409 is not a pass.
Service invocation acceptance is not engine completion, and no Bot output or
successful engine acknowledgment is synthesized.

```bash
python3 runner.py local-session-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --cli-binary "$BCS_CLI_BINARY" --cli-binary-sha256 "$BCS_CLI_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-session-report --state-dir "$NEW_RUN_DIR"
```

The full plan freezes before fixtures/operations. Each helper performs exact
creator-scoped cleanup before the next creates a Group, without raising product
quotas. History retention is independently verified, not equated with resource
absence. Prior public and sharing reports stay unchanged at `reports/` and
`sharing-reports/`; the complete parent report is published at `session-reports/`
only after the owned child stops. All three stages remain offline-verifiable.
A later-stage failure cannot become full-flow PASS or overwrite earlier evidence.
Full local parent coverage does not qualify remote execution, remote cleanup,
all reviewed release Stories, or the shared-stack coverage gate.

### Development regression cadence

During multi-environment migration, batch verification by coherent increment,
not by every edit or worker handoff. Keep tests and safety gates intact; reduce
repeated triggers rather than deleting coverage or soft-passing failures.

- During implementation, run only the directly affected fast unit/contract tests.
  After a failure, rerun the failed selection first, not the entire previous suite.
- Before committing an implementation increment, run its affected regression
  selection once against the final source. Record the selection and actual result;
  do not label a targeted run as full-suite verification.
- Start real owned BCS/CLI processes once per complete Story increment when its
  business requests, transport, identity, persistence or lifecycle changed.
  Pure report projection or case-attribution changes normally need focused tests,
  not another successful end-to-end journey through unchanged business steps.
- Batch shared-runtime/configuration compatibility tests after the related change
  set stabilizes. Security, identity, ownership and cancellation changes still
  require their specific negative tests and affected integration checks.
- Run an independent CLI acceptance when introducing or materially changing an
  entrypoint or orchestration path. Reuse existing frozen evidence for offline
  report checks when no live behavior changed; never relabel old execution as new.
- Run the complete runner regression and all-module Singlebox/artifact gates at
  integration/release milestones. This cadence does not change repository CI,
  coverage thresholds, release acceptance or the reviewed release denominator.
- Workers own focused validation of their disjoint changes. The main process
  runs integration validation after review; it does not reflexively repeat an
  unchanged worker selection. Any shared-dependency change invalidates relevant
  earlier evidence and requires the corresponding tests again.

### Complete owned-local Provider/Organization parent

`local-provider-run` executes all31 original HTTP operations, including the entire
15-operation organization helper, fail-closed attribute/roster/takeover checks,
runtime-token negative checks, disable/enable and original member/Bot retirement.
It requires only an approved BCS binary plus explicit local process/fixture/write
permissions; it does not resolve remote profiles or cookies.

```bash
python3 runner.py local-provider-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-provider-report --state-dir "$NEW_RUN_DIR"
```

The declared downstream fixture returns503 for exactly one authenticated local
Provider2.0 chat.send. The actual admin run is readable as`delivery_failed`; this
preserves the original acceptance/readback obligation but **does not demonstrate
successful Provider engine execution**. No successful ACK or Bot output is faked.

Five resource intents and exact parent/ID histories are durable. Member removal
and Bot retirement are original business steps verified against local SQLite;
logical deletion is not physical purge. Provider, organization and credentials
remain in the private owned database; admin-run state is process-local. Reporting
uses`cleanup=RETAINED`, never claims total erasure, and requires actual BCS stop,
receiver closure, credential-reference clearing and unchanged workspace fixtures.

JSON/JUnit/Markdown reports freeze the31-operation/18-observation evidence and
ledger. Offline report verification never contacts the previous runtime or
rewrites original artifacts. Local parent success does not qualify remote
execution, all reviewed release Stories, or the shared-stack coverage gate.

### Complete disabled-channel HTTP parent

`local-channel-http-run` preserves all six original HTTP operations of
`story_user_validates_external_channel_setup`. It checks the disabled-bridge
binding rejection, full/target empty lists, no-op pause/delete of a fresh missing
ID, and final absence of phantom bindings. It is not the separate CLI Channel
Story and does not prove an enabled provider or actual IM message delivery.

```bash
python3 runner.py local-channel-http-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-channel-http-report --state-dir "$NEW_RUN_DIR"
```

The executor only accepts its fresh owned runtime with default disabled Channel
wiring. Before business calls and again before each no-op mutation it checks
empty owned-local Channel binding/conversation/participant tables and preserved
workspace identities. The finite target is a missing ID derived from this Run,
not a borrowed resource. A lost response becomes UNKNOWN with no retry or cleanup.
All requests are journaled before dispatch. Reports require actual process stop,
credential-reference clearing, six exact responses and unchanged local state;
offline verification does not contact the previous runtime or alter its evidence.

### Complete disabled-channel CLI parent

`local-channel-cli-run` preserves the separate original CLI Channel Story:
seven actual product CLI commands plus creator-scoped Session and Group deletion.
The disabled bind must return nonzero with its specific diagnostic and must not
leak the synthetic client secret in either stdout or stderr. Ordinary failures
are never accepted as this negative case.

```bash
python3 runner.py local-channel-cli-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --cli-binary "$BCS_CLI_BINARY" --cli-binary-sha256 "$BCS_CLI_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-channel-cli-report --state-dir "$NEW_RUN_DIR"
```

CLI commands use private temporary contexts and exact one-request forwarding to
the owned runtime. The only new DELETE forwarding contract is the declared
missing-Channel-binding no-op, with durable empty-table admission; Group/Session
business deletion is a separate exact-owner path. ENG is temporarily public only
inside this fresh fixture, then restored. No friendship changes or quota increases.

The three owned resource rows are tombstoned child-before-parent after API and
SQLite deletion proof. Four bootstrap context histories remain retained even
with `store_messages=false`; the runner verifies their digest rather than deleting
them or misclassifying them as unrelated state. Owned process stop and credential
context clearing precede canonical JSON/JUnit/Markdown publication. This does not
qualify actual external IM delivery, remote auth/cleanup or full release coverage.

### Local Story command ownership

Standalone local Story parsers and dispatch live in
`bcs_e2e/local_story_commands.py`; `cli.py` retains shared/global, remote, runtime
and original pilot handling. New local Story commands should extend the explicit
finite local command set and corresponding parser/dispatch functions rather than
adding another block to the main CLI. This is structural separation, not dynamic
plugin loading or automatic authorization. `write_reports` is passed explicitly
so existing CLI interception/tests retain the same behavior.

### Complete owned-local Direct Chat parent

`local-direct-chat-run` executes the original preliminary ping and main async
conversation/read/cancel/terminal-read Story. Both returned Runs are journaled;
the preliminary ping has separate cancellation/readback lifecycle checks rather
than disappearing from the denominator or remaining untracked.

```bash
python3 runner.py local-direct-chat-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --state-dir "$NEW_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write
python3 runner.py local-direct-chat-report --state-dir "$NEW_RUN_DIR"
```

One owned PM WebSocket receives two exact Run/session/caller/target/message frames.
It sends no delivery-success ACK and starts no engine. PM becomes public only
inside this fresh isolated fixture and returns to protected on the successful
path; friendship/provider state must remain unchanged. Failure does not trigger
unadmitted restoration, retry, rediscovery or broad cancellation. The outer
lifecycle closes the held receiver and stops only its owned BCS child.

Both effects require cancellation acknowledgement **and** exact terminal readback
before the ledger marks CANCELLED. This proves memory-store Run lifecycle and
local transport closure, not external engine termination or persistent DB state.
Direct `chat:...` session keys are not treated as BCS Group Sessions; no Group or
Session DELETE is issued. Offline JSON/JUnit/Markdown reporting requires both
Run effects, the original five assertions and the full twelve-operation journal.

### Owned-local collaboration template assets

The Structured collaboration parent begins by reading the real template catalog.
Owned BCS defaults still create an **empty** template directory; this prerequisite
is opt-in and does not yet register the complete Structured parent executor.

`LocalTemplateAsset(directory, manifest_path, manifest_sha256)` accepts an actual
repository seed directory and a separately reviewed JSON manifest. Its shape is:

```json
{
  "schema_version": 1,
  "contract": "bcs-local-template-asset-v1",
  "files": [
    {"path": "registry.yaml", "sha256": "<approved file SHA256>", "size": 123},
    {"path": "en-US/single-bot-guided-answer.yaml", "sha256": "<approved file SHA256>", "size": 456},
    {"path": "zh-CN/single-bot-guided-answer.yaml", "sha256": "<approved file SHA256>", "size": 789}
  ]
}
```

The values above illustrate the schema, **not an executable approval**. List every
actual catalog YAML, not only the three illustrated entries. Supply the SHA256 of
the manifest file separately; changing an asset requires reviewing/updating its
manifest and approval digest. `README.md` is not a runtime asset. Both supported
locale directories and guided-answer templates are mandatory. No catalog files
are generated or downloaded by the installer.

Pass the asset as `template_asset=` to `OwnedBcsProcess` (or the configuration
preparer). It validates file count/size, relative paths, links, exact catalog
membership and hashes, then copies exclusively into the fresh owned runtime's
`templates/` and writes private `template-assets.json`. Existing destinations are
never adopted or overwritten; failed copies remain available for diagnosis.
Without this input existing local callers keep their previous behavior.

`verify_owned_templates(runtime)` performs two fixed read-only HTTP operations:
list the zh-CN catalog and get the guided-answer JSON detail. It compares the
catalog membership and exact authoring YAML with the approved owned assets,
rechecks child ownership/config/receipts, and reports
`scope=OWNED_TEMPLATE_CATALOG_ONLY`, `full_story_status=NOT_MIGRATED`.
It does not create Groups, start workflows, connect an engine or read Cookies.
There is no new standalone command for this prerequisite; the complete Story
entrypoint will consume it when its full lifecycle is implemented.

### Complete owned-local Structured collaboration parent

`local-structured-run` now preserves all17original operations from
`story_user_runs_structured_collaboration`. Provide the approved BCS binary and
real template catalog manifest described above:

```bash
python3 runner.py local-structured-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --template-dir "$TEMPLATE_DIR" --template-manifest "$TEMPLATE_MANIFEST" \
  --template-manifest-sha256 "$TEMPLATE_MANIFEST_SHA256" \
  --state-dir "$FRESH_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write

python3 runner.py local-structured-report --state-dir "$FRESH_RUN_DIR"
```

The catalog/detail, inline definition, patch/version and rejected upgrade,
explicit Run status/graph/node/Human checks, cancellation, rejected rerun and
creator-scoped Group deletion retain their original requests and assertions.
Group creation keeps its default implicit Run: **both** implicit and explicit
Run/Session pairs are recorded. A CEO WS receiver accepts two exact Node dispatch
frames but sends no successful execution ACK and starts no engine. The PM fixture
is temporarily public and restored only on the successful path; friendship and
other fixture configuration are not changed.

Each operation has durable intent before dispatch. An unknown outcome is never
retried or rediscovered. Lost Group creation leaves its implicit effects unknown;
lost cancellation remains cancellation-requested. Failure performs no extra
fixture restoration, Group deletion or broad Bot stop. Only held local child and
socket handles authorize shutdown.

The successful path cancels and reads back both Runs, checks exact completed
service Sessions (`error_message=aborted`) in owned SQLite, then executes the
original Group DELETE. Group/Session/runtime-binding removal is verified while
Run/Node/definition history is retained. Canonical cleanup is **RETAINED**, not
physical database erasure. JSON/JUnit/Markdown and independent offline projection
cover17requirements,22HTTP operations and the reviewed release parent denominator.
No remote authentication, external engine completion, production/aliyun or full
release qualification follows from this local result.

### Event Webhook owned receiver prerequisite

The Event Webhook parent is source-mapped but is **not yet a registered complete
executor**. Its local sandbox now provides `OwnedLocalEventSink(run_id)`:

1. Enter one fresh ephemeral loopback listener; no shared server is adopted.
2. Persist the Story's create intent, then call `arm_create_intent(run_id)`.
3. Use `sink.url` only in the admitted inline Subscription request.
4. Bind the exact create-response Group and Subscription with
   `bind_group(group_id, subscription_id)`. Earlier callbacks remain buffered,
   not validated evidence, until this binding succeeds.
5. `wait_for(event_type, group_id, deadline)` observes an actual bound callback;
   the monotonic deadline must be finite and no more than five seconds away.
6. Stop owned BCS first, then close the owned receiver; save its sanitized
   `snapshot()` with the Story evidence. No remote reset/clear endpoint exists.

The sink accepts only the four event types required by this original Story,
checks canonical envelope identity and absence of Authorization, and enforces
unique event IDs and strictly increasing sequences per stream. The synchronous
`event_subscription.test` has its own subscription-scoped stream at sequence1;
repeating that sequence is rejected too. It is not substituted for asynchronous
Group/Session/message delivery. Unknown methods/paths, links to foreign Groups,
invalid headers/bodies, quota violations and ordering violations latch failure.
Limits are32records,64accepted TCP connections,64KiBbody and1second body reads;
empty TCP connections count toward the limit. Raw payload content and endpoint
capability URLs do not appear in snapshots.

Pass the live receiver as `event_sink=` to `OwnedBcsProcess` with
`store_messages=True`. The same Run identity is rechecked before spawn.
Provider/Eventing sinks cannot be combined in this finite profile. Only Eventing's
separate outbound policy permits local HTTP/nonstandard ports; the general
outbound policy continues blocking private networks and loopback. Other callers
retain disabled Eventing and unchanged defaults. There is no new standalone CLI
command until the complete parent execution/report lifecycle is implemented.

### Complete owned-local Event Webhook parent

`local-event-run` executes the20mapped original obligations:13API operations,
actual Group/Session/message/test callbacks, isolated receiver setup, local
Principal and stream-order checks. The previously described receiver is now
integrated into this complete local parent:

```bash
python3 runner.py local-event-run \
  --binary "$BCS_BINARY" --binary-sha256 "$BCS_SHA256" \
  --state-dir "$FRESH_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write

python3 runner.py local-event-report --state-dir "$FRESH_RUN_DIR"
```

The runner uses the original public Group/Subscription/Session APIs and the
original legacy Group-message route. It preserves subscription revision changes
1(active),2(disabled),3(active),4(deleted), synchronous test delivery, asynchronous
callbacks and original business deletes. Both implicit and explicit Sessions are
recorded. The explicit Session callback is matched by its own subject ID, not by
reusing an earlier implicit Session event.

**Message identity distinction:** the legacy send response's `message_id` is not
the same identity as the persisted message/event subject in this product path.
Both are recorded separately. Before the one admitted send, the exact owned Group
and PM sender must have no matching test message; afterward one unique persisted
row must match Group, sender, known Session and exact original content. Its logical
ID is then required in the actual `message.created` callback. Wrong scope,
ambiguous duplicates or missing rows block; they do not trigger discovery/retry
or acceptance of an arbitrary Group event.

Only the new Run's exact Subscription, explicit Session and Group may be deleted.
Failure never retries a write, resets a shared mock, changes fixture relationships,
or performs additional business cleanup. Local BCS stops before its receiver;
Principals and Bot credential references are cleared. The final report requires
API results plus exact SQLite evidence and matches retained callback Event IDs to
successful Delivery history. The Subscription tombstone, Event/Delivery history
and message row remain: cleanup is **RETAINED**, not physical data erasure.

No Bot receiver/engine is started. Actual Webhook HTTP delivery is verified, not
Bot reasoning or successful delivery to an engine. Local Principal signing is a
local test boundary, not SSO or production Gateway qualification. Reviewed parent
release scope, remote gaps and all CI/release gates remain unchanged.

### Slow-Judge owned sandbox prerequisite

The slow-Judge parent has22mapped obligations but is not yet a complete registered
executor. `OwnedLocalJudgeSandbox(run_id)` provides an isolated positive Provider
acknowledgement endpoint and one blocked OpenAI-compatible Judge endpoint. It is
not the existing503negative Provider sink and must not be substituted for it.

- Start one new sandbox; neither endpoints nor shared mock controls are adopted.
- Register the Provider using `provider_url`, publish its Bot with provider ref
  equal to the Run ID, then call `arm_provider(provider_id, bot_id, bearer_token)`.
- `wait_provider_request(body_id, monotonic_deadline)` selects an exact request,
  never the latest request. Up to3immutable receipts preserve implicit review,
  explicit review and publish. `body.id` and `X-BCN-Message-Id` are separate fields.
- `wait_judge_started(deadline)` observes the one real HTTP request to this fixture.
  It remains blocked until the owner calls `release_judge()`. The deterministic
  approved decision matches the old mock; it does not represent model reasoning.
- `close()` aborts blocked/partial requests, joins owned threads and clears
  sandbox-held credential references; it never releases an approved decision
  just to finish shutdown. There is no HTTP reset/clear/release endpoint.

Budgets are3concurrent/16total connections,3Provider receipts,1Judge request,
64KiBrequest bodies,1second body reads and20seconds maximum Judge hold. All waits
use finite monotonic deadlines; invalid credentials, duplicate requests, quota
violations and foreign endpoints fail closed. Snapshots contain only bounded
identities/hashes/lifecycle evidence, not credentials, endpoint URLs or prompts.

The optional `judge_sandbox=` input to `OwnedBcsProcess` requires the same live
Run, `store_messages=True` and no separate Provider/Eventing sink. The actual
local OpenAI-compatible adapter points only to the owned listener; its key is
passed through `BCS_E2E_LOCAL_JUDGE_KEY` in the child environment, not written into
configuration. This controlled profile enables Provider loopback while retaining
private-network blocking; all defaults remain `llm.type=none`. The future Story
lifecycle must clear the copied environment key after BCS stops, in addition to
closing the sandbox. Clearing references is not secure memory erasure.

Fixture tests verify these mechanisms, **not yet** the real BCS one-second
callback/Judge/continuation chain. That acceptance belongs to the complete Story
integration, not repeated prerequisite smoke runs.

### Complete owned-local Caller execution Story

The upstream caller-execution Story
(`apps/bcs/scripts/e2e-test/caller_execution_story.py`, dev
`74dd751b623b7701608374fc4c7c4a6cf58278da`) is a self-contained Python script: it owns
its mocked Provider, the instrumented test-utils BCS process, that process' SQLite store
and the cache-loss restart. The runner treats it as one black-box child process instead
of porting its code, and freezes the script plus its coverage wrapper before any
executor exists.

Build the frozen test-utils binary explicitly (auto-build is disabled for this slice):

```bash
cd apps/bcs && cargo build -p bcs --features test-utils --bin bcs-caller-e2e
sha256sum target/debug/bcs-caller-e2e
```

Then run and report:

```bash
python3 runner.py local-caller-execution-run \
  --binary "$CALLER_E2E_UTILS" --binary-sha256 "$CALLER_E2E_UTILS_SHA256" \
  --state-dir "$FRESH_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write

python3 runner.py local-caller-execution-report --state-dir "$FRESH_RUN_DIR"
```

The run freezes permissions, state directory and binary SHA256 before any process
starts, rejects symlinked or non-fresh state directories, redacts token-bearing child
streams out of every evidence file, parses the three Story markers in strict order, and
requires all 9 obligations and the observed HTTP assertion count. The child writes one
shared receipt; RUN and REPORT never rewrite the original evidence, and offline rendering
outside the state directory is rejected.

This is an independent local-only slice: `release_parent_denominator=false`,
`independent_slice=true`, `remote_validation=NOT_RUN`, `grants_release_credit=false`,
evidence granularity `SHARED_CHILD_RECEIPT`. It is not part of the reviewed case coverage
registry, it does not touch the pilot inventory or trusted-team manifest, and it starts
no OpenClaw/engine-adapter process of its own.

### Complete owned-local Draft group Story

The upstream collaboration Draft-group Story
(`apps/bcs/scripts/e2e-test/collaboration_draft_group_story.py` with the frozen
`indentation-regression.json` fixture and `http_provider_judge_mock.py`) is
treated as one black-box child beside a second black-box mock child and one
owned BCS runtime. The Story registers the local Human principal and a
disposable author Bot, registers the scripted coverage Provider and its
driver/reviewer provider Bots, completes friendship, authors an immutable
Draft revision, shares the V1 preview reference, runs grant and browser guard
negative matrices, launches the edited collaboration through the browser API,
asserts persistence and Draft immutability, revokes the share and tears every
resource down.

The BCS binary stays owner-built and opt-in (`BCS_E2E_TEST_BINARY` /
`BCS_E2E_TEST_BINARY_SHA256`, `auto_build=false`). Then run and report:

```bash
python3 runner.py local-draft-group-run \
  --binary "$BCS_E2E_TEST_BINARY" --binary-sha256 "$BCS_E2E_TEST_BINARY_SHA256" \
  --state-dir "$FRESH_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write

python3 runner.py local-draft-group-report --state-dir "$FRESH_RUN_DIR"
```

The run rejects a state directory that already exists and requires a
non-symlink parent; the owned runtime creates the directory and the runner
freezes permissions, the 17-obligation binding (`LOCAL-DRAFT-GROUP-01`,
suite `owned-local-draft-group-v1`, Human `39464`), the 34-story release
independence fence and the BCS binary SHA256 before the coverage mock child
starts. The mock child publishes a ready file on an ephemeral port and the Run
requires zero observed Provider and event-webhook requests with the strict
Judge never started; both children are terminated by process group even on
timeout or SIGTERM (SIGTERM canonicalises to CANCELLED). The four child
streams pass a bot-token/provider-admin-token/Bearer redaction gate before any
bounded log or receipt is written, the single Story marker must prove live
HTTP assertions including cleanup, and the binding, binary, executor digest
and every evidence file stay under the drift fence. RUN and REPORT never
rewrite original evidence and offline rendering inside the state directory is
rejected.

This is an independent local-only slice: `release_parent_denominator=false`,
`independent_slice=true`, `remote_validation=NOT_RUN`,
`grants_release_credit=false`; it is outside the reviewed case coverage registry and
both pilot manifests, and it starts no OpenClaw or engine-adapter process of
its own.

### Complete owned-local Result queue Story

The upstream one-shot Result-queue Story
(`apps/bcs/scripts/e2e-test/result_queue_story.py`, inheriting the frozen
`fixed_loop_story.py` base) is treated as one black-box child beside a second
black-box mock child and one owned BCS runtime. The Story registers the Human
principal, a disposable static-bearer native-tool Provider, the release-domain
driver and observer Bots and their friendship, snapshots and replaces the real
message-delivery policy through compare-and-swap, starts one state-machine
run, answers the scheduled node delivery, proves owner Send plus observer
Inject admission rows, completes and consumes the durable result, re-reads the
unique published history and restores every owned scope.

The BCS binary stays owner-built and opt-in (`BCS_E2E_TEST_BINARY` /
`BCS_E2E_TEST_BINARY_SHA256`, `auto_build=false`). Then run and report:

```bash
python3 runner.py local-result-queue-run \
  --binary "$BCS_E2E_TEST_BINARY" --binary-sha256 "$BCS_E2E_TEST_BINARY_SHA256" \
  --state-dir "$FRESH_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write

python3 runner.py local-result-queue-report --state-dir "$FRESH_RUN_DIR"
```

The run rejects a state directory that already exists and requires a
non-symlink parent; the owned runtime creates the directory and the runner
freezes permissions, the 18-obligation binding (`LOCAL-RESULT-QUEUE-01`, suite
`owned-local-result-queue-v1`, Human `39464`) and the BCS binary SHA256 before
the Provider/Judge mock child starts on an ephemeral port with a ready-file
handshake. This slice freezes a scheduled-callout boundary rather than a
zero-traffic one: exactly three provider webhooks (answer node, final result,
observer followup) must be observed, while the event-webhook counter stays
zero and the strict Judge never starts. All four child streams pass the
bot-token/provider-admin-token/Bearer redaction gate before bounded logs land,
the single fixed Story marker must be present, and both children are terminated
by process group even on timeout or SIGTERM (SIGTERM canonicalises to
CANCELLED). RUN and REPORT never rewrite original evidence and offline
rendering inside the state directory is rejected.

This is an independent local-only slice bound to the already unmapped
`story_state_machine_result_queue` backlog parent:
`release_parent_denominator=false`, `independent_slice=true`,
`remote_validation=NOT_RUN`, `grants_release_credit=false`; it is outside the
reviewed case coverage registry and both pilot manifests, and it starts no OpenClaw
or engine-adapter process of its own.

### Complete owned-local Nested fixed-loop Story

The upstream nested fixed-loop Story
(`apps/bcs/scripts/e2e-test/nested_fixed_loop_story.py`, inheriting the frozen
`fixed_loop_story.py` base) is treated as one black-box child beside a second
Provider/Judge mock child and one owned BCS runtime. The Story validates both
nested definitions through the real product CLI, executes the two-level Bot
loop (17 nodes, 15 completed and 2 skipped) and three-level Human loop
(31 nodes, 21 completed and 10 skipped), proves terminal-callback replay
rejection, pending-node scheduling, iteration-context isolation and Human
authentication boundaries, verifies machine-output equality and finally clears
the mock carrier.

The BCS and product CLI binaries stay owner-supplied and opt-in. Then run and
report:

```bash
python3 runner.py local-nested-fixed-loop-run \
  --binary "$BCS_E2E_TEST_BINARY" --binary-sha256 "$BCS_E2E_TEST_BINARY_SHA256" \
  --cli-binary "$CLI_BINARY" --driver "$DRIVER_ARGUMENT" \
  --state-dir "$FRESH_RUN_DIR" --run-id "$RUN_ID" --confirm-run-id "$RUN_ID" \
  --allow-local-processes --allow-fixture-write --allow-write

python3 runner.py local-nested-fixed-loop-report --state-dir "$FRESH_RUN_DIR"
```

The run rejects symlinked, non-executable CLI input, a stale state directory
and mismatched permissions before any child starts. It freezes the 16-obligation
binding (`LOCAL-NESTED-FIXED-LOOP-01`, suite
`owned-local-nested-fixed-loop-v1`, Human `39464`), both binary inputs, the
executor digest and the Story CLI arguments. Both complete Bot and Human
markdown markers must appear exactly as frozen, while the post-cleanup control
plane must show zero provider webhooks, zero event webhooks and a strict Judge
that never started. All four child streams pass the token redaction gate before
bounded evidence lands; Run and Report never rewrite original evidence and
offline rendering inside the state directory is rejected.

This is an independent local-only slice bound to the already unmapped
`story_nested_fixed_loop_workflows` backlog parent:
`release_parent_denominator=false`, `independent_slice=true`,
`remote_validation=NOT_RUN`, `grants_release_credit=false`; it is outside the
reviewed case coverage registry and both pilot manifests, and it starts no OpenClaw
or engine-adapter process of its own.
