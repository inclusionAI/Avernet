# Service-owned proposal integration

## Ownership and lifecycle

Formal run → Bot single-case analysis → original diagnosis/proposal/evidence storage
→ ClawWeb durable background cause/goal grouping → one shared plan selection for
manual and daily optimization → frozen source-mapped input → TaskGuard complete
candidate → existing review/optional trial/explicit publication → exact-version
effect observation. Generation and review are never deployment or evidence of effect.

New analysis input advertises `issueAggregationSupported=false` and
`issueAggregationOwner=clawweb-service`. The Bot completes its single-case result
without dispatching the old cross-run reason summarizer. Old aggregation read and
completion routes remain compatible for in-flight jobs and historical summaries.
New cause assessments and repair plans are produced together by the server worker;
there is no second Bot aggregation or business-run dispatch in this worker.

Workflow, Host environment, frozen baseline and original content/source versions
bound every projection. Existing issue signatures remain stable. Model work units
may include several issues so shared goals retain many-to-many source references;
work-unit boundaries are scheduling boundaries, not a claim of shared root cause.
Titles/node paths never establish semantic equivalence. Deterministic equal
operations/constraints deduplicate exactly. Model assistance classifies equivalent,
complementary, uncertain and incompatible goals. Scalar setting contradictions
cannot be overridden by model output. Overlap alone is not labeled a proven
business conflict. Unresolved cross-unit overlaps are deferred for integration.

## Durable scheduling and provider protection

`WorkflowAutomationRepository` reuses the existing release-record journal, workflow
locks, leases, CAS state versions and next-at timestamps for `proposal_scan`,
`proposal_group` and `proposal_budget`. No Redis, process-only Promise queue, or
new runtime service is required. The existing Host monitor starts the independent
worker; it does not wait for model work before reconciling repairs/automation.

A durable cursor scans workflows and then 20 work units at a time, resumes after
restart and wraps continuously. It reads already-existing applicable pending
sources, independent of new analyses and page visits. Subsequent source writes and
baseline changes are detected on the continuing scan and enqueue a new content
fingerprint. The same version is idempotent; a two-second initial delay coalesces
bursts. A result is revalidated against current input before persistence. Old jobs
are superseded, never overwrite a newer projection, and do not reopen deployed
sources. Source/read failures advance the scan so one workflow cannot starve others.

Model work uses at most 100 originals and 180 KB per unit. Every source is assigned
a unit; overlarge evidence/individual goals get an explicit failure reason and
remain visible. There is no apparently complete first-page cutoff. The shared
provider journal permits one active request across instances, at most 20 requests
and a conservative 200,000-token budget per minute (UTF-8 input bytes plus 10,000
system/output allowance). Calls abort after 25 seconds. HTTP 429 respects numeric
or date Retry-After and exponential backoff/jitter; transient 5xx/timeout retries
have four attempts. Authentication/billing/config failures block the provider
instead of retrying each pending issue. Edit-authorized retry reopens failed jobs
and the provider gate. Provider configuration changes have a separate cache key.

The Host uses existing analysis API URL/key/model configuration; credentials are
not placed in sources, UI, queue payloads or logs. Missing configuration yields an
unavailable status, not a fallback to a Bot/AIS/business analysis. Reads never call
a model and grouping failures never turn the issue list into an empty result/500.

## Plans, evidence and selection

`RepairInboxItem.aggregation` is an optional display projection: stable plan ID,
content fingerprint, original IDs/content revisions, goal bullets, exact distinct
flow IDs, cause certainty/references, unknowns and original records. Original item
and source rows remain unchanged. A single checkbox expands into original IDs in
the same selection store used by list, drawer and cross-page review. Request bodies
still submit source IDs; the server rereads their state/content and permissions.
Partially selected semantic plans cannot silently add excluded sources. Daily
optimization uses the same memberships and defers incomplete/conflicting plans;
a plan cannot be split across automatic generation batches.

Legacy automatic selection keeps at most five displayed choices, selecting each
plan's originals atomically within 100 originals / 100,000 input bytes. Partial,
conflicting or over-budget plans are deferred whole; other fitting choices remain
eligible. Display projections are excluded from the original-input byte count.

