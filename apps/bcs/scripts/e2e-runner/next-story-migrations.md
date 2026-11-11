# Next parent-Story migration contracts

Scope: task 6.1 planning prerequisite, **not migrated executors**.
Source review: `e692fade42c5a1badc03982a39b7856958026c58` on the task branch,
not a fresh deployed-OCB baseline. No remote request or mutation was performed.

`migration-contracts.json` freezes three next parent Stories against five complete
legacy source files. The ordinary `plan` command checks that binding and exposes
`next_story_migrations`. The implementation does not execute/source Shell, resolve
credentials, acquire a lease or change fixture state. Source drift requires
explicit review; there is no automatic checksum update or Story remapping.

The map contains **23 direct business steps and 33 migration obligations**. These
are planning declarations, **not executed assertions or a replacement for the
legacy runtime test denominator**. All three parents remain `NOT_MIGRATED`, with
zero migrated Stories and zero passed assertions. The existing reviewed release
inventory, PRE-01/02/03 Case versions and existing execution contracts are unchanged.
The map covers only the next three parents, not all of task 6.1.

## 1. Workspace is a complete collaboration Story

`stories.sh::story_user_operates_group_workspace` calls seventeen business
helpers: nine HTTP-oriented group helpers, six CLI helpers, the incident workspace
round trip and proposal confirmation. Its full scope requires the logical Bot
roles CEO, PM, ENG and QA, not only the pilot's driver/member pair.

The required behavior includes:

- Group create/detail/PATCH/list, exact membership addition/removal, label and
  visibility read-back.
- Actual CLI create/get/add-member/fuse/status/termination; HTTP cannot stand in
  for a CLI operation while retaining CLI coverage credit.
- Bot-originated incident Group creation, three participants, ENG self-mute,
  workspace replacement/read-back, driver routing and service concurrency.
- Persistent messages, chat and callback delivery, with exact Group identity.
- Proposal preview and same-token confirmation, expected driver, exact cleanup.

The native `routes/groups.rs::update_workspace` constructs
`GroupUpdateWorkspaceCommand` with `caller_actor_id: None`. The inspected V1
Group contract does not provide the same workspace operation. This does **not**
prove deployed gateway exposure or authorization behavior; it means the native
handler alone cannot establish the authenticated remote contract. Do not invent
a V1 path, silently fall back to native, or treat a local mock identity as proof
of gateway authorization. Role binding, API-face review, per-resource effect
admission and bounded execution/retention must precede operational migration.

Known legacy shortcuts must be corrected rather than preserved as false success:
label 401 counted as PASS, nonzero fuse with arbitrary JSON accepted, termination
exit zero accepted without verified state, list totals unrelated to the created
Group, and best-effort or incomplete Group cleanup. Source hashes preserve the
old behavior for review; they do not endorse it as the new acceptance contract.

## 2. Trust relationships need distinct positive/negative baselines

`stories.sh::story_user_builds_trusted_team` calls five helpers in `friends.sh`:
public auto-accept, protected acceptance, protected rejection, friend listing and
actual CLI request/list/accept/list. Logical roles are CEO, ENG, PM, QA and CS.

- Protected accept must operate on the exact newly created QA→PM request from an
  initially absent relationship; an existing friendship is not accept coverage.
- Reject uses the exact new ENG→CS request and verifies the relationship remains
  absent. Pre-existing unrelated pending requests must not be selected.
- CLI requester/receiver contexts must represent distinct legitimate identities;
  the first parsed request ID, warning-only failure or skipped acceptance is not
  sufficient. CEO requests, PM accepts, and the final CEO CLI friend list must
  contain PM. One Human owner does not itself provide every Bot credential.
- Restore the observed original visibility only if it has not drifted; never
  blindly set borrowed configuration to `public`.
- Request history and accepted relationship retention/cleanup need exact ownership
  before mutation. Pilot `friendship=unchanged` must remain unchanged globally.

The friend prefix of `story_cli_operator_builds_collaboration_team` is another
parent's subset, not that whole parent's migration. Edge permission
`test_ep_friend_connections_friend_accept_reject_cancel` and V1 friendships are
separate API contracts; legacy `/friends` coverage cannot qualify them.

## 3. Full CLI file Story is not PRE-03

`session_files.sh::story_session_file_workspace` has Bot-originated PM/ENG setup,
six actual `session file` CLI leaves (upload/list/capabilities/download/share/delete)
and three HTTP-only checks (authenticated metadata, anonymous shared metadata,
anonymous shared bytes). It covers eleven legacy file endpoint shapes.

The migration preserves exact returned IDs, source/download/shared bytes,
explicit environment backend expectation and anonymous metadata omission of
`session_id`. PRE-03 is a Human-originated HTTP slice; it does not prove the CLI
leaves, CLI capabilities or anonymous metadata privacy. The inspected V1 file
surface exposes anonymous **content**, not the legacy shared-metadata endpoint.
Do not derive the missing route by removing `/content` from a returned URL.

Before enabling this parent:

1. Select legitimate PM CLI identity in an isolated context; configure the
   target's actual file API face rather than bypassing gateway rejection.
2. Journal exact Group/initial Session/explicit Session/File ownership. CLI upload
   internally creates a file before its final output; failure can leave an unknown
   resource ID. Preserve uncertainty, not blind retry, prefix sweep or broad delete.
3. Account for completion notifications and the separate completion share link,
   not just the explicitly requested CLI share. These are execution effects.
4. Keep CLI business-delete assertions separate from fallback cleanup. Finish
   File/upload before Sessions/Group, preserve failures, and close/scrub owned
   temporary context/files without claiming physical-history erasure.

## Maintainer constraints and next implementation order

The validator checks the exact parent helper order, finite required obligation
IDs, required role sets, requirement-to-helper coverage, source file safety/hash
and agreement with the release inventory. Missing/reordered helpers, dropped
privacy/capability requirements, invented migration PASS or source drift fail the
offline plan. Whole-file hashes also cover nested helpers and manual counters within the five
listed files (including common CLI/auth/API/assertion wrappers), not the entire transitive Shell harness. This is deliberately a
finite source binding, not a general Shell parser.

Next implementation must resolve expanded fixture/identity and actual route
contracts first, then port the business steps through controlled adapters and
write-intent ownership, then run real owned-local regressions and separately
admitted preproduction regressions. Add versioned executor/effect/evidence
contracts when implementations exist; never change these planning labels to
`MIGRATED` merely because a smaller pilot passes. The remaining legacy parents,
conditional Stories and edge entries retain their own unmigrated obligations.


## Implemented local prerequisite: explicit role-set provisioning

The owned-local `runtime fixture-smoke --fixture-set` path now provisions the
four workspace, five trusted-team or two CLI-file role IDs and independently
checks their owners/state. Pilot initialization remains the default and cannot
consume expanded receipts. This resolves only local actor creation: relationship
baselines, per-Bot credential contexts, remote binding and the actual parent
executors remain outstanding. All migration labels in the source-bound plan stay
NOT_MIGRATED; no helper or business obligation is credited by fixture smoke.


The optional `--verify-bot-identities` local initialization flag now checks fresh
credentials with bounded, bracketed legacy self-read operations and clears the
managed holder on every exit. It provides local credential evidence only;
per-operation role authorization, actual CLI context switching, mutable relation
baselines and the parent executors remain to be implemented. The native
friends-route Human fallback is specifically negative-tested, not mistaken for
Bot authentication.


The trusted-team local smoke can now check the API-visible relationship/request
baseline and independently corroborate zero incident legacy rows in its owned
SQLite database. It records original visibility and required changes without
applying them. Actual accept/reject/auto-accept, CLI identity switching, request
journaling and visibility restoration remain the next executor work; baseline
observation does not qualify these business assertions or the edge API family.

## Implemented local legacy friendship API slice

A separate `LOCAL-TRUST-API-01` executor implements the public
CEO→ENG, protected QA→PM accept, and ENG→CS reject paths. It is not the complete
trusted-team parent: CEO→PM actual CLI request/accept/list is supplied only by the opt-in
continuation below, not by this default API-only slice. The executor uses a fresh owned runtime, exact role
credentials, original baseline, write-ahead intents and returned request IDs;
legacy history/dual-write graph artifacts are retained, never erased by SQL.
API and exact SQLite outcomes must both agree. Product-internal edge sync and
notification behavior are not qualified by legacy API success.

## Owned-local product CLI continuation

Opting into an approved product CLI binary on `local-trust-run` now continues the
same owned five-role API sequence with CEO→PM CLI request, PM received pending
list and exact acceptance, and CEO final self-resolved CLI friend list. A finite
loopback capture preserves actual HTTP/envelope/request-correlation evidence that
the product CLI normally omits; business requests reach the real owned BCS, not a
mock server. CLI output and API/SQLite outcomes must agree. Context credentials,
wire intent, cancellation, retention and visibility drift follow the controlled
local lifecycle. This adds the missing local CLI business steps, not remote
route/auth admission, edge/notification qualification or release inventory
integration. The planning map's NOT_MIGRATED labels remain unchanged until those
executor integration and environment acceptance obligations are satisfied.


The registered owned-local parent now has canonical Case/JSON/JUnit/Markdown
reporting. `plan.local_registered_executors` reports `INTEGRATED_LOCAL`; this is
not an executed PASS count and does not change the NOT_MIGRATED release labels.
The next parent-specific gap is remote route/identity/effect/retention admission,
not another local smoke/report alias. Remaining workspace and CLI-file parents
retain their complete unmigrated obligations.

## CLI-file semantic assertion prerequisite

`FileExpectation` now supports an explicitly selected semantic `bot` owner while
preserving `human` as the PRE-03 default. `LegacyFileExpectation` keeps the native
wire contract separate: `Ready` and `Bot`/`Human` are normalized only after strict
DTO validation. Native shared metadata is validated without inventing a missing
session ID and rejects any exposed `session_id`, internal object handle or extra
credential/URL field. A fresh file-page assertion requires exactly one expected
file; local capabilities require the declared local backend, strict capability
booleans and exact size limit. Nullable metadata SHA still requires independent
bytes/hash verification, never a skipped round-trip.

A real owned-local PRE-03 fixture verifies six native read views over its actual
V1-created Human-owned file: authenticated metadata/list/capabilities/content and
anonymous shared metadata/content. PRE-03 still performs its original lifecycle,
assertions and cleanup. Bot ownership/native DTO negative cases are unit-tested;
this prerequisite does not execute product CLI leaves, Bot-originated setup,
native upload/delete, remote routes or the complete CLI-file parent. The original
10-obligation CLI-file map and NOT_MIGRATED label remain unchanged.

The existing friendship CLI capture must **not** be reused unchanged for file
upload: one product upload comprises prepare, streamed PUT and complete; the
current product uploader follows the returned upload URL, sends credentials based
on hostname comparison (not full origin), and uses chunked streaming. A file
adapter therefore needs a bounded multi-step state machine with a write-ahead
intent and exact returned ID before PUT/complete, fixed owned destination and
explicit framing/redirect controls. No arbitrary URL returned by a server may
become an egress permission. The CLI download output path must be private and
create-once; product file creation otherwise truncates an existing path.

The legacy CLI-file setup has two distinct Session effects: `create-group`
defaults to creating the initial Session, then `session create` creates another.
Both can select driver Send plus member Inject; switching to `--no-session` or
inject-only is a changed scenario and cannot silently replace the original setup.
File completion separately attempts a 15-day no-auth link and an Inject/public
history notification for other Bot participants. Completion may still return
success when that notification fails. These source-observed effects require
explicit local containment and separate remote admission, not an inference from
metadata PASS. Do not confuse the notification link with the later explicit CLI
share operation, or the initial Session with the returned explicit Session ID.

## Shared file steps before retained preproduction admission

`file_steps.file_control_steps` now owns the existing PRE-03 create, upload,
complete, metadata/owner/backend, exact persisted projection, authenticated bytes
and anonymous-share bytes sequence. Storage backend is explicit (`local` or
`baas`), never inferred from a successful response. Mutable progress preserves
the Group, implicit/explicit Session and file intents across partial failures.
The share capability remains in memory, not in evidence. These steps perform no
DELETE, stop, lease release or result qualification.

The original `run_pre03` calls those same steps and retains its existing exact
cleanup and deletion assertions. This extraction does not enable a retained file
Case, extend remote permissions, change its manifest, or claim BaaS/notification
acceptance. A separate versioned retained-file contract must admit upload egress,
implicit complete-time share/Inject effects and resource/byte quotas first.

Validation on 2026-10-14: 55 focused tests passed (10.064s, no skips), including
the real approved owned-BCS PRE-03 lifecycle/native file views and normal cleanup,
plus no-delete shared-step, lost-prepare, bad-byte, explicit-backend, transport,
retention and reporting tests. No PRE writes or full Singlebox coverage run were
performed for this extraction. The unrelated unfinished local CLI-file setup is
not included.

## Complete CLI-file continuation: source-verified wire obligations

The native product CLI's upload is not one JSON request. The next executor must
capture prepare → PUT bytes → complete, and commit the exact returned File ID to
the resource ledger **before** exposing the prepare response to the CLI. The
owned-local storage response points at the actual BCS origin, so a capture can
rewrite only that already-validated exact owned upload URL to its loopback
listener; it must record that transport-only rewrite and forward the same method,
owned path, content type and bytes to the actual BCS. It must never rewrite a
foreign response URL into an approved target or synthesize a product success.

The product uploader streams with chunked framing. A finite local exchange needs
a strict bounded framing parser (no conflicting Content-Length/Transfer-Encoding,
trailers, unbounded chunks or body overruns), an absolute deadline and cancellation
that closes only its owned sockets. The shared friendship exchange is intentionally
single-request JSON-only and must not be broadened for every caller.

A source-specific failure detail matters: the CLI propagates prepare/PUT errors
directly, but on **complete** failure it attempts a best-effort File DELETE. The
new capture must explicitly journal and reject that unrequested fallback during
failed upload rather than silently deleting uncertain state. The original
complete error remains a failure. The independently selected business `file
delete` leaf remains a different required operation and needs exact ownership,
API absence, SQLite absence and share invalidation assertions.

Download uses create/truncate on the output path. Allocate a new private path
inside the current CLI context, reject an existing file or symlink before invoking
the leaf, and verify immutable original source bytes rather than comparing two
possibly incorrect downloads. Share capability output remains in memory only;
anonymous metadata/content requests must have no Bot/Human authorization.

These are implementation obligations for all ten CLI-FILE requirements, not
completed coverage or an alternate remote File Story. The setup increment below
only qualifies PM/ENG ownership and both Session identities; six File CLI leaves
and canonical parent-Story reporting still need their own actual-run evidence.

## Actual owned CLI-file setup milestone (2026-10-14)

`local-file-setup` now drives real approved BCS and CLI binaries with two freshly
created/identity-verified PM and ENG actors. Only that fresh local ENG's protected
visibility is explicitly changed to public (acknowledged and read back), so PM's
Bot-originated Group can admit it. This is local fixture preparation, not a remote
Human ownership fallback or permission to modify borrowed Bots. Its disposition
is retained in the isolated database; no claim of restored shared fixtures or
complete downstream execution is made.

The actual product CLI performs create-group (HTTP 200, preserving its default
initial Session) and explicit session-create (HTTP 201). Each has a write-ahead
resource intent and finite loopback capture with real response/request identity.
CLI output must agree with the actual wire response before IDs are bound.
Independent API and SQLite reads verify driver/originator, exact participant
set, both distinct Sessions and their Group parent. Missing output, nonzero CLI
exit or cancellation retains unknown intents rather than retrying/adopting.
Private CLI context credentials are cleared; only the owned child is stopped.

`OwnedLocalFileSetup.run()` is reusable inside the caller's live owned runtime;
the standalone command additionally stops that runtime and saves
`cli-file-setup-report.json`. It explicitly reports `file_operations=NOT_RUN`,
`full_story_status=NOT_MIGRATED`, `ready_to_execute=false`. No six-leaf File or
canonical parent-suite credit is inferred from setup success.

Validation: 26 focused tests passed in 34.676s with approved real BCS/CLI binaries
and no skips. This includes normal setup, lost Group/Session output, nonzero
CLI exit, cancellation, failed prerequisite, repeat-directory preservation,
reuse inside a still-live owned runtime, capture boundaries and role bindings.
A separate actual CLI invocation `cli-file-setup-20261014-174738-312bf7` passed all
9 setup assertions; both temporary CLI contexts were cleared and the BCS child
exited normally. Private artifacts are retained under the ignored owned-runtime
`local-cli-file/` directory. Full runner/Singlebox coverage was not rerun here.

```bash
python3 runner.py local-file-setup \
  --state-dir /private/path/new-cli-file-setup \
  --run-id cli-file-setup-001 --confirm-run-id cli-file-setup-001 \
  --binary /absolute/path/bcs --binary-sha256 '<approved-bcs-sha256>' \
  --cli-binary /absolute/path/bcs-cli --cli-binary-sha256 '<approved-cli-sha256>' \
  --allow-local-processes --allow-fixture-write --allow-write
```

## Actual six-leaf owned-local File Story

`local-file-run` continues the same PM/ENG setup in the still-live owned BCS and
executes **actual product CLI** upload, list, capabilities, download, share and
delete. It independently verifies native HTTP metadata, anonymous shared metadata
(without `session_id`), anonymous shared bytes, exact SQLite File/Session/owner
expectations, and post-delete File metadata, anonymous share metadata/content and SQLite-row absence. All ten `CLI-FILE.*` obligations
are required in order. PRE-03 HTTP calls do not substitute for any CLI leaf.

The finite local File exchange is distinct from the friendship exchange. Upload
captures exactly prepare → PUT → complete, records File ownership before the CLI
receives its target, accepts only the owned BCS URL, and rewrites that exact URL
to the capture listener. Chunked CLI input is bounded, decoded and forwarded with
an exact Content-Length; reports label this framing/URL rewrite as transport-only.
The actual BCS responses, original content, authorization and endpoint semantics
remain exercised. This is not an unmodified byte-for-byte HTTP transport test.

An unexpected automatic DELETE from the product uploader after a complete error
is refused, not treated as authorized cleanup. Only the separately invoked
business delete leaf can delete the exact registered File. Failure or cancellation
retains the local database; process shutdown never replaces a failed delete
assertion. Group, both Sessions, relationships and notification history remain
in the retained local database. The newly provisioned owned ENG Bot also remains
public after participant admission; this is an explicit retained fixture mutation,
not restoration or a change to a borrowed/remote Bot. No engine runtime or remote service is started.

A create-once `file-story-binding.json` freezes the parent obligations, original
reviewed denominator, executor/binary digests and Run root before fixture writes.
A source-bound completion receipt is published after owned process shutdown.
`local-file-report` uses those artifacts offline and the shared JSON/JUnit/Markdown
renderers; a pending publication, missing completion, changed source, missing
requirement, false ledger deletion or unconfirmed shutdown cannot render PASS.
This dedicated binding/report command is not the general pilot Profile schema.
The original migration label remains NOT_MIGRATED until remote/environment and
full legacy-gate integration are separately accepted.

```bash
python3 runner.py local-file-run \
  --state-dir /private/path/new-cli-file-run \
  --run-id cli-file-001 --confirm-run-id cli-file-001 \
  --binary /absolute/path/bcs --binary-sha256 '<approved-bcs-sha256>' \
  --cli-binary /absolute/path/bcs-cli --cli-binary-sha256 '<approved-cli-sha256>' \
  --allow-local-processes --allow-fixture-write --allow-write

python3 runner.py local-file-report --state-dir /private/path/new-cli-file-run
```

CLI context files are private and cleared after each command. Share tokens,
signed URLs and raw CLI stdout/stderr are not report material. Local downloads
use fresh private output paths, and both download assertions compare against the
immutable original source payload, not against each other.

Validation on 2026-10-14: initial real CLI/BCS and nearby reports ran 62 tests
successfully; adding upload-complete fallback refusal, failed business deletion,
missing-obligation and false-ledger-deletion cases produced a 90-test passing
regression. The final source/plan/correlation/child-status changes passed 87
focused tests in 138.149s with approved actual BCS/CLI binaries and no skips.
These sets overlap and are not summed as a coverage count.

Independent fresh Run `cli-file-20261014-223612-651402` then executed all six
product CLI leaves and all ten obligations successfully: LOCAL_SUITE_PASS,
8 captured File wire operations, one JUnit Case with zero failures/errors/skips.
The setup has its own captured creation requests; the 8 File operations are
three upload stages plus the other five leaves, not the complete HTTP count.
A separate process ran `local-file-report` and reproduced PASS without modifying
any original Run artifact. The BCS child stopped normally; per-command CLI
credentials were cleared. Full runner and all-module Singlebox coverage were not
rerun for this increment. No PRE call, cleanup or quota change occurred.


## Complete runner regression recovered and verified (2026-10-15)

The full runner regression started after commit
`3f01b2714bac9c5048cd2d1045018d773cf6f402` completed on October 14;
the continuation recovered its completed log rather than launching a duplicate
15-minute run. The saved unittest summary is **1430 tests, 943.170 seconds,
OK, no skips**. Current discovery independently returns 1430 tests, and the
worktree was clean at that commit when the result was checked.

Command used by the saved regression launcher, with approved BCS and CLI binary
paths and SHA256 values supplied through `BCS_E2E_TEST_BINARY`,
`BCS_E2E_TEST_BINARY_SHA256`, `BCS_E2E_TEST_CLI_BINARY`, and
`BCS_E2E_TEST_CLI_BINARY_SHA256`:

```bash
python3 -m unittest discover -s apps/bcs/scripts/e2e-runner/tests -q
```

Evidence anchors (local artifacts, not committed runtime data):

- Log: `/private/tmp/e2e-full-owned-file-milestone.log`; SHA256
  `759b3da49362fc2cd43b0525da5d5815e8b325523f63d0fd20d90178e7287ced`.
- BCS binary SHA256:
  `f7bf2743323c8273fa642da2572d4ff2ce7da59dfbf411601c4849eec9cef359`.
- CLI binary SHA256:
  `cdef67cfa9de20f141c12ed93aa656add53202a8e8feade096b806b5ab83ba6a`.
- Both binaries still match their saved build-evidence records. Git comparison
  from their recorded source commits (`4d4565737` for BCS and `3edb50777`
  for CLI) finds no subsequent changes to the respective product source.

This closes the missing full-runner regression for the latest CLI File
implementation; it does not replace the all-module Singlebox coverage gate,
qualify the original remote PRE-01/02/03 suite, or clear the observed anonymous
share ingress failure. No new PRE requests, writes, deletes or quota changes
were made while recovering this evidence. OpenSpec remains **38/54**, with
full migration and remaining environment acceptance still incomplete.


## CLI File review corrections (2026-10-15)

The next owned-local binding is `owned-local-cli-file-binding-v2`. Its business
delete assertion separately reads authenticated metadata, anonymous shared
metadata and anonymous shared content and requires 404 for all three, plus
the existing exact SQLite absence. A stale anonymous metadata response of 200
now fails even when content has disappeared. Both anonymous readbacks omit
credentials. Canonical v2 reporting independently requires the three recorded
observations for the exact File; the ten parent requirements remain unchanged.

JSON, JUnit properties and Markdown explicitly describe the owned ENG fixture's
acknowledged setup visibility (`public`), restoration (`NOT_PERFORMED`), and
current-state verification (`NOT_PERFORMED`). Failed or missing setup evidence
is not presented as confirmed visibility. This remains an isolated local fixture,
not a borrowed or remote Bot mutation. The declaration does not claim live
visibility after the runtime stops.

Archived v1 completion projections remain readable under their original
observation contract. Reading the saved `cli-file-20261014-223612-651402` v1
Run still yields LOCAL_SUITE_PASS and leaves every original artifact unchanged;
it does not gain the v2 post-delete metadata check retroactively.

Validation: the integrated File setup/exchange/story, report, migration and case
policy set passed **67 tests in 143.664s**, with approved actual owned BCS/CLI
binaries and no skips. After completing all three output formats and v1/v2
report compatibility checks, the final Story/report/policy set passed **40 tests
in 109.118s**, again with no skips. These overlapping sets are not summed.
The original 1430-test full run predates these corrections; it is not presented
as a full regression of this patch. OpenSpec strict validation and diff whitespace
checks also passed. No remote requests, production data changes or full-module
Singlebox coverage run were made for this correction.


## Workspace API migration increment (2026-10-15)

`local-workspace-api` now composes fresh CEO/PM/ENG/QA fixtures, verified
one-lifecycle identities and `OwnedLocalWorkspaceApi` in a single owned BCS
process. It preserves the first nine legacy API helper obligations as seven
named requirements: create, detail, patch, list, members, label and visibility.
Two fresh Groups preserve driver-only creation and three-participant creation;
mutations and independent reads reuse the first Group rather than constructing
a separate Group for every helper. Both implicit initial Sessions remain in
the resource ledger. No legacy shell harness is executed.

The Human operations use the owned runtime's built-in local Human auth with
mock headers disabled. Label update uses the actual CEO Bot token; a 401 is not
a passing skip. The product requires every participant of a public Group to
be public, so only the newly provisioned CEO/PM/ENG fixtures are made public
and independently checked. This state is explicitly retained, not restored,
and does not authorize changes to any borrowed or remote Bot. QA remains its
provisioned protected state. Local mock authentication does not prove gateway
authentication.

The finite ordered ten-write plan binds exact operation, method, path, body,
actor and owned primary-Group ledger before dispatch. Repeated creation is
rejected before a new ledger intent. Each normal create journals the Group
and initial Session before HTTP; lost responses retain unknown IDs without
list adoption, retry or deletion. Group/detail reads are finite and bound to
known IDs; independent local SQLite queries have exact Group filters and
3/5/3 row limits for Groups/participants/Sessions.

Assertions fail fast; a wrong membership read cannot proceed to member removal,
label or visibility writes. Member ordering is not a product contract: creation
validates exact membership and cardinality rather than imposing driver-first
response order. All 18 parent obligations are frozen before fixtures and shown
in the final JSON outcome; the eleven CLI/incident/proposal requirements remain
NOT_RUN. Plan exposes this as `LOCAL_API_INCREMENT_ONLY`, not a registered
complete parent or release credit.

The owned runtime must stop normally and credential holder references must be
cleared before `LOCAL_WORKSPACE_API_PASS`. A missing assertion, cancellation,
source/journal mismatch or report-publication error cannot qualify. Generated
Groups, Sessions, actors and history remain in the isolated local database; no
automatic Group/Session deletion is attempted. Runtime shutdown is not remote
execution convergence. Full Workspace canonical reporting, real CLI operations,
incident/message routing and proposal confirmation are subsequent increments.

Validation: initial live integration exposed an unjustified participant-order
assumption and the product's all-participants-public prerequisite; both were
corrected without weakening the membership or public-visibility assertions.
The final frozen combined source passed **54 tests in 157.032s, no skips**,
including real owned BCS Workspace behavior/failure paths, role identities,
actual BCS/CLI File regression, migration inventory and case policy checks.
The suite includes lost committed-create responses, pre-dispatch journal failure,
401 label rejection, wrong membership, foreign deletion target, replay rejection,
missing obligations, cancellation and report-publication failure. Log:
`/private/tmp/e2e-workspace-integrated-20261015.log`.

An independent subprocess command also passed with Run
`workspace-api-20261015-091836-a76377`: seven selected assertions PASS, eleven
parent obligations NOT_RUN, ten acknowledged writes, exact two Groups/two
initial Sessions/four participant rows/four ledger resources, owned child
STOPPED and credential references CLEARED. Its private local JSON report SHA256
is `240a2f06753fa06f1074cdebf224fd18ad34a16e358e317e49295bc6995e9f87`.
Generated runtime data remains ignored and is not committed. No PRE call,
remote fixture mutation, full reviewed migration claim or all-module Singlebox
coverage run occurred. OpenSpec remains 38/54; task 6.1 is still incomplete.


## Workspace actual CLI increment (2026-10-15)

`local-workspace-run` extends the same owned Workspace lifecycle with the actual
product CLI, requiring an explicitly hashed CLI binary before runtime creation.
It executes eleven bounded CLI invocations: primary create/get, add-member/get,
fuse, completed status/get, second create/get-active, terminate/get-completed.
The first primary starts with PM/ENG and adds QA; the second termination Group
is independently shown active before termination. Both default initial Sessions
remain exactly associated in the ledger and SQLite. Four CLI obligations join
the seven API obligations, but the full 18-obligation parent remains unmigrated
with seven incident/proposal obligations NOT_RUN. No remote/release credit is
introduced by the separate incremental command or plan entry.

Fusion is a real product local-simple-fusion path: fresh exclusive PM/ENG
SOUL.md and MEMORY.md files are created under the owned configured bots directory
and bounded/hash-verified against a create-once receipt. The product response
and CLI output must agree, include exactly the two Bot perspectives with their
original text markers, and carry the expected recommendation; nonzero exits,
empty perspectives or arbitrary JSON never pass. No external LLM, engine or
remote fusion is claimed. Existing directories and symlinks are not adopted,
partial creation is retained INTENDED and cannot be automatically retried.

Code and first live execution confirmed that legacy `get-group` and `fuse` use
raw reqwest requests without `add_auth`, and their native handlers do not
resolve caller identity. Capture therefore uses two separately named, finite
local-only no-auth contracts chosen before execution, with empty capture token,
exact path/body/method and no extra auth headers. The original absence of auth
is forwarded unchanged; no supplied token is stripped or injected. These are
known remote/gateway identity gaps, not an authentication fallback or proof of
remote eligibility. All other operations preserve their actual PM Bearer,
while QA admission uses QA's own fresh credential. PUT support does not permit
PUT for a capture event admitted as POST/GET, and route/body/duplicate checks
remain enforced.

