# Retained file slice: implementation and remote admission boundary

## Scope

The next file slice is `RETAIN-FILE-01` in a separate
`preprod-retained-file-v1` inventory. The existing two-Case
`preprod-retained-v1` inventory and all frozen historical Run files remain
unchanged. Both inventories retain the original reviewed release denominator.
This is a PRE-03 Web API subset, not CLI-file, notification-delivery, deletion,
physical-erasure or full-release qualification.

It reuses `file_control_steps`: Human creates a normal Group (including its
implicit Session), creates an explicit Session, prepares/uploads/completes one
small text file, checks list/metadata/owner/backend and the exact persisted file
projection, then compares authenticated and anonymously shared content against
the original bytes. A nullable metadata hash never skips either comparison.

The retained inventory is exactly one Group, two Sessions and one File. The File
must parent the **explicit** Session, not merely any Session in the same Group.
Share capabilities stay in memory and are not report fields or independently
manageable ledger resources. Success, failed assertions, ambiguous responses and
cancellation do not authorize file/Session/Group deletion or remote cancellation.

## Delivery order

1. **Shared steps**: extracted from PRE-03; original cleanup remains unchanged.
2. **Local/mock retained contract**: versioned inventory, execution, independent
   JSON/JUnit/Markdown reporting and serial fresh-preflight reuse. Remote file
   admission must fail before reads, preparation, reservation transfer or writes.
3. **Remote admission**: explicit file-effect review, bounded storage egress,
   durable file/byte/share quota accounting and reviewed profile transition.
4. **Real PRE acceptance**: new Run under the designated runner guard, fresh
   dependencies and separately bound observation authority; no blind retries.

Implementation/validation evidence for each increment belongs in the milestone
section below. Steps 3–4 are not enabled by passing step 2.

## Remote prerequisites still to implement

- Preserve the existing Group/Session baseline and every reserved maximum. The
  current v1 quota cannot admit a File. A versioned extension must account for
  file count, bounded uploaded bytes and up to two share-mint attempts (implicit
  completion link plus explicit share). Reserve maxima before effects; uncertain
  responses do not refund them. These are admission budgets, not measured OSS
  bytes or autonomous Bot execution limits.
- Keep the same fixture pool. A reviewed quota transition must bind the exact v1
  journal digest and its usage; creating a new pool/registry is not a migration.
  Once extended, subsequent Group/Session runs must preserve new dimensions too.
- Existing PRE inputs currently omit `file_transfer.allowed_storage_origins`.
  Configure only source/owner-reviewed exact origins. A returned signed URL does
  not grant egress. Product Cookie, Authorization, Origin and Referer must not be
  sent to storage, including redirects. Do not guess a bucket origin or allow a
  wildcard. Never persist signed upload/download URLs in reports.
- Adding that profile field changes the profile fingerprint. Do not edit old
  Run inputs, completion receipts or reservation digests. Add a separately bound,
  narrowly scoped transition which compares the old frozen profile with the new
  one under the same guard, permits only the approved storage-origin addition,
  and checks all new dependencies before transferring scheduling authority.
  Identity, actors, gateway, database, pool and admission generation may not
  change under that receipt.
### PRE share-content redirect characterization and probe alignment — 2026-11-01

Live PRE characterization (fresh share mint on the retained r20 fixture,
evidence `20261101-session-file-303-characterization`): the authenticated share-content
GET now answers 303 with a signed storage URL on the allowlisted OSS origin, and the
credential-free storage hop returns the exact original bytes (29/29, sha256 match).
Several share flows are therefore healthy through the bounded redirect chain. The same
window showed station ACE ingress drift: anonymous share-content GET oscillates between
the reviewed 200 JSON login projection (denial_kind ACE_LOGIN) and a 303 redirect, and
the anonymous invalid-token route probe currently sees the 200 login wall instead of
the reviewed 40400 not-found envelope.

Runner contract alignment (fail-close preserved):

- The anonymous share probe accepts a 303 only when Location resolves to the configured
  product origin (absolute reviewed share origin, entrypoint origin, or same-origin
  relative); it is recorded as denial_kind HTTP_303_PRODUCT_REDIRECT with the Location
  origin only (query, tokens and bodies never persisted).
