# Issue coverage and read-only repair review

## Issue list facts

Issue cards show diagnostic severity, observed issue counts for the last 24 hours
and seven days, and the latest actual run time. Counts deduplicate affected flow
IDs. Run timestamps normalize seconds/milliseconds; missing/invalid or future
times are excluded, and an unknown latest time is shown explicitly. Analysis
completion time is never substituted for runtime.

List reads fetch timestamps only for flows already associated with visible-scope
issues, in bounded ID batches. They never scan unrelated runs, read node execution
records, or compute coverage, sample rates, population rates or period trends.
Default impact sorting uses severity then recency; the existing `frequency` query
value now sorts by observed seven-day issue-run count and is labeled accordingly.
Filtering and sorting apply to the full version-scoped issue set before pagination.
Retired denominator/rate fields remain null in the response for compatibility,
including previousWeek and trend; null never means zero coverage or healthy runs.

Version association, historical-reference selection and daily evolution scope
remain governed by the versioned-evolution contract. Read projections retain a
10-second bound and invalidate on analysis/diagnosis or deployment changes; a late
run association is re-read after that bound. Timestamp facts are read fresh, and
write preparation always rebuilds authoritative source/version associations.

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

Retained summary text is labeled separately from the latest aggregation attempt.
A not-generated, queued or failed current attempt never labels retained content
as nonexistent. Frozen summary evidence and current issue evidence remain distinct.

Suggestions are selected directly in their original list. Independent changes and
equal-value changes do not create a comparison directory. Only the existing
structural conflict checker over all selected items adds an actionable conflict
notice: the choices, node/setting, differing values and an option to remove one.
Long conflicting values remain available in collapsed details. Parent/child paths,
delete operations and test operations keep their existing semantics. Cross-page
selection, generation blockers and server-side source/Pack validation remain in
place; no title-based merge, recommendation or identity rewrite is introduced.

The Diff's optional trailing-whitespace fold is presentation-only. It compares
corresponding removed/added lines, preserves indentation and blank-line changes,
and keeps raw statistics, line numbers, metadata and the complete original hunk.
The default view is the original Diff; the candidate is never rewritten.