All four generated Bots are now explicitly retained public (QA's change is
separate from the API journal); previous API artifacts remain unchanged. Each
CLI context is fresh, private, bounded and cleared even when a credential write
commits then raises. Cleanup checks owned directory identity before writing.
Unknown creation/output, cancellation, wrong CLI status, context drift or
missing fusion evidence cannot continue the sequence or qualify its report.
Completed Group status does not imply initial Session completion: their actual
running state is independently reported, without stopping unrelated work or
issuing Group/Session DELETE. Full incident/proposal flow and canonical
full-parent report integration remain required.

The frozen combined implementation passed **74 tests in 234.654s, no skips**:
Workspace API/CLI/lifecycle/context guards and real owned-runtime failures,
finite exchange contracts, existing CLI File setup, migration inventory and
Case guard. The first real run exposed unauthenticated `get-group` wire behavior;
that request was blocked, then the source-confirmed explicit local contract was
added before a new Run rather than falling back within the failed Run. The
subagent's earlier 47-test result had ten opt-in skips and is not the acceptance
baseline.

Independent subprocess Run `workspace-cli-20261015-101359-36b287` returned
LOCAL_WORKSPACE_API_CLI_PASS: eleven selected obligations PASS and seven NOT_RUN,
eleven real CLI operations (five Bearer mutations, five unauthenticated legacy
Group reads and one unauthenticated legacy fuse), owned child STOPPED, credential
references CLEARED. Four Groups and four initial Sessions remain locally; exact
DB verification shows two Groups active, two completed and all four initial
Sessions running. The final private JSON report SHA256 is
`59cb098ea4add4cef2df8b7cddc6ad2245934b1cc7648483bee385dfc94b4b10`.
No remote network request, PRE mutation, full runner regression or all-module
Singlebox coverage run was made. OpenSpec strict validation and diff whitespace
checks passed; 38/54 and the remaining full-parent/environment gaps remain.

Because this increment extends the shared CLI exchange, the existing trusted-team
CLI and canonical reporting regressions were also rerun with actual approved
BCS/CLI binaries; their completed result is recorded in the adjacent commit
validation below. These tests protect prior Bearer enforcement, create/decision
identity, cancellation and report integrity rather than granting Workspace
remote or full-parent credit.

Shared-exchange compatibility result: **48 tests in 517.427s, OK, no skips**.
Log: `/private/tmp/e2e-workspace-cli-trust-regression-20261015.log`.

## Workspace incident configuration and remaining message requirements

The next increment composes `OwnedLocalWorkspaceIncident` after the frozen API
and CLI stages in the same owned runtime. It adds four obligations without
changing the original eighteen: `incident-group`, `participant-mode`, `workspace`
and `routing-service`. Its default initial Session is preserved rather than
suppressing the original creation behavior. Exact local resource binding and
source/journal checks precede finite writes; no borrowed/remote resources are
admitted. Message, deletion and proposal obligations remain NOT_RUN pending
actual execution, not quietly exempted.

Source inspection identifies these requirements for the subsequent message
increment, which is **not executed by the configuration-only stage**:

- The current owned runtime has `store_messages: false`. A returned message ID
  alone cannot prove persistence; the message Story must explicitly select
  owned-local message persistence before starting its runtime, without changing
  existing command defaults or weakening any remote setup boundary.
- `group_messages.rs::send_message` returns `routed_to` even when downstream
  delivery fails. `group_chat` and `group_callback` expose per-Bot success flags;
  nonempty arrays alone cannot prove success. The future controlled receivers
  must observe the exact message/group/participant through the real Bot wire.
- The native Bot stream is `/ws/bot`, with `bot.connect` carrying the freshly
  provisioned credential and a response matching its Bot ID and `is_new:false`.
  Runtime identity must be verified before connection; a newly registered Bot
  response is not successful fixture binding. Neither tokens nor returned env
  maps are report material.
- `chat.send` and `chat.inject` are different product effects. A bounded local
  receiver may attest transport reception and return the product acknowledgment,
  but cannot be reported as real engine reasoning, complete producer quiescence
  or production permission. Only the exact newly owned resources may be in scope.
- Workspace PUT and settings PATCH handlers do not resolve caller identity.
  Executing them against owned loopback is not evidence of gateway authorization;
  remote admission must keep this route-specific limitation visible.

These are source-grounded next-step dependencies, not a request to defer the
original message/cleanup assertions or replace them with configuration PASS.

### Incident configuration actual-run evidence (2026-10-15)

Independent subprocess invocation of `local-workspace-run --with-incident-config`
completed as `workspace-config-20261015-113436-173e3e`, exit 0. It selected fifteen
of the original eighteen obligations: fifteen PASS; messages, incident deletion
and proposal NOT_RUN. Original reviewed inventory, NOT_MIGRATED parent status and
NOT_QUALIFIED release status remain unchanged.

Private retained artifacts are under
`scripts/.dependencies/e2e-owned-runtime/workspace/workspace-config-20261015-113436-173e3e/`.
`workspace-config-report.json` SHA256:
`205892120731cc57bdf901a2a5fb0e6393570bd55ae89d3e79a2ebadf3884405`.
All eight incident operations are ACKNOWLEDGED. Read-only SQLite verification
found exactly five local Groups, including incident Group
`bcs_grp_10042720601b4a21bde98eaad7c78606` and its initial Session
`bcs_grp_10042720601b4a21bde98eaad7c78606:e41edd63`.
The Group remains active and Session running in retained storage; the owned
server child is STOPPED. This is not a claim of Session business completion.
Credential holder references are CLEARED, not securely erased. Workspace content
has process-local API readback evidence only, not durable DB persistence.

The first positive combined live test passed in 37.431s. A separate gpt-5.6-sol
read-only review reported no findings. Expanded regression includes actual create
response loss (fifth Group committed, both ledger handles UNKNOWN, no retry),
workspace readback mismatch (no subsequent routing/settings writes), cancellation,
missing assertions, failed final publication and foreign Group/Session ledger
bindings. Final test totals are recorded below. No PRE request, shared database
write, deletion, full runner suite or all-module Singlebox coverage was performed.

Final focused regression: **66 tests in 465.426s, OK, no skips**. Selection:
all `tests/test_local_workspace*.py`, `test_migration_inventory`, and
`test_case_guard`, with actual BCS and CLI paths/SHA256 loaded and verified from
owned-runtime build evidence. Log:
`/private/tmp/e2e-incident-workspace-regression-20261015.log`.
Independent CLI evidence log: `/private/tmp/e2e-incident-independent-20261015.log`.
BCS Rust source is unchanged from the approved server build commit
`4d4565737f4220a5243c05a7b1ac2fa701747a92`. OpenSpec strict validation and
`git diff --check` passed. These results do not replace the older complete runner
regression or satisfy the unexecuted all-module coverage gate.

## Workspace message prerequisite and discovered persistence defect (2026-10-15)

Added an owned-local protocol-v2 Bot receiver and an explicit pre-start
`store_messages` input (default remains false for existing commands). The
receiver authenticates an existing freshly provisioned Bot on the owned loopback
listener, bounds Upgrade/frames/time, verifies delivery Group/Session/method/text,
and closes on errors/cancellation. It emits no Bot replies, success ACKs or fake
execution-terminal events; receipts are TRANSPORT_RECEIVED_ONLY. Token/env values
are excluded from receipts.

Prerequisite regression: **31 tests in 7.235s, OK, no skips**, covering local
configuration, process ownership, receiver and existing WS transport. This
includes actual CEO/PM/ENG handshakes against the approved owned BCS binary,
receiver closure, cleared credential holder references and stopped child. Log:
`/private/tmp/e2e-receiver-runtime-live-regression-20261015.log`.
This proves receiver admission, not the complete Workspace message obligation.

The first original legacy message request in diagnostic Run
`workspace-message-20261015-115454-727dc0` returned HTTP 500 after successful
API/CLI/incident setup and three Bot connections. No chat/callback was then
triggered. The exact owned SQLite error was `transaction parameter 3 references
missing row 0 from step 1`. Source analysis confirms that sessionless ordinary
message persistence uses an empty Session ID when eventing is disabled, while
the SQL message store requires an existing Session for sequence allocation.
The initial Session exists and is running; this is not missing fixture setup.

The existing MessageFlow test
`persistent_group_send_resolves_running_session_and_persists_message_event`
already requires running-Session resolution, but covers only eventing enabled.
The narrowly scoped product repair must separate Session resolution from event
generation, preserve explicit Session IDs and database constraints, and add
non-eventing persistent/chat/callback regression. No dummy Session rows, direct
DB repair, alternate endpoint substitution or swallowed persistence error is
admitted. Product repair and the composed message runner remain unqualified
until their own tests and actual BCS rerun complete. Original 18 obligations,
29 Stories and OpenSpec 38/54 remain unchanged.

### Separate product persistence repair verification

The minimal MessageFlow repair retains the existing eventing/chat-error default
Session resolution and additionally resolves missing/empty Session IDs for
sessionless ingress (`run_id` empty) independently of eventing. Explicit IDs and
the no-message-repository fast path remain unchanged. Non-eventing legacy Bot
reply semantics are not broadened in this repair. The original latest-running
selection policy is preserved; this is not a new ambiguity policy or schema.

SessionManagement delegates to SessionRepo `try_list_by_group`: one bounded
read, filtered by environment/Group/running status, `LIMIT 1`, ordered by
creation/id descending. There is no participant hydration or per-recipient N+1
lookup. Newly repaired sessionless ingress adds that one query per persisted
message; existing eventing resolution and explicit-Session paths add no new
lookup. Failure does not retry or hold a database transaction across delivery.
No production load/scalability claim follows from these local tests.

Final `cargo test --offline --locked -p bcs-message-flow` passed **462 tests,
0 failures, 0 ignored**, across 23 suite summaries. `contract_message_flow`
contains 89 passing tests, including eventing-disabled persistent/chat/callback,
explicit Session, no repository, and preservation of eventing-enabled Bot reply
with a nonempty run. Log: `/tmp/bcs-message-flow-product-fix-test.log`.
An intermediate over-narrow predicate was rejected during parent review and
corrected before this final suite; it is not part of the committed repair.
Actual BCS rebuild and original Workspace live rerun are still required next.

## Workspace messages actual ingress and receiver increment (2026-10-15)

The composed `local-workspace-run --with-messages` preserves the original three
legacy ingress steps and the configured Incident Group. It substitutes only the
PM role's actual generated display name in the mention, not another API or
business interaction. Persistent send routes to PM/ENG; live chat sends to PM
and injects CEO/ENG; callback sends to CEO and injects PM/ENG. Eight actual WS
requests are matched by role, method, Group/Session and expected content. These
are transport receptions, with no synthetic response/terminal event or real
engine reasoning claim. All receivers close before the process/credential
lifecycle ends. The fifteen previous obligations plus messages select 16/18;
Incident deletion and Proposal remain NOT_RUN.

BCS was rebuilt offline after product repair `5169fc12c`:
SHA256 `23c8296c7ac2ccc3535211c3a4b2c389d6e244429d54a48150235f6c735ccdb0`,
version `bcs 0.1.0 (5169fc12c 2026-10-15)`. The original failed Run remains
unchanged; no in-place retry or direct database repair was used.
The actual post-fix diagnostic Run succeeded, then an independent unpatched
subprocess Run `workspace-messaging-20261015-121846-2739cd` completed exit 0,
16 PASS/2 NOT_RUN, eight wire receipts, three matched persistent messages,
STOPPED child and CLEARED credential holder references. Its private report
SHA256 is `ba5ed71215fa064cc6716e2fb85076838e5b4472c0b43726bb82b8b5a603e669`.
Log: `/private/tmp/e2e-message-independent-20261015.log`.
The observed legacy API message ID differs from the durable message ID; the
report preserves both without claiming equality. A bounded before/after message
baseline refinement and its final regression are recorded below when complete.

Expanded regression before the final baseline refinement: **106 tests in
586.164s, OK, no skips**, using the rebuilt actual BCS and approved CLI. Selection:
all Workspace tests, local config/process/receiver/WS tests, migration inventory
and case guard. Log: `/private/tmp/e2e-workspace-messages-regression-20261015.log`.

Read-only sol review identified that checking only matching content could miss
additional rows. Actual SQLite contains three bootstrap/configuration system
messages before the three ingress messages, so asserting three total rows would
be wrong. The final adapter freezes a bounded exact baseline immediately before
receiver setup, requires those rows unchanged, and requires exactly three new
rows with the owned Session, exact sender/type/content and normal status. It
retains baseline count/digest rather than raw system-message text. Unit negatives
cover baseline drift and unexpected additional messages; final live rerun and
refined test counts follow below.

Final independent subprocess Run after baseline refinement:
`workspace-messaging-20261015-122843-f6fb55`, exit 0, 16 PASS/2 NOT_RUN,
eight wire receipts, three baseline rows unchanged plus exactly three new
persisted messages. Child STOPPED, receivers CLOSED, credential holder references
CLEARED. Private `workspace-messaging-report.json` SHA256:
`7fa048787ad89c2183e484ef9b25457e90cef77d45f727b18beee6c39d5ccea2`.
The message journal SHA256 is
`bcb5c0c045f438e83ca019758f724b4c7b8f0522d51c012ae084f42c15ddfe81`.
Log: `/private/tmp/e2e-message-independent-final-20261015.log`.
No production/PRE requests, Group deletion or full Singlebox coverage were run.

Final refined regression: **37 tests in 172.017s, OK, no skips**.
Selection: message execution/failure tests, real receiver, migration inventory
and case guard. Log: `/private/tmp/e2e-message-baseline-regression-20261015.log`.
OpenSpec strict and diff whitespace validation passed. The 106-test result above
precedes only this bounded baseline refinement; neither set claims full runner
or full parent/remote qualification. OpenSpec remains 38/54.

## Workspace Incident deletion and Proposal execution (2026-10-15)

The local lifecycle now retains actual completed receiver objects and uses D46
owned-local foreground closure to admit the original Incident deletion, never a
saved result as authority. Exact Group+initial Session ledger lineage, local
config, absent Provider/queued/state-machine work, API response, independent404,
SQLite cascades and unchanged message history are verified before child-first
ledger deletion acknowledgment. Session status/run-context terminality remains
NOT_CLAIMED; no remote cleanup or engine-completion credit is inferred.

The Proposal stage follows the original create -> strict same-origin URL -> HTML
preview -> token confirmation -> Group read -> exact deletion sequence. Its
confirmation token/path never enters the runner reports; the original create
journal remains AWAITING_DELETE and the independent delete journal proves its
completed disposition. The complete parent combines both, without rewriting the
original history to pretend confirmation alone satisfied the obligation.
The native GET's participant order is not a contract; exact member/role sets
are checked. HTML displays the CEO ID, while member introductions contain the
fixture display names. These source-grounded details are preserved.

The first actual Incident deletion test passed (35.759s). First actual complete
Run `workspace-flow-20261015-124811-6bec95` passed eighteen obligations, preserved
six Incident history rows and three Proposal history rows, removed only those
two Groups/default Sessions, and stopped its owned child. This is execution
evidence, not canonical reporting or remote release qualification. Final
regression/independent-run evidence follows below. OpenSpec remains 38/54.

First full-flow/failure regression: **46 tests in 471.552s, OK, no skips**.
It covers Incident cancellation/lost response/missing live receivers, Proposal
HTML mismatch/lost confirmation/borrowed ledger, complete eighteen-obligation
actual flow, and the existing message tests. Log:
`/private/tmp/e2e-workspace-fullflow-regression-20261015.log`.

Parent and independent sol review then tightened destructive-operation evidence:
all non-target local Group/Session/member/message rows are captured through
bounded readonly snapshots and required unchanged after each delete; generic
or foreign 404s cannot substitute for target Group absence. Receiver close
failure is explicitly CLOSE_UNCONFIRMED and retains its handle, never CLOSED.
These guards apply to both Incident and Proposal deletion. Final rerun results
and an unpatched independent subprocess Run follow below.

Independent final-guard subprocess Run `workspace-flow-20261015-125832-29a772`
completed exit 0 / LOCAL_WORKSPACE_FLOW_PASS with all eighteen obligations PASS.
Both deletion journals include unchanged non-target snapshots; target Group,
participants, Session and Session members are absent, while six Incident history
rows and three Proposal history rows remain. Four unrelated Groups/default
Sessions remain. Child STOPPED and credential holder references CLEARED.
`workspace-flow-report.json` SHA256:
`cafa9f68e16a8b6cd2655b290ebb6cb665f420265eaf746d1ba0a61d64165eb5`.
Log: `/private/tmp/e2e-workspace-flow-independent-20261015.log`.

Final-guard regression: **56 tests in 336.745s, OK, no skips**.
Includes actual Incident/Proposal flow and failure tests, receiver close failure,
non-target drift, structured404 guards, URL/HTML/capability tests, shared WS,
migration inventory and case guard. Log:
`/private/tmp/e2e-workspace-finalguards-regression-20261015.log`.
OpenSpec strict and `git diff --check` passed. No full runner/Singlebox coverage
or remote suite was executed. Canonical report integration is the next step;
18 local obligations passing does not by itself mark task6.1 or release complete.

## Complete Workspace canonical reporting (2026-10-15)

New complete runs freeze an explicit report-v1 requirement/operation inventory,
then publish the seven original stage journals plus ledger/source digests into a
canonical Case and JSON/JUnit/Markdown summary after actual owned-process stop.
Pending -> Case/output -> completion -> remove-pending ordering makes interrupted
publication nonqualifying. The completion record binds all three output files,
not just raw inputs. Offline projection checks the exact stage/source/fixture
chain, all eighteen ordered obligations, four ledger lineages with two deleted
and four retained Group/Session pairs, eight receiver receipts, retained history
and non-target snapshots, CLI evidence and fusion inputs. Local public visibility
is reported as retained, not restored or re-observed after shutdown.

A `local-workspace-report` command verifies old new-format records without
network or credentials; versioned v1 readers do not import mutable executable
step lists. Old reports without the new binding/completion remain nonqualifying.
Separate exports cannot overwrite files within the original Run tree. Hashes are
accidental-drift evidence, not protection against a malicious local owner.

Independent Run `workspace-flow-20261015-131645-d87969` exited0 and published
LOCAL_SUITE_PASS. New-process offline reading returned identical18 requirements,
JUnit `bcs-e2e-workspace` 1test/0failure/0error/0skip, original files unchanged.
Summary JSON SHA256:
`a8c75e660b94c163b232824fba287faaa2a8c76e09994f3023e7581eaba0dd51`.
Logs: `/private/tmp/e2e-workspace-report-independent-20261015.log` and
`/private/tmp/e2e-workspace-report-offline-20261015.json`.

First refined reporting regression:31tests/78.130s/OK/no skips. It includes
actual publication, incomplete marker, original-source and output drift, ledger
semantic drift despite refreshed hash, changed fusion input, missing obligation,
future executable definition change, failed output write with stopped child,
shared rendering, inventory and guard tests. Initial failure was a test-copy
path normalization error (`/var` vs `/private/var`), fixed in the copy harness;
production root binding was not weakened. Final export-preservation regression
and full-runner compatibility results are recorded separately below.

Final reporting/export-preservation regression: **32 tests in 80.866s, OK, no
skips**. Log: `/private/tmp/e2e-workspace-report-final-regression-20261015.log`.
OpenSpec strict and `git diff --check` passed. This validates the reporting
increment; a fresh complete runner discovery is required for aggregate
compatibility and must not be inferred from the historical 1430-test result.

## Next complete parents: network preparation and Session sharing

Read-only mapping at `99681517c` while the complete runner suite is running;
no runner/package source or machine-readable migration contract changed.
These are implementation inputs, not executed/migrated assertions.

### Agent-network preparation

Original `story_user_prepares_agent_network` has eight ordered direct helpers:

1. `_story_platform_entrypoints`: health service/status; manifest schema/env and
   a real downloadable JavaScript bundle; `/me` user/actor/nickname;
   `/me/repair-info`; known-missing secret stable404.
2. `test_actor_list`: bounded CEO-relative actor directory envelope.
3. `test_actor_search`: product-name search envelope/context (original fallback
   permits empty results, not invented successful recommendation).
4. `test_actor_put_status`: CEO authenticates self hidden -> online; exact state
   readback/restoration, never borrowed Bot updates.
5. `test_bots_my`: Human-owned directory is nonempty.
6. `test_bots_paged`: bounded page of registered Bots.
7. `test_bots_query`: bulk lookup resolves exact CEO/PM IDs.
8. `_story_register_and_onboard_owned_agent`: register-token/expiry -> fresh Bot
   registration -> token-authenticated onboarding -> browser onboarding URL ->
   Human admin re-onboarding -> independent stored capabilities -> exact Bot
   delete/404. Register and Bot tokens must remain secret references; unknown
   registration cannot be replayed or searched/deleted by a name prefix.

Sources: `e2e-test/stories.sh:254-365`, `e2e-test/actor.sh:46-198`.
Current owned config intentionally has an empty manifest and cannot qualify the
asset obligation. Use the real panel build, copied/bound into a fresh Run before
server startup; do not replace it with a generated >1000-byte dummy. Exact asset
URL/origin, content type, bytes and SHA256 should be checked. Existing public
artifact semantics are file-backed `/assets/{bundle}/{filename}`. The temporary
Bot's durable creation intent and supported resource type must be explicit before
adding its deletion; Group-only ledger logic is not a Bot cleanup policy.

Panel prerequisite completed from the same worktree: `npm ci --no-audit --no-fund`,
`npm run typecheck`, `npm run build`, `npm run test:umd`, all exit0. Actual
`apps/bcs/assets/panel/dist/index.umd.js` is177711bytes, SHA256
`1b3eccde8566976f499128d350674be04b2218d34063c1c7801b1c62f389ac56`.
The UMD contract confirms both BCS panel exports. Build receipt is retained in
ignored `scripts/.dependencies/e2e-owned-runtime/panel-build-evidence.json`.
This is real artifact validation, not live manifest serving or Story PASS.
No tracked dependency/lockfile changed; node_modules/dist remain ignored.

### Session sharing, completion and service invocation

The original `story_user_runs_and_shares_sessions` has nine ordered helpers;
existing PRE-02 cannot substitute for this complete parent:

1. `_story_connect_with_group_session_jwt`: locally configured legitimate Human
   Principal; no-auth401/unknown-field400; public-v1 Bot lookup, Group/Session
   create/read/update/list/messages; no-store JWT; actual Upgrade/close; exact
   cleanup. Local signing is not remote gateway authorization.
2. `test_group_session_via_cli`: actual PM CLI Group and Session create/list/get.
3. `test_group_invite_link`: exact Group invite token and Human join.
4. `test_session_invite_link`: actual CLI Session invite and URL.
5. `test_bot_groups_of_bot`: exact membership listing includes created Group.
6. `test_session_invite_join`: PM-created Session invite joined by Human.
7. `test_session_lifecycle`: rename, add ENG, ENG mode, PM chat, history read,
   remove ENG, exact Session delete/readback and Group cleanup.
8. `_story_complete_and_invoke_sessions`: missing-message delivery APIs return
   no invented state; missing cancellation rejected and Bot manual resolution403;
   PM chat Session completion/output survives readback; service concurrency2;
   PM invocation202/read; CEO foreign-caller403; wrong completion API403;
   exact creator-scoped chat/service Session and Group deletion.
9. `test_session_collection`: PM collect/uncollect idempotency; PM/ENG listing
   isolation; nonparticipant QA rejected; final collection absence.

Sources: `e2e-test/stories.sh:529-793`, `e2e-test/group.sh:316-784`,
`e2e-test/group_session_ws_probe.py`, auth/CLI helpers in `common.sh`.
Reuse owned CEO/PM/ENG/QA identities, configured Principal/WS/invite secrets,
actual product CLI and bounded receiver. Do not create fake five-demo-Bot
`session.json` layouts merely to run the old shell helpers, fabricate history,
or treat service invocation creation as service terminal execution. The owned
runtime currently has no actual engine reasoning/callback receiver/dispatcher;
these boundaries must remain visible. New implementation should preserve the
nine-step order and all admission/isolation assertions before local registration.

### Actual manifest prerequisite probe (2026-10-15)

The full panel `npm run verify` gate also passed: typecheck/build/UMD, focused
component behavior, integration fixtures, deterministic visual baselines and
public/asset scans. Log: `/private/tmp/e2e-panel-full-verify-20261015.log`.
This is the repository's local panel test gate, not a claim of real remote UI
integration; the build hash remains the one recorded above.

A separate freshly owned BCS probe
`network-asset-probe-20261015-133157-145646` copied the real asset into its private
Run and configured the file-backed manifest **before spawn**. Actual `/manifest`
returned schema1/env local/name bcsPanel/path `/assets/bcsPanel/index.umd.js`;
GET returned `application/javascript; charset=utf-8`, exactly177711bytes and
the approved build SHA256. The owned child stopped normally. Log:
`/private/tmp/e2e-network-manifest-probe-20261015.log`; private receipt:
`scripts/.dependencies/e2e-owned-runtime/network/network-asset-probe-20261015-133157-145646/network-asset-probe.json`.
The probe used a test-only configuration seam before startup, not a running-config
mutation. Production runner asset input/copy validation, full network Story,
canonical registration and remote qualification remain NOT_IMPLEMENTED/NOT_RUN.
No existing credentials, remote endpoint, tracked dependency or runner source
was changed while the full runner regression stayed active.

## Fresh complete runner regression after Workspace integration (2026-10-15)

Started at source commit `99681517c77a941a17b0a6be76787b35a251bfff`; the same
process completed without restart. **1552 tests in 1989.251s, OK, 0 failures,
0 errors, 0 skips**, exit0. Discovery count and final counts both1552.
All runner Python and test sources stayed unchanged during execution; later
commits only recorded documentation/prerequisite evidence. Both BCS and CLI
SHA256 values were verified against owned build receipts before discovery.

Command: Python unittest discovery over `apps/bcs/scripts/e2e-runner/tests/test_*.py`
with `BCS_E2E_TEST_BINARY`, `BCS_E2E_TEST_BINARY_SHA256`,
`BCS_E2E_TEST_CLI_BINARY`, `BCS_E2E_TEST_CLI_BINARY_SHA256` loaded from private
owned-runtime build evidence, and bytecode writes disabled. Full log:
`/private/tmp/e2e-runner-full-after-workspace-20261015.log`; log SHA256
`56d4f19a68302146f77e826b4b227b60a6e8aa0381d346f1918d203bd56b3432`.
The unittest summary and final counters were parsed independently because
some existing tests flush JSON stdout after the unittest summary.

This is complete **runner** regression, not the all-module Singlebox coverage
gate, production/preproduction release acceptance or all reviewed Story migration.
OpenSpec remains38/54; proceed with remaining network/Session Story work.

## Formal owned panel input and manifest readback (2026-10-15)

Replaced the prerequisite's temporary pre-start patch with an explicit optional
`LocalPanelAsset` input to the configuration loader and OwnedBcsProcess. The
artifact is validated before Run creation, exclusively copied before spawn,
read-back verified and privately receipted. Source open is nonblocking before
regular-file validation, so a FIFO cannot hang admission. Wrong digest,
nonregular/link input, mutated source, existing owned paths and partial writes
fail without adoption/cleanup/retry. Runtime defaults remain unchanged.

The existing `runtime smoke`/`fixture-smoke` can opt in using paired
`--panel-asset` and `--panel-sha256`. Actual bounded GETs require exact local
manifest/path, JavaScript media type, size and approved SHA, with pre/post input
checks. No runtime build/download or new remote route exists.

Independent unpatched CLI Run `network-panel-20261015-140402-e0f7ef` completed
exit0, LOCAL_RUNTIME_SMOKE_PASS, asset PASS and owned child STOPPED, serving the
real177711-byte panel from its private copy. Private smoke-report SHA256:
`a423cc60a09efaca1aabae8dcb9596aa4ee4e281c1397f1b45ac94b5f8662ea9`.
Log: `/private/tmp/e2e-local-panel-independent-final-20261015.log`.
A first command with sibling runtime/state directories was correctly denied
before startup; the independent retry used a fresh Run and the established
child-directory contract, not a guard bypass.

This only closes the real manifest asset prerequisite. The Agent-network
identity/directory/onboarding/delete business Story is still NOT_MIGRATED;
the original reviewed denominator and OpenSpec38/54 remain unchanged. The previous1552-test
full runner result predates this increment; focused regression below is its own
evidence rather than reusing the older full-suite claim.

Focused acceptance completed: **71 tests in 199.684s, OK, zero failures, errors
and skips**, process exit0. Selection covered panel asset/runtime, local config,
owned BCS/liveness, Workspace reporting, migration inventory and case guards,
with approved real BCS/CLI/panel artifact inputs enabled. Log:
`/private/tmp/e2e-local-panel-regression-20261015.log`. The independent CLI smoke
above and this regression are separate evidence. `git diff --check` and
`openspec validate bcs-multi-environment-e2e --strict` also passed.

## Complete Agent-network executor implementation (2026-10-15)

The separately versioned `network-migration-contract.json` preserves all eight
ordered original helper obligations without rewriting the historical three-parent
migration map. `local-network-run` owns one fresh BCS/panel/four-role fixture
lifecycle and `local-network-report` verifies its frozen evidence offline.
Temporary Bot registration is a separate exact-resource intent ledger, not an
extension that grants remote Bot deletion. CEO self-status is the only fixture
mutation and is read back online; the remaining original flow performs registration,
Bot-token onboarding, browser URL validation without visiting it, Human review,
capability readback and exact owner deletion/404. No broad cleanup or POST retry.

Canonical publication waits for owned-process shutdown and cleared credential
references, preserves all eight obligations and the original reviewed release denominator,
and binds JSON/JUnit/Markdown outputs plus original sources. This implementation
entry is not acceptance: live regression and independent CLI evidence are still
pending. Task6.1 and OpenSpec38/54 remain incomplete; remote and release gates
are unchanged.

