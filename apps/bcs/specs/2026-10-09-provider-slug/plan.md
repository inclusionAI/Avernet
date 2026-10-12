# Provider Slug Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist optional Provider slugs and discover basic information by slug.

**Architecture:** Follow the existing HTTP/application/core/repository call chain.
Keep public discovery credential-free and enforce uniqueness in both stores.
Preserve existing Provider files per user instruction.

**Tech Stack:** Rust, Axum, serde, SQLite/MySQL through DbPlugin.

## Global constraints

The approved [spec](spec.md) is authoritative. No Provider file splitting,
global formatter, historical migration edits, new caches, private dependencies,
or credential/endpoint fields in public responses. Work on `codex/provider-slug`.
Existing oversized files remain a recorded limitation; CI exemptions are unchanged.
Worktree hooks are installed through the actual repository entry point
`devops/hook/install_git_hooks.sh`. The follow-up request authorizes commit/push
and a PR to dev, without new verification or local Git hooks.

## Task 1: Public HTTP contract (RED)

Create `crates/adapters/http/bcs-http/tests/provider_slug_contract.rs`.
Use real MemoryProviderStore, ProviderCore and ProviderManagement. Register a
Provider with slug, then request `GET /providers/by-slug/coding-provider` without
Authorization. Assert status 200 and the exact documented response keys. Also
cover auth modes, missing/invalid slugs, duplicates, PATCH rename/rollback/null,
legacy requests and disabled state. Run `cargo test --offline -p bcs-http
--test provider_slug_contract`; expect the new discovery assertions to fail.

## Task 2: Persistence and migrations (RED then GREEN)

Create a shared Memory/SQLite Provider repository conformance harness under
`crates/test-support/bcs-test-support/src/contract/` and run it from
`crates/services/bcs-bot-store/tests/conformance_provider_slug.rs`.
Cover duplicate insert/update, multiple nulls, immediate lookup freshness,
environment isolation and read/write errors. Implement optional ProviderRecord
slug and slug-aware repo methods in the existing contract/store files.
Add `migrations/mysql/032_provider_slug.sql` and
`migrations/sqlite/033_provider_slug.sql`; register SQLite 033 in
`crates/bootstrap/bcs/src/migrations.rs`. Add upgrade/history/repeatability tests.
Run repository and migration tests; preserve historical migration checksums.

## Task 3: Core/application/HTTP implementation (GREEN)

Add credential-free ProviderBasicInfo, core slug validation and additive
slug-aware registration/update methods in existing Provider contracts and core.
ProviderManagement passes slug and projects public lookup through core. Wire
request/response DTOs and `/providers/by-slug/{slug}` in existing HTTP files.
Add nullable slug and protocol_version to authenticated info. Update only fixtures
constructing changed structs or synthetic current-schema Provider tables.
Run `cargo test --offline -p bcs-http --test provider_slug_contract`; all cases
must pass. Also run existing Provider unit/contract tests in affected crates.

## Task 4: Documentation and verification

Update both Provider integration guides and affected crate CONTEXT metadata.
Run affected Rust suites and architecture/protocol checks. Use a disposable real
MySQL server for complete-chain validation when available. Record exact commands,
results and unavailable checks in `validation.md`; check changed source line counts
and `git diff --check`. Review the full diff before completion. Leave changes
reviewable on the feature branch. The follow-up publication request authorizes
commit/push and a PR to dev; no merge is requested.

## Progress

- Spec approved; isolated worktree created from the current HEAD.
- User explicitly deferred existing Provider file splitting.
- Baseline protocol tests started with cached public dependencies.
- Task 1: complete. Six initial HTTP contract cases failed before implementation;
  the complete eight-case suite passes after the final review fix.
- Task 2: complete. Shared Memory/SQLite repository conformance, the 37 migration
  unit tests and the legacy-row upgrade test pass. Historical migrations remain
  unchanged. The real MySQL full-chain test now also runs the Provider harness.
- Task 3: complete. Registration, PATCH, public basic-info discovery and legacy
  DTO defaults are implemented in the existing files.
- Final review: one P2 finding fixed. Invalid admin callback URLs previously
  left a slug reserved/renamed after an error. Both regression tests failed first
  and pass after merging callback validation/config with the core write.
- Task 4: module regression passes 1,255 tests (9 ignored), bootstrap unit tests
  pass 309 (5 ignored), and documentation/gate comparisons are recorded in
  validation.md. The extra whole-workspace discovery/baseline listing was stopped
  after prolonged execution; the relevant conformance suites actually ran and
  passed. Follow-up publication proceeds without rerunning verification.
