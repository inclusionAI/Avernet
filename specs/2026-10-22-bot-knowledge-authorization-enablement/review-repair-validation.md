# PR #3087 review repair — local validation (2026-10-25)

## Fixed boundary

- Refreshed PR #3087 and comments before editing. Head remains
  `d2405e2ee21022be38d55b413502ab8081914984`; base/merge-base is
  `980bc488395c7e626eafad704432b4237dd4f5b1`, targeting `dev`.
- Isolated branch: `codex/pr3087-review-repair`. The primary checkout and the
  PR author's existing worktree were not edited. No commit/push, PR comment,
  approval, merge, deployment or production DDL was performed.
- The source import was checked: tests load this repair worktree's `store.py`,
  not the older editable install. Final runs use this worktree's Python 3.12 venv.

## Changes

1. **Tenant-local default draft guard** — before latest-version ordering, prove
   source Bot tenant/env using a correlated primary-key EXISTS. Preserve old
   source PKs when retained source rows prove ownership. A publication whose
   source was hard-deleted has no trustworthy tenant attribution and fails closed;
   it must not reveal an older draft by being silently dropped. Both admission
   and the locked mutation recheck use this rule. Existing draft indexes remain
   required; SQLite EXPLAIN regressions also reject full `ac_bots` scans.
2. **Native Personal Editor semantics** — Personal Owners/explicit Editors do
   not require Space-member rows. Team Owner/actor ACTIVE membership remains
   required. Space deletion and explicit Editor/draft changes are checked again
   in the mutation transaction, including Personal Editor removal after catalog I/O.
3. **Singlebox integration** — 3 real HTTP/DB stories, exact-scope local catalog
   fixture selected only in SINGLEBOX composition, manifest registration, all Core
   paths, both Router endpoints, and both declared Plugin API methods. Existing
   framework report tests retain exact module-list checking; their stale expectation
   also now reflects HEAD's existing tc_file_upload_integrations registration and
   resources pending status. No existing production denominator/gate is removed.

The demonstrated defect concerns saved selection edits only. This work does not
claim a platform authorization bypass or a knowledge-content leak, nor change
platform grants, MCP headers, publication lifecycle or schema.

## Red/green and current tests

- Before the fix, new controlled regressions: **4 failed / 7 passed** — both
  cross-tenant draft-order directions, orphan lineage, and Personal explicit Editor.
- Current Bot knowledge core suite: **37 passed**, including original scope/CAS,
  contention/index tests plus the new authorization regressions.
- Focused core/catalog contracts/HTTP endpoint+flow/DI fixture/two-phase boot and
  adjacent architecture selection: **270 passed** (overlaps the suites here).
- Complete community architecture directory: **361 passed**. The initial missing
  local Plugin API dependency declaration was fixed in the module README and the
  architecture suite rerun successfully.
- Singlebox manifest/report/artifact/workflow infrastructure tests: **31 passed**.
- Three live stories against a separate real Backend process on loopback port
  18888: **3 passed**, rerun after the final correlated-EXISTS change. Only the
  external catalog is doubled; requests use real HTTP, auth, services and SQLite.
- Ruff checks on all changed/new Python; formatting on changed production/new
  Python files; shell syntax; manifest validation; `git diff --check`: passed.
  Existing unrelated formatting in older tests was not swept into this repair.
- All changed/new source files are below 1,000 lines (largest changed shell: 644;
  largest changed Python: 567). No size allowlist or waiver added.

## Measured module coverage — isolated Backend, not unified stack

Final raw coverage is under the ignored local directory
`singlebox/.dependencies/coverage/pr3087-backend-final/`:

- `backend.coverage`, `backend-coverage.json`
- `raw/backend/router_hits.jsonl`
- `bot-knowledge-module-metrics.json` (explicitly labelled Backend-only)

Using the existing module reporter's analysis functions and the real process
artifacts, without creating a fabricated unified `summary.json`:

| Denominator | Covered | Measured threshold |
| --- | --- | --- |
| All production Bot knowledge Core | 153/175 statements | 87.43% |
| Complete Router API | 2/2 | 100% |
| Complete declared catalog Plugin API | 1/2 | 50% |

`list_in_space` has no production consumer in this feature. It stays in the
Plugin denominator as **missing**; tests do not call it directly merely to inflate
coverage. The enabled runtime consumer hits `list_authorized`. Shared-stack
interference and canonical artifacts have not been proven by these measurements.

## Unified Singlebox attempts and actual blocker

Both commands used the unmodified real runner/stack behavior (only the new module
and fixture are registered):

```bash
bash singlebox/ci/singlebox_coverage.sh --module bot_knowledge \
  --coverage-root "$PWD/singlebox/.dependencies/coverage/pr3087-focused"
bash singlebox/ci/singlebox_coverage.sh \
  --coverage-root "$PWD/singlebox/.dependencies/coverage/pr3087-all"
```

- First focused attempt: setup failed at BCSFuse build requirements because
  fetching `https://pypi.org/simple/wheel/` failed the configured SOCKS handshake.
- Retried without HTTP(S)/ALL proxy variables. Backend dependencies and BCS panel
  setup succeeded, but BCSFuse wheel downloads (`numpy`, `mysql-connector-python`,
  `grpcio`) stopped making progress. Stopped only this task's resolver after
  about six minutes; the runner's cleanup trap ran.
- All-module attempt selected `bot_knowledge` plus every existing registered
  module and their acceptance targets. With proxies removed and bounded HTTP
  timeout/retries it again stalled at the same BCSFuse downloads. Stopped only
  this task's resolver after about five minutes; cleanup ran. Neither run reached
  shared-stack acceptance or produced valid final coverage artifacts.
- Ran `verify_singlebox_coverage_artifacts.py --reports-dir
  singlebox/.dependencies/coverage/pr3087-all/reports`: **exit 1**, correctly
  reporting missing canonical summary/acceptance/Backend/BaaS/BCS artifacts.
  No gate was weakened and no passing summary was manufactured.

Local logs (not tracked): `/tmp/pr3087-singlebox-focused.log`,
`/tmp/pr3087-singlebox-focused-retry.log`, `/tmp/pr3087-singlebox-all.log`,
`/tmp/pr3087-artifact-verifier.log`, `/tmp/pr3087-backend-final-tests.log`,
`/tmp/pr3087-validation-delivery.log`, `/tmp/pr3087-architecture-final.log`,
`/tmp/pr3087-singlebox-infra-tests-final.log`.

## Remaining verification / delivery

- Run focused and then all-module Singlebox to completion with working public
  dependency downloads, then verify canonical artifacts. The integration gap is
  implemented, but its full shared-stack closure remains **unverified**.
- Full repository tests, real MySQL/OceanBase locking/query plans, remote catalog
  and deployment/migration/runtime platform evidence were not exercised.
- Hard-deleted/unattributable legacy sources intentionally require authoritative
  recovery evidence rather than automatic current-PK rebinding.
- User decides whether to commit/push and update the existing PR after reviewing
  this local patch and the unresolved shared-stack validation boundary.
