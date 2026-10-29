# Tasks: Knowledge publication review fixes

- [x] 1. Fail invalid publication configuration at root container construction (AC 1).
- [x] 2. Bound source locking and draft-restore binding reads, retain fences (AC 6–7).
- [x] 3. Budget complete ext and freeze via staged upload + revalidated admission; cover all status write entrances (AC 3–5).
- [x] 4. Require observed completion in opted-in shared write path and update callers/compatibility docs (AC 2–3).
- [x] 5. Run adjacent suites, architecture/lint, independent review and record evidence (AC 1–8).

## Groups
- A: Tasks 1–2, startup and bounded persistence.
- B: Tasks 3–4, artifact admission and observed completion.
- C: Task 5, verification and closure.

## Failed Online Restart Retry
- [x] Reproduce enabled-policy failure via real failure observation/retry.
- [x] Add restricted recovery and ledger/status/ext/env fences; retain generic success gates.
- [x] Negative/race/rollback/config and affected regression suites; independent delta review. Original-PR delivery uses these verified commits.

## Legacy-Owner draft restore
- [x] Permanently reproduce supported native PK legacy-Owner restore under enabled/disabled policy.
- [x] Separate native source lock from knowledge trust; preserve all non-Owner guards and replay safety.
- [x] Run focused/full Backend CI and independent delta review; prepare the original PR update with exact-Head evidence.
