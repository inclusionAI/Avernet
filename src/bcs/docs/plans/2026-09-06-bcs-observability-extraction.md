# BCS log observation extraction implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Separate log-only operation observations from OpenTelemetry helpers in both public BCS and its internal overlay.

**Architecture:** `bcs-observability` owns timing, structured log events and task-local correlation data. Log correlation uses request IDs and parent/child operation IDs only. No distributed trace ID acquisition, propagation, log field, OpenTelemetry dependency, span creation or metrics recording belongs in the base. Existing GenAI encoders remain in `bcs-telemetry`; bootstrap retains subscriber/exporter ownership. No new plugin runtime is needed for this extraction.

**Tech Stack:** Rust, Tokio task locals, tracing log events, Cargo, existing OpenTelemetry HTTP integration.

---

### Task 1: Pin the dependency and correlation boundary

- Add `src/bcs/scripts/ci/check-observability-deps.sh` and run it before extraction; expect failure because the independent package does not exist.
- Add scope tests alongside `crates/auxiliary/bcs-telemetry/tests/operations.rs`, then move them with the implementation. Validate request/operation correlation without a tracing SDK, nested/concurrent request scope isolation, cancellation cleanup and detached propagation. Assert observation records omit trace IDs even when an existing OpenTelemetry span is active.
- Keep the real A2A exporter regression in `crates/adapters/http/bcs-http/tests/request_observations.rs`; exercise the HTTP middleware bridge for both existing span names and assert closure before detached work ends.

### Task 2: Extract and migrate public BCS

- Create `src/bcs/crates/auxiliary/bcs-observability/{Cargo.toml,CONTEXT.md,src/lib.rs,src/operation.rs,tests/operations.rs}`.
- Remove the temporary trace-ID helper APIs, task-local trace scope and added log fields. Retain request/operation contexts, log event names, thresholds and outcomes.
- Restore `bcs-telemetry` to its original GenAI encoding responsibility and dependencies.
- Migrate operation API consumers and Cargo dependencies throughout public BCS; retain telemetry dependencies only for GenAI encoder consumers.
- Remove added trace-ID extraction/injection from `gateway_trace::observe_request` and the WebSocket Bot response dispatcher. Preserve existing span creation, parent context, span attributes and downstream TraceContext propagation; cover WebSocket run aliases and missing trace mappings.
- Update context declarations and `src/bcs/docs/observability/request-operation-logging.md` with ownership and migration semantics.

### Task 3: Migrate internal BCS

- Update internal `src/bcs-internal` operation callers and observation documentation. Remove the added OpenTelemetry-derived trace-ID fields from ZDAS, ZCache and AgentPass logs while retaining request IDs and timing evidence; preserve existing dependency protocol tracing fields.
- Consume the matching public revision through the existing `ocb-public` checkout. Keep internal SDKs and business-specific logs in their current crates.

### Task 4: Verify and deliver

- Run `cargo test --offline -p bcs-observability -p bcs-telemetry` in public `src/bcs`.
- Run `cargo test --offline -p bcs-http --test request_observations` and existing gateway span unit tests.
- Run affected public crate unit/contract suites, public workspace compilation, and `bash scripts/ci/arch-check.sh`. Report baseline failures separately; never weaken gates.
- Run internal architecture checks, affected plugin tests and `cargo check --offline --workspace`; redact credential-bearing dependency URLs from captured output.
- Inspect dependency trees and diffs; verify no operation API callers remain in `bcs-telemetry`, no log trace-ID scope/SDK extraction remains, and no new spans, metrics or unrelated formatting are introduced.
- Commit and push on the already-authorized feature branches with `--no-verify`; update the existing public PR description with actual validation. Do not deploy or update Yuque.