Acceptance completed: **111 tests in 251.311s, OK, zero failures/errors/skips**,
exit0. Selection included new network API/lifecycle/report invariants, panel
asset/runtime, migration inventory, case guard, shared reporting, actual Workspace
reporting, CLI File Story and trusted-team registration. All approved actual
BCS/CLI/panel inputs were enabled. Log:
`/private/tmp/e2e-network-regression-20261015.log`; SHA256
`1a0ec9580262b1bd0e2ef9514ae01e5c3a0d354de7caff2e7fe6ffd69ce5caaf`.

Independent unpatched CLI Run `network-flow-20261015-150347-5e77ff` exited0:
LOCAL_NETWORK_PASS / canonical LOCAL_SUITE_PASS, all8 requirements,38 actual
journaled operations, six-state exact Bot ledger through DELETED_CONFIRMED_404,
unchanged four-role snapshots/owned Bot inventory, cleared credential references
and owned child STOPPED. Actual built177711-byte panel served and verified.
Summary SHA256 `45c02d78e45fce68ff6cf2fc2be3710afda181d10cb92df0c82002e03c6a8300`.
A second new CLI process verified the report offline with identical summary and
unchanged original Run files. Log:
`/private/tmp/e2e-network-independent-20261015.log`.

The initial25-test run exposed two test-expectation errors, not product failures:
a textual ban on `name.startswith` also matched finite operation-label dispatch,
and the report-output assertion ignored the established `write_reports` envelope.
Corrected those expectations while retaining behavioral foreign-delete/wrong-phase
checks. All four actual API tests already passed, including lost registration
response, lost deletion response, and denied CEO restoration. The111-test run
above is the final evidence; the initial failed log is retained separately.

This is complete **owned-local Agent-network** acceptance, not remote onboarding,
all reviewed migration, all-module Singlebox coverage or full release qualification.
OpenSpec stays38/54. Continue the original complete Session parent next.

## Session public API/JWT/WS migration (2026-10-15)

The new Session source map preserves all nine original helpers and binds
stories.sh/group.sh/common.sh/group_session_ws_probe.py. The first increment
executes the complete original public API helper, not a replacement PRE-02
slice: public identity/negative schemas, Group and Session operations, JWT
no-store, real WS handshake/closure and exact local cleanup. Full parent coverage
is still1/9 selected; remaining eight obligations are reported NOT_RUN.

Main orchestration/reporting and the gpt-5.6-sol helper worker have disjoint
write sets. No remote operations or credential refresh are involved. This entry
is implementation tracking, not proof of acceptance. Task6.1 stays unchecked.

Acceptance completed: **123 tests in 170.028s, OK, zero failures/errors/skips**,
exit0. Selection covers Session helper/lifecycle/negative reporting, shared WS
transport/evidence/gateway/browser context, actual Agent-network executor and
reporting, migration/case guards, shared reports and actual Workspace reporting.
Approved BCS/CLI/panel inputs were verified and enabled. Log:
`/private/tmp/e2e-session-public-regression-20261015.log`; SHA256
`01b0a050d3f26341290d3d897f6bba03d56f38b93457a42433762f965dcbbfe5`.

Independent unpatched CLI Run `session-public-20261015-154709-9de7fc` exited0:
LOCAL_SESSION_PUBLIC_PASS / canonical LOCAL_SLICE_PASS,21 operations/19 granular
checks, actual WS101/exact group-session connect acknowledgment/explicit close,
Group and two Session ledger tombstones, exact public404s and SQLite removal.
Four per-recipient bootstrap messages remain unchanged. Fixture/unrelated
snapshots remain identical; the owned child stopped and credential references
were cleared. JSON/JUnit/Markdown summary SHA256:
`3bc1c623ac4ca5856a6b17b3a7ac0e5da112ac072bfe6cadfee7667984948e8e`.
New-process offline verification returned the same summary and did not change
any original Run file. Log:
`/private/tmp/e2e-session-public-independent-20261015.log`.

Live implementation corrections, not product patches or lowered gates:
- Human Bot ownership is not Session participation. Original no-view list stays
  tested as empty Human scope; an additional explicit authorized PM view proves
  exact Session membership. See bcs-app-session `resolve_view_actor`/`list`.
- Message history is not empty: the actual product persists recipient-scoped
  SessionContext messages. API PM history must match the exact persisted message;
  after deletion all four bootstrap rows must retain their original digest.
  Retained diagnostic Run `session-history-20261015-153814-b92c91` established
  the mismatch; its failure was not overwritten or counted as acceptance.
- Group delete routing no longer tries to construct a missing Session selector.
- WS Upgrade and connect-control IDs are now journaled before dispatch and used
  unchanged on the actual wire. Optional IDs preserve existing client behavior;
  malformed IDs are rejected before sockets/frames. This changes runner transport
  only, not the product protocol or deployed code.

Storage observations use bounded exact-Run/role queries in the owned local DB,
with at most eight SELECTs per snapshot transaction and explicit row limits.
No remote DB, schema change, SQL deletion, generic Bot stop, or history purge.
The parent denominator stays9, selected helper1 passes and remaining8 are NOT_RUN;
all reviewed release entries remain unqualified. OpenSpec remains38/54, task6.1 open.

## Session CLI/invitation sequence (2026-10-15)

The next increment composes the first six original Session helpers in one owned
runtime: the accepted public helper plus five actual CLI/invitation helpers.
The selected six-helper plan freezes before fixture/public actions. The original
public binding, journal, completion and three reports are preserved; composite
reports publish separately under sharing-reports and bind both stages.

The helper worker uses gpt-5.6-sol; orchestration/reporting work has disjoint
files. No remote service or credentials are used. This entry records ongoing
implementation only; live acceptance and a local commit remain pending.

Acceptance: the final focused suite completed **30 tests in 212.340s, OK,
zero failures/errors/skips**, exit0. It covers finite-operation/ownership guards,
invite signature/target/expiry rejection, actual ten CLI invocations, lost create
response, CLI nonzero/missing token, foreign invite, per-helper cleanup, composite
publication failure and immutable offline reports. Log:
`/private/tmp/e2e-session-sharing-final-focused-20261015.log`; SHA256
`4c87f9f751bbb97fe4ee1a81bb61193ae481187350f0cf34aeb5d93b95781f9c`.
It explicitly verified unchanged runner source digest
`5b822764b63a0d2ab0bcd70ba0be5eecc434f4bb18a2a9a355b83e2ec8b5c50e`
against the independent real Run.

The preceding broader selection executed127 tests in391.354s:126 passed and one
negative-test assertion was stale after restoring the original cleanup order.
It incorrectly forbade the *earlier successful helper's* deletion when a later
helper received a foreign invite. Corrected it to permit exactly h1 deletion,
forbid h2 join/delete, and require three h1 tombstones plus two retained live h2
resources. Only that test changed afterward; the final30-test suite re-ran all
sharing tests successfully. The broader127-test command was not rerun, and is
not represented as a clean single-run PASS. Its126 passing tests include public
Session/WS, Agent-network reports, migration/case guards, shared reports, CLI
exchange and actual CLI File Story. Original log is retained:
`/private/tmp/e2e-session-sharing-regression-20261015.log`.

Independent unpatched CLI Run `session-sharing-20261015-163946-778cb6` exited0:
LOCAL_SESSION_SHARING_PASS / canonical LOCAL_SLICE_PASS, all first6 requirements
PASS and remaining3 NOT_RUN. Sharing stage recorded31 operations including10
actual CLI calls, five Groups/eight Sessions and13 exact deleted ledger resources
(all attempt1). The public predecessor's independent original reports remain
valid. ENG visibility was restored protected; fixture/unrelated snapshots matched.
Sharing history retained17 rows (16 bootstrap contexts plus membership history),
and the public predecessor's four bootstrap rows stayed unchanged. Owned child
STOPPED, private CLI credential contexts cleared. Summary SHA256:
`2e66bdd5c2ace36870eb2ed463599406ec0e41bea09efa1af8bb2b741137011c`.
New-process offline projection matched exactly without modifying original files.
Log: `/private/tmp/e2e-session-sharing-independent-20261015.log`.

Concrete corrections from real execution:
- An eager invite-selector map accessed later, not-yet-created Sessions. Target
  selection is now lazy and restricted to the currently minted owned resource.
- Empty missing-token input no longer falsely matches every serialized journal;
  the separate CLI credential guard still blocks before effects.
- Deferring all cleanup hit the product's default max_groups_as_driver=3 at h4.
  Restored the original per-helper cleanup order; **did not raise the quota** or
  mark a Group completed merely to bypass it. Diagnostic Run
  `sharing-diagnostic-20261015-162858-6a04d4` remains failed/retained.

OpenSpec remains38/54/task6.1 open. The complete Session parent still needs
lifecycle, completion/service invocation, and collection. No remote execution,
full release credit, all-module Singlebox coverage or old-entry replacement.

## Complete Session parent integration (2026-10-15)

`local-session-run` now executes all nine original Session helpers in one owned
local runtime. New final-stage scope is lifecycle, manual chat completion/service
invocation guards, and per-Bot collection:59 operations,2 actual CLI creates,
3Groups/7Sessions,10 precise ledger tombstones. The composed parent executes
111 operations including12 actual CLI calls. Each helper cleans its own resources
before the next creates, retaining the default Group quota and earlier history.

The lifecycle chat requires HTTP200 plus actual API/SQLite message identity;
403/409 cannot become a soft pass. Service202 proves invocation acceptance only,
not successful engine execution. Collection checks repeated collect/uncollect,
PM/ENG isolation and exact QA nonparticipant404. No engines, fake Bot output,
remote credentials or remote cleanup are introduced.

Full nine-obligation plan freezes before any fixtures. `session-reports/` contains
JSON/JUnit/Markdown, binding the complete operation evidence and actual ledgers.
Earlier `reports/` and `sharing-reports/` remain independently readable.
Unknown create/CLI responses are not retried or opportunistically deleted;
later failure/cancellation cannot promote earlier six-helper success to full PASS.

Evidence:

- Initial focused execution:23tests,451.742s,zero failures/errors/skips.
  Log:`/private/tmp/e2e-session-flow-first-20261015.log`.
- Independent unpatched CLI Run:`session-flow-20261015-172704-3010b7`,
  under`scripts/.dependencies/e2e-owned-runtime/session/`; command exit0,
  canonical`LOCAL_SUITE_PASS`,9/9 original obligations,owned child`STOPPED`.
- New-process offline reads of all three reports match their original JSON;
  all59original evidence files retain identical hashes.
  Full summary SHA256:`458082b66ed855c13d1e677f658dfad03a8def8629f5363223eb7679f89a263e`.
  Executor SHA256:`1aafbcb3ebf34e39b288ddc37fcb147c24b3c029e7700bb08f6811cd24667929`.
- The first external verification harness compared the CLI response envelope
  directly with its inner summary and failed after the actual run had exited0.
  Corrected only the external verifier and re-read the same existing Run;
  no business operations were replayed. Corrected evidence is in
  `/private/tmp/e2e-session-flow-offline-20261015.log`.
- Expanded affected regression: **165tests,692.107s,zero failures/errors/skips**.
  Includes public/sharing/full Session, network, migration/case guards, shared
  reporting and CLI/exchange tests; semantic projection rejects13 inconsistent
  success-evidence mutations.
  Log:`/private/tmp/e2e-session-flow-regression-20261015.log`.
  Log SHA256:`a2840a557093cc4105e44d1aa6f45f3620212f53467f559f40874549f8312457`.

This completes only the local Session parent, not task6.1 or remote/full-release
qualification. OpenSpec remains38/54; the original reviewed release Stories and pending
all-module Singlebox/coverage/remote obligations remain unchanged.

## Early Session failure identity correction (2026-10-15)

After complete-parent commit`7074150a1`, a new real-runtime failure injection
reproduced an early-return error: requested`local-session-run` was labelled
`LOCAL-SESSION-SHARING-01` when public execution failed before sharing binding.
The command was already BLOCKED and did not execute later helpers; the defect
was incorrect requested-case attribution, not false success or unexpected writes.

A two-line correction preserves`LOCAL-SESSION-FLOW-01` and explicitly reports
`completion_status=NOT_RUN`, without manufacturing completion binding/reports
or changing the smaller public/sharing commands. Regression first reproduced
1failure in1.987s, then the final affected selection passed **26tests in184.354s,
zero failures/errors/skips**, including complete actual nine-helper execution,
later cancellation/failure, offline integrity, public lifecycle and sharing
admission. Logs:`/private/tmp/e2e-session-early-red-20261015.log` and
`/private/tmp/e2e-session-early-green-20261015.log`.
Green log SHA256:`eead94e8bcdadce17170f32ab64e67a1d89cb2325f197c44e367922e71dfd4de`.
The independent Run above remains evidence for7074150a1, not a new Run under
this follow-up source. Original165-test acceptance is not relabelled as rerun.

## Provider parent source map and owned downlink prerequisite (2026-10-15)

The original Provider parent is frozen as31ordered HTTP obligations:16direct
parent operations and the complete15-operation organization helper. Source hash,
helper nesting, methods, paths and stable IDs are validated before planning.
The ordinary migration plan includes this additional map, but registers **no
Provider executor** and grants no migrated/release count.

`OwnedLocalProviderSink` owns one loopback listener and admits one exact Provider2.0
chat.send, with matched principal/target and bounded body/read/closure. It returns
intentional503 `REJECTED_NO_ENGINE`; it never forwards, sends successful ACKs,
starts engines or fabricates Bot output. Sanitized records retain actual downstream
run/request IDs and body hash, not credentials/raw payload. Unexpected methods,
including methods not implemented by BaseHTTPRequestHandler, invalidate evidence.

Only an explicit live, matching-run sink permits loopback outbound traffic in
owned-local configuration. Other private addresses remain blocked; default local
configuration, eventing, switch allowlists and remote profiles are unchanged.
This is not a port-level product allowlist: the forthcoming finite Provider
executor must still bind every webhook write to this exact owned sink URL.

Validation was batched under D50:

- Worker (`gpt-5.6-sol`) ran7receiver unit tests once:1.248s/OK. Main reviewed
  these changes instead of repeating the entire worker selection.
- Main ran20affected config/source-map/runtime tests in6.430s,zero failures,
  errors or skips. Includes **one actual BCS** probe: Provider+Bot+Organization+
  membership creation, detached admin-start and exact admin-read. Actual
  downstream503 and admin `delivery_failed` are correlated by returned run ID.
  Both receiver and owned BCS close. This six-request probe is explicitly
  `DOWNLINK_PREREQUISITE_ONLY`/full parent`NOT_RUN` and issues no DELETE.
  Log:`/private/tmp/e2e-provider-prereq-20261015.log`.
- Review then added rejection of unsupported methods (e.g.TRACE). Only the two
  affected receiver tests were rerun:2tests/0.114s/OK; actual BCS was not restarted.
  An initial selector used an incorrect test-class name (loader error,zero actual
  tests); corrected selector log:`/private/tmp/e2e-provider-sink-method-corrected-20261015.log`.

Provider/organization/credential/admin-run retention still needs the full executor
and canonical report. No direct DB deletion, remote operation or Cookie access.
Task6.1 remains open; OpenSpec38/54 and the original reviewed release denominator unchanged.

## Complete owned-local Provider/Organization parent (2026-10-15)

The prerequisite above is now integrated as`local-provider-run` and the offline
`local-provider-report`. All31 original source-mapped HTTP operations execute,
including all15 nested organization operations. Supplementary Human admission
and18bounded observations are separate from this business denominator.

Five durable resources are tracked: Provider, Bot, Organization, Membership and
Admin Run. Member/Bot original business DELETEs are only tombstoned after exact
API responses and independent SQLite checks; provider/org/credential records
remain retained. The admin run reaches the declared negative transport variant:
one actual Provider2.0 chat.send rejected503 by the owned sink; create/read return
`delivery_failed` with the exact same run/target/session IDs. No engine success,
remote execution or physical history purge is claimed. QA and the other three
workspace fixtures remain unchanged. Report cleanup is explicitly`RETAINED`.

D50 targeted validation (not a single all-module regression):

-17executor unit tests cover the synthetic HTTP/realSQLite journey, finite wire
 admission, borrowed resources, source/fixture drift, lost create/delete replies,
 independent deletion-proof failures and exact downstream correlation.
-2permission/CLI admission tests and1actual-runtime cancellation test pass.
-Final selection **36tests/11.190s/OK/zero skips** covers the complete real31-step
 parent, offline evidence corruption/semantic rejection, source-map, shared
 reporting and migration guards. All these batches cover **56distinct passing
 tests**; this is not a claim that all56 ran in one invocation on final source.
-The first batch exposed a unit-only macOS temporary-path canonicalization issue
 before any live run; corrected the fixture root and reran only that failed test
 (1test/0.074s). The resumed batch passed18tests then exposed a missing schema
 wrapper on the sink-final artifact; fixed serialization/projection and reran
 only cancellation (1test/2.100s), then continued the remaining36tests. Neither
 correction replayed the completed unit selection or Session/network suites.
-Logs:`/private/tmp/e2e-provider-unit-path-fix-20261015.log`,
 `/private/tmp/e2e-provider-flow-remaining-20261015.log`,
 `/private/tmp/e2e-provider-cancel-fix-20261015.log`,
 `/private/tmp/e2e-provider-final-selection-20261015.log`.

Independent new-entry acceptance:

-Run:`provider-flow-20261015-182648-eecf77`; command exit0,canonical`LOCAL_SUITE_PASS`,31/31operations.
-Actual owned BCS`STOPPED`,receiver`CLOSED`,one exact negative downlink.
-New-process offline summary matches original; all18original files unchanged.
-Summary SHA256:`46878723d361f91d970236e2cf1a4de44c022dc0c8eb9be3da3d14f59a39903f`.
-Executor SHA256:`126b60d5228292db458abfc0666c7b2e2d4a1ee1a7f7ffa0d0cbffc6676103c9`.
-Private evidence under`scripts/.dependencies/e2e-owned-runtime/provider/`;
 independent log:`/private/tmp/e2e-provider-independent-20261015.log`.

Task6.1 remains open,OpenSpec38/54. Provider full local parent is now registered;
the original reviewed release migration qualification remains unchanged. No Cookie,remote
DB,preprod/prod request,wide cleanup or product API modification was performed.

## Complete disabled-channel HTTP parent (2026-10-15)

`local-channel-http-run/report` now implements the entire original six-step
`story_user_validates_external_channel_setup`, with a separately frozen source
map. The CLI Channel parent is explicitly **not** covered: it still requires
actual CLI bind/list/conversation/unbind and owned Group/Session lifecycle.

The disabled bridge must reject bind400/bad_request with its diagnostic; the
isolated runtime must have empty channel bindings, conversations and participant
tables before dispatch and again before no-op PATCH/DELETE. Those mutations
address only a Run-derived missing ID. No create-success or real-resource DELETE
is authorized. Four workspace identities remain unchanged. Unknown outcomes are
not retried, adopted or cleaned up. Six operation intents, two no-op admissions,
API results and bounded localSQLite snapshots are frozen in canonical reports.

D50 validation: **38tests/10.451s/OK/zero failures/errors/skips** in one affected
selection (Channel lifecycle/guards, shared reporting and migration checks).
Includes lost actual response (no subsequent DELETE/retry), failed absence proof
(no business request), six semantic-corruption variants and offline artifact
corruption. No Session/network/Provider/fullrunner repeat was triggered.
Log:`/private/tmp/e2e-channel-batch-20261015.log`.

Independent new-entry Run:`channel-http-20261015-183928-38755c`, command exit0,
canonical`LOCAL_SUITE_PASS`,six/six obligations,owned child`STOPPED`.
New-process offline summary is identical and all16original artifacts unchanged.
Summary SHA256:`253fd4d49d26429e6f91f86d781fce29421803b7608ae69928fe8e44b507360b`.
Executor SHA256:`6391b23c5d43c0cd9958ecf2a492e3c9a7402c29d2edfafddca6f4dc1c4964f1`.
Evidence root:`scripts/.dependencies/e2e-owned-runtime/channel/`;
log:`/private/tmp/e2e-channel-independent-20261015.log`.

Default disabled wiring is deliberately the behavior under test, not a mock
standing in for enabled channels. This does not prove external IM integration,
remote authentication, the separate CLI parent, or full reviewed release coverage.
No Cookie/remote request/DB write/product API change. Task6.1 and OpenSpec38/54
remain open, with original release denominator intact.

## Complete disabled-channel CLI parent (2026-10-15)

The separate`story_cli_operator_validates_channel_management` is now integrated
as`local-channel-cli-run/report`, with all9original obligations:7actualCLI calls
and2exact HTTP business deletions. Additional visibility checks yield14journaled
wire operations. Source mapping includes cli-stories.sh/common.sh/stories.sh and
retains original global JSON mode and create-group context/topic arguments.

The negative bind requires specific400/disabled-bridge error and nonzeroCLI with
both output streams free of the synthetic secret. AllotherCLI commands require
zeroexit and exact output/API agreement. Missing unbind only admits the precise
Run-derived target after a persisted empty-Channel-table check. Group+implicit+
explicitSession are journaled before creation and tombstoned only after independent
ownedSQLite proofs, in explicitSession→implicitSession→Group order. The adapter
itself rejects unadmitted or borrowed deletes. Credentials use private contexts;
cleanup/checkpoint failures and cancellation fail closed without retry/adoption.

D50 evidence across targeted batches (**63distinct passing tests**, not one full
63test invocation):

-8executor guards passed in the initial failfast batch; a unitmock incorrectly
 used CompletedProcess as `side_effect` and failed before successful-return
 finalization. Corrected to `return_value`; all5driver finalization tests passed
 in0.031s,including cancellation/cleanup/checkpoint negative cases.
-4entry/contract guards plus actual-runtime cancellation passed. First live
 setup exposed missing `ROLES` on the executor (before business operations);
 added the constant and reran only the live class.
-The next live run completed all9obligations but correctly blocked its overall
 result because its own retained4bootstrap rows were included in the unrelated
 snapshot. Fixed only the snapshot scope to exclude its exact owned Group;
 independent before/after history digest checks remain required, no rows deleted.
-Live successful parent/offline/semantic projection:3tests/19.434s/OK.
-Remaining shared exchange/report/migration regression:42tests/9.829s/OK.
 All accepted batches have zero skips. No previous large Session/network/Provider
 regressions were replayed. Logs retained at
 `/private/tmp/e2e-channel-cli-batch-20261015.log`,
 `/private/tmp/e2e-channel-cli-driver-fix-20261015.log`,
 `/private/tmp/e2e-channel-cli-remaining-20261015.log`,
 `/private/tmp/e2e-channel-cli-live-fix-20261015.log`,
 `/private/tmp/e2e-channel-cli-live-history-fix-20261015.log`,
 `/private/tmp/e2e-channel-cli-final-rest-20261015.log`.

Independent unpatched CLI Run:`channel-cli-20261015-190321-757818`,exit0,
canonical`LOCAL_SUITE_PASS`,9/9obligations,14wire operations/7actualCLI calls,
3Ledger tombstones,owned child`STOPPED`. New-process offline report matches;
all31original evidence files retain hashes.
Summary SHA256:`7fdeedd70a70796b070727a93488ff66d95b98df8432d166046924f5d2a85820`.
Executor SHA256:`3c37340b6f8306b04672d559481739f84afe10b18b3e1c65e899fb548ad96afc`.
Root:`scripts/.dependencies/e2e-owned-runtime/channel-cli/`;
log:`/private/tmp/e2e-channel-cli-independent-20261015.log`.

Worker model gpt-5.6-sol wrote the bounded executor/driver/storage/tests; main
reviewed and integrated, no worker broad test duplication. No remote/Cookie,
product API change,engine execution,physical history purge or fullrelease credit.
Task6.1 remains open; OpenSpec38/54,the original reviewed Story release denominator unchanged.

## Local Story command extraction before further migration (2026-10-15)

The shared CLI had reached917lines against the mandatory1000line source limit.
Moved the existing19standalone local Story parsers/dispatches into
`local_story_commands.py` (286lines); `cli.py` is now674lines. No command,argument,
help,default,permission,status mapping,lazy import,keyword argument or behavior
was changed. Runtime/pilot/trust/setup/remote/retained branches stay in the main
CLI. This is an explicit finite boundary, not a new plugin/configuration system.

D50 validation without replaying any product journey:

-gpt-5.6-sol worker:8focused unit tests/0.027s/OK, covering exact command order,
 Namespace contracts,Workspace flags,Session variants,returncodes,output guards
 and explicit `cli.write_reports` injection. Main did not repeat this selection.
-Main independent comparison against committed pre-extraction CLI (`1dca105cc`):
 all argparse action schemas,top-level and all19command help texts/defaults match;
 **172dispatch scenarios** match old/new function kwargs,returncode,stdout and
 renderer calls. Business execution is mocked for this differential,not E2E.
 Log:`/private/tmp/e2e-cli-extraction-differential-20261015.log`.
-6existing reports (Session public/sharing/full,Provider,Channel HTTP/CLI) were
 read through the new real CLI in fresh processes. Summaries equal frozen originals,
 all Run files unchanged. **Zero new BCS processes,zero replayed business actions**.
 Log:`/private/tmp/e2e-cli-extraction-offline-20261015.log`.
-Syntax/whitespace and source line limits checked. Earlier real execution remains
 evidence for its original source; offline re-reading does not relabel it as a
 new business execution under this refactor. Task6.1/spec38/54 remain unchanged.

### Next DM parent: reviewed scope, not an executed migration

Source baseline`1dca105cc`,current task branch,not a deployed environment:
`stories.sh:805-834` calls `actor.sh:245` then four explicit requests. Preserve all
**five** original obligations:ping submit202,main submit202,exact caller read,
exact cancel acknowledgement/state,subsequent terminal read. Both submissions
create real Runs; the preliminary ping Run cannot be ignored in the lifecycle.

`routes/bot_chat.rs:115` uses authenticated Bot caller,returns actual run/session
IDs; default session IDs are `chat:<run-prefix>` (`resolve_session_key`,line408),
not BCS Group Sessions. `routes/messages.rs:28/59` delegates exact-owner get/cancel.
`bcs-message-flow/src/a2a_chat/mod.rs:809` marks the owned run cancelled and returns
its state; the application entry also unregisters run routing. This is **not
proof that an external engine stopped**. Default `async_chat_run_store` is memory
(`bootstrap/bcs/src/config.rs:971`),so absence of a local DB row is not persistence
failure or terminal proof. A persistent profile uses a different composition.

Implementation next must open only an owned PM transport receiver,bind both
returned Run IDs and WS request IDs without success ACK/fake engine output,verify
original cancel/read semantics,then separately cancel/read the preliminary ping
and close the receiver/owned process. Existing `OwnedLocalBotReceiver` validates
Group-scoped delivery; it must not reinterpret direct-chat IDs as Groups. Any new
direct-chat receipt must have a distinct exact Run/session/caller/target contract.
No raw DB cleanup,no broad Bot stop,no remote authorization implied. No DM
executor or migration credit has been registered in this increment.

## Complete original Direct Chat parent (2026-10-15)

`local-direct-chat-run/report` implements all5original obligations including the
nested actor helper's preliminary ping. Two real202submissions are separately
journaled and receive exact PM WebSocket frame evidence; the original main
read/cancel/terminal sequence is followed by supplementary exact ping cancellation
and terminal read, without extra parent credit. Twelve ordered HTTP operations
include scoped PM visibility setup/restoration. No engine,success ACK or Bot
output is fabricated; direct Run/session keys never become Group-resource IDs.

Effect states are durable before dispatch:INTENDED→ADMITTED→CANCEL_REQUESTED→
CANCELLED,with the final transition requiring exact caller/target/session-bound
terminal read. Lost create replies becomeUNKNOWN; lost cancellation stays
nonterminal with unknown outcome. No rediscovery,retry,Group/Session DELETE,
failure-path restoration or broad Bot stop. Both receiver and owned child must
close before complete-parent qualification. Default memory-store evidence is
explicitly distinguished from persistent DB/remote engine closure.

D50 targeted validation (**68distinct passing tests across batches**, not one
single68test run):

-Initial28receiver/executor/admission/cancellation tests passed; real parent
 business assertions and bothcancels completed but full result correctly blocked
 at fixture-preservation comparison. Log:`/private/tmp/e2e-direct-chat-batch-20261015.log`.
-Preserved diagnostic Run`direct-chat-20261015-193815-8e3126` established the sole
 drift:PM.bot_info gained null `binding_channels`,`agent_code`,`agent_token`.
 BotInfo serde defaults and bot-store JSON_SET updates (`lib.rs:113,353`) define
 these as absent/None-equivalent. The local fixture observer now hashes parsed
 semantic JSON after removing **only these null values**; every nonnull value
 or other field change remains detected. No product write or rollback changed.
-Focused semantic-negative plus actualparent/offline/projection tests:
 **4tests/5.345s/OK**,log:`/private/tmp/e2e-direct-chat-live-semantic-fix-20261015.log`.
-Remaining command/report/migration regressions:**36tests/3.466s/OK**,
 log:`/private/tmp/e2e-direct-chat-remaining-20261015.log`.
 All accepted selections have zero skips. Previous large Story suites were not
 rerun. The failed diagnostic remains failed; its state was never rewritten.

