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

`executionEnvironment` selects `pre` or `prod` in this same policy row, defaulting
to `prod` for existing policies. The management page saves this selector immediately,
even while analysis is off; changing it does not enable analysis or configure a daily
plan. The Host injects its normalized environment into the shared runtime. Only the
selected environment scans, handles failed-run events, dispatches automatic drafts,
or consumes notifications. Within that environment, existing database claims and
leases still arbitrate all instances. Manual generation and result collection remain
available in either environment. Development Hosts do not consume shared automatic
jobs. Notification queries filter environment ownership before their bounded batch
limit, so another environment's backlog cannot starve eligible deliveries.

When pre-production and production share a database, update all instances to this
version before selecting `pre`: old consumers do not understand this field and can
still claim the shared queue. Environment ownership is not a new queue or schema.

Policy discovery skips individually malformed policy/recipient YAML or invalid
settings, logging `[workflow-automation] invalid settings` without raw contents.
Direct settings reads remain strict; invalid settings never imply production
ownership. A notice with invalid settings is deferred for one minute in the existing
queue (`NOTIFICATION_SETTINGS_INVALID`), without sending or increasing attempts,
so a malformed workflow cannot occupy every bounded batch. Interrupted sends still
become uncertain and are never automatically resent. Database failures are not
classified as invalid settings and remain visible to the caller.

`notificationRecipients` is an optional list of up to 20 account IDs. Empty or
absent means the current workflow Owner; a configured list replaces that default.
IDs are trimmed and deduplicated. They are stored in a separate
`workflow_repair_notify_<workflow hash>` row in the existing `cm_app_config` table.
Recipient-only PUTs use `expectedNotificationVersion` and return `notificationVersion`;
they preserve the scheduling policy and its version, so editing recipients cannot
invalidate an in-flight automatic dispatch. Mixed recipient/scheduling writes are
rejected; the UI submits each named section separately. Existing scheduling writes
do not alter recipients. The optional `dailyConfigured` write explicitly preserves an
unconfigured schedule when changing a switch or recipients; older v1 policies
without the field retain their existing configured schedule. Notification-only
edits neither enable analysis nor activate a daily schedule.

The management page separates automatic analysis, the dependent automatic draft
step, and evolution-task notifications. Switches save immediately; the analysis
plan and recipients have separately named save actions. Pending writes show an
immediate progress message, prevent duplicate submissions, and do not save
unrelated local edits. Errors retain local input, and success requires the API
response rather than an optimistic success message. Feedback appears beside the
changed section; success clears after 2.5 seconds, while failures remain visible.
The execution environment stays in a compact row above the configuration sections.
An unconfigured daily plan is labelled as not enabled, and its displayed time is
explicitly a draft rather than an active schedule. With analysis enabled, the
optimization section previews the currently entered time/impact and offers one
explicit action to save that plan and enable automatic drafts in a single existing
versioned policy write. Merely opening the page does not activate anything. With
analysis disabled, the section offers to enable analysis only; already configured
plans retain the existing independent draft toggle and do not save unrelated edits.

## Evolution-task completion notices

Completion delivery runs even if generation reconciliation, effect observation, or
one workflow's automatic planning fails. The deployed configuration supplies proposal
identity without requiring equality with Git's YAML representation; independent Git
commit, tree, and reviewed-file checks are unchanged.

Search `workflow-notification` (without brackets) for notification diagnostics.
Every entry is a single line containing the prefix and JSON fields, so log search
does not separate the keyword from its reason and correlation IDs. Startup emits
`consumer_configured` (automation / notification_only / none); the public Host also
emits `provider_configured` with sender and public-URL presence booleans, never
their values. `worker_started` identifies the first drain invocation and environment.
`batch_ready` records the first eligible count, including zero, and later nonempty
batches; empty polls do not log every 15 seconds. Counts are environment-scoped,
not a claim that the shared database has no notices.

`completion_observed` means the result observer was reached. `enqueue_prepared`
means the queue insert was prepared inside the result transaction, not that the
outer transaction committed or a message was delivered. `enqueue_failed` preserves
the write error. `recipients_queued`, `skipped` with its reason, `blocked`,
`provider_unavailable`, `provider_failed`, `delivery_failed`, and `drain_failed` expose failures and exits;
provider failures distinguish missing configuration, known HTTP rejection (with
status), and unknown transport outcomes without logging raw provider errors;
the last includes the stage (settings_scan / queue_read / delivery). Exceptions
still propagate, and diagnostic failures contain stable reasons rather than raw
provider errors, credentials, or payloads. Historical results are not replayed for
diagnostics. These entries require deploying the updated consumer instances; no
matching keyword in older logs cannot establish whether a notice was queued.

Per-delivery structured fields
include event (`dispatching`, `accepted`, `retryable`, `uncertain`), workflow/task/
revision, notice ID, execution environment, recipient ID, attempts, and provider
receipt when available. `accepted` means the sender accepted the request, not that
the recipient read it. Notification text, workflow inputs, and credentials are not
logged. Unknown transport outcomes retain their existing no-blind-resend behavior.

The shared Host wires `queueRepairCompletion` through the workbench's optional
transactional completion observer. Both manual generation and its retries/feedback
queue one notice per task revision on successful reviewable output, no-change,
failed generation or failed checks. Queue persistence commits with the result;
remote notification delivery is not part of the result transaction. Historical
results are not backfilled. Automatic successful batches keep their existing
coordinator notification, once the complete chain is reviewable; intermediate
batches do not generate repeated success notices. Automatic failures also notify.

