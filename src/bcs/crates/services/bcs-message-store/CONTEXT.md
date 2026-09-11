# bcs-message-store Context

## Provides

Opt-in `bcs_reply_profile` DEBUG timing separates delivery_writer wait from
hold time. A held-writer span lets the isolated load test attribute SQLite SQL
category timings/counts to the critical section without logging SQL or payloads.
Timing is diagnostic only and does not change public APIs.

Internal run_chat_segments reads exact env/session/sender/run in sequence order.
SQLite 025 / MySQL 024 index this reconstruction; public history filters run_reply
before pagination, while canonical ID reads retain it. Admission can atomically
append one visible chat companion and its event before the non-eventful summary,
alongside terminal CAS and reply target admission. Memory and SQL share conformance.

Control batches query four independent action ranges, reserve equal shares and
lend spare capacity under a single result limit. They ignore the legacy ID cursor;
successful actions leave their due set. SQLite 024 / MySQL 023 add a pending-abort
index; existing deadline indexes serve the timeout classes. Memory follows the
same selection contract; remote execution plans need deployment verification.

Bounded-context reads return newest metadata plus a complete bound count, never
canonical bodies. SQLite 023 / MySQL 022 add context_selection_json and an
ordered bound lookup index; transitions persist selection with send-start and
context outcomes with terminal replies under the existing CAS transaction.

Purpose-specific delivery reads own SQL filtering, cursor ordering, complete
counts, bounded due batches and low-frequency queue GROUP BY statistics. A
downstream run alias column is an indexed projection of transport metadata,
updated in the same transition; SQLite 022 / MySQL 021 backfill existing data.
Selected canonical payloads use 200-ID batches; ordinary scheduling reads no JSON.

MessageDeliveryRepoPort also persists one environment-scoped versioned policy snapshot with CAS and latest actor/time metadata; Memory and SQL share the policy conformance contract.

- `MessageRepoPort` and `MessageDeliveryRepoPort` implementations for Memory and
  MySQL/SQLite canonical-message and target-delivery storage.
- Atomic admission, sender-scoped idempotency, target capacity rejection,
  context binding, version-checked transitions and terminal reply admission.
- Public history projection that excludes queue-retained attachment URLs.

## Consumes

- `bcs-service-api` repository contracts and `bcs-domain` data.
- `bcs-db-api` SQL transactions and `bcs-event-store` event transaction plans.

## Allowed dependencies

- Contract crates, database plugin API, event-store transaction composition,
  serialization and process-local synchronization utilities.

## Forbidden dependencies

- HTTP/WS adapters, Bot transport implementations and bootstrap runtime code.
- Runtime production DDL, environment reads, queues or workflow scheduling policy.

## Configuration

- Bootstrap supplies the database flavor, configured env and shared store instance.
- Clones share a weak keyed lock directory: managed writes lock their sessions;
  Send changes/admissions additionally lock target Bot capacity across sessions.
  Capacity keys precede session keys; all keys are sorted and deduplicated.
  SQL conditional writes independently guard sequence allocation and stale changes.
  Once DB commit starts, guards outlive caller cancellation until it finishes.

## Runtime ownership

The store owns persistence transactions, not network attempts, timers or user
authorization. The application supplies all lifecycle and related context changes
to one transaction. Memory is test/development storage, not restart durability.

## Tests

- `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-message-store`
- Shared delivery contract runs against Memory and migrated SQLite. Remote
  MySQL/OceanBase execution additionally requires a configured test database.
