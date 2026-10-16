# Issue coverage and read-only repair review

## Issue statistics

All windows use actual run start time, normalize seconds/milliseconds, and
deduplicate by flow ID. Related runs executed the issue's relevant nodes, or
already carry evidence of that issue. Workflow-wide runs are used only when the
issue has no node identity. Missing/invalid run times are excluded and reported.

`IssueWindow.analyzed` counts runs with a valid completed analysis applicable to
the issue's nodes. A node-scoped analysis contributes to that node, never to an
unrelated node or whole-workflow coverage. Unknown findings do not erase the fact
that analysis completed. Additive `complete` counts runs whose latest applicable
analysis also has no unknown evidence; `legacyDiagnosed` separately reports
related runs with legacy node diagnoses and no applicable completed analysis.
Legacy diagnoses are evidence of examination, not proof of complete coverage.
Clients receiving old responses show these new counts as pending, not zero.

The latest applicable analysis is selected by completion time with request-time
fallback, then ID; insertion order does not override effective time. Malformed
latest evidence blocks fallback to an older clean analysis. Invalid source
results do not take down the entire paginated issue workspace; compatible
sources and legacy diagnoses remain readable. Reads retain the existing bounded
two-week statistics window, keyset pages, and exact-ID historical timestamp reads.

The sample hit rate uses only completely analyzed applicable runs. It is labeled
as a sample rate. A population occurrence rate and period trend are available
only when all related runs have complete coverage. Missing analysis is never a
healthy result. Severity continues to come from diagnostic evidence and existing
sorting; the UI adds semantic color plus text, including an explicit unknown.

## Task entry and read-only review

The page header exposes issues, pending optimization tasks and the current/latest
task summary. Task summaries add an optional bounded `title` from the frozen
scope; older responses have a readable item-count fallback. Task IDs remain in
technical details. History is collapsed and independent of issue pagination.
Returning from task detail retains the original selection/filter/page state and
restores the page's scroll position; opening a task does not select new items.

After publication, step navigation reads the frozen scope, candidate Pack Diff,
checks and publication/effect records. It never creates a new revision or invokes
publication. Edit, retry and publish actions continue to use their existing
server authorization and candidate-version checks. The selected candidate's
publication receipt controls its status text; task baseline, published deployment
and current observation deployment are labeled separately.

## Automatic verification

The `node-timeout/v1` verdict is unchanged: a rolling seven-day window needs at
least five completed related-node successes at the accurate effective deployment,
with complete applicable analysis and no original signature. Any explicit
recurrence is ineffective; unknown evidence or failed nodes keep observation
open. Other issue types without a verifier remain observing.

`eligibleSamples` now accurately reports the clean valid samples used by that
rule, rather than all completed nodes. Additive `sampleSummary` reports related
completed nodes, clean samples, missing complete analysis and unsuccessful nodes;
the latter two counts may overlap. Rule, threshold, applicable node/signature,
observation version and evidence link are directly visible. No business runs,
automatic publication or weaker verification thresholds are introduced.


## Additional product review cases

The selected revision owns its error, checks and no-change result. Task-level
execution is explicitly labeled with its latest revision, so later success does
not imply a failed historical revision succeeded. Failure without a candidate is
recovery, not a publish target. A no-change attempt does not require republishing
an older effective candidate.

During filter/sort changes the last issue page remains visible with an updating
label and selection disabled. New proposal previews retain their explicit loading
placeholder; cached pages and previews remain reusable. Global legacy suggestions
are never used as a loading fallback. The separate history view explicitly states
that it does not follow the issue filters.

`sampleHits` is an additive count of affected runs within the complete applicable
sample, making the sample percentage reconcilable as sampleHits/complete. The UI
also shows analyzed/related and complete/related. Partial completed analyses remain
visible without being promoted into a healthy population denominator.

Retained summary text is labeled separately from the latest aggregation attempt.
A not-generated, queued or failed current attempt never labels retained content
as nonexistent. Frozen summary evidence and current issue evidence remain distinct.

Proposal comparison groups actual node/path/op/value changes on the loaded page,
not similar titles. Equal values share a comparison direction, with every original
item, source and checkbox retained. Different values remain alternatives. The UI
states the page/total limit and does not invent a best choice without same-baseline
effect evidence. Existing server proposal identities still consolidate exact patch
sources; no new cross-page merge or automatic recommendation is introduced.

The Diff's optional trailing-whitespace fold is presentation-only. It compares
corresponding removed/added lines, preserves indentation and blank-line changes,
and keeps raw statistics, line numbers, metadata and the complete original hunk.
The default view is the original Diff; the candidate is never rewritten.
