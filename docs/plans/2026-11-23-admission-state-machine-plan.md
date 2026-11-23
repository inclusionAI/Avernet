# Distributed Admission State Machine Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the shard-wide admission journal plus separate intent/canonical records with one scoped admission document while preserving idempotent retry, content-conflict detection, durable Inbox activation, and the existing schedule/checkpoint split.

**Architecture:** Each `(scope, input_id)` maps to one `admission` document containing the request and its phase, task/session references, Inbox position, and final decision. The document is created with CAS and advanced through bounded JSON-CAS transitions. `schedule:<sid>`, `ckpt:<sid>`, and `inbox:<sid>` remain separate authorities; active/wake remain disposable hints. MVP does not promise recovery for an admission whose document was never created.

**Tech Stack:** Rust, serde JSON, DocumentCAS, ZSet/Stream hints, Tokio tests, existing bcs-dist-store conformance harness.

---

### Task 1: Update the contract and layout vocabulary

**Files:**
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/admission.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/README.md`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/layout.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/README.md`

**Steps:**
1. Define the single-document admission semantics and explicitly remove the global journal/intent maintenance promise.
2. Add `KeyLayout::admission(scope, input_id)` and remove new call sites for `admission_root`, `admission_intent`, and independent canonical keys.
3. Keep `AdmissionMaintenancePort` only where closure/control still needs it, or reduce its methods to no-op/unsupported if no caller remains.
4. Preserve physical per-document and per-entry size validation.
5. Run `cargo check -p bcs-service-api -p bcs-dist-store`.

### Task 2: Replace the Inbox state model

**Files:**
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/state.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/storage.rs`
- Replace/modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/journal.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/producer.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/progress.rs`

**Steps:**
1. Replace `Intent`, `JournalRoot`, and `Canonical` with `AdmissionRecord` and an explicit phase enum.
2. Store the request, digest, session/task identifiers, Inbox position, registration/activation fields, and decision in one record.
3. Implement create-if-absent plus JSON CAS phase transitions; identical retries resume the record, conflicting digests return `ContentConflict`.
4. Remove sequence allocation, root charge accounting, pending maps, pre-canonical release, and canonical materialization.
5. Keep task/spec create-once checks, schedule registration, Inbox append, activation, and hint writes in the same ordered phase repair loop.
6. Ensure a lost response after record creation is repaired by retrying the same InputId.
7. Run the focused admission tests after each compile milestone.

### Task 3: Remove global admission maintenance dependencies

**Files:**
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/maintenance.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/closure.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/scheduling.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/capacity.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/health.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/scan_scheduler.rs`

**Steps:**
1. Remove journal fixed-cut discovery/repair based on global sequence.
2. Keep schedule due scans and checkpoint/session recovery authoritative for Worker recovery.
3. Remove aggregate history/session/inbox budget checks that only served the root ledger.
4. Retain local resource limits, document-size limits, reader/watch limits, and scan IO budgets.
5. Ensure close/reopen code no longer derives a journal cut from a root document.
6. Run store conformance and closure/control tests.

### Task 4: Align Gateway and Worker callers

**Files:**
- Modify: `apps/bcs/crates/application/bcs-app-dist-gateway/src/submit.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-worker/src/admission.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-worker/src/recovery.rs`
- Modify: `apps/bcs/crates/services/bcs-dist-worker/src/runtime.rs`
- Modify: affected worker/store test fixtures.

**Steps:**
1. Keep Gateway request identity and retry behavior unchanged at the API boundary.
2. Make Worker admission repair consume the single admission record and schedule/checkpoint state.
3. Treat absent admission record as ordinary failed admission; do not invent global discovery recovery.
4. Preserve duplicate success and content conflict semantics for an existing record.
5. Run Gateway, worker admission replay, lifecycle, and gateway-chain tests.

### Task 5: Rewrite tests around the MVP contract

**Files:**
- Modify/remove: `apps/bcs/crates/services/bcs-dist-store/tests/admission.rs`
- Modify/remove: `apps/bcs/crates/services/bcs-dist-store/tests/reliable_inbox.rs`
- Modify/remove: `apps/bcs/crates/services/bcs-dist-store/tests/inbox_maintenance.rs`
- Modify/remove: `apps/bcs/crates/services/bcs-dist-store/tests/admission_closure.rs`
- Add/modify: focused single-record admission tests.

**Required cases:**
1. First admission creates one record and reaches Activated/Accepted.
2. Same InputId and digest retries the existing record and returns Duplicate/Accepted as specified.
3. Same InputId with a different digest returns ContentConflict.
4. Lost response after record creation is repaired by retry.
5. Failure before record creation is not promised to recover until the caller retries.
6. Physical document/input limits still reject oversized requests deterministically.
7. Schedule and checkpoint remain separate documents and continue to fence Worker ownership.

### Task 6: Documentation and verification

**Files:**
- Modify: `apps/bcs/crates/services/bcs-dist-store/src/inbox/README.md`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/README.md`
- Add/update: implementation notes under `docs/specs/2026-11-23-*` if needed.

**Steps:**
1. Document the new layout and recovery guarantee precisely.
2. Check all modified source files are below the repository line limit.
3. Run targeted Cargo tests and workspace checks that fit the change.
4. Run `git diff --check` and report any unavailable CI-only validation separately.
