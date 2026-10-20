# Workflow automation v1

Daily analysis and optional draft generation extend the existing repair workbench.
Publication remains an explicit, authorized review action.

## Configuration and ownership

The management page uses the existing `task_guard_auto_analyze_<workflow hash>`
row in `cm_app_config`. Its `enabled` column is the single switch for failed-run
events and daily scanning. The versioned JSON/YAML policy records `autoDraft`,
`dailyTime`, `Asia/Shanghai`, and an update timestamp. Writes use optimistic
concurrency. Defaults are off; existing environment/legacy failure-event settings
remain compatible, but do not acquire a daily schedule until explicitly saved.
There are no new environment variables or tables.

Avernet owns policy, state, selection, recovery, contracts, and UI. The OCB
composition adapter resolves the current Owner, checks edit permission, invokes
the existing run-analysis starter and Git baseline service, and sends through the
existing notification provider. All automatic generation dispatches recheck the
switch, policy version, and current Owner; analysis additionally checks its lease
token immediately before dispatch.

## Service API and Host Plugin API

`WorkflowAutomationService` exposes trusted `tick` and `onRunTerminal` entries,
status/retry operations, and the final draft authorization guard.
`WorkflowAutomationPorts` defines baseline, Owner, permission, analysis lookup/
start, and notification operations. Analysis results distinguish running,
completed, failed, absent, and uncertain. Notification results distinguish sent,
confirmed rejection, and uncertain. These APIs are separate from HTTP transport.

The existing settings GET/PUT endpoint remains canonical. Full writes include
`enabled`, `autoDraft`, `dailyTime`, and `expectedVersion`; invalid policies return
400 and concurrent writes return 409. `automation-status` requires view access;
`automation-retry` requires edit access. Neither endpoint publishes a candidate.
OCB supplies a verified principal to these routes.

## Durable processing and bounds

The Host's existing reconciliation monitor runs the service. Repair lifecycle
records hold `auto_cycle`, `auto_run`, `auto_draft`, and `auto_notice` journals,
isolated by kind and a workflow task-scope hash. Database locks, CAS versions,
five-minute leases, and dispatch tokens fence competing Hosts and late updates.
This requires the existing repair lifecycle migration, not a new schema.

Missed days coalesce into the latest due Shanghai date. Terminal-run scans page
20 records by modification timestamp and ID, retain a cutoff/cursor, and overlap
the next window by one minute for late arrivals. Run identities deduplicate
analysis. A checkpoint means the scan completed, not that all analysis succeeded;
status separately reports pending, failed, partial, and uncertain coverage.
Initial daily scanning covers the preceding day; retained pending repair issues
remain selectable regardless of that scan window.

Each tick visits at most ten workflows; each workflow processes up to three jobs
per kind. External analysis/generation/notification waits are bounded to 20
seconds. Known analysis failures retry at most three times, the same generation
request at most twice, and confirmed notification rejection at most three times.
Unknown analysis/generation outcomes look up the original identity first. The
legacy notification provider has no idempotency/lookup API: unknown delivery
requires manual verification and is never blindly resent.

Policies without `minimumImpact` retain the legacy first-20/100/five selection.
A switch toggle or old API client does not expand their scope. The management
page labels this compatibility state. Saving execution scope explicitly opts
into `minimumImpact: medium | high` (new form default: medium), stored in the
same configuration row; no environment variable or schema change is needed.

For opted-in policies, the scope is the previous Asia/Shanghai natural day
[00:00,24:00) plus unresolved older items. Run `started_at` determines the
occurrence date; analysis completion only gates evidence availability at the
frozen cutoff. Runs crossing midnight become eligible after completion; late
analyses trigger reevaluation by analysis revision. Each scan also rereads the
whole preceding natural day so a previously excluded today's run is not lost
behind the modification watermark. Historical unhandled evidence is retained.
Only each candidate's own dated diagnoses establish severity, never its issue's
all-time maximum. Unknown impact, unavailable evidence, stale baseline,
conflicts and oversized evidence remain individually recorded with reasons in
the round journal; they do not count as optimized.

Candidate enumeration pages through the entire historical pending inbox. A
round freezes baseline, source digest, item evidence digests, cutoff and all
batch item IDs in the existing auto_draft journal. Every AIS request remains
bounded by MAX_ITEMS=100 and the existing model-input byte contract (64 KiB
reserved for envelope overhead). Batches advance serially as revisions of one
task, with the exact previous validated candidate as parent and the same
original baseline. Prior item reservations persist. The Host checks that later
candidates preserve prior changed operations and runs full Pack checks.
Intermediate candidates cannot publish. One final review covers the full
baseline-to-final diff and all actually changed items; incomplete/failed chains
cannot fall back to an earlier candidate for publication. Review history is
paged and byte bounded independently of the trusted internal complete chain.

