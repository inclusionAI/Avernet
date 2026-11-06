# Runtime read budget: minimal first slice

Owner: Backend / engine_runtime. User authorized implementation on 2026-11-06.

## Problem

Models, messages, sessions, favorites and connection prepare a runtime request
using a management-page Bot lookup. After resolving the scoped address it
re-reads the Bot and enriches it with draft device binding and template data
that these consumers do not use. Online connection logs show reads of the
draft binding before selecting the online binding.

## Solution and compatibility

Inject an engine-runtime-local `RuntimeBotLookup` into the relay and socket
composer. Use the existing BotAddressResolver and repository to return the
resolved row without management enrichment. Retain missing/ambiguous-address
errors and current-Owner-scoped publication PK reads. Keep operator checks,
models' public gate, friendship checks, tenant/environment scope, stage and
instance selection, pagination, ordering, envelopes and failure mapping.

This is an internal implementation/composition change: no Service API, Plugin
API, HTTP schema, configuration, database or credential contract changes.
Existing contracts: `../2026-07-30-openapi-v1-engine-runtime-track-c/spec.md`
and `../2026-08-21-openapi-v1-collaborator-authorization-seam/spec.md`.
No new architectural decision/ADR is required.

Add bounded diagnostic timelines for slow connection and favorite-list reads;
connection's existing thread offload is retained. No private values are added
to timeline markers. Permission checks and credentials are not cached.

IAM and management APIs are untouched. The engine favorite-list fan-out and
models' two enforcement points are deliberately retained in this minimal
slice: changing pagination or access semantics requires separate work.
Friend messages/sessions use ExpertChat, not this relay, and do not gain the
same lookup reduction from this change.

## Validation and rollout

Pin the lightweight query budget, owner/entity addressing, missing/ambiguous
addresses, current-Owner publication reads and production DI wiring. Run
runtime unit, HTTP permission/stage/isolation regression and architecture
tests. Do not claim millisecond improvements from local boundary doubles.

After deployment compare the same Bot, stage, instance and caller: P50/P95,
SQL counts and BaaS counts. `phase_offsets_ms` is emitted only for slow reads;
markers are cumulative offsets, not additive durations. Roll back this change
to restore management enrichment; no data migration is needed.

Local validation: the combined runtime core, HTTP runtime, selected assembled
endpoint, access-log/timing, authorization inventory, module-boundary, Service
API conformance and endpoint-test-policy suites passed (1033 passed, 1 skipped).
Ruff and `git diff --check` passed. The full backend CI/coverage and enterprise
OCB profile were not run; this change has not been pushed or deployed. Actual
preproduction latency reduction remains to be measured after deployment.