- An anonymous 303 whose Location targets a storage origin is a security anomaly — a
  signed content URL would be exposed without credentials — and remains a hard failure,
  never a normalized denial. Any unreviewed absolute origin also fails closed.
- The invalid-token anonymous route probe classifies the 200 ACE login wall explicitly
  ("intercepted by the ACE ingress login wall before BCS token validation") and still
  refuses it as route readiness; the reviewed 40400 envelope stays the required proof.
- Authenticated downloads keep the existing hop semantics: hop 0 carries Cookie, storage
  hops carry no credentials, only allowlisted storage origins are followed, and hops are
  journaled in the receipt.

Environment-owner items (not runner fixes): station ingress must stop oscillating the
anonymous share GET between the JSON login projection and 303, and restore the reviewed
40400 not-found envelope for invalid anonymous tokens. Cookie renewal is not a fix.

- Admit the file observer projection explicitly. Group/Session observation
  receipts do not automatically authorize broader database queries. No bucket,
  prefix, Bot-wide or database cleanup is permitted.

## Completion side effects are not notification assertions

Read-only source review of public commit
`5c5418d84b2f73095d9229749cd726df5401af43`,
`apps/bcs/crates/application/v1/bcs-app-session/src/file.rs`, shows that complete
first commits the file, then best-effort mints a 15-day no-auth link and sends a
GenericNotification to the other Bot participants. The Human uploader excludes
neither dedicated test Bot. Notification/link failures can be warnings rather
than a failed complete response. The separate explicit share operation is another
mint attempt. Review the actual target build again before remote admission; a
source review is not a binary attestation.

Consequently, metadata and byte PASS do not prove notification delivery, absence
of downstream effects, or complete remote quiescence. Declare these effects in
the remote review, preserve `UNCONFIRMED` where appropriate, and do not repeat
complete to repair an uncertain notification. Complete-stop evidence and resource
cleanup remain deferred under D40, not hidden prerequisites for every new Run.

## Milestones

- Shared steps: local commit `d74f0ffb4`; 55 focused tests passed, including a
  real owned-BCS file lifecycle/native views with cleanup. After a semantically
  equivalent cleanup-reference adjustment, 12 file tests and the real owned-BCS
  file test passed again. No BaaS/OSS or live PRE file acceptance is claimed.

- Local/mock retained executor and reports: implemented with a separate one-Case
  manifest, four-resource ownership tree, no deletion and serial preflight.
  Remote File remains rejected before effect-review construction or any HTTP,
  reservation, quota or business operation. Old retained-v1 file effects remain
  disallowed; its manifest and Group/Session quota code are unchanged.
- Validation on 2026-10-14: the worker's 210 focused checks passed. After the
  integration tightened suite-specific effect declarations, 190 focused checks
  passed (17.280s, no skips), including real owned-BCS original PRE-03 file
  lifecycle/native views, new retained-file tests, serial/report/remote/quota
  compatibility and AST guard coverage of shared business steps.
- Two independent CLI local/mock rounds each passed 15 observed assertions and
  produced `RETAINED_CASE_PASS` with one selected Case, 29 unqualified release
  parents, `UNCONFIRMED` execution and retained resources. These are framework
  tests, not live storage acceptance. Exact run IDs end in
  `local-file-20261014-122848-dbb565-r1` and `-r2`; reports are in the ignored
  `scripts/.dependencies/e2e-owned-runtime/local-retained-file/` directory.
- Four historical real PRE Group/Session Runs were independently re-rendered
  offline using the new code: all retained their prior two-Case inventory and
  `RETAINED_CASE_PASS`; every old artifact byte was unchanged. No HTTP/MCP call
  or remote write was made. Full runner and all-module Singlebox coverage were
  not rerun for this increment.

## Local/mock command

Use a private copy of `profiles/local.mock.json` with a dedicated **local mock**
fixture pool, a fresh Run ID and a fresh state directory:

```bash
python3 runner.py retained-run \
  --profile /private/path/local-mock-profile.json \
  --state-dir /private/path/file-run-001 --run-id file-run-001 \
  --case RETAIN-FILE-01 --serial-preflight \
  --allow-write --confirm-run-id file-run-001
```

