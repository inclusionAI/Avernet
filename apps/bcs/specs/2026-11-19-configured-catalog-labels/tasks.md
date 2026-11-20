## 1. Baseline and contract preparation

- [x] 1.1 Refresh authoritative public dev and inspect worktree/dirty state, current migrations and affected source/contracts; keep all work in the task worktree.
- [x] 1.2 Inventory canonical/legacy/named/provider Agent entries, Group create/PATCH/browser aliases, idempotent creation, eventful writes, management views and downstream public API consumers; record compatibility consequences before changing signatures.
- [x] 1.3 Update owning public contracts, DTO/OpenAPI schemas and relevant crate context with the accepted omission table, null rejection, config multiplicity and full-publication semantics without changing auth/envelope contracts.

## 2. Shared policy and config loading

- [x] 2.1 Add named CatalogLabels and immutable dictionary types with explicit allowed_values and allow_multiple for every key; no raw JSON in normalized domain values.
- [x] 2.2 Implement one normalizer/validator: null object rejection, known keys including deletion keys, exact string enums, null/[] removal, deduplication and per-key distinct multiplicity; validate before DB mutation/URL fetch where applicable.
- [x] 2.3 Add standalone environment-independent catalog-labels.toml loading at bootstrap and inject the policy through both Agent and Group application assembly; reject missing/malformed policy and reserved-key collisions.
- [x] 2.4 Ship lifecycle draft/trial/running/offline with allow_multiple=false in runnable public config bundles; update startup fixtures/package manifests and prohibit overlay overrides.
- [x] 2.5 Test generic alternate business keys, multi-valued configuration, empty dictionary, missing boolean/file, malformed enums and all invalid JSON types; prove production code contains no lifecycle-specific branch.

## 3. Additive storage and concurrent updates

- [x] 3.1 Allocate new migration numbers after the current maxima; add both columns in one new migration per dialect without rewriting historical SQL/Rust migrations.
- [x] 3.2 Implement SQL NULL-to-empty decoding and deterministic normalized JSON encoding; propagate malformed persisted data errors; include labels in existing reads, not per-key queries.
- [x] 3.3 Extend Agent registration/update records and atomic writes with labels, revision/identity fences and post-commit cache invalidation; preserve legacy trait entry compatibility without silently dropping new data.
- [x] 3.4 Extend Group Card storage/snapshots with transactional explicit preserve/replace operations, missing-template materialization on explicit writes and memory-store parity.
- [x] 3.5 Cover Group name/context/eventful/idempotent-create/visibility changes and private-to-public transitions; verify no implicit clear or Chat/DM support expansion.
- [x] 3.6 Verify manual/background URL refresh and concurrent label updates never overwrite labels with stale copies; preserve superseded-refresh behavior and durable failure reporting.
- [ ] 3.7 Run full migration-chain upgrades on SQLite and real MySQL/OceanBase targets; verify old binary queries/upserts and old migration runners against expanded schema. Report unavailable targets as NOT_RUN.
- [x] 3.8 Add round-trip/count assertions: no per-label DB lookup, Group snapshot retains existing joined query, Agent/Group writes stay bounded/atomic, Find/list do not introduce per-result N+1 label reads.

## 4. HTTP/application contract

- [x] 4.1 Add Agent register/update/readback support across every supported management entry, including explicit-label presence to distinguish label-only clear from invalid all-empty request.
- [x] 4.2 Add Group create/PATCH/detail labels with omission-preserving PATCH and whole-object replacement; preserve existing authorization and unsupported Group behavior.
- [x] 4.3 Test the complete operation/omission/null/type/dictionary/cardinality matrix through HTTP/application boundaries, including unknown-key null deletion, combined invalid writes and legacy request bodies.
- [x] 4.4 Test register/Get/Find/Update/Refresh normalized readback and legacy NULL/missing-row compatibility; update CLI/schema consumers affected by closed decoding without adding label filtering features.

## 5. Full catalog publication

- [x] 5.1 Assemble full custom-plus-system labels from current persisted snapshots for Agent and eligible Group Cards, with no tombstones or old-key catalog reads.
- [x] 5.2 Notify catalog on label-only changes and retain identity-queued latest-state retries and existing eligibility/delete behavior.
- [x] 5.3 Exercise registration/update/clear/unchanged-refresh/changed-refresh/persistence/restart/retry-to-publication paths; assert payload labels and no extra per-label query/network activity.
- [x] 5.4 Record that Fuse incremental/full-replacement application is externally owned; do not change Fuse server logic or claim downstream stale-key removal from payload tests alone.

## 6. Public validation and downstream handoff

- [x] 6.1 Run affected service-api, policy/config, application, Bot/Group store, HTTP, migration and fusion suites plus relevant architecture gates; record exact commands/counts/gaps, not inferred coverage.
- [ ] 6.2 Test compatibility matrix: old request/new server, new request/old REST server, strict old view decoders, expanded-schema rollback, malformed config and mixed backend routing.
- [x] 6.3 Update owning durable knowledge/navigation with verified implementation results and record spec-to-test coverage; leave deployment status separate.
- [ ] 6.4 When separately authorized, commit/push/merge public change to authoritative GitHub; provide exact implementation SHA, migration/config handoff and tests to the paired OCB change. Do not push internal code or use the internal mirror as a push target.

## 7. Deployment acceptance (separate authorization and evidence)

- [ ] 7.1 Expand target schema and stage policy before new server rollout; verify runner/history compatibility and all-instance policy hashes.
- [ ] 7.2 Upgrade all standalone/public server replicas before enabling label-aware REST writers; verify legacy Agent omission-preservation on every new-server replica.
- [ ] 7.3 Record actual runtime/schema/config versions and write/read/clear/refresh/full-payload smoke evidence; downstream host integration and catalog full-replacement remain separate gates.
- [ ] 7.4 Rehearse rollback with client disablement, retained columns/data/history and documented legacy writer/publication degradation; never perform unrequested destructive down migrations.


## 8. PR #4256 omission-preserving compatibility revision

- [x] 8.1 Adopt review feedback: public and Provider legacy Agent update omission preserves latest labels; explicit objects replace/clear; registration omission remains empty.
- [x] 8.2 Retain presence through delivery/Service API, preserve latest labels on each bounded CAS attempt, fetch once, and keep supplied revisions single-attempt.
- [x] 8.3 Cover omission/explicit clear, deterministic competing SQLite CAS commits, concurrent label writes during URL fetch, and real HTTP readback/publication across canonical aliases and Provider legacy routes.
- [x] 8.4 Fix legacy Group create mapping: catalog-label `InvalidRequest` becomes structured HTTP 400 before Group/event-subscription writes; preserve unrelated management errors as before. Add with/without inline-subscription regression coverage.
- [ ] 8.5 Run fresh local unit/coverage gates with remote thresholds and record pass-rate/coverage denominators plus any platform/environment gaps. Do not push or update PR until separately authorized.
