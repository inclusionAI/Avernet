# Validation — 2026-09-28

## Verdict

TC implementation and automated regression coverage are ready for review, **not
an unconditional merge/production-enable approval**. Delivery defaults to OFF.
The unified Singlebox gate did not pass; real ECB integration and deployment
MySQL validation remain external release gates. At the close of the 2026-09-28
validation run, no commit, push or PR had been made. On 2026-09-29 the user
authorized commit/push for repository CI; PR creation remains with the user.

Base: `cd36acdfc48eec0597126b2cdb1197f44a45d8f7` (`origin/dev` re-fetched at final
verification; fast-forwarded from initial `3c3786ace`, with no overlapping
feature paths). Working branch: `codex/tc-session-resource-withdrawal`.

## Executed checks

| Check | Result | Qualification |
|---|---|---|
| Backend community full suite, pytest-xdist 4 workers | **20,131 passed, 43 skipped**, 563 warnings | Includes architecture and contract tests; skips are not passes |
| Canonical changed-line coverage gate | **98.64%, 364/369 executable added lines**, minimum 80% | Temporary Git tree includes new files; actual Git index untouched |
| Final architecture + focused feature/regression run | **472 passed** | Re-run after final source formatting; includes all Backend architecture tests |
| Isolated foundation-stage regression | **135 passed**; architecture **314 passed** | Archive of stage 1 tree; all 1,813 loaded community modules resolved from that archive, not the original editable checkout |
| Ruff E4/E7/E9/F, all 24 changed/new Python files | Passed | No blanket reformat of existing large files |
| Ruff format, all 18 new Python files | Passed | Existing files retain surrounding style |
| Canonical changed-file Python SAST wrapper | Exit 0 with cached flake8 7.3.0 | Dependency download timed out; offline cache used. Company `antflake`/FLA rules are unavailable locally; this is not proof of those rules |
| Canonical plaintext-secret scanner | Passed | No allowlist, rule weakening, or credential-bearing fixtures introduced |
| File-size and diff whitespace | Passed | Largest changed source 656 lines; largest changed test 888, all below 1,000 |
| Unified Singlebox, focused `tc_file_upload_integrations` | **Failed overall** | Details below; not counted as withdrawal end-to-end evidence |

The coverage checker reports 20,174 JUnit cases as 100% pass-rate because its
existing calculation includes skipped cases. The actual pytest outcome above
(20,131 passed / 43 skipped) is the authoritative human-facing count.

## Test coverage and limitations

Tests were added before each implementation slice; initial failures were missing
new models/worker/config/operator adapter, followed by green targeted runs.

- File-backed SQLite with independent connections: all resource states and three
  single-chat scopes, denied/missing/historical-deleted resources, group/unknown
  exclusion, concurrent repeated delete, restart persistence, insert-failure
  rollback, tenant isolation, claim competition, expired leases and stale finish.
- Worker: valid intake receipt, retry/permanent failure classification, capped
  backoff, retry/crash budget, DB write failure propagation, lease loss, paused
  startup, idempotent startup, storage-error recovery and shutdown drain.
- Plugin v1 consumer contract through production DI world seam; explicit test
  fake versus production HTTP; strict identity, boolean and receipt states.
- HTTP: precise request fields, bearer header and timeout, status/error matrix,
  non-JSON/204/mismatched receipts, redacted errors. A real loopback HTTP server
  and HttpxClient prove 307 does not follow redirects or forward credentials.
- Strict deployment config/unknown keys/secret failures; operator scope, mandatory
  replay audit/CAS/confirmation, correct process exit codes and application DI.
- Formal and legacy HTTP delete entrypoints: personal/friend chat and unfinished
  resources, duplicate delete, wrong owner, and injected DB write failure causes
  500 plus full rollback. Existing deleted-terminal callback regression remains
  green. Separate upload IDs sharing content retain independent TC events.

These tests do not establish ECB intake durability, credential-to-tenant
binding, tombstones, no resurrection, reference filtering or shared-content
behavior. They also do not replace a real UI run or MySQL transaction/DDL test.
Acceptance criteria with a cross-system component remain open in `spec.md`.

## Unified Singlebox evidence

Executed the official runner with `--module tc_file_upload_integrations` and an
external coverage directory. It still runs the shared BCS story suite.

1. Initial startup failed in BCSFuse because inherited `LOG_FORMAT=json` is not a
   valid Python percent logging format. Retry only unset that variable for the
   command; no service code or gate configuration was changed.
2. Retry started the stack. The existing TC upload-integration acceptance test
   passed (1 test); its existing coverage denominators were Core **80.13%**,
   Router **100%**, Plugin **100%**. These are upload-integration regression
   numbers, not coverage claims for the new withdrawal module.
3. During a prolonged BCN npm dependency install, the owned npm subprocess was
   sent SIGTERM; the runner continued. This attempt must not be characterized as
   an undisturbed or independently reproducible clean environment run.
4. BCS stories failed with bots not connected; LLVM tooling (`llvm-tools-preview`
   or LLVM_COV/LLVM_PROFDATA) was unavailable. Endpoint coverage 149/156 and CLI
   53/54 also failed their 100% gates. The required BCS `cobertura.xml` was absent,
   so artifact verification failed. No thresholds were relaxed.
5. Runner cleanup stopped the shared stack. Its BCS log-retention task also
   affected pre-existing local logs, and the scripts recreated local Backend/BaaS
   SQLite runtime databases. These side effects were disclosed to the user and
   recorded outside the repository. Further shared-stack runs require isolated
   runtime databases **and log directories**; they were not retried here.

Before claiming merge-ready, re-run the unmodified required Singlebox gate in a
properly provisioned and isolated environment, then run its artifact verifier.
Do not repair unrelated group-chat code as part of this single-chat change.

## Reproduction

From `src/backend`, using its Python 3.12 environment:

```bash
.venv/bin/python -m pytest tests/community -q -n 4 \
  --cov=agentclaw.community --cov-report=xml:/tmp/withdrawal-coverage.xml \
  --junitxml=/tmp/withdrawal-junit.xml
```

After the intended change is committed (or using review-only tree objects that
include new files), run the repository's `scripts/ci/report_check.py` with the
JUnit/XML paths, `--source-root src/backend/src`, the correct base/head and
`--min-change-line-coverage 80`. Run `scripts/ci/check_secrets.py` on the same
range. The normal pre-push hook only sees committed changes, not this worktree.

Use `scripts/ci/python_sast_local.sh src/backend 1 --base <base> --head <head>`
with the repository-supported SAST toolchain. Local offline fallback results do
not waive the normal CI toolchain. Do not use `--no-verify`.

## Submission sizing

Source, SQL and tests add **1,859** lines (docs excluded), beyond the selected
workflow's 1,500-line per-submission budget. Two review patches are prepared:
**1,477** added non-doc lines for the delivery foundation and **382** for
single-chat wiring/operator tooling and additional qualification tests.
See `submission-plan.md`. Splitting commits while keeping one oversized PR
would not satisfy the PR-range budget.
