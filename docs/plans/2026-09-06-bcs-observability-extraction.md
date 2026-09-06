# BCS log observation extraction implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Separate log-only operation observations from OpenTelemetry helpers in both public BCS and its internal overlay.

**Architecture:** `bcs-observability` owns timing, structured log events and task-local correlation data. HTTP and WebSocket tracing integration extracts an existing trace ID and explicitly passes the string into that package; no OpenTelemetry dependency, span creation or metrics recording belongs in the base. Existing GenAI encoders remain in `bcs-telemetry`; bootstrap retains subscriber/exporter ownership. No new plugin runtime is needed for this extraction.

**Tech Stack:** Rust, Tokio task locals, tracing log events, Cargo, existing OpenTelemetry HTTP integration.

---

### Task 1: Pin the dependency and correlation boundary

- Add `src/bcs/scripts/ci/check-observability-deps.sh` and run it before extraction; expect failure because the independent package does not exist.
- Add scope tests alongside `crates/auxiliary/bcs-telemetry/tests/operations.rs`, then move them with the implementation. Validate trace strings without an OpenTelemetry subscriber, nested/concurrent scope isolation, cancellation cleanup and detached propagation.
- Keep the real A2A exporter regression in `crates/adapters/http/bcs-http/tests/request_observations.rs`; exercise the HTTP middleware bridge for both existing span names and assert closure before detached work ends.

### Task 2: Extract and migrate public BCS

- Create `src/bcs/crates/auxiliary/bcs-observability/{Cargo.toml,CONTEXT.md,src/lib.rs,src/operation.rs,tests/operations.rs}`.
- Add `with_trace_id(String, future)` as a data-only async scope. `current_trace_id` reads only this scope. Keep operation log event names, fields, thresholds and outcomes unchanged.
- Restore `bcs-telemetry` to its original GenAI encoding responsibility and dependencies.
- Migrate operation API consumers and Cargo dependencies throughout public BCS; retain telemetry dependencies only for GenAI encoder consumers.
- Update `gateway_trace::observe_request` and the WebSocket Bot response dispatcher to extract the existing trace ID in their adapters and scope the observed work. Preserve existing span creation and TraceContext propagation; cover WebSocket run aliases and missing trace mappings.
- Update context declarations and `src/bcs/docs/observability/request-operation-logging.md` with ownership and migration semantics.

### Task 3: Migrate internal BCS

- Update `/tmp/ocb-bcs-observability/src/bcs-internal` operation callers, workspace path dependency, member manifests, lockfile and observation documentation.
- Consume the matching public revision through the existing `ocb-public` checkout. Keep internal SDKs and business-specific logs in their current crates.

### Task 4: Verify and deliver

- Run `cargo test --offline -p bcs-observability -p bcs-telemetry` in public `src/bcs`.
- Run `cargo test --offline -p bcs-http --test request_observations` and existing gateway span unit tests.
- Run affected public crate unit/contract suites, public workspace compilation, and `bash scripts/ci/arch-check.sh`. Report baseline failures separately; never weaken gates.
- Run internal architecture checks, affected plugin tests and `cargo check --offline --workspace`; redact credential-bearing dependency URLs from captured output.
- Inspect dependency trees and diffs; verify no operation API callers remain in `bcs-telemetry`, no new spans or metrics, and no unrelated formatting.
- Commit and push on the already-authorized feature branches with `--no-verify`; update the existing public PR description with actual validation. Do not deploy or update Yuque.
