# Shared retrieval extraction implementation plan

Approved scope: reuse retrieval and model implementations through search_core;
retire FAISS, use Qdrant Local for local development. Execute inline.

- [x] Extract vector values, embedding/reranker contracts and HTTP clients.
  Keep TC configuration/diagnostics in app; preserve model formatting and defaults.
- [x] Share dense/sparse Qdrant execution. Split the oversized dense module into
  lifecycle/query and mutation modules; retain Worker/Profile logic in TC adapters.
- [x] Add minimal async hybrid retrieval: independent keyword and embedding+dense
  branches, authorized prefilters, RRF and optional reranker; propagate failures.
- [x] Replace local FAISS with Qdrant + SQLite persistence; preserve startup rebuild
  and readiness. Remove FAISS source/dependencies and update provider tests.
  Do not delete/import existing FAISS files automatically; document rebuild.
- [x] Test core/provider contracts, TC and OCB regressions, temporary Qdrant restart,
  filters, deletion and HTTP doubles; compare baseline failures; build and review.

Boundaries: search-core-boundary.md and composition-contract.md. Shared providers
receive explicit config; applications map Profile/Card into neutral text/metadata.
No cache, scheduler, strategy engine or TR added. TC v1 aggregation stays unchanged.
Review: authorization before top-k; errors versus empty results; keyword independence;
restart recovery; resource cleanup; no TC imports in shared contracts/providers.


Execution rulings: keep TC v1 orchestration intact and extract its reusable
providers; implement the neutral pipeline independently of Worker/Profile rules.
Use SQLite snapshots to preserve the existing local save/load entrypoints, with
strict validation before import. Keep fail-closed internal-only FAISS import stubs
for old boundary checks; they contain no FAISS implementation or dependency.
Final review defects were reproduced and fixed; evidence and known limitations
are in retrieval-extraction-validation.md. No push or deployment performed.
