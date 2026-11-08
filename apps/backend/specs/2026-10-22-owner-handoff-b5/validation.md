# B5 batch verification — 2026-10-23

## Results

| Repository/scope | Result | Limit |
| --- | --- | --- |
| Avernet Backend Community full suite | 24,734 passed, 40 skipped; exit 0 | Unit/adapter/contract/endpoint suites, not live production services |
| Avernet line coverage | 90%: 124,851 / 138,781 lines | Community source only; production coverage run precedes the test-helper-only fix |
| Avernet changed executable lines | 93.79%: 151 / 161 | Working-tree diff vs HEAD, not the committed CI merge-base denominator |
| OpenAPI and App Grant surface suite | 2,178 passed, 2 skipped | Followed by the final full rerun after remaining role/lookup fixes |
| Focused Backend failure regressions and handoff cases | 85 passed | Two-worker file distribution; includes the new B5 regressions |
| Legacy cross-owner preview refusal | 1 passed, 1,590 deselected | Preserves existing 403 behavior |
| OCB Passport/Caller/addressing tests | 107 passed | Uses this Avernet working tree on pythonpath with OCB Corp source |
| tcauthmng apply/reissue tests | 13 passed, no failures/errors; BUILD SUCCESS | Two specified test classes; not all Java tests or real ACM authorization |
| Ruff | Changed production files pass in Avernet and OCB | Existing OCB config deprecation warning retained |
| SAST | Backend source scan and OCB Backend source scan pass | Existing unrelated invalid-escape docstring warning retained |
| Whitespace and source size | git diff --check passes for all three repos | Modified production Python max: Avernet 721, OCB 906 lines |

Community full verification uses the repository-supported `-n 4 --dist loadfile`
with AWS metadata discovery disabled. Coverage uses sysmon and temporary
artifacts; the final complete working-tree rerun does not repeat collection. The first run found eight failures: outdated Bot doubles, one
shared-query mock shape, and Legacy preview's existing 403 contract. These were
fixed without changing stored data or weakening permission assertions. The
last production change additionally required declaring its Bot-addressing module
dependencies: the architecture gate caught the omission, and its focused rerun
passed all three checks. Splitting the oversized app-only test file exposed five
references to its shared response helper; that helper now moves with the cases,
and all 60 tests in the file pass. No production behavior was changed by the split. The final App Grant review additionally separated
live personnel ownership from immutable locator fields in management gates,
owned lists, public response mapping and collaboration metadata pagination.
The full suite and new real-DB regressions exercise those changes. Passport
tests were also split by responsibility: all 107 selected OCB tests still pass.
Every added/modified Python file, including tests, is below 1,000 lines.
The final full working-tree run passed all 24,734 tests with 40 existing skips
in 361.59 seconds; coverage was not recollected because only test helpers had
changed since the coverage run. The independently repeated listing file passed
all 60 tests. Production source is identical between those two full runs.

Running the standalone endpoint registry together with selected unrelated test
files in one process was interrupted after shared-state endpoint failures. It is
not treated as a passing check. The complete supported file-distributed suite and
an isolated 403 contract check are the final evidence.

OCB's wider plugin collection could not run in the reused community Python
environment: three collection errors require the corporate Arca/config SDKs
(including OutBoundOperationRuleUpdatedMode). The combined architecture run had
166 passes and five failures on missing sofapy_base.app.config while importing
existing corporate config modules. These checks are NOT marked passed, no test
was skipped/waived to obtain a green result, and the corporate environment/CI
still needs to execute them.

Java's default local Maven 3.5.2/JDK 17 cannot build this Java 21 project. Final
verification used existing Maven 3.6.3/JDK 23 with maven.compiler.proc=full, cleaned
incomplete earlier class files, then obtained the missing JUnit platform runtime
artifacts from the configured Maven repository. Production and test compilation
and all 13 selected tests passed. No build target, test dependency version,
source policy, or CI gate was relaxed. Maven's generated files remain untracked
or ignored; the only tcauthmng source changes are the AAC option and regressions.

## Actual regression contracts

- Persist resources under A; change only the real SQLite Bot Owner to B.
- App Grant reads/creates/revokes by A entity or B current-Owner locator retain A
  in resource keys and preserve real delegators and historical event facts.
- The app's listing checks exact entity liveness, not another Bot's Owner;
  current_owner_id is a separate non-persisted live projection.
- Both management locators resolve once before permission checks; downstream grant operations use entity; HTTP responses display the real current Owner.
- Owned-app listings follow current ownership; collaboration pages use existing
  bot_pk and exact app entity scope, not historical Owner snapshots.
- Final deletion sweep can resolve the soft-deleted Bot; ambiguity stops access.
- Legacy files use stable entity while current Owner governs preview authority.
- Personal/default and pending-identity handoffs are refused by the read-only plan.
- An ACTIVE independent STAFF binding remains independent; no binding plans B.
- PENDING-with-token and wrong credential generation cannot activate runtime.
- Cross-env maintenance reads the ACTIVE binding in the explicit target env;
  a real pre-env binding is not replaced by the current-env Owner fallback.
- Real Hessian DTO serialization carries stable entity, current Owner, optional
  independent executor, unchanged Caller token and explicit cross-env addresses.
- Reissue can prepare B's execution credentials before the Bot row commits B.
- AAC skip-if-allowed requests may still return PENDING, with no AEC/token.

## Remaining release work

No production/pre-release SQL, real new/old Frontend smoke, actual credential
revocation/confirmation, real runtime token injection, Singlebox full-stack or
SecBaaS exercise was performed. Owner transfer remains CLOSED.

B5.2 App Grant and the B5.3 Passport preparation batch are implemented. B5.1 has
implemented the file/resource and lock-capability fixes above, but its complete
Legacy Skill/SkillSet parser/direct-write audit is NOT finished. The source gate
remains open; this batch must not be reported as all-system transfer readiness.
See legacy-audit.md for the concrete remaining paths.

Before deploying OCB changes, confirm the existing execution binding table's
schema using preflight.sql and ensure OCB pins the merged Avernet commit. This
task did not commit/push code or choose/reset the existing OCB submodule change.
