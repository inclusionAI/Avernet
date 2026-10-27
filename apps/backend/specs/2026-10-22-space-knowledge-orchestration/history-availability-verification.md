# History availability repair — 2026-10-27

## Approved change

A compatible retained outbox must not force a capture-disabled deployment to fail
startup or ordinary Space writes. Remove only the environment-wide history SELECT
and rejection. Keep schema failures, delivery-without-capture rejection and the
producer `require_enabled` guard. No flags, DDL, history, routing or enabled-capture
algorithms change. Earlier verification files describe the previous contract;
this availability amendment supersedes their history-rejection expectation.

## Baseline

Release code under test: Avernet `1131e4af8c30e35cfa3b28effcdaba7fb2d2174c`.
The GitHub `dev` validator at `a69cd7f4e8644770ae30d853055942f0b866f6f5` was
independently read through the GitHub connector and has the same original blob
`d9afba0e0babf3c42097fbc1d311ae4d2d7c5496`. This is a release-based local repair,
not a claim that latest-dev or a published pull request was tested.

## Red → green evidence

- New startup matrix on the original validator: **8 failed**, all with the
  observed `space knowledge history exists; keep capture enabled ...` error.
  Cases cover both app owners, active/disabled status and acknowledged/pending.
- The same matrix after removing the guard: **8 passed**; retained rows unchanged.
- New full-application lifespan/HTTP cases on the original validator:
  **2 failed**, with the same startup error, not a missing-table error.
- Final focused lifecycle, full HTTP and source-persistence boundary run:
  **35 passed**. The final HTTP cases also create a Space, add a member and read
  the persisted member count while keeping the original history row unchanged.
- Adjacent knowledge, Space, Work Order, HTTP and provider-contract suite:
  **395 passed**. Includes existing enabled-capture admission, corruption,
  recovery, concurrency and FIFO regressions. The final focused rerun followed
  an additional assertion in the same two HTTP cases; counts overlap.
- Architecture compliance, module boundaries and protocol-contract mapping:
  **12 passed**.
- Ruff check and format check on all 3 changed Python files, and `git diff --check`:
  **passed**.

Commands run from `apps/backend` with `DEPLOY_PROFILE=test` and the checkout's
source/package paths on `PYTHONPATH`, reusing an existing Python 3.12 dependency
venv. No dependencies, thresholds or test selection gates were weakened.

## Remaining risks / validation limits

Explicit capture=false continues ordinary writes without recording knowledge
revocations or protecting retired SQLite identities. This change does not repair
mixed-writer consistency, reconcile missing history or alter task routing.
Availability is no longer used to enforce the coordinated-rollout recommendation.

Evidence uses real temporary SQLite and the assembled local FastAPI lifespan,
not pre-production/prod MySQL/OceanBase, real provider/SSO, a deployed fleet, the
full repository or unified Singlebox. No remote push, merge, rollout or database
operation is represented by these results.