For the second Run retain that Profile/pool, select another Run/state directory
and add `--previous-state-dir /private/path/file-run-001`. Do not change or reset
a PRE pool to run this mock example. A remote Profile additionally requires the separately reviewed file-v2 inputs
described below; this local example never supplies remote write authority.

## Reviewed first storage-origin Profile transition

`retained-run --serial-preflight --previous-state-dir ...` can additionally take
`--previous-profile` and `--profile-transition-review` together. The old Profile
must match the previous Run fingerprint. The new Profile may **only** add its
first nonempty `file_transfer.allowed_storage_origins`; all other values,
including auth references, Human/Bots, gateway, database, pool, limits and
timeouts, must be identical. An absent field and an empty old allowlist are
supported; replacing/removing/expanding an existing nonempty allowlist is not.

The review is operator attestation of a source/owner-reviewed storage target,
not a signature, server-side permission or a successful storage probe. Its
`storage_origins` must exactly equal the configured list. It binds the old/new
Run IDs and Profile digests, bound Human, evidence reference/digest and a current
validity window of at most one hour. Example shape only:

```json
{
  "schema_version": 1,
  "contract": "retained-storage-profile-transition-v1",
  "run_id": "file-run-001",
  "previous_run_id": "previous-run-001",
  "previous_profile_digest": "<old-profile-sha256>",
  "profile_digest": "<new-profile-sha256>",
  "storage_origins": ["https://reviewed-storage.example.test"],
  "supervisor": "<unchanged-bound-human>",
  "reviewed_at": 0,
  "expires_at": 0,
  "reference": "<actual-storage-review-reference>",
  "evidence_sha256": "<actual-reviewed-evidence-sha256>"
}
```

The same pool guard covers old-state validation, fresh checks on the **new**
Profile, review revalidation and reservation transfer. Dependency failure, expired
review or input drift before transfer leaves the old reservation unchanged.
An I/O failure after transfer is retained for inspection; it never starts the
Case, rolls back ownership, resets quota or automatically retries the transfer.

Only the new Run receives `profile-transition.json`, containing the frozen review
and old non-secret Profile. Its digest is in the scheduling history and new
serial-admission receipt. Old reports/Profile inputs remain unchanged. Historical
reporting uses only frozen files and checks their bindings; it does not need
renewed review validity, the original input files, Cookie or network access.
This transition does **not** remove the retained File remote-execution block.

Profile-transition validation: 95 focused tests passed on 2026-10-14 (6.423s),
including the new transition lifecycle and existing serial, file, remote and
reporting tests. Coverage includes the actual CLI flags, two distinct mock Run
profiles, unchanged historical bytes, rejected identity/pool/auth changes,
expired/drifted reviews, failed dependencies, offline rendering after source
inputs disappear and write failure after reservation transfer with no Case
execution. These tests do not mutate the live PRE reservation or quota.

## Quota v2 contract

The file-capable contract is `retained-resource-quota-v2`, stored at the **same**
pool-local `.retention-quota.json` path. Three explicit modes are supported:

- `initialize`: only when that path is absent, with a reviewed baseline.
- `existing`: requires the exact current v2 journal digest and unchanged policy.
- `upgrade-v1`: requires the exact validated v1 journal digest. The complete old
  journal is embedded unchanged as `legacy_v1`; its Group/Session baseline,
  reservation rows, registry ID, limits and original inventory attestation are
  preserved. New file dimensions need their own explicit inventory reference
  and evidence digest. A v1 request cannot downgrade or reset the v2 journal.

| Case | Groups | Sessions | Files | Uploaded-byte reservation | Share-mint attempts |
|---|---:|---:|---:|---:|---:|
| RETAIN-GROUP-01 | 1 | 1 | 0 | 0 | 0 |
| RETAIN-SESSION-01 | 1 | 2 | 0 | 0 | 0 |
| RETAIN-FILE-01 | 1 | 2 | 1 | 4096 | 2 |

