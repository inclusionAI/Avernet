# Optional Exec Interaction Command Design

## Problem

Some Engine `interaction.requested` events identify an `exec` interaction and
its `toolCallId` but omit the human-readable `command`. BaaS currently drops
those events while converting them to the BCN Provider 2.0 SSE format. Even if
BaaS forwards the event, BCS currently rejects it before storing or publishing
the pending interaction because its service validation requires a non-empty
`command`.

The result is a split workflow: the Engine waits for approval, while BCN never
receives a pending interaction and the request eventually expires.

## Decision

Evolve the Provider 2.0 `exec` requested payload so `command` is optional.

- BaaS copies `command` only when it is a non-empty string. Missing, null,
  non-string, empty, and whitespace-only values are omitted without dropping
  the interaction.
- BCS accepts an `exec` requested payload when `command` is absent. If the
  Provider includes `command`, it must still be a non-empty string.
- Existing `options` requirements and all common interaction identity,
  envelope, sequencing, size, authorization, and resolution validation remain
  unchanged.
- Frontend delivery continues to forward the allowlisted/raw requested payload;
  consumers must render command details only when the field is present.

## Component Changes

### BaaS branch

Base: latest `inclusionAI/REL20260821`.

Update the default SSE converter, its unit tests, and the BaaS-to-BCN
interaction design document. A valid command remains unchanged; an absent or
invalid command no longer emits a conversion warning and no longer consumes a
dropped-event path.

### BCS branch

Base: latest `inclusionAI/dev`.

Update interaction service validation so `command` is optional-but-valid-when-
present. Update unit/conformance coverage and the Provider 2.0 SSE protocol
document to describe the compatible wire shape.

## Compatibility and Rollout

The change is backward-compatible for Providers that already send `command`.
BCS must deploy before or together with the BaaS change; deploying only BaaS
would cause command-less interactions to reach BCS and then be rejected by the
old service validation. Rolling back BaaS restores the prior fail-closed
behavior. Rolling back BCS while the new BaaS behavior remains active recreates
the original lost-interaction failure.

## Testing

- BaaS test-first coverage proves missing and invalid commands produce a valid
  `event: interaction` without a `command` field, while valid commands remain.
- BCS test-first coverage proves command-less exec requests are stored and
  published, while an explicitly malformed command remains rejected.
- Run each module's focused unit/conformance suite and repository hygiene
  checks without global formatting.
