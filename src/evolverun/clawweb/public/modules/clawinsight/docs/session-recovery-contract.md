# ClawInsight session recovery v2

The [API contract](session-recovery-api.md) defines snake-case monitoring requests, a synchronous 202 acknowledgment, and an outbound delivery-result callback. This feature sends handbook advice into an existing OC session. It creates no Repair task, AIS execution, observer or recovery judgment. TE is rejected with 422 pending validation.

## Boundaries

`SessionRecoveryService` consumes `BotRuntime.sendMessage`, `RecoveryDeliveryStore`, `RecoveryResultReporter` and a structured logger. It selects advice but never selects the Bot provider. `SessionRecoveryDeliveryRepository` uses the existing ClawInsight database; `HttpRecoveryResultReporter` sends a fixed result envelope to the monitoring-owned endpoint. The receiver and its alert/UI persistence belong to monitoring.

Shared `server/services/bot-runtime` owns BaaS, legacy Arca and local protocol implementations. Repair delegates shell I/O to it while retaining approvals and evidence policies. OCB composition supplies directory lookup, MIST/local Owner connections, credentials, database and callback configuration. BaaS uses its OpenAPI; legacy Arca uses AgentProxy and Engine chat.send. No Arca CLI is used.

## Durable delivery

`insight_session_recovery_delivery` is owned by this module, separate from Repair and monitoring diagnosis tables. Its exact binary unique key is `(event_id, delivery_key)`. It stores a SHA-256 fingerprint of the canonical validated request, start/settlement times, an immutable outcome (sanitized result or pre-send rejection), and a callback-delivered flag. It stores no raw diagnosis, advice, credentials or full target snapshot.

Only a successful INSERT authorizes a send. Concurrent/restarted requests cannot reclaim that authorization. An active duplicate returns 503; different content returns 409. After ten minutes, a duplicate may atomically settle an unfinished record as unknown, but cannot send again. This deadline is not a renewable execution lease. A late completion cannot replace the recorded outcome. No automatic timer or queue is introduced; settlement requires a same-key retry.

The result is committed before the first callback. Successful callbacks set a monotonic delivered flag. Callback failure retains the exact result for same-key replay. A crash after callback success but before its local acknowledgment may replay the identical callback; the receiver must deduplicate it. Database errors propagate and never cause an in-memory fallback or an automatic message retry. Logging failure also cannot remove the saved outcome.

This provides at-most-one application send attempt per retained key, not exactly-once message delivery. A crash before send may lose a message; a crash after send may leave an unknown outcome. Do not delete delivery rows while callers can still retry those keys.

## Schema provisioning

The module owns `server/services/session-recovery/schema.ts`. `renderRecoveryDeliveryDdl` renders reviewed SQLite/MySQL/ZDAS DDL. Managed databases must be provisioned externally using [session-recovery-delivery.mysql.sql](session-recovery-delivery.mysql.sql), before enabling the API. No production bootstrap or request performs DDL. Requests fail before sending when the table or full uniqueness key is unavailable.

Local/test hosts explicitly call `initializeRecoveryDeliverySqlite(db)`; it refuses managed databases. Internal production composition injects the existing DB connection rather than introducing another database or Repair dependency.

## Validation

Tests cover strict v2 fields, authorization/target-engine matching, real HTTP callbacks, exact message metadata, real SQLite concurrent connections, reopen/restart, altered-content conflicts, missing schema/failed writes, and callback replay after failures. Public runtime/provider and Repair transport suites protect the shared behavior. MySQL/ZDAS DDL is rendered/tested locally; managed DB execution and the real monitoring receiver require deployment integration.