The round identity deduplicates the same scope/evidence/baseline within the
saved policy version. Competing Hosts share workflow locks and leases; no new
round is planned while one is progressing. A persisted automatic task awaiting
review, blocked, failed, generating or publishing also pauses new automatic
drafts for that workflow, even after its generation journal is done. A final
no-change batch still pauses new drafts if earlier changes remain reserved for
review. Manual tasks do not trigger this pause. Daily scans and analysis continue;
new evidence stays in the inbox, while the frozen draft is unchanged. Paused
planning does not checkpoint the evaluated scope, so after publication or
cancellation the next tick can reassess accumulated evidence against the active
baseline. Identical scope still follows the existing round deduplication. Disabled or changed policies stop
new dispatch. Requests of unknown outcome reconcile their stable key without
blind replay. A manual retry of confirmed generation failure persists a new
retry key and preserves the exact failed batch; unresolved conflicts and user
cancellation require review rather than automatic retry. Source/baseline drift
stops the round with an explicit reason and retains its frozen record.

Disabling automation stops new dispatches while preserving lookup of dispatched
work. Current Owner permissions and candidate revision are checked again before
notification. Draft notices require a reviewable successful revision and passed
static checks. Notice identities deduplicate by issue evidence or task/revision;
notification failure does not regenerate the draft. Links lead to the existing
workbench, which still authorizes review and publication independently.

Applied changes continue using the existing verification/recurrence lifecycle.
Unselected, not-needed, not-applied and explicitly failed changes remain pending.
Snoozes retain reason, deadline and evidence identity; expiry or materially new
high-impact evidence derives a new episode while preserving the original audit.

## Validation boundary

Service regressions use real SQLite journals and the existing repair/publication
services with controlled external ports. They cover duplicate Hosts, restart,
missed days, late runs, disable-before-dispatch, manual coexistence, conflicts,
partial analysis, notification recovery, and next-round selection after partial
publication. They do not prove production MySQL/OceanBase locking, external AIS
quality, provider delivery, or deployed behavior. Local browser acceptance covers the version comparison card against controlled
API fixtures; real AIS and OceanBase remain unexecuted integration boundaries. No production automation is
enabled by adding this code.

## Version provenance and comparison

See [workflow-version-provenance.md](workflow-version-provenance.md). The existing
success-trend card offers a version comparison tab, backed by
`GET /api/workflows/:id/version-comparison?baseline=N&current=M&days=7`.
The workflow view permission check runs before reading records. The injected
Host DB defines the same environment for both sides. Only actual releases are
selectable; drafts/edits are excluded. Default baseline comes from the trusted
publication record, otherwise the user chooses. Each exact deploy number is a
separate group, including repeated spec versions and rollbacks. Observation
windows have the same elapsed duration after each release, clipped to available
time (up to seven days by default). Success rate uses terminal samples only;
failed includes failed/aborted/cancelled/canceled, matching the existing overview.
Unknown deployment samples are counted separately. Zero samples produce null,
not 0%; differences are percentage points, and observations do not establish
causation. The task summary links the exact two deployment numbers.


After the complete automatic batch chain is validated, an authorized user can
submit human feedback as the next revision of the same task through the existing
manual revision API. The automatic origin remains a task presentation attribute;
subsequent human dispatches use the existing manual execution contract. A pending
human revision of an automatically created task still pauses new automatic tasks.
An identical, checked no-change feedback result retains the prior candidate and
its pending ownership. Incomplete automatic batches cannot be bypassed this way.

### 聚合输入预算与失败诊断

建议聚合以同一来源版本为边界，保留完整目标、操作值及用户约束，仅向模型
发送至多两条代表诊断的关键事实/推断/未知项及有界事件摘录。完整原始建议
仍保留；摘录注明不完整，不能把仅凭截断证据的原因升级为“证据支持”。

工作单元先按修改字段结构排列，再按 100 项和核心 UTF-8 字节预算拆分；摘要读取尚未加载的事件
预留摘录空间，调用前仍校验 180,000 字节上限。模型每次必须覆盖该单元全部
来源，并继续执行冲突及来源引用校验。字节分批不会跨单元额外合并方案；不同
节点的参数不能因标题相似就视作完全相同修改。

`ProposalAggregation.diagnostic` 可包含 `reason / actualBytes / limitBytes /
itemCount`。超限记录 `SCOPE_TOO_LARGE`，模型异常记录既有失败分类；新证据
使工作单元过期记录 `SUPERSEDED`。这些信息写入现有 proposal_group 状态，
并输出 `[workflow-repair] proposal diagnostic` 结构化日志，只含标识与预算，
不记录模型输入、凭据或上游原始错误。页面明确区分已去重、已聚合、等待聚合、
聚合待重试、聚合未完成、尚无当前结果；失败不会阻断用户明确选择原始目标。