Independent unpatched new-entry Run:`direct-chat-20261015-194135-936400`,exit0,
canonical`LOCAL_SUITE_PASS`,5/5original obligations,12HTTP operations,2WS receipts,
2exact memory effects CANCELLED,receiverCLOSED,owned childSTOPPED.
New-process offline summary matches;all17original files retain hashes.
Summary SHA256:`1b2ca3af006261cb8232e201bbb14b7ef060f493694b817d3d5f0046c2556224`.
Executor SHA256:`a9f7d46fc8debe381a872e9447a12ed966c9d64d614b21e274b0371f52265e47`.
Root:`scripts/.dependencies/e2e-owned-runtime/direct-chat/`;
log:`/private/tmp/e2e-direct-chat-independent-20261015.log`.

Worker gpt-5.6-sol implemented executor/storage/negative tests; main implemented
source/receiver/report/lifecycle and integrated once. No Cookie,remote request,
physical retention/purge claim or product modification. Task6.1 still covers
remaining originalStories; OpenSpec38/54 and fullreleaseNOT_QUALIFIED unchanged.

## Structured collaboration source map and runtime audit (2026-10-15)

The next original parent, `story_user_runs_structured_collaboration`, now has a
frozen17-operation source map in `structured-migration-contract.json`. It covers
template catalog/detail, inline-YAML Group creation/read, definition read/patch/
missing upgrade, explicit Run start/read/graph/node/Human checks/cancel/terminal/
rejected rerun and creator-scoped Group deletion. It is NOT an executable parent
registration and grants no release qualification. All29original parents remain
in the release denominator; task6.1 stays open.

The reviewed source slicer must account for embedded Python: the first isolated
closing brace occurs inside `json.dumps`, not at the shell function end. The
previous first-brace approach would capture only2of17operations. The finite
slicer checks the exact next top-level Story header and pinned complete source
hashes; it is not advertised as a general shell parser.

Product-source prerequisites verified on the current development branch:

- `routes/groups.rs` preserves default `create_initial_session` and
  `start_initial_run=true` for this original inline-YAML request. Group creation
  starts an implicit state-machine Run before returning; the executor must not
  suppress it to obtain an easier passing subset.
- The original explicit endpoint is **POST /groups/{id}/state-machine-runs**,
  not the one-shot `/sessions/{id}/state-machine-runs` endpoint. In
  `bcs-collaboration-runtime/src/runtime.rs`, absent `session_id` creates another
  service-invocation Session; the one-shot idle-session admission is a different
  path. Both implicit and explicit Run/Session effects need exact accounting.
- Cancellation updates the exact Run to aborted and completes its service
  Session. It is not a general Bot stop or proof of an external engine stopping.
  The local transport receiver must not fabricate a successful model response.
- The owned-local config currently creates an empty templates directory. The
  catalog must receive real approved repository seed assets before the original
  first two assertions can pass; no synthetic template list is acceptable.

D50 validation reused the unchanged accepted selection:
`tests.test_local_structured_contract tests.test_migration_inventory`:
**14tests/0.571s/OK**, no skips. Log:
`/private/tmp/e2e-structured-map-20261015.log`.
No BCS restart, remote calls, Cookie reads or business writes were performed for
this map-only increment. Actual full-parent execution and retention checks remain
pending; source inspection alone does not establish runtime success.

## Real owned template catalog prerequisite (2026-10-15)

Added explicit `LocalTemplateAsset` to the owned runtime/configuration boundary;
existing callers retain the empty-template default. The approved manifest pins
all19actual YAML files (registry plus9templates in each of2languages). Input and
copy validation reject traversal, links, file/count/byte overflow, unlisted files,
manifest/hash drift and occupied destinations. Directory scans stop at their
budget before sorting, rather than materializing unbounded directory entries.
No placeholder catalog or modified product template is introduced.

The read-only probe verifies the exact approved catalog and original guided
zh-CN YAML through the actual product's two original HTTP endpoints. Runtime
ownership/config/receipt/assets are checked before and after reads. HTTP uses
fixed loopback, bounded bytes/deadline, no redirects/proxies or auth fallback.
It is explicitly a prerequisite, not all17Structured operations or remote
qualification; no Story registration or task-completion count was advanced.

D50 evidence across targeted batches: **17distinct tests accepted**, zero skips:
- gpt-5.6-sol worker:6asset tests plus4existing config tests,10/0.316s/OK.
- Main bounded-scan refinement plus probe:11tests/0.403s,10passed; one new test
  fixture failed because its LocalBcsInputs constructor omitted required
  environment. This was test setup, not a product or authentication failure.
- Fixed only the fixture construction using dataclasses.replace, then reran only
  that failed test:1/0.051s/OK.
- Remaining live catalog and existing two-child ownership regression:
  2/1.805s/OK. One actual BCS catalog run; ownership regression uses local helper
  processes, not additional business Story runs. No previous full Story replay.

Persistent catalog acceptance:
`scripts/.dependencies/e2e-owned-runtime/template-catalog-20261015/catalog-acceptance.json`.
Actual9catalog entries and byte-exact guided YAML were confirmed with HTTP200;
owned BCS stopped normally (returncode0,not forced). Manifest SHA256:
`a509de4e57c420b75f686cffcd93c9823849a2e3f8d8ca1fc10f11efb6467384`.
Guided zh-CN YAML SHA256:
`f31c7090d6d8dfbcb60dece28f1bb0df9b6e7f4d559b9b86a70791888061a69d`.
Product binary is the existing approved `5169fc12c` build, SHA256
`23c8296c7ac2ccc3535211c3a4b2c389d6e244429d54a48150235f6c735ccdb0`,
not a claim of latest master or deployed code. Final log:
`/private/tmp/e2e-template-final-20261015.log`.
No new CLI entrypoint was added, so no redundant independent command run was
needed. Full runner/Singlebox remain integration milestones. Task6.1 remains
open, OpenSpec38/54 and the original reviewed parent release scope unchanged.

## Complete Structured collaboration local parent (2026-10-15)

Added `local-structured-run/report`, preserving all17original requirements and
request actors/order. In addition to the original17HTTP operations, the finite
22-operation journey temporarily exposes/restores owned PM and reads/cancels/
re-reads the implicit initial Run. No `start_initial_run=false`, no alternate
one-shot Session endpoint, no fabricated model outputs and no skipped Group
DELETE were used to make the parent pass.

Two exact Node dispatches are accepted on the owned CEO WebSocket: original
instruction for the implicit Run, revised instruction after patch for the explicit
Run. Delivery evidence pins Run/Session/Group/node/attempt/recipient/request ID and
instruction hash. It proves transport reception only; no success ACK or engine.

The operation journal includes durable Group/implicit/explicit effects before
writes. Unknown create/cancel failures stop immediately without retries,
rediscovery, failure-path fixture restore or cleanup. API cancellation/readback
and exact SQLite `aborted` Runs plus completed service Sessions with error
`aborted` are required before creator-scoped Group deletion. The successful
post-delete proof confirms absent Group/Sessions/runtime binding and retains
exactly the two aborted Runs, answer Nodes and two definition versions. Bounded
read-only queries use fixed tables and exact identities; no direct DB cleanup.
Fixture comparison uses the previously reviewed null-field normalization; all
other fixture/relationship/provider state remains unchanged.

D50 acceptance, **42distinct tests passed** across selections, zero skips:
- gpt-5.6-sol sidecar storage/receiver:8tests/0.003s/OK.
- Main guard/source-map/CLI/migration selection:28tests/0.634s/OK.
- One full actual parent plus offline/report negative checks:4tests/5.953s/OK.
  Log:`/private/tmp/e2e-structured-first-20261015.log`.
- Additional lost-create/lost-cancel and exact retention-proof negatives:
  2tests/0.007s/OK. Log:`/private/tmp/e2e-structured-failure-20261015.log`.
- Report-only tightening reused the frozen successful Run without business replay;
  five forged receipt-identity variants were rejected offline.

New-entry independent actual CLI acceptance (no patched business calls):
`structured-20261015-202228-9deed9`,exit0,`LOCAL_SUITE_PASS`,17/17requirements,
22HTTP operations,2WS receipts,2cancelled Run effects,exact Group deleted and
history retained. Receiver CLOSED; owned child STOPPED normally. A second CLI
process projected the same canonical summary offline and all36Run files retained
their hashes. Receipt:`scripts/.dependencies/e2e-owned-runtime/structured-independent-acceptance.json`.
Summary SHA256:`647fe761a880aee3935c1cac582ebb02bc48367653b286f41a8f428270878d5f`.
Executor SHA256:`2e9fe4177a4bf55b7f9e4de5c330888d1f712ea7d281b9f8d76c82e276c3b826`.
Log:`/private/tmp/e2e-structured-independent-20261015.log`.
The approved BCS build remains5169fc12c; no new product binary or deployment is
claimed. No Cookie reads or remote actions occurred. Canonical cleanup RETAINED,
remote NOT_RUN and fullrelease NOT_QUALIFIED remain explicit. Task6.1 stays open
for remaining original parents; OpenSpec38/54 and the29parent denominator remain.

## Event Webhook complete source map (2026-10-15)

The next parent `story_user_receives_group_event_webhooks` now maps all20reviewed
obligations:13original API operations,4real callback observations, fresh receiver
initialization, local Principal preparation and per-stream strict ordering. The
source contract also pins the original polling helper, mock server and Principal
probe sources; it cannot silently shrink this parent to only synchronous HTTP.
Conditional legacy cleanup/delivery-read branches remain required for qualification.

The shared mock `/control/event-webhook/clear` must become a new per-Run owned
listener with an initially empty receipt set, not a reset of any existing service.
This is a declared fixture-lifecycle substitution, not a claimed product API.
Runtime configuration currently leaves Eventing disabled. Product bootstrap
`eventing_wiring.rs` owns a separate outbound policy: local Eventing requires
explicit enable/dispatcher enable and local HTTP-loopback/nonstandard-port flags;
normal shared outbound policy must not be relaxed just to receive events.

The original inline public Group creation uses Human Principal, creates an
implicit Session, and may dispatch work before the response; explicit Session
creation adds another effect. Future execution must account for both without
pretending that Group creation is side-effect-free. Group Subscription delivery
is asynchronous; the synchronous Subscription test is a separate operation and
must not stand in for actual group/session/message delivery.

Validation: `tests.test_local_event_contract tests.test_migration_inventory`,
**14tests/0.551s/OK**, zero skips. This map grants no executor or release credit;
Task6.1/6.2 remain open, OpenSpec38/54 and all reviewed parent obligations unchanged.
No BCS process, remote request, credential read or business mutation was needed
for this source-only increment.

## Owned Event receiver and scoped Eventing configuration (2026-10-15)

Added per-Run `OwnedLocalEventSink`, created by gpt-5.6-sol and reviewed/refined
locally. It owns an ephemeral127.0.0.1listener and random POST path, with no
control/reset/query/forwarding endpoint. Create intent must be armed explicitly;
callbacks racing the create response are bounded-buffered, and only exact
Group/Subscription binding turns them into validated receipts. The synchronous
Subscription test has its own exact subject and sequence1stream; all streams,
including that test stream, must remain strictly increasing and unique.

Receipts contain event/type/group/stream/sequence and explicit absent
Authorization, not raw payloads or endpoint URLs. Unexpected requests latch
failure; limits cover32records,64accepted TCP connections (including empty ones),
64KiBbody,1second absolute body read and finite <=5second observation waits.
Closing listener/accepted sockets and joining the owned thread is bounded; saved
IDs or state files never grant process ownership. No engine or Bot outputs exist.

The optional typed `event_sink` input in local config/OwnedBcsProcess requires the
same live Run, explicit message storage and no concurrent Provider fixture sink.
It enables Eventing record/fanout/dispatcher with one worker/host slot,50ms polls,
2s lease and1s drain. Only the dedicated Eventing outbound policy permits local
HTTP/nonstandard ports. Shared outbound remains block-private=true and
allow-loopback=false. Missing/foreign/closed/forged sink fails before runtime
files or subprocess creation; default callers remain unchanged.

D50 validation: **15distinct tests passed across affected selections**, zero skips:
- Final sink/config/default-config selection:13tests/1.809s/OK.
  Log:`/private/tmp/e2e-event-sandbox-20261015.log`.
- Refined TCP counting and explicit absent-auth receipt:3focused tests/0.235s/OK
  (two affected existing tests plus one new empty-connection quota negative).
  Log:`/private/tmp/e2e-event-sink-bound-20261015.log`.
- Existing independent-owned-child isolation regression:1test/1.145s/OK,
  using local helper processes, not real BCS business calls.
Earlier sidecar6test result was not reused as proof of the subsequent refinements.
No complete Story was replayed and no product BCS process, Cookie, remote request,
actual BCS callback or deployment was involved. Real Eventing startup and full
20-obligation parent acceptance are intentionally deferred to one coherent Story
integration, not mislabeled as passed by these loopback-fixture tests.
Task6.2 remains incomplete (Webhook parent/slow-Judge/fixed-loop acceptance),
Task6.1 remains open, and OpenSpec38/54 / the original reviewed parents are unchanged.

## Complete Event Webhook local parent (2026-10-15)

Added `local-event-run/report`, original20requirements and13API requests. It uses
an owned fresh listener instead of shared mock reset, the original public
Group/Subscription/Session face, and the original legacy message route. Actual
Group-created, Subscription-test, explicit Session-created and message-created
callbacks are required; the implicit Session is separately accounted for. Sink
receipts now retain safe subject identity, and exact subject waits prevent an
initial Session/bootstrap message from satisfying a later operation's assertion.

The first actual Run correctly BLOCKED at message callback correlation: legacy
send returned one message ID while the real callback carried another. The failed
Run `event-first-20261015` remains failed and retained, with BCS stopped normally
and no follow-up business DELETE. Product source confirms the distinction:
`bcs-message-flow/src/group_flow/group_send.rs` creates a legacy GroupMessage ID;
`group_flow/persistence.rs` separately creates the persisted logical message and
Event subject. No product defect fix or assertion equality was fabricated.

The adapter records both identities. A fixed exact-Group/sender/content query
proves absence before sending, then exactly one matching row in one of the two
owned Sessions. That persisted ID must match the callback subject. Missing,
foreign or ambiguous rows block. The logical message is re-read after deletion
and must remain byte-semantic-identical to the earlier safe projection.

Success preserves original subscription revision1->2->3->4, exact deletion of
the subscription/explicit Session/Group, absent Group/Sessions/runtime binding,
retained deleted subscription and Event/Delivery/message history. Receipts and
retained events are cross-checked by Event ID; exact Delivery and successful HTTP
Attempt evidence is also required. No DB write, shared reset, fixture relationship
change, engine response or production/remote action occurs. Offline reports retain
the29parent denominator,cleanup RETAINED and fullrelease NOT_QUALIFIED.

D50 evidence across targeted selections: **50distinct tests passed**, zero skips:
- gpt-5.6-sol storage observer:8tests/0.006s/OK.
- Guard/source-map/CLI/migration selection:27tests/0.513s/OK.
- Affected sink with exact-subject filtering:8tests/1.484s/OK;
  `/private/tmp/e2e-event-exact-sink-20261015.log`.
- New logical-message mapping negatives:2tests/0.001s/OK;
  `/private/tmp/e2e-event-logical-message-20261015.log`.
- After fixing the failed full-parent setup,4previously unexecuted live/report
  checks passed in5.693s; `/private/tmp/e2e-event-logical-live-20261015.log`.
- Additional retention/Event/Delivery/Attempt linkage negative:1test/<0.001s/OK.
No previously accepted large Story suite or full runner was replayed.

Independent new-entry CLI Run `event-20261015-205550-8b921d`:exit0,
LOCAL_SUITE_PASS,20/20requirements,13HTTP operations,5actual callbacks (including
both Sessions),Subscription tombstone revision4,exact Group/Session deletion.
BCS STOPPED normally,receiver CLOSED,no engine. Fresh-process offline CLI returned
identical canonical summary and preserved all16Run-file hashes. Receipt:
`scripts/.dependencies/e2e-owned-runtime/event-independent-acceptance.json`.
Summary SHA256:`e76f77af9e285b587a0e93c1ad65b51a35f62ccaca2b0fbb5e6f37b63640748f`.
Executor SHA256:`830c95ad551abc07fb1add6658d52e8eecaac7d2a7aee0a59dc3d5db67d843c1`.
Log:`/private/tmp/e2e-event-independent-20261015.log`.
The approved historical BCS5169fc12c binary is unchanged. Task6.1 remains open for
other original parents;6.2 still includes slow-Judge/fixed-loop;OpenSpec38/54.

## Slow-Judge complete source contract (2026-10-15)

The next parent `story_provider_callback_survives_slow_judge` now has22frozen
obligations:12direct/callback HTTP operations,3state polling obligations,2actual
Provider deliveries and5sandbox/control observations. This includes original
trust setup, implicit Group startup, explicit review awaiting_response,
one-second final callback acceptance, blocked Judge, running/judging node and
persisted candidate artifact, owner release, publish dispatch/callback, completed
output/approved decision and exact Group/Bot business retirement.

The callback helper's `--max-time 1` is pinned and explicitly represented; a
normal eventually successful request is not a substitute. Original shared
reset/Provider-clear become fresh per-Run sandbox and immutable receipt cursors.
They must not discard the implicit Group Run's dispatch or use its receipt as the
explicit review. Shutdown while Judge is blocked must terminate with error rather
than approve work just to release a waiter. Success-path release retains the
original deterministic approved decision; this is a simulated Judge/Provider,
not genuine model reasoning or a real Bot engine.

Product LLM config supports `openai_compatible` and an environment-only API key.
The local adapter will bind its base URL to the live owned Judge listener; absent
that input existing `llm.type=none` remains unchanged. No external model/account
or user credentials are needed. The complete original parent is not yet registered.

Validation:13tests/0.475s/OK for new source-map plus migration inventory; after
adding explicit callback/poll path tamper checks,only3affected source-map tests
were rerun. All passed,no skips; no runtime or remote action. Task6.1/6.2 and
OpenSpec38/54 remain incomplete with all reviewed parents in scope.

## Isolated blocking Judge/positive Provider sandbox prerequisite (2026-10-15)

Added `OwnedLocalJudgeSandbox` (gpt-5.6-sol worker), with two private POST paths on
one bounded owned loopback listener. Positive Provider2.0 acknowledgements preserve
three immutable exact-ID receipts:implicitreview,explicitreview,publish. No shared
reset/clear or lost implicit receipt is allowed. Separate body.id and transport
X-BCN-Message-Id remain distinct; state-machine sender bcs_state_machine is not
incorrectly equated with the target Bot. Incoming secrets are verified in memory
and omitted from snapshots.

The single OpenAI-compatible Judge request remains blocked until owner release,
then returns the original approved fixture decision. Closure while blocked
returns failure,never approval; partial-body reads are also interrupted. Limits:
3concurrent/16total connections,3Provider receipts,1Judge,64KiBbody,1sbody read,
20sJudge hold. This is simulatedProvider/Judge only,not realengine or modelquality.

Added explicit typed `judge_sandbox` to config/process input. The same live Run
and message storage are required; Provider/Eventing sink combinations are denied.
`openai_compatible` uses only the owned base URL,20stimeout and environment-only
random key; config never contains the key. Provider loopback is enabled only for
this admitted local profile; private-network blocking remains. Existing no-LLM,
Provider rejection and Eventing profiles retain their behavior. The full Story
must remove the copied environment key after BCS stops as well as closing sandbox;
no secure-erasure assertion is made.

D50 evidence:**18distinct tests passed**,zero skips:
- Worker6sandbox tests/0.487s/OK,including blockedJudge close (error,not approval),
  exactProvider receipt selection,three-receipt budget and credential guards.
- Main10newJudge/default/Eventing config tests/0.645s/OK;
  log:`/private/tmp/e2e-judge-config-20261015.log`.
- Main1new confirmed-partial-body-close test plus1existing independent-owned-child
  isolation regression:2/1.019s/OK;log:`/private/tmp/e2e-judge-close-20261015.log`.
  Partial-body test confirms the reader was entered before timed closure.
Worker tests were not rerun by main. The process regression uses local helper
children,not productBCS. No actualBCS start,remote request,Cookie,paidmodel or
full-parent callback qualification occurred. Complete22-obligation execution,
canonical reporting and independent CLI acceptance remain next. Task6.1/6.2
remain open,OpenSpec38/54 and the original reviewed parent scope unchanged.

## Slow-Judge complete owned-local runner and canonical report (2026-10-18)

The owned-local slow-Judge parent is now exposed as the foreground commands
`local-slow-judge-run` and `local-slow-judge-report`. The runner keeps the full
22-obligation order from `LOCAL-SLOW-JUDGE-01`, requires explicit process,
fixture-write and business-write permissions plus exact Run confirmation, and
uses one fresh state directory. It binds the owned BCS debug binary SHA256, the
owned Provider/Judge loopback sandbox, the four-role local fixture/identity set
and message storage before any business intent.

Success is granted only when all 22 obligations pass, the reviewed parent legacy
denominator and original source binding remain bound, credentials are cleared,
the owned child exits normally and the Judge listener closes without failure.
The run writes persistent binding/execution/runtime files, then publishes a
canonical JSON/JUnit/Markdown suite report. Offline report mode revalidates
the frozen source hashes and does not contact the runtime, remote service,
database or a paid model.

`local-registered-executors` now lists the owned-local slow-Judge executor with
integrated local reporting, but still grants no remote validation or release
credit. Singlebox and preprod adapters, the fixed-loop parent and the original
remote qualification remain open; the cross-environment rewrite plan therefore
retains its pending honest response.

Validation: 33 targeted tests passed in 6.528 seconds with no skips, including
the two real owned-BCS runs for the adapter and full Story command, source-map
and migration inventory integrity, offline report publication, and CLI dispatch
regressions. No remote request, Cookie use or operator credential entered the
evidence. The full all-module singlebox gate and remote path were not run.

## Fixed-Loop source map prerequisite (2026-10-18)

The fixed-Loop parent is now pinned by a separate source map with eight obligations:
run-owned sandbox isolation, fresh local fixture, the approved two-iteration Group run,
the exhausted one-shot Session run, exactly one public Session result, graph/message-history
agreement, exact Group/Bot cleanup, and replacement of shared reset and provider clear.

The binding hashes both the wrapper and Python executor plus `common.sh`, and checks the
original method bodies for iteration context, stale response 409, six node outputs, one
published message and exact cleanup. It is intentionally not an executor registration: no
BCS process, remote request, model call, release credit or task6.2 completion is claimed.

Validation: 4 focused tests passed in 0.155 seconds with no skips. The next implementation
step is the per-Run Provider/Judge sandbox and immutable receipt cursor, without the slow-Judge
22-step contract or any shared control-plane reset.

## Bot-search owned-local integration (2026-10-18)

The fixed Bot-search source contract remains thirteen original operations: twelve reads
plus the separately admitted `POST /admin/bots/$tc_uuid/ensure` TC fixture mutation.
The owned runtime now disables ambient local-Human identity for this suite while
fixture provisioning and receipt read-back use an explicit, runtime-bound provisioning
identity. Unauthenticated search remains genuinely anonymous and must omit the
viewer-specific `is_friend` marker. General policy fields (`visibility`,
`user_visibility`, `friend_ext`, and `friend_check_in_strategy`) remain visible because
the reviewed endpoint design explicitly exposes current policy configuration; treating
those public-policy fields as viewer-private leakage would have weakened the factual
contract instead of fixing the stale assertion.

`LOCAL-EDGE-BOT-SEARCH-READ-01` is now the third case in `local-edge-read-run`, sharing
one fresh owned runtime with the admission and friend-request-list readers. The local
runner binds all 17 read assertions (2 + 3 + 12), records the TC mutation in its own
service-key receipt, publishes a three-case offline JSON/JUnit/Markdown report, and
keeps the historical fixture mutation out of the read spine. `migration_inventory.py`
registers this as `LOCAL_EXECUTOR_ONLY` with integrated shared-runtime reporting,
`remote_validation=NOT_RUN`, and no release credit. Singlebox/preprod route admission
and remote execution remain pending.

Validation: the focused 72-test regression passed in 11.595 seconds with no skips. It
included the real three-case owned-BCS run, offline report reproducibility, migration
inventory integrity, fixture provisioning-header guards, and Bot-search contract/source
checks. Commits `89bf6352d` and `8035238a8` record the two verified increments. No
remote request, preprod admission, full singlebox gate, or release qualification is
claimed; task 6.1 remains open for other parents.


## Agent-network cross-environment internal search slice (2026-10-18)

The fourth reviewed Agent-network read obligation (`NETWORK.actor-search`) is now represented
as `PUBLIC-NETWORK-ACTOR-SEARCH-01`. Unlike the three public-v1 directory slices, OCB station
deployment exposes the exact legacy equivalent through internal-v1
`GET /api/v1/collaboration/bots/<driver-bot>/candidates/search?q=...&purpose=discovery`.
The route binding therefore names that exact internal-v1 face while the overall profile
remains a public-v1 Singlebox/Gateway entrypoint with `manifest_face=internal-v1`; it cannot
be rewritten to the public candidate page, changed to the member Bot, or promoted to release
credit. The authenticated request is Human-cookie only, with Bot Authorization omitted.

The adapter validates the bounded search result (`items`, `search_mode`, item shape), but the
report persists only item count, search mode, route/viewpoint/transport evidence; candidate
names or profiles are not stored. Route, face, viewpoint, manifest-face and Cookie drift are
rejected before network use. No real Singlebox, preprod request or remote validation was run;
`remote_validation=NOT_RUN` and task 6.1 remain open.

Validation: the focused 68-test transport/route/network regression passed in 5.733 seconds
with compileall and the migrated Case guard passing. The final source then passed the full
e2e-runner Python regression: 2038 tests in 149.059 seconds, OK with 215 documented skips.
This is still not a real Singlebox/preprod execution, full-module coverage gate, or release
qualification.

## Agent-network cross-environment station Bot page (2026-10-19)

The fifth reviewed Agent-network read obligation (`NETWORK.bots-paged`) is now represented as
`PUBLIC-NETWORK-BOTS-PAGED-01`. OCB station does not preserve the legacy `/bots/paged`
route, and the reviewed equivalent used here is the public-v1 Human-actor candidate page:
`GET /openapi/v1/collaboration/bots/human_<owner>/candidates?purpose=discovery&offset=0&limit=5`.
This mapping is deliberately not `/bots/mine`: the original obligation asks for a page of
registered Bots, while `/bots/mine` is a distinct Human-owned-directory obligation. The
Human actor in the path is derived from the profile owner and rejected on drift; the request
uses Human Cookie only and strips Bot Authorization.

The slice validates exactly the first page: at least one returned physical Bot, no more than
five items, total >= 1, offset 0, and limit 5. Candidate records are not persisted; evidence
contains only page counts plus the route, transport, identity, owned Human actor and
`fixture_effect=NONE`. Route, face, limit, acting-actor and Cookie drift fail before network
use. No real Singlebox or preprod request was run, so all station slices remain
`remote_validation=NOT_RUN`, and task 6.1 remains open.

Validation on this worktree: focused network/route transport tests passed in 0.665 seconds
(33 tests, no skips), `compileall` and the migrated Case guard passed, and the full e2e-runner
Python regression passed: 2045 tests in 152.322 seconds, OK with 215 documented skips.
This is contract and local test evidence only, not a Singlebox/preprod execution,
full-module coverage gate, or release qualification.

## 2026-10-19 Agent-network actor-status cross-environment slice

`PUBLIC-NETWORK-ACTOR-STATUS-01` freezes the reviewed `NETWORK.actor-status`
source obligation and adds one `singlebox/public-v1` and `gateway/public-v1`
adapter. It mutates only the dedicated driver Bot, performs hidden and online
updates with raw readback evidence, and restores online before completion.
Failures produce `BLOCKED` with no partial assertion credit.

Focused coverage was 48 tests (both new/affected network files), and the full
runner Python discovery run completed 2073 tests / OK (skipped=215). This is
contract-level mock validation only: no real Singlebox or preprod request was
executed, remote validation remains `NOT_RUN`, and neither 6.1 nor release
credit is granted.

## 2026-10-19 Channel bindings cross-environment slice

`PUBLIC-CHANNEL-BINDINGS-01` freezes four reviewed obligations from the disabled
Channel Story: rejected create (`invalid_channel_params`), a valid global
list-before read without a remote empty-world assumption, exact dedicated
driver + uninstalled channel by-target absence, and list-after with no ghost
binding for that exact pair. `CHANNEL.pause-missing` and `CHANNEL.delete-missing`
remain explicit blockers because local success semantics are not equivalent to
the public-v1 missing-binding 404 contract.

The adapter admits only `singlebox/public-v1` or `gateway/public-v1`, binds all
three routes without path guessing, sends only the Human Cookie, and treats a
201 or a different 400 as failure. The new transport path is finite and does
not retry, fallback, or normalize a 400 into success. Case guard and focused
coverage passed; the full runner regression completed 2080 tests in 185.647
seconds, OK with 215 documented skips. No real Singlebox or preprod request was
made; remote validation remains `NOT_RUN` and no release credit is granted.

## 2026-10-19 Structured collaboration template cross-environment slice

`PUBLIC-STRUCTURED-TEMPLATES-01` freezes the reviewed `STRUCTURED.templates-list`
and `STRUCTURED.template-detail` obligations from the Structured collaboration
Story. The adapter reads the exact station `internal-v1` catalog list and JSON
detail routes for `single-bot-guided-answer` with `lang=zh-CN`, verifies the
template appears exactly once in the list, and requires the detail identity,
language, name, JSON definition, and non-empty YAML contract. Both local and
gateway profiles use the same route contract and Human Cookie only; no Bot
Authorization is sent.

This slice is read-only and has `fixture_effect=NONE`. It does not migrate the
remaining group, definition, run, response, cancellation, or cleanup obligations,
does not mark the Structured parent complete, and grants no release credit.

Validation on this worktree: the new Case contract and adapter passed the
e2e runner Case guard; the new template adapter suite passed 7/7; the affected
network/channel/structured contract run passed 59/59; and the full runner Python
discovery run completed 2087 tests / 182.240 seconds, OK (skipped=215). No real
Singlebox or preprod request was executed, and remote validation remains
`NOT_RUN`.

