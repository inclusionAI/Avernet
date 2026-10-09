# V1 Group Driver Metadata Implementation Plan

**Goal:** Add shared normal-Group driver name and owner display fields.
**Architecture:** Service API owns additive nullable fields; bcs-app-group resolves
metadata through existing registry contracts; HTTP adapters serialize results.
**Tech Stack:** Rust, serde, async traits, Tokio, Axum.

## Steps

- [x] Add `bcs-app-group/tests/driver_metadata.rs`: isolated in-memory fixture;
  assert serialized public-list driver name and authorized detail owner/name,
  null/missing variants, and unchanged DM shape. Run
  `cargo test -p bcs-app-group --test driver_metadata` and observe missing fields.
- [x] Add `#[serde(default)] pub driver_bot_name: Option<String>` to
  NormalGroupSummary in `bcs-service-api/src/application/v1/group.rs`; add the
  same nullable/defaulted owner fields to CollaborationGroupDetail. Do not skip
  serialization of null. Update all Rust struct initializers.
- [x] Update `bcs-app-group/src/projections.rs`: derive name from existing
  participants; resolve owner with registry.get(driver), nonempty created_by,
  registry.get(human_id). Leave authorization and DM branches untouched.
- [x] Split the oversized `bcs-api-http/tests/group_routes.rs` into existing
  fixture/root and `group_routes/existing.rs`, preserving existing assertions.
  Add `group_routes/driver_metadata.rs` to assert both list routes and shared
  detail responses. Update the two legacy bcs-http contract fixtures.
- [x] Add Service API JSON compatibility tests for fields absent in old
  responses and null serialization. Document the shared response behavior in
  the application/adapter CONTEXT.md files.
- [x] Run `cargo test -p bcs-app-group -p bcs-service-api -p bcs-api-http` and
  the full bcs-http suite; check changed source line counts
  and `git diff --check`. Review metadata-only behavior and no extra list reads.

## Validation results

- RED: `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-group
  --test driver_metadata`: seven expected missing-field failures, one unchanged
  DM test passed before implementation.
- GREEN: same focused command: all eight tests passed after implementation;
  subsequently added empty-Human-name and matching-nameless-driver coverage.
- Final regression: `cargo test --manifest-path apps/bcs/Cargo.toml
  -p bcs-app-group -p bcs-service-api -p bcs-api-http -p bcs-http` exited 0:
  **1060 passed, 0 failed, 0 ignored** (including nine application metadata
  tests, three new type contracts, and two parameterized HTTP metadata tests).
- Existing route tests were moved byte-for-byte into the child module; only
  shared fixtures changed to construct the extended DTOs and populate test
  responses. All added/modified Rust sources are below 1,000 lines.
- `git diff --check` passed. Reviewed contract propagation, legacy owner
  semantics, unchanged DM behavior, and absence of additional list reads.
- bcs-http still emits seven glob re-export warnings in untouched modules;
  these warnings are outside this change.
- Not run: full workspace/Singlebox E2E and live database/load tests. This
  additive response change was validated with all four affected crate suites;
  DB access-cost estimates come from store inspection, not a load benchmark.
- No global formatting or remote publication. Explicit formatting was
  limited to the three new test files, with no recursive/module-wide formatting.