Original suggestions/evidence and cause support/hypothesis/unknown are collapsed.
Only actual selected incompatible goals prompt a concrete choice. Queued,
organizing, retryable, unavailable, stale and uncertain states remain distinct.
Frequencies rank issues; they do not establish a root cause or population incidence.

The frozen input contains accurate baseline deployment/Pack commit/digests,
versioned selected plans, original IDs/content revisions/source refs, original
proposals, user instructions/previous feedback/parent candidate, and cause/fact/
inference/unknown context. Evidence payloads are hydrated from trusted readers;
flow/deployment provenance is separate from the target baseline. Evidence packets
redact credentials/PII, retain original payload digests, inline representative
payloads and index exact duplicates. Distinct causes, versions, boundaries and
unknowns are not collapsed. Missing/incorrect payload references or clipping of
critical evidence block integrated generation. No external-only log ID is treated
as if TaskGuard could retrieve its body. Size failures remain explicit, not silent
truncation or invented complete coverage. Evidence is untrusted data, not authority
to execute tools or expand the repair scope.

Each cited cause event must belong to at least one of that cause's original item
IDs. Evidence-array clipping is detected both inside payloads and during final
context redaction, even without a truncation marker; strict freezing then fails.
Intentional credential-field removal remains redaction rather than clipping.

## Generation contract and migration

Multi-source frozen inputs include `integration.schemaVersion=workflow-proposal-plan/v1`,
`baselineSpecDigest` and plans with original IDs, goals, relation and cause/unknown
context. The Host dispatches `execution.action=integrate_candidate_v1`. The paired
TaskGuard schema requires action/input agreement and rejects unknown versions,
wrong baselines, omitted/duplicate original IDs. Older TaskGuard versions reject
this new action clearly; they cannot silently ignore the plan and sequentially
apply the old full Prompt replacements. Single original inputs without full-file
patches retain `generate_candidate`. Deploy paired producer/consumer revisions before enabling
integrated generation; this change does not build or activate a Snapshot.

### Compare deployment configuration and Git integrity separately

`baseline.specDigest` remains SHA-256 of the exact tagged Git file bytes;
`packCommit` and `packDigest` retain commit/tree integrity. The optional
`baseline.proposalSpecDigest` is canonical JSON SHA-256 of the existing active
deployment's stored spec, hashed unchanged to match analysis input identity.
These identities serve different purposes: analysis refers to the deployed
configuration, while generation reads and edits the frozen Git files. A difference
between those representations does not block drafting or background reconciliation.

New Host adapters provide both identities. Legacy frozen inputs without the
optional field preserve their prior digest interpretation. This additive metadata
does not introduce TaskGuard actions, prompts, input schema or Git verification.
Original `proposal.baseSpecDigest` and evidence are never rewritten. Full-file
`proposal.patches`, even from a single selected original, use the existing integrated
generation action: reconcile the selected goals against the frozen current files,
not direct replacement with old file content. Ordinary single structured goals
retain the existing single-original generation path. Regenerating a failed attempt
applies the same routing without rewriting already-dispatched inputs.
A genuinely different or unknown structured proposal baseline is rejected before
task creation/dispatch with `PROPOSAL_BASELINE_CHANGED` or
`BASELINE_IDENTITY_UNKNOWN`; classification/model output cannot override this.
The earlier automatic historical-reevaluation fallback is removed.

The UI still recognizes `BASELINE_CONTENT_MISMATCH` and the exact legacy
`SOURCE_CHANGED` baseline error from existing
servers without treating it as list mutation. A baseline changing during
preparation still rejects before freezing a new task. Evidence, byte limits,
candidate checks and publication binding retain their existing enforcement.

### Precisely revalidate selected originals

View responses carry an optional server-produced `selectionDigest` for each
original, including aggregation members. It hashes original identity, executable
proposal/instruction and content version, excluding supporting-run additions and
pagination. Create/feedback requests may carry a `selection` map with exactly the
requested original IDs and reviewed digests. The server checks current trusted
content and lifecycle before preparation and again inside the freeze transaction;
the route still enforces workflow edit authorization. Unrelated global
`inputDigest` changes do not invalidate these choices. Legacy callers without this
map retain the strict whole-snapshot check. Committed retries retain their original
request/baseline and do not resolve new inputs or redispatch uncertain execution.