The existing monitor drains `auto_notice` independently of automatic-analysis
enablement or daily configuration, including workflows with no saved policy.
Owner resolution remains current for default notices. Configured recipients use
individual deterministic journal records and existing leases, so a confirmed
success is not repeated when another recipient needs a retry. Unknown outcomes
remain unknown, without blind resend. The existing Host notification port and
DingTalk provider are reused; no new notification channel, table, or migration
is introduced. Missing provider configuration remains visible in the existing
notification/retry records and does not trigger AIS again.

Public ClawWeb passes notification-only settings/ports to the shared runtime and
its existing 15-second timer calls `monitor.reconcile()`, including when no AIS
execution adapter is configured. It uses the existing Insight notification sender,
current Owner and workflow permission repositories. This does not install or enable
automatic analysis/draft scheduling. OCB continues to supply full automation ports;
the shared monitor chooses that existing automation consumer or the notification-only
consumer, never both. Shutdown clears the existing timer; no second scheduler is added.

The same queue consumer serves both Hosts. Expanding a configured recipient list
only persists deterministic child records; it does not recursively send them.
Each drain selects at most five due records and processes them concurrently, with
the existing 20-second timeout per provider call. Thus provider waiting consumes
one timeout window per batch, not one window per recipient and parent. This is a
transport-wait bound, not a database-latency guarantee. Pending children survive
restart; successful children and unknown outcomes are not blindly sent again.

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
offers “重试分析”, “重试生成” or “重发通知” only beside the corresponding eligible
blocked record. There is no permanent empty recovery section or mixed bulk button.
`POST automation-retry` accepts optional `{ jobId }`; a target is filtered in SQL
before the existing selection limit and can requeue only that record. Invalid
target parameters return 400 and a no-longer-eligible target returns 409. Older
callers omitting the target retain the existing bounded bulk recovery behavior.
Recovery rechecks current conditions; a concurrent state change
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
It accepts `period=all|today|7d`, `kind=all|analysis|optimization|notification` (omitted means all, for older callers),
`status=all|pending|dispatching|waiting|retryable|uncertain|done|blocked|attention`,
`limit` (integer 1–20, default 8), and an opaque `cursor` of at most 512 characters.
Invalid values return 400. Response: `{ jobs: AutomationJobSummary[], nextCursor: string | null }`.
No exact total is calculated. The next cursor uses immutable creation time plus
record ID and anchors the date window to the first page's time. “Today” starts at
Shanghai midnight; “7d” includes today and the preceding six Shanghai calendar
days. These are creation dates, not the original workflow run's occurrence date.

The settings UI provides separate “查看记录” entries in the “自动分析” and “自动优化”
sections. Records and recovery status are read only after an entry is opened;
the initial settings view requests neither. Kind filtering happens in SQL before
paging, so notification traffic cannot hide real tasks. These are automatic-task
records: optimization corresponds only to `auto_draft`; manual execution is shown
in the corresponding evolution task. Opening an entry displays a compact,
height-limited 20-record list with date/status selectors and previous/next navigation.
Changing a filter resets the cursor trail. Collapsing removes the list; opening
another entry starts at its first unfiltered page. Filters do not change recovery eligibility, but a filtered-out record
leaves no detached action elsewhere on the page. Failed history reads can be retried without a business write.
History pages load on demand and are briefly cached; they do not poll or refresh
on window focus. The refresh button explicitly reloads the current page.
Task-retry responses refresh status and history, including rejected retries. Pending
requests show immediate feedback and prevent duplicate submissions; success means
the selected record was requeued, not that execution completed. Scheduling and
execution concurrency are unchanged.

Successful notification journals remain stored but are not displayed in the main
task list. “查看通知异常” loads only notifications with
`status=attention` (retryable, uncertain or blocked; a query filter, not a new state).
Its eight-record cursor pages expose every exception without a history cutoff.
Task rows show time, result, a shortened target reference and the existing detail
link; full technical IDs and attempt counts are available under “查看详情”. Neither
display filtering nor paging changes notification delivery, retry eligibility or
business execution. The status eligibility list remains read-only; no missing or
unknown eligibility is guessed from an error label alone.

History reads require only the existing v142 journal schema. Status is read from
body_json; no generated column, new index, backfill or DDL permission is required.
The v146 automation-history migration is retired; its version must not be reused.
Environments that previously applied it can keep the unused column/indexes: this
repair does not issue DROP statements or modify migration ledger entries.

Each read uses one query scoped by task_id and the displayed record kinds, using
the existing (task_id, record_kind) index where selected by the database planner.
A limited derived table selects at most limit+1 record IDs and creation times;
primary-key lookups then extract display JSON only for those page records. It never transfers full request/model-input JSON,
computes a total, uses a deep OFFSET or loads all history into the application.
Date/status filtering and creation-time sorting run in the database. Existing
indexes do not cover time or JSON status, so LIMIT bounds returned rows, not rows
examined. Rare-status and deep-history queries can still inspect the workflow's
history and sort qualifying rows. No arbitrary record/date cutoff is imposed;
limits to result coverage require measured benefit before adoption. SQLite
regressions inspect existing-index use; local timings do not establish deployed
MySQL/OceanBase performance.

### Bounded draft-recovery polling

Status polling and recovery use one batched eligibility query against the current
failed revision and its original request. Cancelled tasks, tasks with a newer
revision, stale request IDs, other policy versions and active leases are excluded
before the 50-draft batch limit. The existing task/kind index scopes journal reads;
the task/revision primary key supports excluding newer revisions. MySQL uses
STRAIGHT_JOIN to start with scoped journal rows, avoiding a reverse nested-loop
plan that rescans JSON per failed revision. Status comes from JSON; sorting is
performed by the database. Polling never calls the full workbench task reader for
each historical draft. Owner and current policy authority are still
checked; recovery keeps the journal CAS/lease check and the subsequent generation
checks. After recovering a batch, remaining eligible drafts appear in the next
snapshot. The retry-target list and retry action share this same selector.
