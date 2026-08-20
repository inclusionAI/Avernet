# Bot Catalog BCS metadata port — coding report

> This report describes the current port/fixed-502 implementation. It does not
> define or imply any BCS HTTP route, base-URL configuration, credentials, or
> network call; see [004-implementation-plan.md](004-implementation-plan.md).

## Delivered

- `/openapi/v1/bots/catalog/search` now depends on a typed, tenant-scoped BCS metadata port.
  It addresses Backend candidates by exact `(bot_id, entity_id)` and gives the port only the
  verified caller projection plus request ID.
- The current production, local, and test binding is intentionally unavailable. It logs only
  request ID, candidate count, and the `unconfigured` category, then causes the route to return
  the fixed `502000 / Catalog service unavailable` `ErrorEnvelope` response.
- A future configured port will use strict metadata validation, exact inner join, stable Backend
  order, and join-before-pagination. Backend remains authoritative for all public response fields.
- Legacy `/api/v1/bot-public/search` remains Backend-only; Discover retains its BCSFuse behavior.

## Verification

Focused pytest, DI resolution, router projection, OpenAPI generation, Ruff, and diff checks are
recorded in the task report for this implementation.
