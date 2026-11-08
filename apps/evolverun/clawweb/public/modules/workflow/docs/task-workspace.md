# Task workspace inspection and actions

The task page has three real content views: **问题与方案**, **候选与发布** and
**效果观察**. Selection is browser UI state, not the backend task phase. Tabs use
ARIA tab/panel relationships, roving focus, Arrow/Home/End keys and responsive
three-column navigation. Switching a tab never opens the scope editor or submits
generation, trial, publication or reconciliation requests.

## Selected candidate and latest attempt

One version selector controls all three views. The header names that candidate,
its source (manual or daily automatic), exact publication receipt and, once read,
its observation status. Generation execution state, IDs and errors follow the
selected revision too; missing historical records explicitly show “暂无执行记录”,
never another revision's execution. A separate one-line latest-generation summary
remains visible when viewing an older revision. Viewing published v1 while v2
failed or needed no change retains v1's identity.
An incomplete automatic batch needing no change does not imply the whole round is
publishable. The existing `automaticProgress.publishable` gate remains authoritative.

Manual version selection survives polling; following the latest candidate resumes
only for a new generated/recovered attempt when the user has not pinned a version.
Per-task session storage retains inspection view/version on back/reopen and when
returning from the scope editor. Storage failures do not block inspection. Task
records and version digests remain unchanged. Paged historical records are folded;
there is no second sidebar version picker.

The task detail adds optional `executions`, keyed by revision, from the existing
`ce_steps` records. The history response includes only its page plus the latest
attempt/successful revisions, within the existing response budget. The browser
retains execution metadata with previously loaded revision pages. Legacy
`execution` remains latest-only for existing clients and task recovery; it is never
used as a fallback for a selected historical revision. No schema or execution-state
transition changes are required.

Task HTTP reads reuse the already-read successful revision for release details,
rather than loading the complete frozen history twice. Transport byte limits use
the actual JSON body, not the write-identity canonicalizer. Write digests and the
existing response limit are unchanged. Existing request diagnostics separate
`task_detail` and `release_detail` timing.

Scope adjustment requests only the current candidate page, not all metadata pages
used by the issue browser. Complete frozen choices and requirement/feedback fields
are editable while pending suggestions load; generation still waits for the fresh
read. Response arrival never resets edits or silently selects new suggestions.
Automatic scopes spanning multiple history pages still finish loading their full
original scope before enabling its choices. No persistent cache is introduced.

## Starting another optimization task

After a confirmed publication, **开始下一轮优化** opens the existing problem and
suggestion selection workspace, including while evidence is still collecting or
cannot be read. It remains available when published v1 is retained beside a later
no-change/failed attempt. Viewing or entering selection performs no write. A new
entry clears previous selections, details, requirements and request identities.
The selection workspace and the newly created task retain **返回上一轮观察**.

Submitting uses the existing create endpoint to create an independent task. The
server resolves the latest effective baseline at submission; it does not revise
the published task or inherit its frozen baseline. Previous publication receipts,
observations and evidence remain attached to the previous task. Adopted items
awaiting verification stay unavailable for selection. Existing permissions,
capability, read-failure and idempotency gates still apply.

Source refreshes recheck selected originals instead of clearing the selection.
Unchanged choices and requirements survive unrelated source/evidence updates.
Changed content, unavailable historical sources and lifecycle blockers name the
affected item and required action; that reason survives subsequent refreshes.
Failed rechecks retain old reviewed values and offer a retry. Unknown create
outcomes retain the request identity and require lookup before another submission.
New aggregation members are never selected implicitly: valid plans are retained,
while changed membership/goals need scope confirmation. Old-baseline suggestions
explain that generation reevaluates their original proposals/evidence on the latest
effective Pack, without promising the old edit still fits.

The previous-round link persists together with the exact parent task, child task
and published revision. Reloading that child restores the link. The relationship
is used only within that task pair; ordinary navigation back to the issue list or
opening another task clears it, so unrelated tasks never inherit an old parent.

## Content reduction