## 2026-10-19 Session public-JWT/WS dual-environment slice

`PUBLIC-SESSION-JWT-WS-01` freezes the first complete Session obligation
(`SESSION.public-jwt-ws`) and binds it to the already reviewed shared `PRE-02`
executor and its existing owned-local public helper. The mapping requires the same
pilot adapter methods and public-v1 Group/Session/JWT/WS routes for Singlebox and
Gateway; a side-specific implementation cannot replace or weaken the shared
business assertions. An offline admission check verifies the exact route binding
before network use and projects PRE-02 results without extending credit.

For an owned local Singlebox profile, admission reports the existing local scope.
For a Gateway preprod profile, it remains fail-closed on the reviewed Group/Session
effect admission; the mapping explicitly states no automatic remote write, no
request, and no release credit. The other eight Session parent obligations remain
unmigrated, and task 6.1/4.2 are intentionally not marked complete.

Validation on this worktree: the new case guard passed; 6 focused
`PUBLIC-SESSION-JWT-WS-01` tests passed; the affected network inventory test suite
passed 41/41; and the full e2e-runner Python discovery run completed 2093 tests
in 175.926 seconds, OK (skipped=215). This is contract-level mapping and local
admission validation only; no real Singlebox or preprod request was executed
and remote validation remains `NOT_RUN`.

## 2026-10-19 Edge native admission dual-environment slice

`PUBLIC-EDGE-ADMISSION-01` freezes the two reviewed Edge admissions from
`test_ep_admission`: the prefixed-Human actor read and the bare-Human
`actor_kind` read. Both Singlebox and Gateway profiles reuse the exact native
routes derived from the dedicated driver Bot and profile Human:

- `GET /bots/{driver_actor_id}/admission?actor=human_{human_actor_id}`
- `GET /bots/{driver_actor_id}/admission?actor={human_actor_id}&actor_kind=human`

The route validator accepts only a `public-v1` entrypoint with the
`internal-v1` native manifest face. Both finite operations send neither a
Cookie nor an Authorization header and record `credentials_sent=false` and
`authorization_sent=false`. The adapter requires the exact native admission
JSON shape (`allowed`, bounded `grants`, non-empty `reason_code`, and
`public_default`), stores only the grant count, and gives no partial credit if
the first response or bound route drifts.

This slice adds contract mocking and local validation only. No real Singlebox
or preprod request was executed, `remote_validation` remains `NOT_RUN`, and no
release credit is granted. The broader `6.1` parent remains open because other
obligations are still unmigrated.

Validation in the e2e-runner worktree: the Case guard passed for both new
files; the new admission suite passed 8/8; the split platform-asset suite
passed 8/8; the affected network read adapter regression passed 33/33; related
Edge, channel, and structured-contract suites passed; and the full e2e-runner
Python discovery run completed 2101 tests in 185.119 seconds, OK with 215
documented skips.

## 2026-10-19 Friend-connection decision dual-environment slice

`PUBLIC-FRIEND-CONNECTION-DECISIONS-01` freezes the two decision obligations from
the trusted-team Story (`FRIEND.protected-reject` and `FRIEND.protected-accept`)
as one independent public-v1 slice for both the local Singlebox and Gateway/preprod
contracts. It closes the previous one-sided read-only list mapping by binding the
actual create/list/decision routes:

- `POST /openapi/v1/collaboration/friend-connections/requests`
- `GET /openapi/v1/collaboration/friend-connections/requests?...direction=received&status=pending`
- `POST /openapi/v1/collaboration/friend-connections/requests/{request_id}/reject`
- `POST /openapi/v1/collaboration/friend-connections/requests/{request_id}/accept`
- `GET /openapi/v1/collaboration/friend-connections?actor_type=bot&actor_id={driver}&target_type=bot`

The finite order is reject then accept, using only the dedicated driver/member
Bots owned by the same Human: create driver→member, uniquely select the exact
pending request, reject it, and verify no member friendship; then create
member→driver, uniquely select that exact pending request, accept it, and verify
the member is visible in the driver friend list. A preexisting or auto-accepted
create blocks the case rather than being adopted as another request. Evidence
persists only request IDs, statuses, actor IDs, bounded page counts, and transport
records; it never persists Cookie, Bot token bytes, or request bodies.

This is an opt-in bounded mutation. A profile must declare
`relationships.friendship=bounded-owned-bot-decisions`, all seven route bindings,
exclusive fixture use, and a Cookie-only Human auth context. Bot Authorization and
the older `/friends/*` local trusted-team route cannot be substituted. The
rejected request history and accepted friendship are intentionally retained; there
is no implicit unfriend/delete/retry after a mutation.

This slice is contract-tested only. No real Singlebox or preprod request was
executed; `remote_validation=NOT_RUN` and `grants_release_credit=false`. Task 6.1
remains open, and the shared implementation still needs live effect admission and
execution in each environment.

Validation on this worktree: the migrated Case guard passed; the new
friend-decision suite passed 9/9; the affected profile-transition, Edge-read,
Edge-admission, and actor-status regression passed 49/49; `compileall -q` passed.


## 2026-10-19 Friend public auto-accept dual-environment slice

`PUBLIC-FRIEND-CONNECTION-AUTO-ACCEPT-01` freezes the reviewed public auto-accept
and friend-list obligations from the trusted-team Story (`test_friend_auto_accept_public`
and `test_list_friends`) as one opt-in public-v1 slice that can run against either the
local Singlebox or the Gateway/preprod contract. It binds only two exact operations on
the two dedicated Bots owned by the configured Human:

- `GET /openapi/v1/collaboration/friend-connections?actor_type=bot&actor_id={driver}&target_type=bot`
- `POST /openapi/v1/collaboration/friend-connections/requests`

The sequence is read, create, independent read, and a final explicitly mapped
`FRIEND.list` friend-list read. The first read must prove that the exact
driver/member pair is not already visible; otherwise the case blocks instead of taking
idempotency credit. The create must return the exact Bot-to-Bot public auto-acceptance
result (`status=approved`, `auto_accepted=true`, exactly two request IDs, and exactly
two non-zero edge IDs). The post-create page and the separate `FRIEND.list` read must
each contain exactly one member entry. There is no generic create method, arbitrary
URL admission, alternate `/friends/*` route, retry, or request-body/credential
persistence. Both step projections must also carry the exact admitted Bot pair:
the accept projection records `from_actor_id`/`to_actor_id` and the `FRIEND.list`
projection records `driver_actor_id`/`friend_actor_id`; the contract validators
reject any projection whose actor IDs drift from the profile-owned pair, so a
fabricated or misrouted friendship cannot earn the exact-pair proof.

A profile must explicitly declare
`relationships.friendship=bounded-owned-bot-public-acceptance`, both exact route
bindings, exclusive fixture use, and a Cookie-only Human auth context. This mode is
deliberately mutually exclusive with the protected-reject/accept decision mode so the
same fixture pair cannot silently combine incompatible retained friendship states. The
approved friendship is retained; there is no implicit unfriend/delete/retry.

This slice is contract-tested only. No real Singlebox or preprod request was
executed, `remote_validation=NOT_RUN`, and `grants_release_credit=false`. Task 6.1
remains open because the trusted-team CLI obligation and broader workspace/session
parents are not complete.

Validation on this worktree: the new Case guard passed; the public auto-accept suite
passed 10/10; the friend-decision suite stayed green at 9/9; the affected public
inventory ordering test stayed green at 7/7; and the full e2e-runner Python
discovery run completed 2120 tests in 155.977 seconds, OK with 215 documented skips.

Exact-pair strengthening on 2026-10-19: the contract validators now also
assert the admitted Bot pair inside both step projections
(`from_actor_id`/`to_actor_id` for the accept step, `driver_actor_id`/
`friend_actor_id` for the `FRIEND.list` step), so a fabricated or misrouted
friendship projection blocks the case instead of earning exact-pair proof;
the identity is profile-driven, not hardcoded. Focused public auto-accept
suite 13/13, case guard and compileall clean, and the full discovery run
completed `Ran 2297 tests OK (skipped=215)`.

## 2026-10-19 Dual product-CLI friend acceptance slice

`DUAL-FRIEND-CLI-ACCEPT-01` migrates the trusted-team Story obligation
`test_friend_flow_via_cli` (`FRIEND.cli-accept`) as one product-CLI first slice for both
the owned local Singlebox and Gateway/preprod profiles. It intentionally does not replace
CLI coverage with a public-v1 HTTP adapter.

The case performs five controlled product-CLI leaves in this order:

1. member precheck of received/pending requests and block if the exact
   driver-to-member pair already has a pending request;
2. driver `friend request --bot-uuid <member>`;
3. member `friend requests --direction received --status pending`, requiring exactly one
   match for the newly returned request ID, exact pair, and `pending` status;
4. member `friend accept --request-id <request_id>`;
5. driver `friend list`, requiring exactly one member entry.

The local profile uses the product CLI through private role contexts containing the
configured owned Bot token for each role. The preprod profile uses a Human Cookie passed
to the CLI through `BCS_COOKIE`, while each role session binds a Cookie-only CLI call to
its dedicated owned Bot UUID. Cookies and Bot tokens are not passed as command arguments
or persisted in evidence; private contexts are removed and any unconfirmed cleanup blocks
the operation. The product-CLI transport evidence records the command arguments, declared
route, caller role, credential kind, finite process status, and elapsed time.

This slice remains contract-level. No real Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`. The accepted friendship
and request history are retained; there is no implicit unfriend, delete, or retry. Task
6.1 remains open because the broader trusted-team and workspace/session obligations are
not complete.

Validation on this worktree: the Case guard passed for the four admitted new files; the
new friend-CLI suites passed 14/14; the affected migration/profile-transition/case-rewrite
and related friend-slice suites passed 61/61; and the full e2e-runner Python discovery
run completed 2134 tests in 159.108 seconds, OK with 215 documented skips.

## 2026-10-19 Dual product-CLI Workspace create/get slice

`DUAL-WORKSPACE-CLI-CREATE-GET-01` migrates the Workspace-parent obligation
`WORKSPACE.cli-create-get` (the reviewed `test_group_create_via_cli` plus
`test_group_get_via_cli` pair) as a second product-CLI slice for both the owned local
Singlebox and Gateway/preprod contracts. It uses the dedicated driver and member Bots
owned by the same configured Human and does not replace CLI coverage with a generic HTTP
adapter.

The case performs only two reviewed product-CLI leaves:

1. driver `create-group --driver <driver> --participants <driver>,<member>
   --topic bcs-e2e-workspace-cli-create-get`, accepting that the product retains the
   created dynamic Group and its initial Session;
2. driver `get-group --id <group_id>`, requiring the exact Group ID, driver, two
   participant bot IDs, and `active` status on readback.

Local execution binds the product CLI through a private owned-Bot token context.
Preprod execution uses the configured Human Cookie via `BCS_COOKIE` with a Cookie-only
bot context and the explicit `--url <gateway> --json` product invocation. Credentials
remain outside command arguments and evidence; each private context is removed and an
unconfirmed clear blocks the operation. The case has no fallback route, HTTP substitute,
delete, retry, or cleanup.

This slice is contract-level only. No real Singlebox or preprod request was executed,
Group/Session effects remain retained, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`. Task 6.1 remains open because other Workspace
obligations and the complete CLI-file Story are not migrated. A future live preprod run
still requires an explicit Group/initial-Session effect review and serial admission.

Validation on this worktree: the Case guard passed for the four new Workspace CLI files;
the new Workspace CLI suites passed 13/13; the affected migration/profile-transition/
case-rewrite plus previous FRIEND and Workspace product-CLI suites passed 55/55; and the
full e2e-runner Python discovery run completed 2147 tests in 160.322 seconds, OK with 215
documented skips.

## 2026-10-19 Dual product-CLI Workspace status/termination slice

`DUAL-WORKSPACE-CLI-STATUS-01` migrates `WORKSPACE.cli-status`
(`test_group_status_via_cli` plus `test_group_terminate_via_cli`) as a
product-CLI slice for both owned local Singlebox and Gateway/preprod profiles.

To avoid conflating two different mutations, it uses two distinct retained
Groups and their initial Sessions:

1. create/read the completion Group as `active`, invoke
   `group-status --group <id> --status completed --reason
   bcs-e2e-workspace-cli-completion`, and read back `completed`;
2. create/read the termination Group as `active`, invoke
   `terminate-group --group <id>`, and read back `completed`.

The case requires the exact completion and termination acknowledgements, the
exact caller/driver, and independent CLI reads of both Groups. The Group and
Session resources are retained. There is no delete, broad stop, retry, HTTP
fallback, or coverage from a nonzero exit alone. Local execution uses private
owned-Bot token contexts; Gateway execution uses the Human Cookie only through
`BCS_COOKIE`, and credentials never enter command arguments or evidence.

This is a contract-level implementation and mock regression. No real local or
preprod request was executed, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`. Task 6.1 remains open.

Validation on this worktree: the new Case guard passed; the Workspace/CLI
status suite passed 9/9; the related Workspace and FRIEND product-CLI suites
passed 33/33; and the full e2e-runner Python discovery run completed 2156
tests in 161.897 seconds, OK with 215 documented skips.

## 2026-10-19 Dual product-CLI Workspace bounded-list slice

`DUAL-WORKSPACE-CLI-LIST-01` migrates `WORKSPACE.list`
(`test_list_groups`) as a product-CLI slice for both owned local Singlebox and
Gateway/preprod profiles. It creates one retained dynamic Group with its
initial Session and then invokes the bounded product leaf
`list-groups --batch-size 50`.

The list result must report the exact pagination contract and contain exactly
one item with the newly created Group ID, matching driver and `active` status.
A list with a larger total but no exact newly created Group is BLOCKED; a mere
nonzero count does not pass. No credential, request body, or unrelated group
record is added to evidence.

This is also contract-level only. No real local or preprod request was
executed, resources remain retained, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`. Task 6.1 remains open.

Validation on this worktree: the new Case guard passed; the Workspace CLI list
suite passed 7/7; the related Workspace and FRIEND product-CLI suites passed
40/40; and the full e2e-runner Python discovery run completed 2163 tests in
159.222 seconds, OK with 215 documented skips. `compileall -q`, File-size
verification, and `git diff --check` passed.

## 2026-10-19 Dual product-CLI Workspace detail-read slice

`DUAL-WORKSPACE-CLI-DETAIL-01` migrates `WORKSPACE.detail`
(`test_get_group_detail`) as a product-CLI slice for both owned local Singlebox
and Gateway/preprod profiles. It creates one retained dynamic Group with its
initial Session and then invokes the reviewed product leaf
`get-group --id <group_id>`.

The detailed read is exact, not a smoke check: it must return the created Group
ID, the dedicated driver, `active` status, exactly the ordered driver/member
participant Bot IDs, and non-empty `group_kind` and `visibility`. A response
that omits or changes any required field is BLOCKED with no partial credit.
Local execution uses a private owned-Bot token context; Gateway execution uses
the Human Cookie only through `BCS_COOKIE`. Credentials are never command
arguments or evidence, and repeated create/read operations are rejected as
one-shot violations.

This is a contract-level implementation and mock regression only. No real
Singlebox or preprod request was executed, the Group and initial Session remain
retained, `remote_validation=NOT_RUN`, and `grants_release_credit=false`.
Task 6.1 remains open.

Validation on this worktree: the Case guard passed; the new Workspace CLI
detail suite passed 7/7; the full e2e-runner Python discovery run completed
2170 tests in 169.174 seconds, OK with 215 documented skips; `compileall -q`
and `git diff --check` passed. All modified or added source files remain below
the 1,000-line limit.

## 2026-10-19 Dual product-CLI Workspace fusion slice

`DUAL-WORKSPACE-CLI-FUSE-01` migrates `WORKSPACE.cli-fuse`
(`test_group_fuse_via_cli`) as a Shared product-CLI slice for both owned local
Singlebox and Gateway/preprod profiles. It creates one retained dynamic Group
with its initial Session and invokes `fuse --group <id> --question
how to align? --participants <driver>,<member>` exactly once.

The old shortcut that accepted any nonzero exit with arbitrary JSON is removed.
The contract requires exit zero, the exact `Fusion result:` product output,
`Simple fusion for: how to align?`, exactly two non-identical perspectives, and
the ordered driver/member participant Bot IDs. The Group and initial Session
remain retained. Local execution binds an owned-Bot token; Gateway execution uses
the Human Cookie only through `BCS_COOKIE`; credentials never enter command
arguments or evidence.

This is contract-level only. No real Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`. Task 6.1 remains
open. To preserve the executor source-file guard, the fusion adapter is co-located
with its contract and the earlier detail adapter is consolidated into its contract
without changing its business logic.

Validation on this worktree: the new fusion Case guard passed; the fusion suite
passed 8/8; the detail suite passed 7/7 after consolidation; the full e2e-runner
Python discovery run completed 2178 tests in 171.505 seconds, OK with 215
documented skips; `compileall -q` and `git diff --check` passed.

## 2026-10-19 Dual product-CLI Session-file core slice

`DUAL-SESSION-FILE-CORE-01` migrates all ten obligations of the Session-file Story
(`CLI-FILE.setup`, `CLI-FILE.upload`, `CLI-FILE.list`, `CLI-FILE.capabilities`,
`CLI-FILE.metadata`, `CLI-FILE.download`, `CLI-FILE.share`,
`CLI-FILE.shared-metadata`, `CLI-FILE.shared-content`, and `CLI-FILE.delete`)
as one cross-environment product-CLI case.

It creates a retained dynamic Group through product CLI, records its initial
Session, creates one explicit `file-ws` Session, uploads the deterministic
29-byte source file once, lists that exact file, and reads capabilities. Local
Singlebox requires storage `local`; preprod requires the separately admitted
BaaS backend `baas`. `CLI-FILE.metadata` uses the existing internal-v1 file
transport to read authenticated HTTP metadata for the exact uploaded file; the
workspace CLI owns setup/upload/list/download/share/delete. It then downloads
the selected file once to a create-once private target and verifies the exact
29-byte payload and SHA-256. Finally it invokes the explicit share leaf once
and records only `share_granted`, `share_token_present`, and `expires_at`; the
share token and share URL remain in adapter memory and are excluded from step
data and evidence.

`CLI-FILE.shared-metadata` consumes the token-only legacy metadata route without
ambient Human or Bot authentication, validates the exact native DTO, and rejects
any response that leaks `session_id`. `CLI-FILE.shared-content` uses the
existing internal-v1 token-only shared-content transport and receives the same
exact 29-byte payload under a redacted request record.

`CLI-FILE.delete` invokes the reviewed product-CLI DELETE leaf exactly once.
The runner accepts only the product CLI's `null` acknowledgement for a 204
no-content delete or an exact `{"deleted": true}` response. It then independently
checks authenticated internal-v1 file metadata, token-only legacy shared
metadata, and token-only internal-v1 shared content; each must return the exact
reviewed 404 outcome. A failed business delete is never replaced by fallback
deletion or broad cleanup. After a validated delete, the Group and both Sessions
remain while the File and its share link are invalid.

Every operation is one-shot, exit-zero, and exactly bound to the driver/member
Group, explicit Session, and selected File identifiers. No partial credit is
granted when a later obligation returns a non-exact payload. Local credentials
are the owned driver Bot token; preprod uses the Human Cookie only through
`BCS_COOKIE`; credentials are excluded from arguments and evidence.

This remains contract-level. No real Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`. Task 6.1
remains open. To preserve the 225-entry executor source inventory, the
create/get adapter was also consolidated into its contract without changing its
business logic.

Validation on this worktree: the Session-file core Case guard passed; its suite
passed 9/9 with 16 subtests; the focused File-transport/Workspace-runner
neighborhood passed 28/28; and the full `pytest` discovery run passed 1,974
tests with 215 documented skips and 2,371 subtests in 184.900 seconds.
`compileall -q`, modified-file size checks, and `git diff --check` passed. This
mock/contract regression does not replace a live Singlebox/preprod execution or
a full-module coverage run.

### 2026-10-19 Session CLI create-list-get dual-environment slice

`DUAL-SESSION-CLI-CREATE-LIST-GET-01` freezes the reviewed
`SESSION.cli-create-list-get` source obligation from the Session parent. It is the
first non-PRE-02 Session obligation that is represented by one shared product-CLI
contract for the owned local Singlebox and preprod Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member Group through `bcs-cli create-group` and bind
   its initial Session;
2. create the exact explicit chat Session through `session create`;
3. list Sessions and require the exact new ID once;
4. read that exact Session through `session get`;
5. delete only that exact owned Group through the reviewed legacy cleanup route;
6. read the deleted Group as an exact 404 (`group_not_found` and the exact ID).

The CLI and cleanup routes stay separate: CLI owns the four original business
operations, while `WriteClient` executes only two bound cleanup routes. The local
profile injects the owned driver Bot token; the preprod profile injects only the
Human Cookie. Clearing CLI contexts, no retry, no borrowed Bot, no path guessing,
and no public-prefix rewriting are unchanged. Full function, cleanup, and evidence
integrity are required in one pass with partial evidence yielding `BLOCKED`, not
partial credit.

Impact: this migrates one of the nine original Session parent obligations and adds
the case to the cross-environment product-CLI inventory. It does not execute a real
Singlebox or preprod request, does not satisfy remote effect admission, and does not
grant release credit. Task `6.1` remains open because seven Session parent
obligations are still unmigrated under this shared contract model.

Validation on this worktree: the migrated Case guard passed; the new focused suite
passed 6/6; the affected Session/Workspace product-CLI suites and write-transport
regression passed 66 tests with 102 subtests; and the full runner regression
passed 1980 tests with 215 documented skips and 2376 subtests in 194.019 seconds.


### 2026-10-19 Session CLI Bot-groups dual-environment slice

`DUAL-SESSION-CLI-BOT-GROUPS-01` migrates `SESSION.bot-groups`
(`test_bot_groups_of_bot`) as the second non-PRE-02 Session parent slice that is
represented by one shared product-CLI contract for both the owned local Singlebox
and preprod Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member Group through `bcs-cli create-group` and bind
   the initial Session;
2. read the driver Bot’s exact Group list through the reviewed bound route
   `GET /bots/{driver}/groups?limit=50&group_kind=all`;
3. require the exact fresh Group ID once in the returned `items`;
4. delete only that exact owned Group through the reviewed legacy cleanup route;
5. read the deleted Group as an exact 404 (`group_not_found` and the exact ID).

The CLI owns the original `create-group` operation. The Group listing and cleanup
read/delete remain on their exact reviewed legacy HTTP routes with no path
substitution, soft success, or bootstrap fallback. Local execution binds the owned
driver Bot token; preprod execution binds only the Human Cookie. Clearing CLI
contexts, no retry, no borrowed Bot, and no cross-environment credential mixing are
unchanged. The case is still mock/contract-level only: no real Singlebox or preprod
request was executed, `remote_validation=NOT_RUN`, and `grants_release_credit=false`.

Impact: this migrates one more Session parent obligation and reduces the remaining
unmigrated Session parent obligations from seven to six. Task `6.1` remains open.

Validation on this worktree: the migrated Case guard passed; the new focused suite
passed 6/6; the affected Session/Workspace product-CLI suites and write-transport
regression passed 19 tests in 1.0 seconds; and the full runner discovery run
completed 2201 tests in 192.782 seconds, OK with 215 documented skips.
`compileall -q`, modified-file size checks, and `git diff --check` passed.
### 2026-10-19 Session CLI Group-invite dual-environment slice

`DUAL-SESSION-CLI-GROUP-INVITE-01` migrates `SESSION.group-invite`
(`test_group_invite_link`) as the third Session parent slice that is
represented by one shared product-CLI contract for both the owned local
Singlebox and preprod Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member Group through `bcs-cli create-group` and
   bind its initial Session;
2. mint a bounded `POST /groups/{group_id}/invite-link` invitation for exactly
   that Group;
3. join the exact Group through `POST /groups/join/{invite_token}`;
4. delete only that exact owned Group through the reviewed legacy cleanup route;
5. read the deleted Group as an exact 404 (`group_not_found` and the exact ID).

The invitation credential and its `join_url` are consumed in adapter memory
only; the request evidence records the join route as redacted, and neither the
token nor the URL enters step data or durable evidence. Local execution binds
the owned driver Bot token; preprod execution binds the Human Cookie. Clearing
CLI contexts, no retry, no borrowed Bot, no path guessing, and no
cross-environment credential mixing are unchanged. The case remains
contract-level: no live Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`.

Impact: this migrates one more Session parent obligation and reduces the
remaining unmigrated Session parent obligations from six to five. Task `6.1`
remains open.
### 2026-10-19 Session CLI Session-invitation dual-environment slice

`DUAL-SESSION-CLI-SESSION-INVITE-01` migrates `SESSION.cli-session-invite`
(`test_session_invite_link`) as a Session parent slice that is represented by
one shared product-CLI contract for both the owned local Singlebox and preprod
Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member Group through `bcs-cli create-group`;
2. create the exact explicit chat Session through `session create`;
3. mint that Session’s invitation through `session invite-link`;
4. delete only the exact owned Group;
5. read the deleted Group as an exact 404.

The CLI outputs a JSON body with `invite_token`, `expires_at`, and `join_url`;
the token and full URL are consumed in adapter memory and excluded from step
data and durable evidence. The source contract only checks the token and full
URL presence, so this migration additionally requires the URL to be bound to
the admitted origin. Local execution uses the owned driver Bot token; preprod
execution uses the Human Cookie. The case remains contract-level: no live
Singlebox or preprod request was executed, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

Impact: this migrates one more Session parent obligation and reduces the
remaining unmigrated Session parent obligations from five to four. Task `6.1`
remains open.

### 2026-10-19 Session CLI invite-join dual-environment slice

`DUAL-SESSION-CLI-INVITE-JOIN-01` migrates `SESSION.invite-join`
(`test_session_invite_join`) as one shared product-CLI contract for both the
owned local Singlebox and preprod Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member Group through `bcs-cli create-group`;
2. create the exact explicit chat Session with title `share-sess`;
3. mint that Session’s invitation through `session invite-link`;
4. join exactly that Session through `POST /sessions/join/{invite_token}`;
5. delete only the exact owned Group through the reviewed legacy cleanup route;
6. read the deleted Group as an exact 404.

The invitation and join are held only in adapter memory for one pass. The join
request is recorded with `[REDACTED]`, the exact join response is required
(`joined=true`, `already_member=false`, `target_type=session`, the exact Session
ID, and the configured Human actor), and neither the invitation token, full URL,
nor Cookie enters step data or durable evidence. Local execution uses the owned
driver Bot token; preprod execution uses the Human Cookie. This is still
contract-level: no live Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`.

To preserve the 225-entry executor source budget while keeping the runner under
the 1,000-line file limit, the small browser-context helper was consolidated
into the profile module and this invite-join case moved into a focused existing
budget slot. The change makes source intent and validation colocated without
increasing the artifact inventory.

### 2026-10-19 Session CLI lifecycle dual-environment slice

`DUAL-SESSION-CLI-LIFECYCLE-01` migrates `SESSION.lifecycle`
(`test_session_lifecycle`) as one shared product-CLI contract for both
the owned local Singlebox and preprod Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member Group through `bcs-cli create-group`;
2. create the exact explicit chat Session with title `lifecycle-sess`;
3. rename the exact Session through `PATCH /sessions/{session_id}` and
   require the `renamed-sess` title read-back;
4. admit ENG as a Session participant through
   `POST /sessions/{session_id}/members` and require its presence;
5. set ENG to `muted` through
   `PATCH /sessions/{session_id}/members/{member_actor_id}` and require
   the exact mode read-back;
6. send the `hello-session` chat through
   `POST /sessions/{session_id}/chat` and require the healthy
   acknowledgement shape (`delivered`, exact Session and Group IDs);
7. read the Session history through
   `GET /sessions/{session_id}/messages` and require a message array;
8. remove ENG through
   `DELETE /sessions/{session_id}/members/{member_actor_id}` and require
   its absence;
9. delete only the exact owned Session through the creator-scoped legacy
   route `DELETE /sessions/{session_id}?bot_id={driver_actor_id}` and
   require the exact acknowledgement;
10. list the Group Sessions through a healthy `GET /groups/{group_id}/sessions`
    and require the deleted Session to be absent — a failed or
    unparseable listing verification fails the case, it never defaults to
    "gone";
11. delete only the exact owned Group through the reviewed legacy cleanup
    route and read the deleted Group as an exact 404.

