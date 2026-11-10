# Caller refresh after Owner Passport callback

User-confirmed scope (2026-11-03): reuse the existing owner renewal trigger; do not add a Caller scheduler. Authorizations are isolated by Bot stable entity, environment, and actual caller. A Callback cannot guarantee continuous one-hour Caller validity when owner credentials are not due.

## Coding boundary

Avernet owns an injectable `ScheduledCallerRefreshProtocol.refresh(bot_id, owner_user_id, entity_id, env)` service contract, its community no-op implementation, and the call after successful owner runtime updates for service Bots. Enterprise code implements the port and owns ASC exchange and runtime installation. Caller preparation/runtime failures are best effort at the owner callback boundary: log credential-safe metadata and preserve the successful owner result; they must not trigger owner notification retries. Original owner update failures still propagate. Personal/unsupported Bots do not invoke the service.

Add a dedicated `DeviceBindingRepository.list_active_caller_refresh_bindings(bot_id, entity_id, env)` query for ACTIVE BaaS and direct ARCA Caller bindings. Keep existing BaaS owner-token fanout unchanged. Filter environment, stable Bot entity, staff type, provider, status and exact Caller reason; return newest binding for each device identity. `applied_by` identifies the actual Caller. BaaS is the actual product Caller control plane; its physical ARCA sandbox is resolved by the enterprise adapter. TeClaw is outside this feature.

## Review and test

Test owner update before Caller invocation, exact stable identity, failure propagation, no invocation after owner failure, personal/unsupported bypass and community no-op DI. Exercise repository filtering on real SQLite rows including other owners, environments, statuses, providers and Bot reasons. Assert no credential enters new logs or exception strings. Update context contracts and run affected service/repository and architecture tests. No enterprise imports, no SDK/network calls, no changes to owner scheduling or IAM exchange.

## Ship

Local implementation and validation only in this worker. Main agent owns enterprise integration, remote dev rebase, commits and PR. No production DB operations or deployment.

The Caller hook enters the resolved Bot row's Avernet tenant for the duration of
the call. The callback's default tenant must not hide an external tenant's Bot
from the enterprise updater, and the previous context is restored on success or
failure. This does not bypass the guard for downstream queries.
