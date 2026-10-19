# Task workspace inspection and actions

The task page has three real content views: **问题与方案**, **候选与发布** and
**效果观察**. Selection is browser UI state, not the backend task phase. Tabs use
ARIA tab/panel relationships, roving focus, Arrow/Home/End keys and responsive
three-column navigation. Switching a tab never opens the scope editor or submits
generation, trial, publication or reconciliation requests.

## Selected candidate and latest attempt

One version selector controls all three views. The header names that candidate,
its source (manual or daily automatic), exact publication receipt and, once read,
its observation status. Latest-attempt failures and execution records are separate;
viewing published v1 while v2 failed or needed no change retains v1's identity.
An incomplete automatic batch needing no change does not imply the whole round is
publishable. The existing `automaticProgress.publishable` gate remains authoritative.

Manual version selection survives polling; following the latest candidate resumes
only for a new generated/recovered attempt when the user has not pinned a version.
Per-task session storage retains inspection view/version on back/reopen and when
returning from the scope editor. Storage failures do not block inspection. Task
records and version digests remain unchanged. Paged historical records are folded;
there is no second sidebar version picker.

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

## Validation boundary

Interaction regressions exercise real tab content, keyboard navigation, single
candidate selection, published-v1/latest-v2 states, partial automatic rounds,
read-only inspection, back/reopen, failed reads, request recovery and late responses.
Local browser acceptance uses actual React components and controlled HTTP read
fixtures, at desktop and 390-pixel width. Browser API writes are blocked and audited;
no real model/AIS/Bot/trial/workflow publication or deployment is validation evidence.
No TaskGuard, backend aggregation, trial protocol or version-comparison algorithm
is changed. The view uses optional integrated-plan metadata compatibly.
