# BaaS Public Interaction ID Design

## Problem

Engine interaction events identify a request with an Engine `sessionKey` and
`interactionId`. BaaS currently forwards that Engine ID to BCN and later resolves
the persisted row with the pair `(BCN session_id, interactionId)`. The BCN session
identifier is not an Engine session key, so a valid `interaction.resolve` can fail
to find the row. Forwarding the Engine identifier also couples the public BCN
contract to an Engine-owned identity.

## Decision

BaaS owns a separate public interaction identifier:

```text
BAAS-INTERACTION-<first 32 lowercase hex characters of SHA-256>
```

The hash input is the UTF-8 encoding of the canonical JSON array containing the
trusted Engine session key and Engine interaction ID:

```python
json.dumps(
    [session_key, engine_interaction_id],
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")
```

This construction is deterministic for Engine redelivery, keeps the identifier
to 49 characters, and retains 128 bits of the SHA-256 digest. The JSON array is
length-delimited by its syntax, avoiding ambiguous string concatenation.

## Persistence contract

`baas_bot_run_interaction` stores all three identities explicitly:

| Column | Owner | Purpose |
| --- | --- | --- |
| `baas_interaction_id` | BaaS | Public BCN/frontend identity; globally unique lookup key |
| `session_key` | Engine | Internal websocket session routing |
| `interaction_id` | Engine | Internal Engine interaction dispatch |

New rows require `baas_interaction_id` and enforce a unique key on it. Existing
rows are backfilled with their previously exposed `interaction_id`, preserving
the ability to resolve interactions emitted before deployment.

## Request and resolve flow

1. BaaS receives an Engine `interaction.requested` event.
2. The interaction service derives the BaaS ID from the trusted Engine identity
   pair and persists it beside the original Engine values.
3. The raw Engine envelope is retained in the database, while the SSE delivery
   copy replaces its public `interactionId` with the BaaS ID.
4. BCN and the frontend use only that BaaS ID.
5. BCN sends `interaction.resolve`. BaaS looks up and transitions the row only by
   `baas_interaction_id`; it does not compare or normalize `session_id`.
6. BaaS reads the Engine identity pair from the matched row and dispatches the
   answer to Engine with the original Engine interaction ID.
7. Engine terminal events are matched internally by their Engine identity pair,
   then exposed over SSE with the stored BaaS ID.

## Compatibility

- The BCN wire field remains `interactionId`; only its ownership changes to BaaS.
- The outer BCN `session_id` remains accepted for protocol compatibility and
  routing metadata, but is not an interaction lookup key.
- Existing persisted interactions remain resolvable after the backfill.
- Engine websocket request and response frames remain unchanged.

## Failure behavior

- Unknown BaaS IDs return the existing interaction-not-found error.
- A deterministic-ID collision or duplicate public ID for a different Engine row
  fails the database write through the unique constraint; it is never silently
  treated as an idempotent Engine redelivery.
- Persistence failures continue to propagate to the caller.
