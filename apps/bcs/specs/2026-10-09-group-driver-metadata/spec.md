# V1 Group driver metadata

## Scope and contract

Owner: BCS. Extend shared V1 application response types, as approved by the user.
NormalGroupSummary gains nullable `driver_bot_name`; CollaborationGroupDetail
gains nullable `driver_bot_owner` and `driver_bot_owner_name`. Fields are always
serialized, with JSON null for unavailable values; deserializing old responses
without these fields remains supported. Existing DM types remain unchanged.

Summary names come from the driver participant after existing name backfill.
An empty driver ID or no matching participant yields null. Owner resolution
matches the legacy Group detail: look up the driver registration, read nonempty
created_by, prefix it with `human_`, then resolve that Human registration's name.
Unavailable driver/ownership yields two nulls; unavailable Human name preserves
the owner ID and yields null for the name. This is display metadata, not an
authorization decision. Existing authorization is unchanged.

All consumers of the shared types receive the additive fields, including both
normal/public group lists and create/get/update detail responses. No persistence
schema, Plugin API, route, or configuration changes are required.

## Boundaries and references

- `docs/arch/arch.rules.md`: contracts and transport-agnostic application logic.
- `crates/adapters/http/bcs-api-http/CONTEXT.md`: V1 serialization boundary.
- `crates/application/v1/bcs-app-group/CONTEXT.md`: application projection owner.
- Existing legacy `groups/projections.rs::resolve_driver_bot_owner` semantics.
No ADR changes: this preserves the existing application/delivery boundary.

## Access cost

Summary projection adds no registry/DB reads beyond existing participant-name
backfill. Normal detail adds at most two sequential registry lookups; an empty
ID needs none, a missing driver or owner needs at most one. PersistentBotRepo
checks nonexpired in-memory records, then performs one environment-scoped query
by Bot UUID per miss, reading at most one registration row. No writes, new cache,
retries, unbounded scans, or DB transactions are introduced. DM adds no reads.
This applies per detail projection, including create/update results; concurrency
scales the additional reads by the number of concurrent detail requests.

## Validation boundary

Cover successful and missing metadata, driver/owner mismatch from caller,
unchanged DM responses, legacy JSON deserialization, both list routes, and shared
create/get/update response serialization. Run affected application, contract,
and HTTP adapter suites; record actual results in plan.md. Live database and
full Singlebox E2E are outside focused verification. No commit/PR created by this
implementation task unless separately requested.
