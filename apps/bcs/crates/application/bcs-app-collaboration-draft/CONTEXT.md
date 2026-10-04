# Collaboration Draft application context

## Provides

The inbound `CollaborationDraftService`: save/read/manage immutable revisions
and validate a precisely authorized stored source. Errors map to application
categories without exposing persistence or cryptographic details.

## Consumes

`CollaborationDraftCoreService` for version/ownership/read-capability policy and
`CollaborationRuntimeService` for existing BCS definition validation.

## Allowed dependencies

Service API, async trait support and clock. Bootstrap injects both services and
the current judge capability.

## Forbidden dependencies

Repo ports, database plugins, HTTP, environment/config reads, Backend, Gateway,
Engine Runtime and frontend.

## Tests

The reusable application conformance harness runs against this implementation in
`bcs-collaboration-draft/tests/conformance_collaboration_draft.rs`.
The HTTP contract independently exercises fixed-version evidence and auth precedence.