The file reservation covers the small payload's bounded maximum, **not measured
stored bytes**. The two share attempts cover completion's implicit link and the
explicit share request; they do not represent independently deletable resources.
Failed/ambiguous requests, terminal execution, time passing and later deletion
never refund these reservations. Subsequent Group/Session reservations carry all
five dimensions so file usage cannot disappear from their accounting.

Configuration retains v1's `max_groups`, `max_sessions`, `baseline_groups`,
`baseline_sessions`, registry and inventory fields. It adds `max_files`,
`max_uploaded_bytes`, `max_share_mints`, their three `baseline_*` counterparts,
and `file_inventory_reference` / `file_inventory_sha256`. Counts are strict
integers, not booleans. Limits cannot change during ordinary existing/upgrade
admission. Extending a registry is not permission to run the File Case: the
separate remote effect/transport contract must still admit it.

New receipts freeze their bounded journal snapshot and source bindings. Offline
reporting recomputes all totals from that snapshot rather than trusting the
receipt's claimed remaining capacity or querying the mutable current registry.
An uncertain journal write stays charged if it persisted, and the Case must not
start without a confirmed receipt. A new Run does not retry an ambiguous old
reservation. Historical v1 Run artifacts retain their original receipt shape.

Quota-v2 validation on 2026-10-14: 53 worker-focused tests passed, followed by
209 integrated quota/transition/serial/remote-composition/file/observer/report
checks (12.052s, no skips). Additional integration verifies a v2 Group Run through
the actual retained composition with synthetic external transports, permanent
charges after a lost Group response, and v1 → File → later Group accounting
with nonzero file baselines. Four historical real PRE Run reports still render
offline without changing old artifacts. No live journal has been upgraded and
no real file has been uploaded by this milestone.

## Remote file effect and transport admission

Remote execution uses a distinct `supervised-retained-file-v2` review. A control-v1
Group/Session review cannot admit File operations. The File review binds the same
Human, dedicated protected Bots, admitted remote identity (gateway Human-view
Cookie generation or bcs-direct dual-header generation), complete Profile and
fresh Run, plus quota-v2 and explicit process-serial preflight; it may declare the
same optional `execution_generation` as the control review. Remote File execution
engages for both entrypoint kinds; on the bcs-direct station legacy face the
transport answers through the public-V1 envelope, and the internal-v1 manifest
route requirement below is unchanged. It declares:

- BaaS backend and the exact nonempty configured storage-origin list;
- reviewed storage key prefix, original small payload and a 4096-byte ceiling;
- completion's implicit 15-day BCS share-link mint and two owned Bot Inject
  notification attempts, plus the separate explicit share request;
- internal-v1 File routes and source evidence identifying those effects.

The resource APIs are bounded to one Group create, one explicit Session create,
one prepare/upload/complete/list/metadata/download/share/shared-download call.
There is no File-subset surface for update, token, WS, DELETE, stop or retry.
Every attempt consumes its budget even if the response is lost or denied.

For the small-file path, the returned target must be a single PUT on an approved
storage origin. Its once-decoded object path must be the reviewed prefix, the
**exact current Session**, a transfer-ticket ID and the expected filename. Proxy,
multipart, foreign Session/path, wrong method and unlisted-origin targets are
rejected before PUT. Encoded slashes from the pinned storage SDK are supported;
traversal, double encoding and backslashes do not grant another path.

Storage uploads and redirected downloads carry no Human Cookie/Authorization or
browser Origin/Referer. Existing TLS validation and bounded redirect handling are
retained. Anonymous BCS content access uses only the freshly issued share token;
its contents must equal the original bytes, independently of nullable stored hash.

Completion notification/share outcomes remain `NOT_VERIFIED`: successful file
metadata and bytes cannot assert delivery to the Bots. The quota's two share
mints count BCS implicit/explicit link attempts, not every downstream HTTP call.
Reviewed BaaS download signing reads the existing DONE transfer ticket and does
not create a separate download ticket. File observation remains the exact
current-Case file projection with its environment and owned Session parent; no
Bot-wide, bucket-wide, table-wide or unrelated resource query is added.

Remote implementation tests use synthetic external transports and database
replies; they are not real BaaS/OSS acceptance. Actual PRE qualification requires
new current instance/build/identity/dependency checks and independently preserved
Run evidence after the implementation regression and local commit.

