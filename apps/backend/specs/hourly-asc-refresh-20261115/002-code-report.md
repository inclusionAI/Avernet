# Public implementation report

## Existing chain and allowed changes
Owner Passport hot-update installed Owner credentials then synchronously invoked optional Caller refresh. The independent scheduler replaces that automatic trigger; Owner service/personal installation/error handling and manual refresh remain unchanged. Optional BotService constructor/DI refresh port is retained solely for compatibility with existing enterprise assembly. No unrelated HTTP, engine, Cron, authentication or schema changes.

## Implementation
- Added abstract repository contract and concrete parameterized ORM keyset query list_caller_token_refresh_candidates. Current env, serving status(init/success/active), unknown or inclusive UTC due expiry, ascending ids, bounded1..1000page. Service consumers independently validate Bot/engine/physicalbinding.
- Removed Owner post-install Caller trigger and its rollout switch; retained existing Owner diagnostics and return/error behavior.
- Updated Caller domain contract/docs to distinguish manual refresh, independent scheduling and installed-token expiry coordination.

## Logs
caller_refresh_candidates_requested records env/cursor/page-size/cutoff; succeeded records count; failed records safe exception type without raw error data. Query returns only existing instance metadata, never credential values. Owner existing boundary logs remain unchanged; no new outbound calls are introduced by this public change.

## Validation
- Public focused realSQLitecandidate/expiry/repository+Owner service/HTTP callback+actualDI:82passed,0failed/skipped,1.58s.
- Ruff all changed Python files PASS; git diff --check PASS.
- Public architecture suite:426passed,0failed/skipped,62.84s.
- No commit/push/rebase/deployment/external schema writes by this worker.