- Scope shows original problem/goal titles, selected integrated goals when provided,
  and distinct source run IDs. Unknown recent impact is explicit; source counts
  never become invented recent failure counts. Original proposals, evidence and
  baseline identifiers are folded. Editing requires an explicit existing action.
- Candidate review starts with actual per-source outcomes, static/Mock status and
  uncovered checks. Missing outcomes remain visible. Machine-generated summary
  text and full reports are folded; human summaries, line-level Diff, file
  navigation, Git links and Diff retry remain available.
- Publication actions belong only to the candidate view. The existing one-decision
  confirmation retains affected scope, the full-Diff/check acknowledgment and
  uncovered-effect risk. Trial form, options, execution and protocols are unchanged;
  this page retains its existing entry and candidate-bound results.
- Observation uses only the selected candidate's confirmed deployment receipt.
  An unpublished or uncertain candidate explains the missing evidence and points
  back to candidate review. It does not pretend that scrolling to the publish panel
  is effect observation. No samples, no rule, insufficient evidence, recurrence and
  read failure are distinct. Actual rule thresholds determine sample progress;
  identifiers, rules, full reasoning and audit samples are folded. Aggregate version
  comparison stays a link and is not substituted for an individual repair result.

The current-view action row is inline, so it cannot obscure mobile content. A
published read-only page has no fixed publication footer or permanent safety card.
Necessary publication risks appear at explicit confirmation. Latest failures keep
existing dispatch/result/generation recovery paths and request-ID behavior. A lost
publication response retains its exact request across tab switches; a response from
an older selected revision cannot attach its error to a different candidate.

The browser no longer posts reconciliation on a view timer: background Host
reconciliation remains server-owned, and explicit recovery buttons retain the
original request/deployment identity. Read-only task/observation polling remains.

## Suggestion reads and revision submission

The adjustment editor requests `metadataPage=1&includeTasks=false`: its candidate
page does not need task summaries or their frozen revision bodies. Default callers
retain task metadata. The history/version scope, selection digest, pagination and
original evidence contract do not change; a history-enabled read still constructs
the requested historical source summary, rather than pretending to be a SQL-limited
twenty-row lookup.

Identical concurrent candidate reads share only their pending request for a known
browser principal and query scope; every subscriber gets a separate response object.
Completed/failed requests are removed, so an explicit later refresh stays fresh.
Unknown principals and different users/pages/scopes do not share requests. The
editor depends on its effective history scope, so changing an overridden parent
history flag cannot start the same historical read again.

New revision submission reads selected source evidence once under the existing
workflow transaction/lock and reuses the task snapshot already read for recovery;
it rereads authoritative task state inside the transaction. Automatic chain,
auto-to-manual feedback, source/version/claim checks and request idempotency remain.
Plain manual tasks skip only the automatic-chain history scan whose validator was
already a no-op for a manual predecessor. The response reads the exact submitted
revision, not the complete task history. AIS dispatch still waits for a job receipt;
no new background queue or response-before-dispatch behavior is introduced.

The existing `[workflow-repair] request diagnostic` record now separates
`task_state`, `revision_write`, `evidence_freeze`, `dispatch_materials`, `ais_submit`
and `revision_response` in `stagesMs`. `revision_write` includes nested source and
evidence stages: do not sum nested timings. Logs contain stage timings, not evidence,
credentials or AIS request bodies. Local query-count regression is not proof of
production latency after deployment.

## Validation boundary

Interaction regressions exercise real tab content, keyboard navigation, single
candidate selection, published-v1/latest-v2 states, partial automatic rounds,
read-only inspection, back/reopen, failed reads, request recovery and late responses.
Local browser acceptance uses actual React components and controlled HTTP read
fixtures, at desktop and 390-pixel width. Browser API writes are blocked and audited;
no real model/AIS/Bot/trial/workflow publication or deployment is validation evidence.
No TaskGuard, backend aggregation, trial protocol or version-comparison algorithm
is changed. The view uses optional integrated-plan metadata compatibly.
