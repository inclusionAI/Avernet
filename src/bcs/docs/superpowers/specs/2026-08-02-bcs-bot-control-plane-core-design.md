# BCS Bot Control-Plane Core Boundary Design

## Problem

The V1 `bcs-app-bot` application facade currently holds
`BotControlPlaneRepoPort`, `ProviderRepoPort`, and
`ProviderBotBindingRepoPort` directly. That lets an Application service
orchestrate persistence queries and joins, contrary to the BCS dependency
rules: Application services call Core services, while Core implementations
consume repository ports.

The fix must preserve the approved V1 Bot HTTP contract and must not add more
responsibilities to the already broad `BotCore` implementation.

## Decision

Add a narrow `BotControlPlaneCoreService` contract and a separate
`BotControlPlaneCore` implementation. This is a Core capability within the
existing Bot domain, not a new bounded context, deployable component, Cargo
package, or workspace crate.

The implementation stays inside existing crates:

- `bcs-service-api` owns the Core service contract, Core-facing views, and
  shared transport-agnostic control-plane values.
- `bcs-bot` owns `BotControlPlaneCore`.
- `bcs-bot-store` continues to own implementations of the repository ports.
- `bcs-app-bot` continues to own V1 Human authorization and use-case policy.

No new `Cargo.toml`, workspace member, lifecycle, configuration source, or
storage implementation is introduced.

## Responsibilities

### `BotControlPlaneCoreService`

The Core service exposes fallible operations for:

- exact Bot/Human record lookup without Provider hydration for authorization
  checks;
- exact hydrated Bot/Human view lookup for response projection;
- request-ordered batch lookup;
- candidate selection with total-before-pagination semantics;
- owner-scoped record listing;
- atomic mutable-property patching; and
- Provider display metadata hydration for physical Bots.

Its output is transport-agnostic. Core-facing views contain the persisted Bot
record and an optional Provider summary, but do not contain V1 HTTP envelopes,
Principal types, or reachability.

### `BotControlPlaneCore`

The implementation consumes:

- `BotControlPlaneRepoPort` for Bot/Human control-plane records;
- `ProviderBotBindingRepoPort` for Bot-to-Provider bindings; and
- `ProviderRepoPort` for Provider display records.

It delegates filtering, ordering, pagination, and patch atomicity to the narrow
Bot repository port, then performs batch Provider hydration while preserving
the repository result order. A missing binding or Provider produces no
Provider summary. Repository failures remain failures and are propagated as
`ServiceError`.

### `BotServiceImpl`

The V1 Application service depends on:

- `BotControlPlaneCoreService`;
- `BotRegistryCoreService`, only for runtime reachability; and
- `FriendCoreService`, for candidate friendship context.

It continues to own:

- authenticated Human Principal enforcement;
- request validation;
- acting-Bot and owner authorization;
- V1 Bot/Human DTO projection;
- the Human-descriptor patch restriction;
- reachability projection; and
- post-reachability pagination for `list_mine`.

It must not import, store, or call a repository port.

### Existing `BotCore`

`BotCore` remains unchanged in responsibility. It continues to own the legacy
registry, onboarding persistence, connection state, delivery resolution, and
runtime reachability. It does not implement `BotControlPlaneCoreService` and
does not gain the new repository dependencies.

## Contract Type Placement

Control-plane records, queries, patches, and candidate records are shared
transport-agnostic values under `bcs-service-api::types`, which both Core and
repository contracts may consume without a reverse dependency. The Core
service contract, hydrated views, and Provider summaries belong to
`bcs-service-api::core`. The repository contract remains under `port::repo`.

`bcs-app-bot` may import the Core-facing types but not
`BotControlPlaneRepoPort`, `ProviderRepoPort`, or
`ProviderBotBindingRepoPort`.

## Data Flow

For exact lookup, batch lookup, owner listing, and patch:

1. `BotServiceImpl` authenticates the Human and validates the command.
2. `BotControlPlaneCore` performs the repository operation.
3. `BotControlPlaneCore` batch-loads bindings and Provider summaries for
   physical Bot records.
4. `BotServiceImpl` computes reachability through `BotRegistryCoreService` and
   projects the V1 response.

For candidate listing:

1. `BotServiceImpl` authenticates the Human, loads the acting Bot record
   through the Core service without Provider hydration, and checks ownership.
2. `BotServiceImpl` obtains friend IDs from `FriendCoreService`.
3. `BotControlPlaneCore` delegates candidate filtering and pagination to the
   repository and hydrates Provider summaries.
4. `BotServiceImpl` computes reachability and returns V1 candidate DTOs.

## Error Semantics

- Principal, validation, ownership, and Bot-kind failures remain
  `ApplicationError` decisions in `bcs-app-bot`.
- Missing persisted records remain `None` at the Core boundary and are mapped
  by the Application service to the existing V1 `bot_not_found` behavior.
- Acting-Bot and update ownership denials occur before Provider hydration, as
  they did before this refactor.
- Repository and Provider hydration failures remain `ServiceError` and are
  mapped to the existing V1 internal-error response.
- This refactor does not add an onboarding predicate or redefine corrupted
  persisted-data handling.

## Testing

Implementation follows a red-green-refactor sequence:

1. Add a layering contract that fails while production `bcs-app-bot` source
   references any `*RepoPort`.
2. Add focused `BotControlPlaneCore` tests for request-order preservation,
   Provider hydration, candidates, owner listing, and patch delegation.
3. Rewire existing `bcs-app-bot` behavioral and conformance tests through the
   new Core implementation; the expected V1 behavior remains unchanged.
4. Run the `bcs-service-api`, `bcs-bot`, `bcs-app-bot`, `bcs-bot-store`, and
   V1 HTTP Bot route tests, plus the relevant architecture checks.

## Compatibility and Scope

This is an internal dependency-boundary refactor. It does not change HTTP
paths, request or response schemas, Principal semantics, authorization rules,
database schema, persistence invariants, or Legacy endpoint behavior.

The change does not refactor other V1 Application crates that may still hold
repository ports. Those boundaries require separate review and are outside the
scope of the Bot PR comment.