Unlike the legacy shell oracle, a 200-with-403-or-409 fallback is not
accepted: every mutating step requires its exact 200 acknowledgement, and
the chat acknowledgement must be a well-formed JSON object bound to the
exact Session and Group. Local execution uses the owned driver Bot token;
preprod execution uses the Human Cookie. This is still contract-level: no
live Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`.

The member-scoped routes required a new
`dual-session-cli-lifecycle-profile-v1` contract with an explicitly
validated ten-operation route-binding set, and the allowlisted write
transport gained `{member_actor_id}` substitution for the member-scoped
path parameter. To preserve the 225-entry executor source budget while
keeping both Session CLI cases under the 1,000-line file limit, the
invite-join and lifecycle cases now share the renamed
`session_cli_shared.py` module.

### 2026-10-19 Session CLI completion-invocation dual-environment slice

`DUAL-SESSION-CLI-COMPLETE-INVOKE-01` migrates
`SESSION.complete-invoke` (`test_session_cli_complete_invoke`) as
one shared product-CLI contract for both the owned local Singlebox
and preprod Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member service Group through
   `bcs-cli create-group --manager <driver> --participants
   <driver>,<member>` (manager-worker shape; CLI output format is
   identical to chat groups);
2. create the explicit sign-off Session titled `Release sign-off`;
3. read unmanaged message deliveries and require `[]`;
4. batch-query the delivery message IDs and require `[]`;
5. reject a missing delivery cancel with an exact 400;
6. reject a missing message cancel with an exact 400;
7. reject Bot queue resolution with an exact 403;
8. complete the sign-off Session with
   `output={"summary": "release approved"}` and require the exact
   `status=="completed"` acknowledgement;
9. read the completed Session back with output preserved;
10. `PATCH /groups/{group_id}/settings` to
    `service_spec.max_concurrency == 2` and require the exact read-back;
11. invoke one service Session through
    `POST /services/{group_id}/sessions` (202) and require
    `session_kind=="service_invocation"` with the exact title;
12. read the invoked service Session back;
13. read the service Session as the member Bot and require an exact
    403 `{"error":"forbidden","message":"caller_principal mismatch"}`
    — the member is a legitimate foreign caller under
    `caller_principal` isolation, preserving the legacy CEO semantics;
14. complete the service Session through the chat-complete route and
    require the exact 403 guardrail
    `service sessions cannot be completed via this endpoint`;
15. delete only the exact creator-scoped sign-off Session;
16. delete the `bot:`-prefixed service Session through the bot-scoped
    route;
17. delete only the exact owned Group;
18. read the deleted Group back as an exact 404 proof.

The service routes (`/services/*`) resolve Bot identity only — a
Human Cookie gets 401 — so this contract's auth policy is
`owned-bot-tokens` with BOTH `driver_token_env` and `member_token_env`
on both tiers, unlike the human-cookie lifecycle contract. This is
still contract-level: no live Singlebox or preprod request was
executed, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`; the recorded blocker is the owned
preprod Bot-token requirement for both dedicated-pool Bots.

The eleven-operation (plus four delivery/observe operations) route set
is frozen in `dual-session-complete-invoke-profile-v1` with a
per-operation `auth` expectation (member read uses
`case_owned_member`). To preserve the 225-entry executor source
budget, the 45-line `delivery_activity.py` module was consolidated
into `delivery_observation.py` (its `snapshot_activity` helper and
its sole importer and test were moved with it), freeing the slot for
this case.

### 2026-10-19 Session CLI collection dual-environment slice

`DUAL-SESSION-CLI-COLLECTION-01` migrates
`SESSION.collection` (`test_session_collection`) as one
shared product-CLI contract for both the owned local
Singlebox and preprod Gateway credential contexts.

The finite order is:

1. create one dedicated-driver/member Group through
   `bcs-cli create-group` (topic `bcs-e2e-session-collection`);
2. create the explicit `collect-sess` Session;
3. read the driver collected list and require it empty;
4. require `collected=true` without `participant` to be the
   exact 400 `{"error":"invalid_params","message":
   "collected filter requires participant"}`;
5. collect the sign-off Session as the driver
   (`POST /sessions/{session_id}/collect`) and require the
   exact `{"collected":true,"session_id":...}` acknowledgement;
6. repeat the collect and require the idempotent 200;
7. read the driver collected list and require the Session
   present with `collected==true`;
8. read the member collected list and require the driver
   collection absent (per-Bot collection isolation);
9. collect as the non-participant QA Bot and require the
   exact 404 `{"error":"participant <qa> not in session
   <sid>"}` — QA is a valid pool Bot but not a Group or
   Session participant;
10. uncollect as the driver
    (`DELETE /sessions/{session_id}/collect`) and require
    the exact `{"collected":false,"session_id":...}`;
11. repeat the uncollect and require the idempotent 200;
12. read the driver collected list and require it empty again;
13. delete only the exact owned Session through the
    creator-scoped legacy route and require the exact
    acknowledgement;
14. delete only the exact owned Group and read it back as an
    exact 404 proof.

Collector identity is resolved from the caller credential by
the legacy routes themselves: on Singlebox each dedicated pool
Bot acts through its own Bot token (a Bot token resolves to
itself, so the driver cannot read the member collected list or
collect as QA — the member and QA steps use their own tokens);
on preprod the owned Human Cookie names the pool Bot it acts
for through the request participant (the Human principal must
own all three dedicated pool Bots). The mid-flow listings are
therefore read with the participant's own credential, and the
member listing binding carries
`auth: case_owned_member`. Unlike the completion-invocation
contract, no preprod Bot tokens are required because the
collection routes accept the owned Human principal naming its
owned Bots.

The eight-operation route set is frozen in
`dual-session-cli-collection-profile-v1`, the first Session
CLI Profile with three admitted actors (`driver`, `member`,
`qa`; the non-participant QA Bot must be distinct and dedicated).
To preserve the 225-entry executor source budget, the
38-line `trust_cli_steps.py` module was merged into
`trust_api_steps.py` (its single production importer) and the
runner's per-contract validator ladder was compacted into a
dispatch table, keeping `workspace_cli_runner.py` at the
1,000-line CI limit. This is still contract-level: no live
Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`.

### 2026-10-25 Dual Session product-CLI command entry

`dual-session-run --case create-get|bot-groups|group-invite|session-invite|invite-join|lifecycle|complete-invoke|collection`
is now the stable command entry for the eight reviewed Session product-CLI slices.
It selects that case's strict Profile validator, requires an approved CLI binary,
explicit `--allow-write`, an exact `--confirm-run-id`, and a fresh private state
directory. Every invocation writes an exact Profile, binding, and Run receipt, binds
the same `WorkspaceCliRunner` transport used by the reviewed adapters, and reports
the case's frozen effect, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`. The command never embeds Bot tokens or Human Cookies;
only the local `owned_local_bot_token` or preprod `human_cookie` credential kind is
recorded.

This is a command/report integration increment, not a newly invented assertion
surface: the eight underlying Case, Profile, adapter, and route-contract tests remain
authoritative. The focused command suite passed 5/5 and the adjacent eight Session
contract suites passed 67/67 together (`67 tests, OK`). No live Singlebox or preprod
request was executed in this increment. The one focused command module increases the
reviewed source-file inventory allowance from 277 to 278 executor source paths
(including `runner.py`), with the reason recorded in `bcs_e2e/artifact.py`.

### 2026-10-25 Dual Channel product-CLI command entry

`dual-channel-run` is the stable command entry for the reviewed dual-environment
`DUAL-CHANNEL-CLI-01` slice. It loads `dual-channel-cli-profile-v1`, requires the
approved CLI binary digest, explicit `--allow-write`, an exact `--confirm-run-id`,
and a fresh private state directory, then binds the reviewed `ChannelCliRunner`,
`RemoteChannelCliAdapter`, and nine-obligation execution contract.

The command writes channel-cli Profile/binding/Run receipts and records
`OWNED_CHANNEL_CLI_LIFECYCLE_THEN_EXACT_SESSION_GROUP_DELETE`,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`. Local execution
records `owned_local_bot_token`; preprod records `human_cookie`. No Bot token,
synthetic channel client secret, or Human Cookie value is embedded in command-line
evidence or durable reports. This command integration passed its 5/5 focused suite
without executing a live Singlebox or preprod request.

### 2026-10-25 Dual Session-file core command entry

`dual-session-file-run` is the stable command entry for the reviewed dual-environment
`DUAL-SESSION-FILE-CORE-01` slice. It loads `dual-session-file-core-profile-v1`, requires
the approved CLI binary digest, explicit `--allow-write`, an exact `--confirm-run-id`,
and a fresh private state directory, then binds the reviewed `WorkspaceCliRunner`,
`RemoteSessionFileCoreAdapter`, and ten-obligation session-file execution contract.
The command writes session-file-core Profile/binding/Run receipts and records
`RETAINED_DYNAMIC_GROUP_SESSIONS_AND_FILE`, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`. Local execution records `owned_local_bot_token`;
preprod records `human_cookie`. No Bot token, share URL or Human Cookie value is
embedded in command-line evidence or durable reports. This command integration
passed its 5/5 focused suite without executing a live Singlebox or preprod request.
### 2026-10-25 Dual friendship product-CLI command entry

`dual-friend-run` is the stable command entry for the reviewed dual-environment
`DUAL-FRIEND-CLI-ACCEPT-01` slice. It loads `dual-friend-cli-profile-v1`, requires
the approved CLI binary digest, explicit `--allow-write`, an exact `--confirm-run-id`,
and a fresh private state directory, then binds the reviewed `FriendCliRunner`,
`RemoteFriendCliAcceptAdapter`, and one-obligation trusted-team execution contract.
The command writes friendship Profile/binding/Run receipts and records
`BOUNDED_OWNED_BOTS_RETAINED_CLI_ACCEPTANCE`, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`. Local execution records `owned_local_bot_token`;
preprod records `human_cookie`. No Bot token or Human Cookie value is embedded in
command-line evidence or durable reports. This command integration passed its 5/5
focused suite without executing a live Singlebox or preprod request.
### 2026-10-19 Workspace CLI member-addition dual-environment slice

`WORKSPACE.cli-add-member` (source
`test_group_add_member_via_cli` in `group.sh`) is now a
dual-environment product-CLI slice
(`DUAL-WORKSPACE-CLI-MEMBER-01`). The executor drives the
reviewed sequence in order:

1. create the retained Group through the product CLI
   (`create-group --driver <driver> --participants
   <driver>,<member> --topic bcs-e2e-workspace-cli-member`,
   `POST /groups`) and require the exact create output
   (group ID, driver, both participants, initial `<gid>:`-prefixed
   Session);
2. add the member Bot through the actual CLI leaf
   (`add-member --group <gid> --bot-uuid <member>`,
   `POST /groups/{group_id}/members`) and require the exact
   acknowledgement naming the created Group and the member Bot
   — a missing prefix, wrong Group or wrong Bot is a failure,
   not a pass;
3. read the Group back through the product CLI
   (`get-group --id <gid>`, `GET /groups/{group_id}`) and
   require the added member Bot in the exact participant
   cardinality `[driver, member]`, replacing the legacy habit
   of only checking CLI exit zero.

The caller is the driver on both tiers: its owned local Bot
token on Singlebox and the owned Human Cookie (which owns both
dedicated Bots) on preprod. The roster stays the admitted
two-Bot `driver`/`member` pair; the legacy PM/QA pairing is
represented by those roles, and no borrowed or impersonated
credential is admitted. The route and output contract follows
the deployed bcs-cli `add-member` leaf
(`crates/tools/bcs-cli/src/main.rs::Commands::AddMember`) and
the legacy Group API shape it wraps.

The profile contract is frozen as
`dual-workspace-cli-member-profile-v1` with the retained effect
admission `group=bounded-owned-bots-retained-cli-create-add-member`,
`initial_session=retained`, `cleanup=none`; the Group and its
initial Session stay retained and no automatic cleanup is
claimed. To preserve the 225-entry executor source budget, the
standalone `remote_workspace_cli_list_adapter.py` module (its
only production consumer was the list contract itself) was
consolidated into `workspace_cli_list_contract.py`, and
`workspace_cli_runner.py` was compacted (shared JSON loader,
dict-driven fixture-effect selection, one shared
HTTP-evidence builder for the six repeated adapter evidence
blocks, tighter `_context` environment construction), keeping
the runner under the 1,000-line CI limit while adding the
`member.group.create` / `member.group.add-member` /
`member.group.read` operations. This is still contract-level:
no live Singlebox or preprod request was executed,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`.

### 2026-10-19 Workspace Group lifecycle dual-environment slice

`WORKSPACE.create`, `WORKSPACE.detail`, `WORKSPACE.patch`,
`WORKSPACE.list` and `WORKSPACE.members` (source
`test_create_group`, `test_get_group_detail`, `test_patch_group`,
`test_list_groups` and `test_add_member` in `group.sh`) are now a
dual-environment legacy-HTTP slice
(`DUAL-WORKSPACE-GROUP-LIFECYCLE-01`). The executor drives the
reviewed sequence in order:

1. create the retained Group through the legacy face
   (`POST /groups` with the exact `driver_bot`) and require the
   exact create acknowledgement (Group ID, driver, the
   driver-only participant roster, non-empty `group_kind`,
   `created=true`);
2. read the Group detail back (`GET /groups/{group_id}`) and
   require the exact identity plus non-empty `group_kind` and
   `visibility` and the driver participant;
3. patch the Group through the legacy route
   (`PATCH /groups/{group_id}` with `{"name","context"}`) and
   require both the acknowledgement echoing the new name and a
   follow-up detail read-back of the persisted `label` and
   `context` with an unchanged roster — an echo without a
   read-back, or a drifted read-back, is a failure, not a pass;
4. list Groups (`GET /groups?limit=50&group_kind=all`) and
   require the exact newly created owned Group to be present
   exactly once in the bounded `items` with its driver Bot, per
   the migration-contract correction that replaces "global list
   totals" with exact owned identity — a nonzero `total` without
   the exact Group is a failure, not a pass;
5. add the member Bot (`POST /groups/{group_id}/members` with
   the exact `bot_uuid`), read the two-participant roster back,
   remove it (`DELETE /groups/{group_id}/members/{bot_uuid}`),
   and require both exact acknowledgements (`added=true` naming
   the Group and member; `removed=true` naming the Group and the
   removed Bot) plus the final driver-only roster.

This is the raw legacy HTTP face, not public-v1: the deployed
route contract for these operations is the root `/groups` tree
(`apps/bcs/crates/adapters/http/bcs-http/src/router.rs`), the
same routes the product CLI and `group.sh` exercise, and the
V1 collaboration routes sit behind Gateway Principal JWT
verification that the migration contract does not admit for this
slice. The caller is the case-owned driver on both tiers: its
owned local Bot token on Singlebox, and the owned Human Cookie
that owns both dedicated pool Bots on preprod. The roster stays
the admitted two-Bot `driver`/`member` pair; the legacy
CEO/PM/ENG/QA pairing is represented by those roles, and no
borrowed or impersonated credential is admitted.

The profile contract is frozen as
`dual-workspace-group-lifecycle-profile-v1` with six
`case_owned_driver` legacy route bindings (create, detail,
patch, bounded list, member add, member remove) and the retained
effect admission `group=bounded-owned-bots-retained-group-lifecycle`,
`initial_session=retained`, `cleanup=none`; the created Group
stays retained and no automatic deletion is claimed. The
membership round trip ends with the driver-only roster, so the
retained Group carries no foreign participant. To preserve the
225-entry executor source budget, the 33-line
`actor_admission.py` module (its pilot actor-scope admission
belongs with the observer admission gate that already enforced
the same policy) was consolidated into `observation_gate.py`,
with its two importers (`fixtures.py`, `observation_gate.py`)
and `tests/test_actor_admission.py` repointed to the new home.
This is still contract-level: no live Singlebox or preprod
request was executed, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

### 2026-10-19 Workspace Group governance dual-environment slice

`WORKSPACE.label` and `WORKSPACE.visibility` (source
`test_update_group_label` and `test_update_group_visibility`
in `group.sh`) are now a dual-environment legacy-HTTP slice
(`DUAL-WORKSPACE-GROUP-GOVERNANCE-01`). The executor drives
the reviewed sequence in order:

1. provision the retained owned Group through the legacy
   create route (setup, exact acknowledgement with the
   driver-only roster);
2. relabel it (`PUT /groups/{group_id}/label` with the exact
   `{"label"}`) and require the exact acknowledgement
   (`updated=true` naming the Group, the new label and the
   `changed_by` actor) plus a detail read-back proving the
   persisted label;
3. set its visibility (`PUT /groups/{group_id}/visibility`
   with the exact `{"visibility"}`) and require the exact
   acknowledgement (`updated=true` naming the Group, the new
   visibility and the `changed_by` actor) plus a detail
   read-back proving the persisted visibility.

Caller identity is exact on both tiers: the deployed
`resolve_group_member_caller` resolves a Bot token to the Bot
itself and a Human Cookie to the coordinator Bot the Human
owns, so the `changed_by` acknowledgement equals the dedicated
driver Bot id in both the owned-local and preprod credential
contexts — a mismatched `changed_by` (foreign actor, borrowed
Bot) is a failure, not a pass. The legacy mock-human 401
skip in `test_update_group_label` does not apply: the dual
case admits real credentials only and requires the 200
acknowledgement on both tiers.

The roster stays the admitted two-Bot `driver`/`member`
pair; the legacy CEO/PM pairing is represented by those
roles, and no borrowed or impersonated credential is
admitted. The governed Group stays retained after both
mutations.

The profile contract is frozen as
`dual-workspace-group-governance-profile-v1` with four
`case_owned_driver` legacy route bindings (create, detail,
label, visibility) and the retained effect admission
`group=bounded-owned-bots-retained-group-governance`,
`initial_session=retained`, `cleanup=none`. To preserve the
225-entry executor source budget, the 27-line
`qualification.py` module (the honest pilot qualification
gate) was consolidated into `pilot.py`, whose PRE-01
execution it gates, with its two importers (`local_run.py`,
`cli.py`) repointed to the new home. This is still
contract-level: no live Singlebox or preprod request was
executed, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

### 2026-10-19 Workspace Group incident core dual-environment slice

The incident core of the legacy Group Workspace
round trip (`_story_group_workspace_round_trip`) is
migrated as `DUAL-WORKSPACE-INCIDENT-CORE-01`, binding
the three still-unmigrated obligations that share that
legacy function: `WORKSPACE.incident-group`,
`WORKSPACE.participant-mode` and
`WORKSPACE.incident-delete`.

The executor drives the exact legacy incident sequence
with three distinct owned credentials, one per caller
kind, and records the credential used per request in the
step evidence:

1. the driver Bot creates the incident response room
   (`POST /groups` with `driver_bot`, the exact
   `Incident response room` label and the three owned
   participants with their legacy roles) and requires
   the exact acknowledgement (`id`, `driver_bot`,
   `participants` equal to the owned driver/member/QA
   roster, `group_kind` and `created=true`) plus a
   detail read-back proving the label and roster;
2. the QA Bot mutes itself
   (`PUT /groups/{group_id}/participants/{qa_actor_id}/mode`
   with the exact `{"mode":"muted"}`) and requires the
   exact acknowledgement (`success=true` with `data`
   naming the Group, the QA actor and `mode=muted`)
   plus a detail read-back proving the muted mode on
   exactly that participant;
3. the owning Human finalizes the room
   (`DELETE /groups/{group_id}?bot_id={driver_actor_id}`)
   and requires the exact acknowledgement
   (`deleted=true` naming the Group) followed by a
   404 detail read proving the Group is gone.

The authenticated caller is exact per route: the
create resolves a Bot token (the driver Bot itself), the
self-mute resolves the QA Bot token to itself, and the
finalization and detail reads are made by the Human
Cookie that owns the three dedicated pool Bots on
preprod; on Singlebox the owned driver Bot token plays
the Human principal exactly as in the earlier
lifecycle/governance slices. The three-Bot roster
(`driver`/`member`/`qa`) is the collection precedent;
the QA Bot participates as a consultant in the incident
room and never borrows another actor's credential.

The profile contract is frozen as
`dual-workspace-incident-core-profile-v1` with the
`incident-bundle` auth admission (Human Cookie plus the
owned driver and QA Bot token references), four
`case_owned_driver` legacy route bindings (create,
detail, participant-mode, incident-delete) and the
custom effect admission
`incident_group=bounded-owned-bots-incident-room`,
`participant_mode=self-muted-by-owned-bot`,
`finalization=exact-owned-group-delete-and-not-found`.
Unlike the retained slices, the incident Group is
finalized exactly (created then deleted with a not-found
proof), so the fixture effect is
`FINALIZED_BOUNDED_OWNED_BOTS_INCIDENT_CORE` and no
resource stays behind after the case. To preserve the
225-entry executor source budget, the 58-line
`shared_database_review.py` module (the owner-confirmed
shared-database review scope) was consolidated into its
sole importer `remote_observer.py`, whose reviewed
observer-binding admission already covers that scope. This is
still contract-level: no live Singlebox or preprod
request was executed, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

### 2026-10-20 Session CLI profile split and admission consolidations

To keep the migration prerequisites sound for the two
remaining Workspace obligations, the 398-line Session CLI
profile family (`SESSION_CLI_CREATE_GET_PROFILE_CONTRACT`,
`SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT`,
`SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT`,
`SESSION_CLI_COLLECTION_PROFILE_CONTRACT`, their ten
operation constants and their eight validators) was split
into a dedicated `session_cli_profile.py` module imported
by every Session CLI contract and its tests, keeping each
Case module within the 1,000-line CI limit. The shared
finite-evidence admission (`validate_evidence_policy` and
its per-suite required-evidence contracts) was consolidated
from the 51-line `evidence_policy.py` into its sole
importer `inventory.py`, whose `current_manifest` and
`freeze_manifest` already apply it on every admission
path; the test import and the reporting patch target moved
with it. The executor source budget stayed fail-closed:
224 `bcs_e2e` modules plus `runner.py` fill the 225-entry
budget exactly after these consolidations.

### 2026-10-20 Workspace round trip dual-environment slice

The shared-workspace, routing-service and
message-exchange obligations of the legacy Group Workspace
round trip (`_story_group_workspace_round_trip`) are
migrated as `DUAL-WORKSPACE-ROUNDTRIP-01`, binding
`WORKSPACE.workspace`, `WORKSPACE.routing-service` and
`WORKSPACE.messages` — the three obligations that share
that legacy function and were still unmigrated after the
incident core slice.

The executor drives the exact legacy round-trip sequence
with two distinct owned credentials, recording the
credential used per request and the reviewed route's
caller admission per request in the step evidence:

1. the driver Bot creates the round-trip room
   (`POST /groups` with `driver_bot`, the exact
   `Workspace round trip` label and the three owned
   participants with their legacy roles) and requires the
   exact acknowledgement (`id`, `driver_bot`,
   `participants` equal to the owned driver/member/QA
   roster, `group_kind` and `created=true`) plus a Human
   detail read-back proving the label and roster;
2. the owning Human documents the shared workspace
   (`PUT /groups/{group_id}/workspace` with the exact
   decisions, notes, empty tasks and audit log) and
   requires the exact acknowledgement (`updated=true`,
   `group_id`, `workspace.decisions` and
   `workspace.notes` preserved), followed by an
   independent `GET /groups/{group_id}/workspace`
   read-back proving the same decisions and notes;
3. the driver Bot wires structured routing
   (`PUT /groups/{group_id}/routing-policy` with
   `mode=structured`, `default_bot_final_delivery=
   inject_observers` and the sender route from the
   driver to the member) and requires the exact
   acknowledgement (`ok=true` with the persisted
   `routing_policy`), while the owning Human bounds the
   group as a service (`PATCH
   /groups/{group_id}/settings` with
   `service_spec.max_concurrency=2`) and requires the
   exact acknowledgement (`id`, the persisted
   `max_concurrency` and `status=ok`);
4. the owning Human posts a persistent message
   (`POST /groups/{group_id}/messages` with the driver
   as sender) and requires the exact acknowledgement
   (`message_id` non-empty, `routed_to` non-empty), the
   driver Bot exchanges a live chat message
   (`POST /groups/{group_id}/chat` with the `@产品经理`
   coordination text from the driver) and the Human
   delivers an agent callback
   (`POST /groups/{group_id}/callback` with the
   engineering-validation text and the driver mention),
   each requiring `group_id` and non-empty
   `delivery_results`;
5. the owning Human finalizes the room
   (`DELETE /groups/{group_id}?bot_id={driver_actor_id}`)
   and requires the exact acknowledgement
   (`deleted=true` naming the Group) followed by a 404
   detail read proving the Group is gone.

The authenticated caller is exact per route where the
reviewed OCB HTTP surface binds one: creation resolves the
driver Bot token, structured routing resolves the
authenticated Bot from headers, the persistent message
resolves the Human identity, live chat resolves the
requesting Bot, and deletion resolves the query
`bot_id`. Three routes do not bind a route-level caller in
the reviewed surface — the shared-workspace replacement,
the bounded-service settings patch and the agent callback
— so the evidence trail records them as
`route_caller_binding=documented_gap` instead of an
exact-actor admission, while the open Group detail read is
recorded as `open_read`. The user precedent of accepting
documented identity gaps applies; these are route-level
admission gaps, not Case-scope reductions. On preprod the
Human Cookie owns the calls that the legacy story makes as
the Human; on Singlebox the owned driver Bot token plays
the Human principal exactly as in the earlier
lifecycle/governance/incident slices.

The profile contract is frozen as
`dual-workspace-roundtrip-profile-v1` with the
`roundtrip-bundle` auth admission (Human Cookie plus the
owned driver Bot token reference), the ten
`case_owned_driver` legacy route bindings (create, detail,
workspace update/read, routing-policy, settings, messages,
chat, callback, bot-scoped delete) and the custom effect
admission `creation=
finalized-bounded-owned-bots-incident-room`,
`workspace=finalized-bounded-owned-bots-shared-workspace`,
`routing_service=
finalized-bounded-owned-bots-structured-routing`,
`messaging=finalized-bounded-owned-bots-group-messaging`,
`finalization=exact-owned-group-delete-and-not-found`. The
round-trip Group is finalized exactly (created then deleted
with a not-found proof), so the fixture effect is
`FINALIZED_BOUNDED_OWNED_BOTS_WORKSPACE_ROUND_TRIP` and no
resource stays behind after the case.

### 2026-10-20 Workspace proposal confirmation dual-environment slice

The proposal obligation of the legacy Group Workspace
round trip (`_story_group_proposal_confirmation`) is
migrated as `DUAL-WORKSPACE-PROPOSAL-01`, binding
`WORKSPACE.proposal`.

The executor drives the exact legacy proposal sequence
with two distinct owned credentials:

1. the driver Bot requests a release-readiness Group
   (`POST /groups/request` with the exact
   `Review release readiness` topic, the owned member and
   QA Bots as suggested participants and the owned driver
   Bot as suggested driver) and requires the exact
   acknowledgement (`proposal_created=true`,
   `driver_bot`, `participants` equal to the owned
   member/QA pair) plus the minted confirmation URL;
2. the owning Human previews the confirmation page
   (`GET` on the minted capability path) and requires a
   200 `text/html` response whose single form posts back
   to the same token path and whose visible text carries
   the requested topic;
3. the owning Human confirms the proposal
   (`POST` on the same minted capability path) and
   requires the exact acknowledgement (`created=true`
   with the created `group_id`);
4. the confirmed Group is read back by the Human
   (`GET /groups/{group_id}`) proving `id` and the
   proposed driver Bot;
5. the owning Human finalizes the Group
   (`DELETE /groups/{group_id}?bot_id={driver_actor_id}`)
   and requires the exact acknowledgement
   (`deleted=true` naming the Group) followed by a 404
   detail read proving the Group is gone.

The confirmation URL is a capability, not a credential:
the transport validates it against the owned entrypoint
origin (scheme, host and port must match; no credentials,
query or fragment may escape the origin), admits only the
UUID-shaped `/groups/<token>/confirm` path, and exposes
only that bounded in-memory path plus a SHA-256 digest of
the URL — the raw usable URL never reaches the Case
executor, its reported data or its evidence trail, and the
HTML confirmation page is parsed (allowed tags only, no
script/iframe/object/embed/base, no event handlers, one
form whose action is exactly the token-relative path with
method POST) before the preview is accepted. The
confirmation page and action are minted-token scoped routes
that do not bind a route-level caller in the reviewed
surface, so both are recorded as
`route_caller_binding=documented_gap`; the proposal
request resolves the authenticated Bot token and the
bot-scoped delete resolves the query `bot_id`
(`route_bound`); the post-confirmation detail read is an
open read (`open_read`).

The profile contract is frozen as
`dual-workspace-proposal-profile-v1` with the
`proposal-bundle` auth admission (Human Cookie plus the
owned driver Bot token reference), five `case_owned_driver`
legacy route bindings (proposal request, confirmation page
GET/POST on the minted token path, detail, bot-scoped
delete) and the custom effect admission
`proposal=finalized-bounded-owned-bots-group-proposal`,
`confirmation=
finalized-bounded-owned-bots-proposal-confirmation`,
`finalization=exact-owned-group-delete-and-not-found`. The
confirmed Group is finalized exactly (created then deleted
with a not-found proof), so the fixture effect is
`FINALIZED_BOUNDED_OWNED_BOTS_PROPOSAL_CONFIRMATION` and
no resource stays behind after the case.

The Case modules keep the executor source inventory
fail-closed: the two new focused modules raise the
documented focused-module allowance of the source-file
budget from 225 to 227 entries (the same focused-module
precedent as the earlier remote-adapter split), keeping
the per-file 1,000-line CI limit and the consolidation
discipline for every other module. Both cases remain
contract-level: no live Singlebox or preprod request was
executed, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

Retirement note (2026-10-26): upstream dev commit `72f9dff36` (Avernet #3114)
removed the group proposal/confirmation product surface end to end. The local
WORKSPACE.proposal executor family (`local_workspace_proposal.py`,
`workspace_proposal_contract.py`, the profile contract and `PROFILE_EFFECTS`
entry, the case registration and the focused tests) is retired from this
runner with it. The complete Workspace story becomes the incident round trip:
17 obligations, `local-workspace-run --with-incident-delete`, case
`LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01`, execution report
`workspace-incident-roundtrip-report.json`, canonical projection
`report-LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01.json`, cleanup scope
`OWNED_INCIDENT_ONLY`. The historical 2026-10-15 flow record and the 2026-10-20
proposal slice description above remain as history (not executable claims).
No live Singlebox or preprod request was made for this retirement; local
regression evidence and the `stories.sh` drift re-freeze are recorded in the
OpenSpec tasks log.

## 2026-10-25 Remaining Workspace command lifecycle

The reviewed `DUAL-WORKSPACE-INCIDENT-CORE-01`,
`DUAL-WORKSPACE-ROUNDTRIP-01` and
`DUAL-WORKSPACE-PROPOSAL-01` contract/adapter pairs now have one
stable command entry, `dual-workspace-story-run --case
<{incident-core|roundtrip|proposal}>`. The entry does not redefine
the business obligations; it only applies their strict Profile
validator, requires `--allow-write`, an exact
`--confirm-run-id`, a fresh private state directory, and a
non-symlinked receipt path.

Every Run writes the three-file local receipt set
(`workspace-<case>-profile.json`, `workspace-<case>-binding.json`
and `workspace-<case>-run.json`) with the executor digest, Profile
digest, parent Story id and complete case snapshot. Reports keep
the hard release boundary: `remote_validation=NOT_RUN`,
`grants_release_credit=false`, and credentials are reduced to
`owned_local_bot_token` (Singlebox) or `human_cookie` (preprod);
Cookie or token values never enter receipts. The remaining
Workspace command adds one focused module, so the executor
source-file allowance grows from 280 to 281 while the per-file
1,000-line limit stays unchanged.

The command increment is contract-level. No real Singlebox or
preprod request was executed during this change, and live
authorization remains an independent admission step.

## 2026-10-20 Structured collaboration core cross-environment slice

`PUBLIC-STRUCTURED-CORE-01` freezes the reviewed 15 group,
collaboration-definition and state-machine-run obligations of
the Structured collaboration Story (the template catalog/detail
pair stays in `PUBLIC-STRUCTURED-TEMPLATES-01`). The dedicated
CEO Bot creates the exact two-Bot `state_machine` Group with
the frozen inline definition: `structured.group.create` binds
its `caller_actor_id` to the dedicated driver Bot and sends the
Human Cookie plus the Bot Authorization reference, while every
remaining route authenticates with the Human Cookie only and
strips the Authorization header. The Human reads and patches the
bound definition (the patch must increment `default_definition.version`
by exactly one and persist the revised instruction), and the three
reviewed negative envelopes are asserted explicitly: the
unpublished-definition upgrade returns 404 naming the exact
`definition@version`, the bot-task Human response returns 400
with `not a human_input node`, and the aborted-run rerun returns
409 with `must be failed before rerun`. Negative assertions require
explicit `parse_error` admission in the write transport; an
unadmitted error status is a transport failure and never a silent
pass. The bot-only workflow must expose an exact empty
pending-Human array, projected as `{"items": []}` with any entry
treated as fixture drift; the Human starts, reads, graph-inspects,
node-reads and cancels the state-machine run, the cancelled run
remains `aborted` on read-back, and the creator-scoped delete
(`DELETE /api/v1/groups/{group_id}?bot_id={driver_actor_id}`)
finalizes the fixture (`FINALIZED_STRUCTURED_COLLABORATION_GROUP`),
so no resource stays behind after the case.

The obligation chain is stateful inside the remote adapter
(`group_id` → `definition_id`/`base_version`/`patched_version` →
`run_id`), every obligation runs at most once, and ordering guards
refuse out-of-order or repeated invocation before any request is
issued. The Case contract re-derives its 15 requirement rows from
the frozen `structured-migration-contract.json` source map and
cross-checks each row against the reviewed route-binding registry,
so legacy-story or route drift fails the plan instead of being
silently re-derived.

Validation on this worktree: the new Case contract and adapter
passed the e2e runner Case guard; the new core adapter suite
passed 21/21; and the full runner Python discovery run completed
2354 tests / 150.654 seconds, OK (skipped=215). No real Singlebox
or preprod request was executed, and remote validation remains
`NOT_RUN`.

The Case modules keep the executor source inventory fail-closed:
the structured-collaboration core slice adds its Case contract and
remote adapter, raising the documented focused-module allowance of
the source-file budget from 227 to 229 entries (the same
focused-module precedent as the earlier remote-adapter splits),
keeping the per-file 1,000-line CI limit and the consolidation
discipline for every other module. Both cases remain
contract-level: `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

## 2026-10-20 Register-onboard register core cross-environment slice

`PUBLIC-NETWORK-REGISTER-01` freezes the reviewed register core
of the Agent-network Story (`NETWORK.register-onboard`,
`_story_register_and_onboard_owned_agent`) that transfers
faithfully to the public-v1 Gateway/Singlebox face: the Human
mints a register token (`GET
/openapi/v1/collaboration/register/token`, Human Cookie only),
the minted token anonymously registers a new agent (`POST
/openapi/v1/collaboration/register` with the register token and bot-name query parameters, HTTP
201 with envelope code 20100 — the minted register token is the
sole credential and travels in the query string, so the create
sends neither a Cookie nor an Authorization header), and the
owning Human reads the registered agent back (`GET
/openapi/v1/collaboration/bots/{bot_uuid}`, Human Cookie only)
to prove the exact reviewed `capabilities.name`, `created_by`
and `actor_kind` ownership of the registered Bot. The route
declarations were re-derived from the station-internal
`bcs-api-http` openapi-v1 routers, not from the community
`bcs-http`/BCN face: the community mount points
(`POST /register`, `POST /bots/onboard`, `GET /onboard/url`,
`POST /admin/bots/onboard`, `DELETE /bots/{uuid}`) differ from
or have no public-v1 Gateway equivalent, and only the
register-token mint, the token-bound create and the Bot detail
read-back are shared by both faces.

The reviewed BCN-only closures — Bot-token capability onboarding,
the browser onboarding URL, administrator re-onboard, owner
delete and the post-delete 404 — therefore stay blocked
community-face obligations (`community_only_closures`) instead of
being normalized to PASS: the public-v1 face has no Bot delete,
so the registered temporary Bot is retained as an owned Human
resource (`fixture_effect=REGISTERED_TEMPORARY_BOT_RETAINED`),
and preprod execution requires an explicit retention or reaping
disposition before the slice can grant release credit, matching
the adopted preprod default-retained-resource direction.

The adapter chains the three operations in order, runs the
obligation at most once, and keeps both credentials (the minted
register token and the returned Bot token) in adapter memory
only: evidence carries the redacted create path
(the token query value appears as [REDACTED-register-token]), SHA-256 digests of both
credentials, and per-request identity records
(`AUTHENTICATED_HUMAN_COOKIE`, `ANONYMOUS_REGISTER_TOKEN`,
`AUTHENTICATED_HUMAN_COOKIE`); the write transport re-validates
the reviewed anonymous route, the 2–64 character Bot-name bound
and the 201/20100 envelope before returning. The Case contract
re-derives its requirement row from the frozen
`network-migration-contract.json` source map and cross-checks
the three operations against the reviewed route-binding registry,
so legacy-story or route drift fails the plan instead of being
silently re-derived.

Validation on this worktree: the new Case contract and adapter
passed the e2e runner Case guard; the new register adapter suite
passed 15/15; the network read adapter suite and migration
inventory suite passed 43/43; and the full runner Python
discovery run completed 2369 tests / 151.783 seconds, OK
(skipped=215), up from 2354 before this slice. No real Singlebox
or preprod request was executed, and remote validation remains
`NOT_RUN`.

The Case modules keep the executor source inventory fail-closed:
the register-onboard register core slice adds its Case contract
and remote adapter, raising the documented focused-module
allowance of the source-file budget from 229 to 231 entries,
keeping the per-file 1,000-line CI limit and the consolidation
discipline for every other module. The case remains
contract-level: `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

## 2026-10-20 Event Webhook subscription lifecycle core cross-environment slice

`PUBLIC-WEBHOOK-SUBSCRIPTION-01` freezes the reviewed
subscription lifecycle core of the Event Webhook Story
(`story_user_receives_group_event_webhooks`) that
transfers faithfully to the public-v1 Gateway/Singlebox
face: the Human principal creates a Group with an inline
webhook event subscription whose sink points at the exact
receiver origin supplied by the composition root (`POST
/openapi/v1/collaboration/groups` with inline
`event_subscriptions`, HTTP 201 with envelope code 20100),
proves the exact subscription through the listing (`GET
/openapi/v1/collaboration/event-subscriptions`) and
detail-read (`GET
/openapi/v1/collaboration/event-subscriptions/{id}`)
routes, exercises the synchronous delivery test (`POST
/openapi/v1/collaboration/event-subscriptions/{id}:test`)
and the optimistic-concurrency disable/enable lifecycle
(`PATCH` revision 1 to 2 disabled to 3 active), creates
and removes a webhook observation Session (`POST
/openapi/v1/collaboration/sessions`), and lists and reads
the successful delivery history (`GET
/openapi/v1/collaboration/event-subscriptions/{id}/deliveries?status=succeeded&limit=20`,
`GET /openapi/v1/collaboration/event-deliveries/{id}`)
before finalizing every fixture (subscription tombstone via
`DELETE ...?revision=3`, then the Session and the Group).
The route declarations were re-derived from the
station-internal `bcs-api-http` openapi-v1 routers, not
from the community `bcs-http`/BCN face: only the twelve
reviewed lifecycle obligations above are shared by both
faces, and each operates through the opt-in cross-environment
Case bindings, never as an ambient route.

The reviewed community-only closures — empty-receiver
handling, the local principal mint, the group/session/message
event emissions, the `:test` event rejection, and the stream
ordering proof (`WEBHOOK.receiver-empty`,
`WEBHOOK.local-principal`, `WEBHOOK.group-event`,
`WEBHOOK.test-event`, `WEBHOOK.session-event`,
`WEBHOOK.message-create`, `WEBHOOK.message-event`,
`WEBHOOK.stream-order`) — therefore stay blocked on the
community-only face: their routes have no public-v1
Gateway equivalent.

The slice introduces the `gateway_principal` edge-case
auth kind: an owned-local Singlebox profile mints the
principal from its live loopback runtime signing
capability, and a gateway/preprod profile requires an
operator-provisioned principal signing key reference
(`auth.principal_signing_key_env`, matched against
`[A-Z][A-Z0-9_]{2,}`, alongside `header_env` with at
least one declared) resolved from the environment at
request time to mint the HS256 JWT (kid `bare`, iss
`gateway`, aud `bcs`, exp = iat + 60, tenant `bcs-e2e`,
subject id the profile Human user id). Neither a Cookie
nor a Bot token ever travels on the twelve lifecycle
requests; they authenticate exclusively with the minted
principal header.

Admission is fail-closed on both faces. The local
composition root requires the owned-local runtime signing
capability, exclusive fixture use, and the owned loopback
receiver origin: an off-loopback receiver for an owned-local
Singlebox run is a `ConfigError`, because the webhook sink
of an owned run always points at the owned event receiver.
The gateway admission requires the operator-provisioned key
reference and the `webhook.subscription.*` /
`webhook.delivery.read` edge-case bindings, and group
creation routes through `require_group_effect_admission`,
so remote preprod execution stays write-blocked
(`RemoteBootstrapAdmissionError` at step 0) until an
explicit effect permit exists. The read and write
transports carry the principal auth and the
subscription/delivery path templating for the new edge
routes, with the delete route carrying its optimistic
`?revision=3` query form.

The adapter chains the twelve obligations in the frozen
order, runs each obligation at most once, and keeps the
minted principal in adapter memory only: every step's
evidence carries `identity=AUTHENTICATED_HUMAN_PRINCIPAL`,
`principal_sent=true`, `credentials_sent=false`,
`authorization_sent=false`, the per-step fixture effect,
and the correlation records, and a credential-leak
assertion scans the whole evidence before it is returned.
The Case contract re-derives its requirement rows from the
frozen `webhook-migration-contract.json` source map and
cross-checks the twelve operations against the reviewed
route-binding registry, so legacy-story or route drift
fails the plan instead of being silently re-derived.

Validation on this worktree: the new Case contract and
adapter passed the e2e runner Case guard; the new webhook
subscription adapter suite passed 21/21; and the full
runner Python discovery run completed 2390 tests /
153.413 seconds, OK (skipped=215), up from 2369 before
this slice. No real Singlebox or preprod request was
executed, and remote validation remains `NOT_RUN`.

The Case modules keep the executor source inventory
fail-closed: the webhook subscription slice adds its Case
contract and remote adapter, raising the documented
focused-module allowance of the source-file budget from 231
to 233 entries, keeping the per-file 1,000-line CI limit
and the consolidation discipline for every other module.
The case remains contract-level: `remote_validation=NOT_RUN`,
and `grants_release_credit=false`.

## 2026-10-20 Dual channel product-CLI core slice

`DUAL-CHANNEL-CLI-01` migrates the reviewed Channel
management parent (`story_cli_operator_validates_channel_management`,
`channel-cli-migration-contract.json`, nine
`CHANNEL-CLI.*` obligations) as a dual-environment
product-CLI slice. Seven obligations execute through the
station-internal `bcs-cli` binary — the CEO bind under
the disabled DingTalk bridge (`channel bind`, expected
nonzero with the `channel bridge is disabled` diagnostic
and a redacted synthetic client secret), the initial and
final PM/ENG binding listings (`channel list`), the PM
`create-group` that retains the release-coordination
context and topic, the PM explicit `session create`, the
participating PM `channel conversation-id` DingTalk
lookup, and the CEO `channel unbind` of the run-derived
absent binding — and the two member-scoped legacy
deletion obligations (`DELETE
/sessions/{session_id}?bot_id={member_actor_id}` and
`DELETE /groups/{group_id}?bot_id={member_actor_id}`)
execute through the reviewed legacy HTTP cleanup face.

The Channel Profile contract
(`dual-channel-cli-profile-v1`) admits three dedicated
driver/member/engine Bots, owned-local Bot tokens on
Singlebox and the Human Cookie on the admitted preprod
HTTPS gateway, the channel command family, and the two
case-owned member cleanup route bindings. The runner
mints one in-memory synthetic client secret
(`synthetic-…-channel-secret`, 57 characters) per
bind execution, never persists it, and fails closed
when the product CLI emits the credential, returns zero
under the disabled bridge, loses the disabled-bridge
diagnostic, lists a phantom binding for the case
account, or drifts from the human create-group summary
or the exact Session/Group contracts. Private CLI
credential contexts are zeroed and removed with
fail-closed `CLEARED` confirmation.

Validation on this worktree: the new Case adapter and
runner passed the e2e runner Case guard; the new
channel CLI adapter suite passed 16/16; and the full
runner Python discovery run completed 2406 tests, OK
(skipped=215). No real Singlebox or preprod request was
executed, and remote validation remains `NOT_RUN`. The
durable owned-local empty-SQLite-table admission for the
missing-binding unbind stays with `LOCAL-CHANNEL-CLI-01`;
the remote slice proves the run-derived absent binding
only through the product acknowledgement. The case
remains contract-level: `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

## 2026-10-25 Dual collaboration-team CLI command lifecycle

`DUAL-COLLABORATION-TEAM-CLI-01` now has one stable dual command entry:
`dual-collaboration-team-run`. The command binds the reviewed
collaboration-team product-CLI suite
(`story_cli_operator_builds_collaboration_team`) and reuses the existing
contract, runner, adapter, and report path unchanged; it does not invent a
new business surface.

The entry admits both Singlebox and preprod through the strict
`dual-collaboration-team-cli-profile-v1` validator. For Singlebox, the
suite uses the four owned-local Bot tokens; for preprod, it uses the Human
Cookie from the configured preprod gateway. The command also requires
`--allow-write`, an exact `--confirm-run-id`, a fresh non-symlinked private
state directory, and a pinned CLI binary. Each Run writes the same
three-file local receipt set:
`collaboration-team-profile.json`, `collaboration-team-binding.json`, and
`collaboration-team-run.json`.

The command keeps release semantics unchanged: `remote_validation=NOT_RUN`,
`grants_release_credit=false`, credential values remain outside journal,
report, and commit, and each report reduces credentials to only the
`owned_local_bot_token` or `human_cookie` kind. The resource disposition
for this slice is explicit as `REQUEST_AND_GROUP_HISTORY_RETAINED`, matching
the case’s admitted relationship disposition rather than silently claiming
a broader storage/live-validation result.

## 2026-10-25 Dual Edge read command lifecycle

`dual-edge-read-run` is now the stable command entry for the four reviewed
Edge read slices that can transfer between Singlebox and the preprod Gateway:
`PUBLIC-EDGE-ADMISSION-01`,
`PUBLIC-EDGE-FRIEND-REQUESTS-LIST-01`,
`PUBLIC-EDGE-FRIEND-CONNECTIONS-LIST-01`, and
`PUBLIC-EDGE-BOT-SEARCH-CANDIDATES-01`. It is a command lifecycle only: the
existing Case contracts, route validators, HTTP adapters, obligation order,
and business assertions remain the source of authority.

Admission uses the two unauthenticated native admission reads. Friend-request
and friend-connection listing use the reviewed dedicated-driver Bot view.
Bot-search uses only the two station candidate-search obligations
(`name-fuzzy` and `empty-query`) from the dedicated-driver viewpoint with a
Human Cookie; candidate records are reduced by the adapter to only bounded
result counts and search mode. The other ten legacy Bot-search reads and the
separate TC fixture mutation stay outside this command and uncredited.

A Run requires a strict Profile, exact operation bindings for the selected
case, resolved credential references, `--allow-write`, an exact
`--confirm-run-id`, and a fresh absolute non-symlinked private state
directory. It writes immutable Profile, binding, and Run receipts
(`edge-<case>-profile.json`, `edge-<case>-binding.json`, and
`edge-<case>-run.json`) before and after execution. A route, identity, face,
viewpoint, response-shape, or state-directory violation fails closed.

Every report keeps the evidence boundary explicit:
`resource_disposition=READ_ONLY_NO_BUSINESS_RESOURCE`,
`remote_validation=NOT_RUN`, and `grants_release_credit=false`. Credentials
are serialized only as `unauthenticated`, `owned_local_bot_token`, or
`human_cookie`; Cookie and Bot token values never enter receipts.

## 2026-10-25 Dual Channel-binding command lifecycle

`dual-channel-bindings-run` now exposes `PUBLIC-CHANNEL-BINDINGS-01` as a stable
command entry for the reviewed Singlebox and preprod Gateway slice.

The command does **not** add a second product surface. It reuses the existing
`channel_bindings_case_plan`, `RemoteChannelBindingsAdapter`, and
`execute_channel_bindings_case` contract, keeping the four reviewed obligations
in their frozen order:

- `CHANNEL.bind-denied`
- `CHANNEL.list-before`
- `CHANNEL.target-empty`
- `CHANNEL.list-after`

Badmission is fail-closed. A Profile must bind exactly the three reviewed
channel-binding operations, carry a Human Cookie reference, and must not carry
a Bot Authorization reference. The command also requires `--allow-write`, an
exact `--confirm-run-id`, and a fresh, non-symlinked private state directory.
It writes three receipts before network use:

- `channel-bindings-profile.json`
- `channel-bindings-binding.json`
- `channel-bindings-run.json`

The binding receipt and Run report freeze the case scope, including the two
explicitly blocked legacy obligations (`CHANNEL.pause-missing` and
`CHANNEL.delete-missing`) so the command cannot be mistaken for full Channel
parent coverage. The report keeps the strict evidence boundary:
`resource_disposition=NO_BINDING_CREATED`, `remote_validation=NOT_RUN`, and
`grants_release_credit=false`.

Validation: the command tests passed 5/5, `compileall` passed, and the
OpenSpec validation remains strict. No real Singlebox or preprod request was
executed for this increment.

## 2026-10-25 Dual network actor-status command lifecycle

`dual-network-status-run` now exposes `PUBLIC-NETWORK-ACTOR-STATUS-01` as a stable command entry for the reviewed Singlebox and preprod Gateway slice.

The command reuses the existing `network_actor_status_case_plan`, `RemoteNetworkActorStatusAdapter`, and `execute_network_actor_status_case` contract. It does not redefine the bounded mutation, readback order, or restoration logic. A Run requires a strict Profile that binds exactly the two reviewed operations, carries a Human Cookie, and does not carry a Bot Authorization reference.

Each Run performs the frozen five-request journey:

- `GET` the dedicated driver Bot before mutation
- `PATCH` the same Bot to `hidden`
- `GET` and confirm `hidden`
- `PATCH` the same Bot back to `online`
- `GET` and confirm the restored `online` state

The command also requires `--allow-write`, an exact `--confirm-run-id`, and a fresh, non-symlinked private state directory. It writes immutable Profile, binding, and Run receipts before network use, and the report retains the hard evidence boundary:

- `resource_disposition=BOUNDED_OWNED_BOT_STATUS_RESTORE`
- `remote_validation=NOT_RUN`
- `grants_release_credit=false`

No real Singlebox or preprod request was executed during this increment.

## 2026-10-25 Dual network register command lifecycle

`dual-network-register-run` is the stable command entry for the reviewed `PUBLIC-NETWORK-REGISTER-01` Singlebox/preprod slice. It reuses `network_register_case_plan`, `RemoteNetworkRegisterAdapter`, and `execute_network_register_case`; no second product surface or obligation set is introduced.

Admission is fail-closed. A Profile must bind exactly the three reviewed public-v1 operations (`register.token.read`, `register.bot.create`, and `register.bot.readback`), carry a Human Cookie reference, and must not carry a Bot Authorization reference. The command also requires `--allow-write`, an exact `--confirm-run-id`, and a fresh, absolute, non-symlinked private state directory.

Each Run performs the fixed three-request register core: Human Cookie token mint, anonymous token-bound Bot create, and Human Cookie owner read-back. Minted register/Bot token values remain in adapter memory and never enter receipts, reports, tests, or commits; evidence records only their SHA-256 digests. The `register.bot.create` request is anonymous, so the case also documents why the BCN-only onboarding/delete closures are not transferable to the public-v1 Gateway face.

The binding and Run reports freeze both halves of the denominator: the three selected public operations and the five `community_only_closures` that stay unexecuted. The resource disposition is explicit as `REGISTERED_TEMPORARY_BOT_RETAINED`, because public-v1 has no Bot delete route. Every report keeps `remote_validation=NOT_RUN` and `grants_release_credit=false`.

## 2026-10-25 Dual structured-collaboration core command lifecycle

`dual-structured-core-run` is the stable command entry for the reviewed `PUBLIC-STRUCTURED-CORE-01` Singlebox/preprod slice. It reuses `structured_core_case_plan`, `RemoteStructuredCoreAdapter`, and `execute_structured_core_case`; it does not create a second collaboration-definition or state-machine surface.

Admission is fail-closed. A Profile must bind the exact reviewed operation set, and because this slice owns both a Human Cookie path and the dedicated driver Bot Authorization path, it must carry both reviewed credential references. The command also requires `--allow-write`, an exact `--confirm-run-id`, and a fresh, absolute, non-symlinked private state directory.

The Run preserves the owner/facet separation: the driver Bot creates the state-machine Group, while the owning Human reads, patches, starts, inspects, cancels and deletes it. The command maps the 15 requirement rows to those operations and never replaces one actor with another. The three reviewed negative responses remain explicit branches, and success requires the creator-scoped Group delete.

The command keeps failure honest: a successful Run claims `FINALIZED_STRUCTURED_COLLABORATION_GROUP`, but a blocked Run reports `UNKNOWN_UNFINALIZED_STRUCTURED_COLLABORATION_GROUP`. The binding receipt records the planned finalization, while the Run receipt records the actual execution status. Reports keep `remote_validation=NOT_RUN` and `grants_release_credit=false`.

## 2026-10-25 Dual friend-connection decision command lifecycle

`dual-friend-connection-decisions-run` is the stable command entry for the reviewed `PUBLIC-FRIEND-CONNECTION-DECISIONS-01` Singlebox/preprod slice. It reuses `friend_connection_decision_case_plan`, `RemoteFriendConnectionDecisionAdapter`, and `execute_friend_connection_decision_case`; it does not create a second friendship surface or silently broaden the bounded effect.

Admission is fail-closed. A Profile must declare `relationships.friendship=bounded-owned-bot-decisions`, carry the Human Cookie, and refuse a Bot Authorization reference. The command also requires `--allow-write`, an exact `--confirm-run-id`, and a fresh, absolute, non-symlinked private state directory.

Each successful Run performs only the reviewed two-decision sequence:

- create and reject a driver→member request;
- create and accept a member→driver request.

The rejected request history and approved relationship remain in place, so the success disposition remains `BOUNDED_OWNED_BOTS_RETAINED_DECISIONS`. A blocked Run reports `UNKNOWN_OWNED_BOT_DECISIONS` instead of inventing a cleanup claim. Reports keep `remote_validation=NOT_RUN` and `grants_release_credit=false`.

## 2026-10-25 Dual public friendship auto-accept command lifecycle

`dual-friend-public-accept-run` is the stable command entry for the reviewed `PUBLIC-FRIEND-CONNECTION-AUTO-ACCEPT-01` Singlebox/preprod slice. It reuses `friend_public_accept_case_plan`, `RemoteFriendPublicAcceptAdapter`, and `execute_friend_public_accept_case`; it does not invent a second friendship effect, mutate additional relationships, or silently treat a slice as the full trusted-team Story.

Admission is fail-closed. A Profile must declare `relationships.friendship=bounded-owned-bot-public-acceptance`, bind exactly the reviewed public create and driver-connection read operations, carry the Human Cookie reference, and refuse a Bot Authorization reference. The command also requires `--allow-write`, an exact `--confirm-run-id`, and a fresh, absolute, non-symlinked private state directory.

A successful Run performs only the reviewed sequence: verify that the exact owned driver/member Bot pair is not already friends, create the public Bot-Bot friendship with automatic acceptance, and independently read the established connection list. The approval and connection history remain in place, so success records `BOUNDED_OWNED_BOTS_RETAINED_PUBLIC_ACCEPTANCE`. A blocked Run records `UNKNOWN_OWNED_BOT_PUBLIC_ACCEPTANCE`, while every report keeps `remote_validation=NOT_RUN` and `grants_release_credit=false`.

Validation: the focused public-accept adapter, command lifecycle, migration inventory, and inventory suites passed 47/47; `compileall`, the E2E case guard, and strict OpenSpec validation passed. No real Singlebox or preprod request was executed for this contract-lifecycle increment.

## 2026-10-25 Dual public network-read command lifecycle

`dual-network-read-run` is now the stable command entry for the six reviewed public-v1 network read slices:
`PUBLIC-NETWORK-BOTS-QUERY-01`,
`PUBLIC-NETWORK-BOTS-MINE-01`,
`PUBLIC-NETWORK-ACTOR-LIST-01`,
`PUBLIC-NETWORK-ACTOR-SEARCH-01`,
`PUBLIC-NETWORK-BOTS-PAGED-01`, and
`PUBLIC-NETWORK-PLATFORM-ASSET-01`.
The command does **not** duplicate those Cases; it binds their existing plan, adapter, and execution contract, and only adds the command lifecycle and receipt.

Admission is unchanged and fail-closed. A Profile must declare the exact one operation binding for the selected Case, carry the Human Cookie reference, and omit a Bot Authorization reference. For the platform-asset slice, the selected obligations remain manifest, identity, and asset; the Cookie proves the first two reads and the asset read itself stays unauthenticated. The command also requires `--allow-write`, an exact `--confirm-run-id`, and a fresh, absolute, non-symlinked private state directory. Each Run writes a Profile, binding, and Run receipt (`network-<case>-profile.json`, `network-<case>-binding.json`, and `network-<case>-run.json`) with only the credential kind; Cookie values never enter reports or commits.

The report records the exact selected obligation and keeps the evidence boundary explicit:
`resource_disposition=READ_ONLY_NO_BUSINESS_RESOURCE`, `remote_validation=NOT_RUN`, and `grants_release_credit=false`.

Validation: the focused network-read command, network adapter, friend-public-accept command, migration inventory, and inventory suites passed 74/74; `compileall`, the E2E case guard, and strict OpenSpec validation passed. No real Singlebox or preprod request was executed during this command-lifecycle increment.

## 2026-10-25 Dual structured-template catalog command lifecycle

`dual-structured-templates-run` is the stable command entry for the reviewed `PUBLIC-STRUCTURED-TEMPLATES-01` Singlebox/preprod slice. It reuses `structured_templates_case_plan`, `RemoteStructuredTemplatesAdapter`, and `execute_structured_templates_case`; it does not duplicate the template contract or widen the Structured Story.

Admission is fail-closed. A Profile must bind exactly the two reviewed station catalog operations (`structured_templates.list` and `structured_templates.detail`), carry only the Human Cookie reference, and refuse a Bot Authorization reference. The command also requires `--allow-write`, an exact `--confirm-run-id`, and a fresh, absolute, non-symlinked private state directory.

To keep the modified CLI within the repository's per-file 1,000-line limit, the crowded cross-environment command registration moved to `case_command_registration.py`; `cli.py` is now 875 lines. The CLI still imports each command lifecycle directly, so tests and dispatch remain unchanged, and only the parser-registry concern is split.

A successful Run performs only the two reviewed catalog reads, in their frozen order:

- read the station template list;
- read the exact `single-bot-guided-answer` template detail.

Each Run writes a Profile, binding, and Run receipt (`structured-templates-profile.json`, `structured-templates-binding.json`, and `structured-templates-run.json`). The reports retain only the credential kind and never serialize the Cookie value.

The command keeps the same read-only evidence boundary:
`resource_disposition=READ_ONLY_NO_BUSINESS_RESOURCE`, `remote_validation=NOT_RUN`, and `grants_release_credit=false`. The slice does not migrate the Structured group, definition, run, cancellation, or cleanup obligations, and it grants neither Structured-parent nor release credit.

Validation: the new command lifecycle suite passed 7/7, the focused cross-environment CLI-lifecycle suites passed 76/76, the full E2E-runner Python discovery suite passed 2764/2764 (224 documented skips), `compileall`, the E2E case guard, and strict OpenSpec validation passed. The focused-module artifact allowance grows by exactly two source paths, from 290 to 292: one new command lifecycle and one dedicated command-registration module. No real Singlebox or preprod request was executed during this command-lifecycle increment.

## 2026-10-25 Dual Event Webhook subscription command lifecycle

`dual-webhook-subscription-run` is the stable command entry for the reviewed `PUBLIC-WEBHOOK-SUBSCRIPTION-01` Singlebox/preprod slice. It reuses `webhook_subscription_case_plan`, `RemoteWebhookSubscriptionAdapter`, and `execute_webhook_subscription_case`; it does not create a second Event Webhook contract, transfer the community-only closures to public-v1, or claim the complete Event Webhook parent Story.

Admission is fail-closed. A Profile must bind exactly the eight reviewed `webhook.subscription.*` and `webhook.delivery.read` operations, and must carry the operator-provisioned principal signing key reference. The command additionally requires `--receiver-origin`, `--allow-write`, an exact `--confirm-run-id`, and a fresh, absolute, non-symlinked private state directory.

The Run preserves the frozen 12-step HTTP core: subscribed Group create, subscription list/read/test, disable/enable with explicit revisions, Session create, successful delivery list/read, subscription tombstone, and Session/Group finalization. The receiver origin remains an explicit operator input rather than a silently inferred local endpoint. Gateway principal key material is consumed by the adapter and never enters the Profile, binding, Run report, tests, logs, or commit; reports retain only `credential_kind=gateway_principal`.

The binding receipt records the planned finalization, while the Run receipt records the actual result. A successful Run claims `FINALIZED_WEBHOOK_SUBSCRIPTION_GROUP`; a blocked Run reports `UNKNOWN_UNFINALIZED_WEBHOOK_SUBSCRIPTION_GROUP` instead of inventing cleanup. Community-only receiver, worker-stream, message persistence, and principal minting closures remain explicit exclusions. Every report keeps `remote_validation=NOT_RUN` and `grants_release_credit=false`.

Validation for this increment: the new command lifecycle suite passed 6/6, the focused command-lifecycle discovery suite passed 101/101, `compileall`, the 4-test E2E case guard, and strict OpenSpec validation passed. The full E2E-runner discovery suite ran twice at 2770 tests with 224 documented skips; each run exposed only the known unrelated `test_local_judge_sandbox` thread-liveness assertion, and that focused test passed in isolation after each run. No real Singlebox or preprod request was executed during this command-lifecycle increment.


## 2026-10-25 Dual public Session JWT/WS command lifecycle

`dual-session-public-run` is the stable command entry for
`PUBLIC-SESSION-JWT-WS-01`. It does not create a second Session business
contract: the command reuses the existing source-bound admission and the
already reviewed owned-local Session/JWT/WS executor. It requires a strict
resolved Profile, the exact reviewed public-v1 Group/Session route set, a
Cookie-only auth reference, an approved BCS binary and digest,
`--allow-write`, an exact `--confirm-run-id`, and a fresh absolute
non-symlinked private state directory.

For a Singlebox profile, the command runs the existing owned-local executor
underneath `state_dir/owned-local-session-public`. The dual receipt stores
only a digest of that executor result plus its status and remaining
requirement IDs; it does not fork or weaken the Session assertions. For a
Gateway/preprod profile, the command emits a BLOCKED admission receipt without
a request, because no reviewed Gateway Session executor is admitted yet. A
blocked Gateway receipt records `NO_REMOTE_BUSINESS_RESOURCE_CREATED`; it
must not be conflated with an executed remote failure.

The dual receipt keeps the evidence boundary explicit:
`remote_validation=NOT_RUN`, `grants_release_credit=false`,
`full_story_status=NOT_MIGRATED`, and
`full_release_status=NOT_QUALIFIED`. The eight remaining Session parent
obligations stay unclaimed. Credentials remain outside Profile, binding, Run
report, tests, logs, and commits; the receipt retains only
`owned_local_principal_jwt` or `human_cookie` as a kind.

Validation for this increment: the new Session-public command lifecycle suite
passed 6/6, the focused command/admission/discovery collection passed 171/171
(plus 61 subtests), `compileall`, both new command-lifecycle case-guard passes,
and strict OpenSpec validation passed. The full runner discovery run completed
2551 passed / 224 skipped with one transient unrelated
`test_local_judge_sandbox` thread-liveness assertion; that focused test passed
on immediate isolated rerun. No real Singlebox or preprod request was executed
during this command-lifecycle increment.

## 2026-10-26 increment — owned-local caller-execution black-box slice

- Contract freeze: upstream dev `74dd751b623b7701608374fc4c7c4a6cf58278da`; story SHA256
  `7d7f565b0b9c6e2d72c1ba4df452027e8af47e0400decb981703d9d10d893945`; coverage wrapper
  SHA256 `4e4ee24551eca8f0b29a879802f57f8378f0e23f52530694c0dd9e151c6b4cf3`. Per-obligation
  anchors, the 3 strict markers and the 9 `LOCAL-CALLER-EXEC.*` obligations are frozen in
  `caller-execution-migration-contract.json` (case `LOCAL-CALLER-EXEC-01`, suite
  `owned-local-caller-execution-v1`, parent `caller_execution_story`).
- Runner wiring: new commands `local-caller-execution-run` / `local-caller-execution-report`;
  new modules `local_caller_execution_run.py` (admission freeze, binding, SIGTERM deadline,
  stream redaction gate, strict marker order, shared child receipt, drift fence, publication
  status gate), `local_caller_execution_reporting.py` (offline projection) and
  `local_caller_execution_contract.py` (source binding); `migration_inventory.py` exposes the
  slice under `local_independent_slices` (1 row, 19 fields) while `local_registered_executors`
  stays 29; `artifact.py` focused-module budget grows 295→298.
- Wiring regression caught by full discovery: the dispatch insertion had accidentally
  replaced the `local-file-run` elif branch (UnboundLocalError on file admission); the branch
  was restored verbatim from HEAD and `tests.test_local_file_story` passes 14/14 afterwards.
- Validation: focused suites caller-execution 16/16, story-commands 17/17,
  migration-inventory 19/19, coverage-suite 24/24, file-story 14/14 all OK; `py_compile` and
  `compileall` clean; full e2e-runner discovery `Ran 2802 tests ... OK (skipped=225)`.
  Intermediate discovery runs also surfaced the wiring regression above plus the known
  load-dependent judge-sandbox timing flake (`test_scripted_outcomes_are_consumed_in_order`;
  passes isolated: module 17/17 OK, single test OK); after the dispatch fix the
  authoritative discovery rerun is fully green.
- Boundary: independent local-only slice — outside the reviewed case release denominator, the case
  registry and both pilot manifests; `remote_validation=NOT_RUN`, `grants_release_credit=false`,
  binary `auto_build=false` (`cargo build -p bcs --features test-utils --bin bcs-caller-e2e`
  runs explicitly by the owner). No live Singlebox or preprod request was made for this
  slice. The orphaned OpenClaw/engine-adapter uvicorn processes found in this environment
  were unrelated fixture leakage from earlier runs (killed); this slice's child launch is
  session-scoped with kill-group teardown.

## 2026-10-26 increment — owned-local Draft group run lifecycle and reporting (2/3)

- New commands `local-draft-group-run` / `local-draft-group-report` execute the complete
  upstream Draft group Story as black-box children beside one owned BCS runtime.
  `bcs_e2e/local_draft_group_run.py` freezes permissions, the fresh non-symlink state
  directory, the 34-story independence fence, the 17-obligation `LOCAL-DRAFT-GROUP-01`
  binding (Human `39464`, suite `owned-local-draft-group-v1`) and the BCS binary SHA256
  before the frozen Provider/Judge mock child starts on an ephemeral port with a
  ready-file handshake; the binary remains owner-built with `BCS_E2E_TEST_BINARY`
  (`auto_build=false`).
- The requirement proof has no ambient route: the strict Judge never starts, the Story
  child has a 900-second deadline with group-signalling teardown, SIGTERM canonicalises
  to CANCELLED, all four child streams pass the bot-token / provider-admin-token / Bearer
  redaction gate before bounded logs land, and the single marker must prove live HTTP
  assertions including cleanup. Success additionally requires zero observed provider and
  event-webhook requests, an unchanged executor digest / binding / binary, and owned
  runtime disposition STOPPED with returncode 0.
- `bcs_e2e/local_draft_group_reporting.py` is the offline projection: 10 sources, three
  outputs, completion marker `owned-local-draft-group-completion-v1` and 17 required
  assertions; tampering the case, traffic evidence, runtime disposition or a missing child
 receipt produces BLOCKED, and offline rendering inside the state directory is rejected.
  The friend-auth-sync / work-order count surfaces stay disclosed as not independently
  observable instead of being invented.
- Wiring: `migration_inventory.py` exposes a second `local_independent_slices` row
  (`draft_group_slice`, legacy id `collaboration_draft_group_story`, 19 fields) while the
  29-executor release registry is unchanged; the runner CLI registers 44 local-story
  commands; `artifact.py` focused-module budget grows 300→301.
- Handoff-blocking test repair: normalize state-path expectations with `Path.resolve()`
  (macOS `/var` → `/private/var`), add the missing `story_seam(stderr=...)` seam, fix the
  wrapped `story` overrides, and reference the tamper class' own mock launcher. After the
  repair the new suite is fully green (22 pytest cases plus 11 subtests).
- Validation: focused `test_local_draft_group_run`, `test_local_draft_group_contract`,
  `test_local_story_commands`, `test_migration_inventory` and `test_inventory` combined
  passed 57 cases plus 114 subtests; `compileall` clean. Full e2e-runner discovery:
  `Ran 2836 tests in 264.028s ... FAILED (failures=1, skipped=225)`; the single failure was
  the known load-dependent `test_local_judge_sandbox` thread-liveness flake, which passes
  isolated immediately afterwards (`17 passed, 9 subtests passed`). No live Singlebox or
  preprod request was executed for this increment; the slice starts no OpenClaw or
  engine-adapter process of its own.

## 2026-10-26 increment — owned-local Result queue contract and run lifecycle (2/3)

- The already unmapped `story_state_machine_result_queue` backlog parent is now a frozen
  independent slice: `local_result_queue_contract.py` freezes
  `result_queue_story.py` (SHA256 `b672cc7bc0543686426159d8a694389fe621f9c1cbf9557ee1ae1f1cd41d1dca`,
  git `71860eae99c62d7bf1349a0f46562f6395acbf1e`), its `fixed_loop_story.py` base
  (`7e42cff9820a68e9099309d5a313f0f9a9d4814dda66bb7fc89e8aee5dd7aefb`, same git revision) and the
  carrier mock (`fe56c8c2…`) in `result-queue-migration-contract.json`; 18 disjoint
  obligations, single fixed marker
  `Queued result: owner Send, observer Inject, bound context and unique history verified`,
  and the strict argument freeze `--base-url --mock-url --human`.
- New commands `local-result-queue-run` / `local-result-queue-report` run the story as a
  black-box child beside the frozen Provider/Judge mock child and one owned BCS runtime
  (`BCS_E2E_TEST_BINARY`, `auto_build=false`). The completion gate requires the exact
  scheduled-callout boundary - exactly three provider webhook observations, zero
  event-webhook observations and a never-started strict Judge - plus the four-stream
  bot-token/provider-admin-token/Bearer redaction gate, bounded logs, fixed single
  marker, unchanged binding/digest/binary and owned runtime STOPPED with returncode 0;
  SIGTERM canonicalises to CANCELLED and both children use process-group teardown.
- `local_result_queue_reporting.py` projects the ten frozen sources into the three
  canonical reports with 18 required assertions; tamper/timing/unpublished evidence
  produces BLOCKED and offline rendering inside the state directory is rejected.
- Wiring: CLI 44→46 local-story commands; `migration_inventory.py` exposes a third
  `local_independent_slices` row (legacy id `story_state_machine_result_queue`, 19
  fields) while the 29-executor release registry and both pilot manifests stay unchanged;
  the artifact budget grows 301→304 for this slice's contract, run and report modules.
- Validation: contract suite green (marker pattern, raw tamper matrix, source drift,
  anchor/range coverage, fixed-loop base validity); the new run suite is green after
  port-specific denominators (22 cases plus 12 subtests), including scheduled underflow
  and overflow gates; focused suites combined 84 cases plus 128 subtests; `compileall`
  clean. Full e2e-runner discovery: `Ran 2871 tests in 280.250s ... OK (skipped=225)`. No live Singlebox
  or preprod request was executed, and the slice starts no OpenClaw process of its own.

## 2026-10-26 increment — owned-local Nested fixed-loop run lifecycle (2/2)

- The already unmapped `story_nested_fixed_loop_workflows` backlog parent now has its
  executable local slice. `local_nested_fixed_loop_run.py` launches the frozen combined
  Bot/Human Story as one black-box child beside the Provider/Judge mock child and one
  owned BCS runtime. It requires the real product CLI input (`--cli-binary`) and the
  frozen Story `--driver` argument in addition to the owner-supplied BCS binary SHA256.
- Success requires both complete markers:
  `  Nested Bot: 17/15/2 routing, paths, history and stale callback verified` and
  `  Nested Human: three levels, parent isolation, stale reply and outer subtree skip verified`.
  It also requires the post-cleanup zero-traffic boundary (provider/event counters zero
  and the strict Judge never started), the four-stream bot-token/provider-admin-token/Bearer
  redaction gate, unchanged executor digest/binding/Binary/CLI bytes, and owned runtime
  disposition STOPPED with returncode 0. SIGTERM canonicalises to CANCELLED and both children
  use process-group teardown.
- `local_nested_fixed_loop_reporting.py` projects the ten frozen sources into the three
  canonical reports with 16 required assertions; missing, changed or unpublished evidence
  is BLOCKED, and offline rendering inside the state directory is rejected.
- Wiring: CLI 46→48 local-story commands; `migration_inventory.py` exposes a fourth
  `local_independent_slices` row (legacy id `story_nested_fixed_loop_workflows`, 19 fields)
  while the release registry and pilot manifests stay unchanged; the previously reserved
  artifact budget remains 307.
- Validation: `compileall` clean; the focused contract/run/CLI/inventory regression set passed
  `Ran 168 tests ... OK`; full e2e-runner discovery passed
  `Ran 2909 tests in 292.971s ... OK (skipped=225)`. No live Singlebox or preprod request was
  executed, and the slice starts no OpenClaw or engine-adapter process of its own.

## 2026-10-27 clean-rebase preprod admission recheck

On the rebased branch `codex/e2e-case-rewrite-rebased` (`origin/dev` base
`48b23001e2edf63fbedc80acdf1474e5c105ad23`), the offline migration suite and
runner regression had already passed. The first live preprod attempt after that
rebase used the existing r4 remote Profile. The gateway manifest endpoint was
reachable and returned HTTP 200, but the business payload was the ACE
`USER_NOT_LOGIN` envelope rather than the collaboration manifest. The existing
human Cookie file was last refreshed more than twelve hours earlier and the
Profile references only that private `BCS_E2E_HUMAN_COOKIE` environment value.

This is a credential-boundary blocker, not a network or runner-auth failure. No
business resource was created, no observer metadata call was made, and the
expired observer receipts were not renewed or reused. The next live admission
requires a fresh human Cookie in the existing private local file; after that,
rerun HTTP discovery with the same Profile before issuing a new observer
receipt or executing any preprod write validation.

The collaboration-team Story's preprod route was checked separately as well:
`https://bcs-pre.alipay.com/manifest` returned the same business-level
`USER_NOT_LOGIN` envelope, so the expired human credential blocks both the
generic pilot gateway and the legacy collaboration-team product face. The
current r12 collaboration-team artifacts already show 8/8 obligations passing,
and its reviewed prebuilt `bcs-cli` (SHA256
`72fef9d390eacc0e5455daa3d5838c91368c0ec59508342a2f48c7708b4937fe`) remains
available; after the Cookie refresh, rerun that case on the rebased runner
with a fresh Run rather than rebuilding the Rust product locally.

## 2026-10-27 clean-rebase collaboration-team live preprod run

The collaboration-team Story was rerun on the clean rebased branch after the
human Cookie was refreshed. The run used the same r12 collaboration-team
Profile, but with the post-rebase runner and the prebuilt `bcs-cli` binary whose
SHA256 still matches the reviewed binding. The preprod manifest was first
confirmed independently with the refreshed Cookie and returned HTTP 200 with
`env=pre`; this proved both service reachability and authentication before the
write path was opened.

The actual collaboration-team Run then completed all 8 obligations on
`https://bcs-pre.alipay.com`:
`CLI-COLLAB-TEAM.protect-visibility`,
`friend-request-reject`, `group-create-read`, `group-add-member`,
`group-list`, `group-fuse`, `group-complete`, and `group-terminate`.
Driver status and termination steps were executed with the reviewed
`human_cookie_with_owned_driver_bot_token` identity; all child group reads
returned completed/terminated states consistent with the business contract.
The resulting resource disposition remains
`REQUEST_AND_GROUP_HISTORY_RETAINED`.

This live result is bound to the fresh post-rebase Run
`collab-team-preprod-post-rebase-r13`, and it supersedes only the previous
credential-blocked check. It is not a release qualification claim.

## 2026-10-28 preprod network-register browser-context closure

The first post-rebase live attempts for `PUBLIC-NETWORK-REGISTER-01` exposed
two separate preprod facts. The registered-agent read-back used the station
public-v1 projection (`bot_id`/`kind`/`name`, with `created_by` as the Human
staff ID), not the older community `bot_uuid`/`capabilities` projection.
The anonymous create request also reached the deployed Gateway's ACE ingress
before the anonymous public-v1 route: without browser context it returned
HTTP 200 with the platform `USER_NOT_LOGIN` envelope and the case correctly
reported `WriteTransportError`.

The reviewed product contract is unchanged.  `register.bot.create` still has
route auth `register_token`, never sends a Bot Authorization header, requires
HTTP 201 with envelope code 20100, and binds the exact new Bot to the minted
token.  For a preprod Gateway profile the WriteClient now explicitly records
that the Human Cookie and reviewed browser Origin/Referer are only platform
ingress context (`platform_gateway_browser_context_sent=true`); locally the
create remains fully anonymous.  The current station read-back projection is
accepted only when it proves the exact reviewed name and `created_by==user_id`.

Live validation was rerun with the refreshed Cookie and bound browser origin
on run `network-register-preprod-20261028-browser-origin-r4`.  All three
operations passed on the preprod Gateway:

- `register.token.read`: HTTP 200, Human Cookie only.
- `register.bot.create`: HTTP 201/code 20100, platform browser context plus the
  minted register token; no Authorization header.
- `register.bot.readback`: HTTP 200, Human Cookie only, exact station Bot
  projection and exact Human ownership.

The resulting temporary Bot remains retained under
`REGISTERED_TEMPORARY_BOT_RETAINED`; this run is case validation, not release
credit.  The focused remote-register adapter contract passed 17/17 tests and
`compileall` remained clean.


## 2026-10-29 preprod Direct-chat DM asynchronous convergence surgery

The r3 preprod DM leaf replay (evidence root
`~/.codex/bcs-e2e/preprod-live-evidence/20261029-direct-chat-preprod-r3/r3-leaf-replay/`)
established that the station DM ticketer is a true asynchronous queue with rate
limiting: submit acknowledgements can remain pending/queued, the cancel
acknowledgement can precede abort convergence (`cancelled=false` with the Run
still running), and the preliminary ping Run completes naturally ("pong") before
its supplementary cancel. The frozen CLI leaves are one-shot and strictly
ordered, so the preprod tier now converges those pacing conditions through a
case-owned derived-profile WriteClient GET on the reviewed
`/chat/runs/{run_id}` route (PRODUCT_HTTP, legacy-json) with disclosed bounded
budgets; the Singlebox local tier keeps the reviewed synchronous contract
byte-for-byte unchanged and never constructs a convergence HTTP client.

Leaf changes (`direct_chat_cli_case_leaf.py`): `_parse_cancel_output` admits
any boolean `cancelled` together with a legal chat Run state (the station abort
may remain unconfirmed downstream, per the `bcs-cli chat` cancel help);
genuinely invalid shapes still fail closed. `CHAT_RUN_STATES` is unchanged: no
unobserved `cancelling` state was invented.

Adapter changes (`direct_chat_cli_case_run.py`):
`RemoteLocalDirectChatAdapter` gains a convergence profile (deep-cloned case
profile with the three status GET bindings and two cancel bindings bound to the
concrete `/chat/runs/{run_id}` and `/chat/runs/{run_id}/cancel` paths; preprod
trio auth rewritten to `header_env` Cookie/Authorization, with the bearer scheme
recorded only when a driver Bot token actually exists), a per-Run WriteClient
cache, and `_converge_remote` bounded polling. An acknowledgement projection
without `is_terminal` is synthesized non-terminal so the first authoritative
GET corrects it, and the acknowledgement version is disclosed as the
convergence starting point. Deadline exhaustion raises `RunStateError`
carrying run_id/state/is_terminal/wait_reason/polls/budget. `read_main_run`
converges to running (45 s); `cancel_main_run` enforces the cancelled terminal
obligation on the converged projection (120 s, version >= acknowledgement
version) instead of the one-shot acknowledgement; `read_main_terminal_run`
converges only when the one-shot CLI read is not yet the exact cancelled
terminal (30 s); the ping cancel records the asynchronous acknowledgement shape
and `read_ping_terminal_run` converges to a versioned terminal (120 s),
admitting natural completion (response content exactly "pong", disclosed as
`ping_natural_completion` / `ping_cancel_preceeded_by_completion`) or the strict
cancelled projection, and failing closed on any other terminal state. Every
convergence is disclosed in the step data (transport PRODUCT_HTTP, method GET,
path template, operation, deadlines, poll count, version first/last, version
regressions, HTTP status sequence, full HTTP records) while step evidence
transport remains PRODUCT_CLI exactly once per obligation.

Surgery corrections found while authoring the convergence tests:
`ping_lifecycle()` previously returned before its convergence-summary block
(dead code and an unbound `lifecycle` name); it now builds the lifecycle dict
and appends the convergence disclosures, tier-gated so the local Singlebox
lifecycle shape stays unchanged. The ping acknowledgement/terminal disclosure
keys are remote-tier only. `cancel_main_run` now persists
`main_cancel_convergence` on the adapter so the lifecycle summary can disclose
it.

Validation: focused leaf+run suites pass 61 tests / 65 subtests
(`tests/test_direct_chat_cli_case_leaf.py`,
`tests/test_direct_chat_cli_case_run.py`). New
`RemoteDirectChatConvergenceTests` cover the full remote asynchronous chain
(exactly one derived-profile WriteClient per Run, pending -> running -> running
cancel ack -> cancelled terminal convergence with version bookkeeping, all step
evidence still PRODUCT_CLI), ping terminal lag converging on its own Run-scoped
client with natural completion disclosure, running-budget exhaustion BLOCKED,
cancel converging to a non-cancelled terminal BLOCKED (the main obligation is
never loosened), wrong pong content BLOCKED, the local tier executing PASS with
an exploding WriteClient patched in (zero HTTP, no convergence lifecycle keys),
the cookie-only remote derived profile carrying Cookie only, and invalid
convergence budgets failing closed. Full e2e-runner regression parity was
verified against the unpatched rebase head `d6cbd69d3`: identical pre-existing
failure set of 19 entries (browser-context, failure-evidence,
response-correlation x2 plus subfailures, retained admission quota order,
retained file remote, storage targets x5 plus one subfailure) on both trees;
surgical tree 2989 passed / 220 skipped versus baseline 2978 passed / 220
skipped (the +11 are this surgery's new tests). `test_local_judge_sandbox`
tripped once under full-suite ordering on the surgical tree and passes in
isolation and on the full-suite rerun, matching its known flake character.
No credential value is echoed; all fixtures keep the synthetic placeholder
policy.

Live closure remains the separate gate: DM live r4 against the preprod station
with the refreshed trio Cookie/token material and the reviewed convergence
budgets, the ledger rebuild for the retained parent stories, and
`remote_validation=NOT_RUN` / `grants_release_credit=false` stay in force until
that evidence lands.

## 2026-11-11 preprod owned per-role Bot token credentials (collaboration-team + channel CLI)

The preprod branch of these two dual-environment product-CLI stories carried only
the Human Cookie for non-driver roles, so the product CLI's station identity
bootstrap failed on preprod (`Current Bot identity request failed`) whenever a
non-driver role invoked a written leaf, and the whole case reported BLOCKED
before its first obligation. The preprod branch is now symmetric with the owned
Singlebox branch: a Profile may declare `auth.kind`
`human-cookie-with-owned-role-bot-token` with optional per-role
`{role}_token_env` entries. When the referenced environment holds a value, the
CLI context carries that owned Bot token in `session.json` alongside the bound
Human Cookie, exactly like the local tier; declared-but-empty role environments
fail closed to the reviewed cookie-only context. The legacy
`human-cookie-with-owned-driver-bot-token` (collaboration-team) and
`human-cookie` (channel) kinds remain admitted and behave unchanged. Case
reports derive `credential_kind` from the Profile, and the product-CLI output
scrub additionally removes the bound Human Cookie for role-token contexts.
Profile contracts `dual-collaboration-team-cli-profile-v1` and
`dual-channel-cli-profile-v1` are unchanged (auth shape extension only).
`tests/test_collaboration_team*.py` and `tests/test_channel_cli_case_run.py`
cover validator admission/rejection, credential resolution with cookie
fallback, context emission, report mapping, and default-profile compatibility.
Live preprod execution evidence for the acct2 five-Bot roster lands under
`~/.codex/bcs-e2e/preprod-live-evidence/20261111-acct2-a2r5-*`. The station
legacy `/friends` CLI faces rejecting Bot-token identity contexts (and the
401 on `/friends/requests`) are product defects disclosed separately and are
re-checked every regression instead of being waived silently.

## 2026-11-11 acct2 preprod regression closeout: incident error_detail, fuse `:default` normalization, channel divergence evidence

Incident core (WORKSPACE.incident-* family):
- The preprod station deployed a participant-mode authorization tightening
  between 02:03Z and 03:09Z on 2026-11-11: the bearer owner must equal the
  path actor. The QA leg reclaiming a member-fallback token
  (`WriteTransportError: operation workspace_group.participant_mode
  returned HTTP 403`) was runner wiring leftover from the two-Bot r5 era,
  not a product regression; the contract `relationships` entry
  `self-muted-by-owned-bot` was already correct. The acct2 wrapper now
  defaults `BCS_E2E_QA_BOT_TOKEN` to the owned QA Bot token, and the
  rerun (`incident-core-a2r10`) returned PASS 3/3. Probe evidence:
  `~/.codex/bcs-e2e/acct2/probes/20261111-channel-binda2r5/`
  (`incident_qa_identity_probe_summary.json`).
- `execute_workspace_incident_core_case` now carries `error_detail` on the
  failing step and on the case summary; the receipt previously showed
  `error_detail=null` because the case run dropped it. Parity with the
  collaboration-team case run.
- Fixture hygiene: four incident rooms leaked by the a2r6-a2r9
  authorization BLOCKED runs plus two probe-created groups were finalized
  (exact delete + 404 verification) and removed.

Workspace CLI fusion (WORKSPACE.cli-fuse):
- The station returns default-profile perspective owners as the
  three-segment `"<bot>:default"` participant id (bcs-fuse
  src/core/fuse_backed.rs builds `f"{bot_id}:{profile_id}"` with the
  profile id defaulting to `"default"`), so every warm fusion failed the
  runner's two-segment exact compare. The comparator now admits exactly
  the `:default` suffix via `_station_perspective_owner` and still fails
  closed on any other suffix. Member fusion profile registration on the
  station remains a user action; a missing member profile still blocks
  the case honestly.

Channel CLI story #11 (registered product divergence, wire-level closure):
- Preprod legacy `channel bind` returns rc=0 and persists an active
  binding (probe account `cli-channel-probe-a2r5`, binding
  `934ed310-50f3-4b09-934a-26148be37767`, config e2e-client/e2e-robot
  synthetic, unbound rc=0), while the local sim contract requires a
  nonzero bind failure. The runner's `_parse_bind_output` nonzero
  assertion is correct for the reviewed contract; the divergence is
  registered as a preprod legacy-face product gap (no disabled-bridge
  guard), not waived.
- Blocked channel runs leaked one phantom binding each on the bind leg
  (create without cleanup); the acct2 phantoms (`f09bd9b6...`,
  `fbd65af5...`) were unbound (rc=0). Runner gap on record:
  bind-assertion failures retain no transcript (state dir holds three
  JSON files only).

acct2 roster sweep receipts (2026-11-11):
- collab r8 PASS 8/8: story #15 first acct2 full-green under the owned
  per-role token contract `human_cookie_with_owned_role_bot_token`;
  friend request/reject/list legs pass in the correct dual context (the
  earlier "legacy /friends bot-token rejection" was a probe shape issue).
- register r8 PASS (`dual-network-register-run`): story #2 register leaf
  closed; the earlier register-a2 NO_VERDICT was a batchA worker passing
  the unsupported `--case register` (wiring error, not a product defect).
- operator flap: r8/r9 health leg BLOCKED during the ~03:02Z-03:05Z
  `bcs-pre.alipay.com` unhealthy blip (CLI `health` rc=1 body
  `{"status":"unhealthy"}`), operator r10 PASS 13/13 after recovery -
  non-regression.
- Coverage matrix: 17 stories all-green, 1 partially green, 13 blocked
  (structural harness owner-id regex `^\d{8}_[a-z0-9]+:\d+$` vs
  ANT_WORK owners; on-record product defects M6/M11/M12 and the ACE
  gate; owned-local design gates), 3 never executed (#20 subbots, #27
  webhook, #28 slow-judge), #37/#38 not ported for acct2.

`remote_validation=NOT_RUN` and `grants_release_credit=false` stay in
force; the live preprod evidence above is disclosed as receipts, not as
release credit.