Remote implementation validation on 2026-10-14: the worker ran 1396 runner tests
with 67 environment-dependent skips (not an owned-runtime/full release gate).
After integration, 218 focused tests passed (17.788s, no skips), including the
real approved local BCS original file lifecycle. A final continuous-serial-lease
check before direct remote File execution was added; 74 affected tests passed
(4.287s). Four old real PRE control Runs and two local File-v1 Runs still render
from their frozen inventories without changing any original artifact bytes.

The current File Case is version 2 within the same separate File suite. It keeps
the same shared assertions/denominator while adding reviewed remote capability;
old Case-v1 Runs are not rewritten or executable against changed runner code.
Notification outcomes and full execution closure remain unverified. These checks
are implementation evidence; live PRE File acceptance is recorded separately.

## Real PRE File attempt and anonymous-access blocker (2026-10-14)

Implementation commit `e2f988df8` was exercised in
`serial-file-20261014-132153-246c8f-r1`. **14 of 15 observed assertions passed;
the Case failed `share_bytes_match` and the second planned round did not run.**
Upload PUT, completion, metadata, exact environment/File/Session database
projection and authenticated original-byte download passed. The 63-byte file was
Ready in BaaS. Anonymous shared content returned HTTP 200 without a storage
redirect and failed the original-byte assertion. The failure is retained, not
retrospectively converted to success.

Private independent diagnostics establish an ingress authentication problem:
anonymous GETs returned `buserviceErrorCode=USER_NOT_LOGIN`, `actionType=LOGIN`,
`decisionBy=ACE`; the same gateway path with a Cookie and an invalid test token
reached BCS and returned `40400/shared_file_not_found`. A separately labeled
native-route anonymous diagnostic was also login-intercepted, not used as an
acceptance fallback. The exact owned Session's completion notification contained
one matching File link; a readonly check of that existing link also hit ACE
login interception. No replacement share was minted for this diagnostic.

One invalid-token preflight probe did reach BCS token validation while other
anonymous diagnostic probes were intercepted. This mixed evidence is preserved:
a negative-token probe does not prove that a valid signed link will work or that
all ingress paths/instances share the same policy. No reason for that variation
has been established. The real signed-link failure remains authoritative for
this Story; do not repair the assertion by supplying a Human Cookie.

Retained resources:

- Group: `bcs_grp_6c9e4cb1f5fc42a787000c8bfa3aad55`
- Explicit Session: `bcs_grp_6c9e4cb1f5fc42a787000c8bfa3aad55:c8434738`
- File: `01M4WDSNFNBVNX22FZFZ9F7Y07`

The same pool's v1 journal was preserved inside v2. Accounted usage is now
7 Groups, 11 Sessions, 1 File, 4096 maximum upload bytes and 2 BCS share-mint
attempts. There was no DELETE, stop, direct DB write, quota refund or Case retry.
The real upload was 63 bytes; 4096 is the conservative reservation, not measured
physical storage. Exact database evidence and an independent failed JSON/JUnit/
Markdown report are in the ignored batch artifacts. JUnit correctly has one
failure; full release remains NOT_QUALIFIED.

### Early anonymous-route probe

File preflight now sends one invalid-token GET without Cookie, Authorization or
browser credentials at each of its two existing preflight phases. It accepts
only the reviewed BCS `40400/shared_file_not_found` envelope, not an arbitrary
404, redirect or HTTP-200 login response. Observed interception blocks before
the first Run reservation/quota/business mutation. No valid File is read and no
share is minted. The actual post-upload original-byte sharing assertion remains
mandatory even if this probe passes.

The environment owner needs to inspect the PRE ACE/ingress anonymous-access
policy for **GET `/api/v1/collaboration/sessions/shared-file/content`**. Gateway
source already declares an empty route-security requirement for this path, but
that does not prove outer ingress policy is aligned. Any fix must remain limited
to this intended signed-share surface and preserve BCS token verification; do
not disable whole-site authentication or mutate the failed Run to release it.
Cookie renewal is not a fix for anonymous access.

