# Enable owner-triggered Caller refresh

Set SCHEDULED_CALLER_REFRESH_ENABLED=True on latest GitHub dev. Retain owner-first ordering, sanitized Caller request/result/failure logs, and best-effort isolation. Keep explicit disable tests. Assert the shipped default enables the existing refresh behavior, test architecture and affected callbacks/DI/Passport suites. Rebase the new topic onto fresh origin/dev and create a dev PR. No changes to OCB gitlinks, schedulers, database or SDK. Existing diagnostic events cover this flag activation; do not add duplicate boundary logs.

## Conflict reconciliation

Merge latest dev into the published topic without rewriting history. Retain upstream unrelated changes, restore the True rollout gate and best-effort isolation including metadata/tenant preparation, keep original owner errors propagating, and restore regression tests/contract text for these behaviors. Run callback, Passport, DI and architecture tests.

## Latest dev restoration reconciliation

Upstream45a6c6c6eb restores the False rollout default and conflicts with this topic True default in three places. Keep the topic True code, documentation and default assertion; accept all other upstream updates. Re-run relevant regressions and verify mergeability against the freshly fetched base after normal push.