`POST /api/workflow-repairs/selection/query` is a read-only, view-authorized query
for up to 100 original IDs. It returns bounded summaries, current digests, missing
IDs, edit capability and compact current-plan authorization. It does not hydrate
full evidence, materialize items or apply frozen-input byte limits to a read; the
existing response budget still applies. Generation hydrates complete evidence and
rejects oversized/partial frozen inputs before dispatch.

The browser preserves unaffected original IDs and requirements across pages and
refreshes. Only changed, unavailable or lifecycle-blocked choices are removed,
with persistent per-item explanations. New plan members are never auto-selected.
Valid complementary plans retain authorization; changed plans need explicit scope
confirmation. Failed rechecks retain old reviewed values and block generation
until recovery. Awaiting-verification/verified/ineffective protections remain;
an owning unpublished task can continue its own frozen processing original after
the live source disappears.

A single explicitly reviewed original remains its own generation scope, even if
unselected suggestions change or a newer group also contains that original. It
does not implicitly accept the other members or the combined goals. Independent
non-overlapping exact originals likewise do not wait on an unrelated group job.
Multiple overlapping originals still require validated compatible-plan coverage;
real conflicts and unknown relationships are never auto-authorized.

Confirming a subset of an expanded exact-duplicate group retains only the reviewed
raw originals and their own provenance/counts. Unselected equivalent members are
not copied into selection metadata or frozen input. Non-exact complementary plans
continue to require complete reviewed membership and explicit confirmation.

TaskGuard consumes each plan once, exposing original proposals, sources, evidence
packets and integrated goals to the writer. It generates complete file content
against the current candidate, never concatenates or sequentially replaces source
Prompts. Changes are checked against the union of original file/node/setting scope.
Per-goal outcomes and every original source outcome are mandatory. `changed` must
cover all operation indexes of an atomic original; omitted/partial/unknown outcomes
leave the unit unresolved without writing its patches. Pack checks and exact Git
result binding remain in place. Model claims are not semantic/effect proof; the
reviewer still inspects the exact candidate and uncovered checks.

Only actually changed sources covered by the exact successful publication receipt
enter observation. Failed, cancelled, unpublished, not-needed and unresolved sources
keep their existing lifecycle handling. Verified closure still requires the exact
new deployment and the existing evidence/sample threshold; trials stay excluded
from formal statistics/automatic analysis. No automatic rollback is added.

## Local acceptance boundary

Regression suites cover historical backfill/restart/competition, source updates,
provider Retry-After/permanent failures, exact/semantic/complementary/contradictory
or uncertain sources, baseline identity, strict transport, source/evidence refs and
selection. Cross-repository acceptance sends the actual public dispatch envelope
to the actual TaskGuard parser/writer/checker with deterministic model substitutes,
real temporary Git and SQLite, then inspects the Diff and original-source outcomes.
Publication receipts and new-version samples are local simulations only. These
checks do not prove deployed Snapshot support, external provider quality,
MySQL/OceanBase concurrency or real business effects. No real workflow, Bot, AIS,
trial, production configuration, merge or deployment is exercised.

### Version-scoped source goals

The workbench requests `versionScope=current` for the active deployment and
`versionScope=historical` for the folded archive. Omitting the issue API scope
preserves legacy consumers. Selected-ID hydration includes the complete history.

Historical selection declares `targetSnapshotKey` and the exact `historicalItemIds`.
The server verifies source identity and freezes `context.sourceSnapshot`,
`context.targetSnapshot`, and `context.referenceOnly`; it preserves the original
proposal and its digest. Historical sources are goals in the existing v1 integrated
writer, without source reservations or lifecycle updates. Feedback keeps the
task's frozen target. New daily work accepts only the active deployment.

The optional execution-port `integrationVersion` advertises local support for the
existing v1 transport. A Host without this declaration rejects historical generation
before task creation. This flag does not certify the configured AIS Snapshot:
the existing paired-consumer deployment check above still applies. No consumer
upgrade, Snapshot publication, or real model call is part of this implementation.