Validation of the new probe: 104 focused tests passed (5.173s, no skips), including
no-credential transport, login/redirect/unrelated-404 refusal and a failed
preflight with no Run, reservation or quota file created. Live readonly checks
left the existing reservation and quota unchanged. Production/Alibaba Cloud and
the original full PRE-03 cleanup/notification/CLI scope remain unqualified.

### PRE SSO contract local validation — 2026-10-19

121 focused tests passed (18.277s, no skips) across File transport, new SSO negative/positive transport, remote/local retained File, reporting, frozen inventory, serial admission and profile transition. No full runner/build or remote call was executed. One additional focused frozen-v2 compatibility test passed (0.219s): old reports remain readable and byte-identical, but cannot execute under v3. Total: 122 distinct tests passed. OpenSpec strict validation and git diff checks also passed.


## Case v4 implementation checkpoint — 2026-10-20

User-approved issued-capability contract is implemented in the manifest,
Run-bound review, guarded preflight, shared business steps, transport and
version-dispatched offline report verifier. The diagnostic404 does not satisfy
issued-login or byte assertions. Old reviews cannot start v4 execution, and
v3 offline evidence retains its original strict preflight interpretation.

Focused validation: **103 distinct tests passed**, in non-overlapping groups:
32 retained composition/local/frozen-report tests (5.586s),48 transport/probe/
shared-step/Case-guard neighbors (6.639s),23 v1/v2 transport tests by the bounded
sol worker (0.046s). The worker's initial import invocation lacked its tests
PYTHONPATH and ran no tests; the corrected invocation passed. Four frozen-report
tests were also checked independently before the32-test group, not double-counted.
OpenSpec strict validation, syntax, diff whitespace and all modified Python
source-file size checks passed. No full runner/Singlebox build was repeated.

Live v4 acceptance is **NOT_RUN**. At23:42:47+08:00, `observ auth doctor`
reported local Human AgentPass renewal_required, effective_source none and no
server identity probe. Dynamic target refresh must wait for user renewal; no
old instances or review timestamp is substituted. This is not evidence of Cookie
or alisddbquery expiry. No new Run, business write, quota transfer or cleanup was
performed. All69 snapshotted historical files, pool/quota bytes and the preexisting
Rust helper diff were unchanged. Spec remains43/54, not new PRE-03/release credit.


## Authorized single-Group quota amendment — 2026-10-21

The user approved **max_groups10→11 only** after the first File-v4 Run.
`retained-resource-quota-v2` supports explicit `mode: raise-group-limit` with a
`group_limit_change` receipt: contract `retained-group-limit-increase-v1`,
run_id, profile_digest, supervisor, reviewed_at, expires_at, reference,
evidence_sha256, previous_max_groups and new_max_groups. Validity is at most1h.
The receipt is operator attestation, not cryptographic server authorization.

The runner verifies the exact previous journal and same target/policy, allowing
only +1 Group. It preserves the entire previous journal under `previous_v2`
with its digest, embeds the change receipt, then reserves the new Run together
in one guarded atomic write. Prior legacy-v1 records and Run reports are not
rewritten. All other limits/baselines and historical reservations stay fixed.
Old journal without an amendment cannot be silently opened under raised limits;
normal `existing` mode does not imply permission. Reviewed increases may chain, but
every transition is a separately receipted exact `+1` and embeds the complete
predecessor journal. A request cannot jump two limits in one admission.

A preflight failure does not apply the change. A failure after reservation keeps
its full maxima; no refunds, cleanup, alternate pool or automatic replay. Offline
reporting checks both predecessor and amendment digests without requiring the
original live approval window to remain valid. Current Group allowance after
this approved second File reservation would be11/11; it grants no further run.


### Amendment implementation and verification checkpoint

The explicit one-Group-at-a-time transition is implemented in the quota
validator/reserver, remote review admission and offline report verifier. Live
amendment checks run
before HTTP/pool handoff and again under the held lease. No new endpoint, CLI,
background job or remote cleanup capability was added. The reusable validator
requires exactly+1; this user's operative authorization is10→11, not arbitrary
new limits. Existing-mode histories retain their original semantics.

