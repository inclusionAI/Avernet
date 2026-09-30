# Issue cause aggregation implementation plan

Goal: summarize latest per-run diagnoses within deterministic workflow/signature groups while preserving distinct causes and source references.

Approved design: the conversation's two-stage grouping and model synthesis design. Execute inline; do not dispatch subagents.

Constraints: Avernet owns public API/storage/UI; ClawMind owns the Bot model invocation. Preserve existing diagnostics and application states. No automatic remediation. Tests must not contact production.

- [x] Add latest-per-run grouping and strict source-bound aggregation contracts; test replacement, multiple causes, invalid citations and empty latest analyses.
- [x] Persist aggregation snapshots using the existing analysis storage with a separate scope/version; expose authenticated input/result and view endpoints. Verify idempotency and stale-input handling through SQLite/API tests.
- [x] Invoke the model in a second phase using the same analysis Bot; preserve single-run success on aggregation failure; retain same-signature diagnoses.
- [x] Render server groups and model causes with source runs; show unique affected-run counts and collapsible single-run diagnoses. Existing suggestion actions remain separate.
- [x] Document contracts, run affected checks/builds and focused regression suites; inspect the final diff. Do not claim deployment or push without evidence.

Verification: Workflow 194 tests, ClawEvolve 276 tests, ClawMind 23 focused tests passed. Shared, Workflow, ClawEvolve and ClawMind builds/checks passed. No live Bot or browser visual validation; no commit, push or deployment.

Implementation boundary discovered: the existing suggestion table is unique on workflow/signature. Competing proposals are retained as source candidates, not published over one another. Multiple independently executable cause suggestions remain a separate schema/lifecycle migration; see the module contract. No standalone manual aggregation dispatch or hierarchical large-input synthesis is included.
