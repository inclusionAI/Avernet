# Catalog Search read budget

Owner: Backend bot_public. Status: implementation authorised by the user.

## Problem

Production trace `2182b92617942268360393883ecf0e` spent 7706.5 ms returning
24 results from 745 candidates: eight serial BCS reads took 6257.9 ms.
Exact totals require intersecting BCS visibility/order with Backend publication
availability; returning the first upstream page or its unfiltered total is wrong.

## Compatibility boundary

- Preserve request/response fields, BCS order and viewer filters, exact filtered
  totals, personal/native Bot inclusion, and existing service-publication checks.
- Read the first batch normally, then allow at most two outstanding page reads
  per request, merge in page order, and continue following observed totals.
- Copy tenant/trace ContextVars separately into every worker. Sessions remain
  repository-owned, never shared between workers. Cancel queued work and join
  running workers on exit. Upstream/database failures must not return partial data.
- As before, independent upstream pages are not a transactional snapshot during
  concurrent catalog mutation. Stable-directory results must match serial reads;
  observed total growth/shrink and empty-page stop rules remain unchanged.
- Bound parallel scans process-wide; requests unable to reserve a parallel scan
  slot use the original serial algorithm rather than creating additional pools.
  All page reads (including first pages and serial fallbacks) share a process
  budget of four concurrent reads so thread offload cannot flood BCS/the DB.
- Add a bounded catalog repository operation equivalent to the old first-page
  join but without its unused COUNT. Existing repository callers are unchanged.
- Offload the synchronous search service at the async HTTP boundary. Preserve
  exception mapping and add static, opt-in timing markers.

No cross-request response cache, BCS contract change, schema/index migration,
approximate total, IAM change, or speculative publication-state projection.
Lightweight BCS search/projection is a separate follow-up: do not remove fields
that participate in visibility, relationship, name or availability semantics.

## Verification

Compare filtered pages/totals and failures; test out-of-order completions,
per-request concurrency bounds, serial fallback, tenant isolation and cleanup.
Verify real repository output, query count and publication/tenant/env isolation.
Run catalog HTTP/service/repository and architecture regression suites.
Production latency and aggregate BCS/database load still need rollout validation.

Implemented on the current local branch; no PR, push or deployment performed.
Focused service/repository/HTTP/endpoint/architecture regression: **1100 passed**
(71.86 s). Ruff and `git diff --check` passed. Full Backend CI and changed-line
coverage were not run; live throughput/latency have not been measured.
