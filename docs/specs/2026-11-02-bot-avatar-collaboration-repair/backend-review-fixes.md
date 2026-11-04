# Backend review fixes

## Scope and decisions

Restore the existing design's bounded request and four-item report guarantees.
Keep initial facts/ownership validation errors as preconditions while no write
has started. Once ownership/profile have run, report classified manager facts
errors on the managers item, preserve prior outcomes, and stop the remaining
caller step. Unexpected programming errors and cancellation still propagate.

Bound each threaded facts read with the request's remaining deadline. Cancelling
the await does not kill a database thread: discard its result and start no later
write. A timeout before the existence probe remains a 504 precondition; a timeout
reading an execution item's facts is failed/delivery_timeout, not unknown.

Normalize the managers plugin's missing-target response to the kernel's
`BOT_NOT_FOUND_CODE`. Background synchronization continues to classify it as
REJECTED; repair maps it to collaboration_bot_not_found and stops caller delivery.
Other managers delivery failures retain the existing independent-caller behavior.

The two unrelated failing CI tests will retain their behavioral assertions while
replacing timing assumptions with explicit coordination or controlled clocks.
No production retry, configuration, or CI threshold changes are intended.

## Implementation and verification plan

- [x] Add failing regressions for blocked reads, partial-report preservation,
  and a real managers HTTP 404 followed by zero caller writes.
- [x] Apply remaining budgets to reads; classify execution-phase facts failures;
  normalize missing managers delivery without putting HTTP details into Core.
- [x] Update existing assertions and boundary/contract documentation together.
- [x] Diagnose and fix both CI failures with reproducible scheduling evidence.
- [x] Run focused suites, architecture/SAST, and the backend CI gate; record
  actual results and any unavailable checks in backend-validation.md.
- [x] Review final diff and changed source file lengths, then update the PR branch.

## Validation follow-up

Backend full gate now passes on the final working tree: **29,295 passed, 40
skipped, 6 subtests passed**; line coverage **90.95%**, changed-line coverage
**86.66%**. The review-time clustered `TestOverTheWire` setup errors were
resource contention with a concurrent Singlebox stack and pass both in isolation
(12/12) and in the final serial full gate.

The credentialless live stories would never execute the repair core, so the
coverage assembly adds an acceptance-only ephemeral BCS Provider, registers the
fixture Bot through BCS's Gateway provider channel where a caller config must
pre-exist, and keeps public defaults unchanged. Fresh focused run:
`core=71.11% router=100% plugin=100%`; fresh all-module run:
`singlebox coverage gate passed`, collaboration_repair again
`core=71.11% router=100% plugin=100%`, BCS endpoint/CLI coverage 100%/100%.
The artifact verifier passed on `pr3569-final-v3/reports`, and the runner's
EXIT trap removed the temporary Provider credential.

The earlier BCS story failure came from pending delivery records in a reused
runtime created the previous day. Preserve that evidence and use a new coverage
root; do not modify queue records or weaken queue-draining admission.