**70 distinct focused tests passed**:26 quota-v2/amendment tests initially,
43 review/composition/report tests, then9 amendment tests after adding one bounded
non-magic-number test (8 repeated,1 new). The first4 composition tests also passed
before the43-test group and are not counted twice. The worker's initial test
import invocation lacked its test PYTHONPATH and ran no tests; corrected commands
passed. Strict OpenSpec validation, modified Python syntax/1000-line limits and
diff whitespace checks passed. No full-stack build or broad suite rerun.

The previous real File-v4 report was rendered in a separate process without the
Cookie environment variable: RETAINED_CASE_PASS, identical semantic summary
(except generated_at),13 original state files byte-identical. All139 snapshotted
historical/input files, current pool/quota and pre-existing Rust helper diff
remained unchanged. These are offline compatibility checks, not a second real
File execution.

Real amendment/second Run: **NOT_RUN / AUTHORIZED_NOT_APPLIED**. On2026-10-21,
Observ was ready at15:43:56 and fresh topology found3 BCS/2 Gateway PRE instances.
Health failed to return a service/version projection and the authenticated
identity probe failed. One bounded classification GET at15:48:01 returned
HTTP200 with exact ACE LOGIN/USER_NOT_LOGIN/decisionBy ACE despite the configured
Cookie. No deployment version was guessed from that response. The Cookie file
was preserved; user refresh is required. No reviewer timestamp or quota receipt
was fabricated to bypass missing fresh admission, no resource created/deleted.

## 2026-10-24 live host recovery and anonymous ingress blocker

The scheduling reservation for `retained-file-v4-20261021-162349` still pointed
to retired host `U-6Q2N9J74-2319.local` and no-longer-live PID `93993`. With the
user's explicit confirmation, only local scheduling metadata was migrated to the
current host `U-6Q2N9J74-2332.local`. The recovered object remains an
intermediate legacy reservation: `SerialAdmission` atomically replaces it with
the exact v5 reservation only after all preflight gates pass. Do not unlink the
lease manually. The recovery performed no remote operation or quota mutation and
kept the journal fingerprint
`bd4fd2c5c0c70b1d3c0b4cee82ba8f9c0dc91ae3c7479bd257844b8cee3c6fa4`.

The subsequent `RETAIN-FILE-01` attempt
(`retained-file-v5-20261024-1812`, attempted around 18:15–18:17 CST) reached the
authenticated preflight stage but was stopped before execution. The passing
checks were fixture exclusivity, observer environment binding, target
environment, Human identity, and both dedicated actor ownership checks; observer
admission reached `METADATA_ADMITTED`. The mandatory credential-free invalid-token
probe on `GET /api/v1/collaboration/sessions/shared-file/content` was intercepted
by PRE ingress/ACE before BCS token validation and returned the login contract
`actionType=LOGIN`, `buserviceErrorCode=USER_NOT_LOGIN`, and
`decisionBy=ACE`. The run therefore ends `BLOCKED_BEFORE_EXECUTION` with no state
directory, no remote resource operation, no remote write, and no quota
mutation. A refreshed Cookie is not a fix: it can prove an authenticated route is
available, but the original business contract requires anonymous signed-share
access to reach BCS token validation.

Evidence SHA-256 values (ignored local run artifacts, intentionally not committed):

- attempt summary: `b7795eca474f773bf6eea36a7097260ce4eb744c3157ef40babf59f4ea633052`
- runner stdout JSON: `1dc559b5eda128afd9386a5e6ac9e8ccb39104433bcfbdb1150987e9abbd7f0e`
- blocking recovery: `b72fd27a8c91639fcd9dc46dd33798614c1c6ccc756a1712915a633c19584245`
- lease host recovery: `1233c7330baf73366a46d5604d858b6c3853778e7f6aa34835886152d6e8386f`

Required disposition: the environment or product owner must align ingress
policy for this exact anonymous signed-share content route, or the user must
explicitly redefine the share-access contract. Do not disable whole-site login,
attach a Cookie, rewrite the route, or weaken the original-byte assertion. This
attempt does not alter the original PRE-03 denominator or grant retained-file,
PRE-03, 4.2, 5.3, or release credit.
