# Bot Control-plane OpenAPI V1 Implementation Design

- **Date:** 2026-08-02
- **Status:** Approved contract, implementation-ready
- **Scope:** `src/bcs` only
- **Contract:** `api-contracts/v1/openapi/bots.yaml`

## Goal

Implement the five approved Human control-plane Bot operations without changing
Legacy Actor/Bot routes or making the new V1 adapter production-reachable before
the trusted Principal transport is available.

## Vertical Slice

The implementation follows the existing BCN V1 layering:

```text
bcs-api-http Bot routes
        -> application::v1::BotService
        -> bcs-app-bot use-case facade
        -> BotControlPlaneRepoPort + existing BotRegistry/Friend/Provider ports
        -> bcs-bot-store implementations
```

HTTP owns only request decoding, response envelopes, and error translation.
`bcs-app-bot` owns Human-only authorization, ownership checks, kind rules,
filter semantics, ordering, reachability/provider enrichment, and V1 projection.
The store owns SQL/file/in-memory mapping, including `gmt_create` and
`gmt_modified` conversion.

## Service API

Add `application::v1::bot` with:

- the V1 Bot union and descriptor/provider/reachability value types;
- commands for candidates, batch query, exact read, patch, and mine;
- `BotService`, whose methods accept a normalized `Principal` as part of each
  command and return `ApplicationError`.

Every method rejects `Principal::Bot`. Human ownership uses raw
`HumanPrincipal.subject.id`, which is the same identity stored in `created_by`.

## Control-plane Store Port

Add a narrow `BotControlPlaneRepoPort` instead of adding timestamps and
provider-facing fields to Legacy `RegisteredBot`. Its record contains only the
non-sensitive persisted fields needed by V1:

- identity, kind, name, visibility, raw status, env, optional created_by;
- descriptor fields and optional read-only agent_code;
- created_at/updated_at in Unix milliseconds.

The port supports exact read, ordered batch hydration, candidate paging,
owner-list static filtering, and one partial mutable-field patch. It never
returns session tokens, agent tokens, provider credentials, endpoints, or
binding-channel internals.

`PersistentBotRepo` maps `gmt_create/gmt_modified` with dialect-aware Unix-time
SQL. `MemoryBotRepo` keeps equivalent timestamps in process/file-backed state
for local and test behavior.

## Use-case Rules

### Candidates

The acting path Bot must exist, be physical, and have
`created_by == current subject.id`. The query uses the acting Bot's persisted
environment, excludes self/Humans/deleted rows, and applies the approved
purpose visibility rules. Friendship is read from the existing Friend core.
Ordering and pagination happen after all filters.

### Query and Exact Read

Both kinds are projected without ownership or visibility filtering. Batch
query de-duplicates by first occurrence, preserves request order, and omits
missing rows. A maximum of 100 IDs is enforced at both HTTP and application
boundaries.

### Patch

The target must have `created_by == current subject.id`; missing `created_by`
is forbidden. Name, visibility, raw status, and descriptor are mutable.
Descriptor updates are physical-Bot-only and replace any supplied arrays in
full. The store applies the patch in one write and refreshes `updated_at`.

### Mine

Select by `created_by == current subject.id`, then apply optional kind, trimmed
case-insensitive name, raw status, and computed reachability filters. Omitted
kind means both kinds. Reachability removes Humans before pagination.

## Projection

Physical Bots receive normalized descriptor fields, effective reachability,
optional provider `{provider_id, name}`, and optional agent_code. Reachability
uses the existing batch runtime-active/downlink logic combined with raw
`status=online`; it introduces no database column. Human records omit all four
physical-only projections.

Provider metadata is obtained by batching existing binding and provider ports.
Bindings or providers that cannot be resolved produce no provider projection;
store failures remain internal errors.

## Rollout Boundary

This slice adds executable services and HTTP route tests through an injected
`PrincipalVerifier`, matching the existing V1 implementation strategy. It does
not mount the V1 router in production or invent a Principal signing format.
Those remain part of the separately approved rollout slice.

## Verification

- store conformance tests for timestamps, ordering, filtering, patch semantics,
  and credential exclusion;
- application tests for the authorization and behavior matrix;
- HTTP route tests for all five methods, DTO validation, envelopes, and error
  mapping;
- boundary tests proving HTTP production sources depend only on the V1
  application Service API;
- targeted Cargo tests and the OpenAPI contract test suite.