核心投影的缓存指纹与扫描版本更新为 core-v1，旧的超限 blocked 结果不会阻止
新投影重新排队。既有请求预算、租约和来源变化核对继续生效。

## Execution record presentation

`AutomationSnapshot.jobs` includes optional `createdAtMs`, `flowId`, `analysisId`,
`complete`, and the existing `taskId`. Missing historical fields do not imply
complete evidence or authorize fabricated links. Run records link to the existing
`/runs/:flowId` view, with `analysisId` when available; draft/notice task records
reuse the repair workspace. Records without a locator expose their recorded ID,
status, reason and attempt count inline. `waiting` analysis jobs await the analysis
result; `done` means analysis ended, and `complete=false` or `PARTIAL_ANALYSIS`
means evidence is incomplete. None of these states establishes problem resolution.
Daily scan progress is separate from failure-event analysis history.

The optional `retryableJobs` is a read-only list using the same recovery selectors
as `retryFailures`, independent of the recent-history limit. Analysis/notice jobs
must be blocked with one of the existing recoverable reasons and no live lease;
the existing 50-record selection limit applies. Drafts must satisfy the current
Owner/policy, batch, original request, failed revision and lease checks. The UI
lists each target and its type before offering retry; missing or empty lists offer
no retry action. Recovery rechecks current conditions; a concurrent state change
can remove eligibility. In-flight and unknown-outcome requests keep reconciling
their original identities. Status reads do not claim jobs or submit business work.

The settings panel distinguishes failure-event enablement from daily scheduling.
“保存设置” submits the existing time/impact policy write; labels do not migrate
or enable a policy on render. “调整建议与要求” opens the existing same-task
revision editor, whose submit is “生成新版本”. Generation retry retains the
selected suggestions, instructions and feedback; `GIT_RESULT_UNAVAILABLE` only
reads the original candidate. Existing automatic-batch and publication guards apply.

## Browsing execution history

`GET /api/task-guard/workflows/:workflowId/automation-records` requires the existing
workflow view permission. It reads history only: no coverage aggregation, policy,
Owner, repair-task detail, or recovery eligibility reads run on this endpoint.
It accepts `period=all|today|7d`, `status=all|pending|dispatching|waiting|retryable|uncertain|done|blocked`,
`limit` (integer 1–20, default 8), and an opaque `cursor` of at most 512 characters.
Invalid values return 400. Response: `{ jobs: AutomationJobSummary[], nextCursor: string | null }`.
No exact total is calculated. The next cursor uses immutable creation time plus
record ID and anchors the date window to the first page's time. “Today” starts at
Shanghai midnight; “7d” includes today and the preceding six Shanghai calendar
days. These are creation dates, not the original workflow run's occurrence date.

Default UI shows eight newest records, regardless of task status. “查看全部记录”
opens a 20-record page with date and status selectors and previous/next navigation.
Changing a filter resets the cursor trail. Collapsing restores the unfiltered
latest eight. Filters do not affect the independent retry-target list or its
eligibility. Failed history reads can be retried without a business write.
Only the first page polls; older pages load on demand and are briefly cached.
Successful task-retry requests invalidate the history cache.

Migration v146 adds a virtual status projection restricted to auto_run, auto_draft
and auto_notice plus two compound indexes on task/kind/time/record ID and
task/kind/status/time/record ID. JSON stays authoritative; historical statuses are
available without rewriting journal bodies. ZDAS uses ALTER TABLE ADD INDEX,
not the dialect's skipped standalone CREATE INDEX. Normal migration DDL authority
is required before the new reader is deployed; a managed runtime without DDL
permission must receive the schema through its existing migration process.
Creating indexes can take time on existing large tables and is a deployment-time
operation, never a request-time operation. No migration was run against a shared
or production database during development.

Each read performs three ordered index-range queries (one per displayed kind),
returning at most limit+1 scalar projections each, then merges that bounded set.
It never loads full request/model-input JSON, sorts the complete history, computes
a total, or uses a deep OFFSET. The index key lengths remain below ZDAS's
767-byte ceiling with utf8mb4. Regression tests seed 20,000 records and inspect
SQLite query plans for both unfiltered and rare-status pages, including cursors.
MySQL/OceanBase live query plans and index build times remain integration checks;
DDL rendering tests do not establish deployed performance.

### Bounded draft-recovery polling

Status polling and recovery use one batched eligibility query against the current
failed revision and its original request. Cancelled tasks, tasks with a newer
revision, stale request IDs, other policy versions and active leases are excluded
before the 50-draft batch limit. The task/revision primary key and v146 status/time
index support the join and ordering. Polling never calls the full workbench task
reader for each historical draft. Owner and current policy authority are still
checked; recovery keeps the journal CAS/lease check and the subsequent generation
checks. After recovering a batch, remaining eligible drafts appear in the next
snapshot. The retry-target list and retry action share this same selector.
