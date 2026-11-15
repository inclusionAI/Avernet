# Distributed coordination contracts

Status: contract definitions only; runtime implementation is not authorized in this change.

## Intent and ownership

Define transport-independent Gateway, Worker and Connector boundaries for
structured group collaboration. Public models belong in `bcs-dist-model`;
application/core/repository ports belong in `bcs-service-api`. Storage and
notification implementations remain independently replaceable. No deployment,
schema migration, adapter, engine loop, hashing or retry implementation is added.

The baseline is Avernet `5a126bd84f` from the authoritative `dev` branch.
Existing collaboration and direct A2A behavior is unchanged. The new contracts
are opt-in, additive declarations with no bootstrap registration.

## Invariants

1. A successful admission means a durable logical input and durable activation.
2. Input identity, output identity, replay cursor and delivery acknowledgement
   are different types. Backend positions are not ordered by business code.
3. Checkpoint state, consumed position and fixed outbox commit atomically under
   owner fencing and an expected revision. Unknown is not NotApplied.
4. A required effect blocks the next business commit until confirmed. Replay
   keeps the original effect identity, digest and source commit.
5. Event replay succeeds only with identity/content or valid compaction evidence.
6. Snapshot content, status and coverage publish together; recovery returns one
   consistent cut. Gaps require rebuilding a full view.
7. Schedule settlement uses an observation token acquired before checking work.
   New activation advances that token even when the due time is unchanged.
8. Dispatch control and execution have separate owners. Confirmed input progress
   survives dequeue. Unknown downstream outcomes are never fabricated success.

## Deliverables

- Shared serializable domain models and explicit opaque cursor types.
- Object-safe service/port traits, operation-specific outcomes and contract docs.
- Contract shape/serialization checks and reusable conformance test entry points.
- [Ordered development tasks](tasks.md), including scenarios not yet executable.
- Validation record that distinguishes declaration checks from backend behavior.

See the [service contract context](../../crates/service-api/bcs-service-api/CONTEXT.md)
and [architecture rules](../../CLAUDE.md). No private source links or deployment
credentials form part of the public contract.
