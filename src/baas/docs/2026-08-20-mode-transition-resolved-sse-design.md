# Mode Transition Resolved SSE Design

## Problem

BaaS currently subscribes to Engine `interaction.requested` and
`interaction.resolved` events. A mode-switch request is exposed correctly, but
the Engine completes it with a distinct top-level event:
`mode_transition.resolved`.

The accepted `mode_transition.resolve` RPC is already used as a compatibility
fallback to terminalize the persisted interaction. Therefore, merely routing
the newly observed event through the existing `interaction.resolved` handler
is insufficient: by the time the event arrives, the database transition can
legitimately return `False`, and the resolved SSE would still be dropped.

## Goal

Expose one BCN-compatible resolved interaction SSE for an Engine
`mode_transition.resolved` event without changing the Engine protocol or
regressing the accepted-RPC fallback used by older Engine versions.

The public SSE shape is:

```text
event: interaction
data: {
  "interactionId": "int:...",
  "kind": "mode_switch",
  "phase": "resolved",
  "decision": "proceed",
  "runId": "provider-run-id",
  "seq": 1
}
```

The Engine's raw `phase`, such as `proceeded`, is not exposed. The event name
defines the canonical BCN phase `resolved`.

## Boundary mapping

BaaS subscribes to all three Engine interaction events:

- `interaction.requested`
- `interaction.resolved`
- `mode_transition.resolved`

The typed Engine boundary preserves the original event name in its envelope.
It does not relabel `mode_transition.resolved` as `interaction.resolved`.

The SSE converter accepts the original event names and maps them explicitly:

| Engine event | BCN SSE event | BCN phase |
| --- | --- | --- |
| `interaction.requested` | `interaction` | `requested` |
| `interaction.resolved` | `interaction` | `resolved` |
| `mode_transition.resolved` | `interaction` | `resolved` |

For the mode-transition alias, the converter uses the existing resolved-field
allowlist. It exposes `interactionId`, `kind`, and `decision`, plus the normal
BaaS `runId`, `seq`, and timestamp fields. It does not expose Engine lifecycle,
status, options, or reconciliation fields.

## Single-delivery rule

Each active stream keeps a bounded set of mode-switch interaction IDs whose
requested chunks were emitted to that stream.

1. When a newly persisted `mode_switch` requested event emits an interaction
   chunk, its interaction ID is added to the stream-local set.
2. When `mode_transition.resolved` arrives, BaaS validates that it is a
   `mode_switch` terminal event and attempts the normal database terminal
   transition with the original envelope.
3. BaaS emits the resolved chunk only when the interaction ID is present in
   the same stream-local set, then removes it before emission.
4. A replayed terminal event cannot emit twice because the ID has already been
   removed.
5. An unsolicited or non-mode event cannot enter this compatibility path.

The stream-local set is released with the existing `SessionState`; it does not
create process-lifetime interaction state. The database keeps its existing
first-terminal-write semantics. If the accepted RPC fallback terminalized the
record first, the later Engine event may return `False` from `mark_resolved`,
but it still supplies the one terminal SSE for the exposed active stream.

## Backward compatibility

The accepted `mode_transition.resolve` RPC continues to mark a mode-switch
record resolved. Older Engine versions that do not emit
`mode_transition.resolved` therefore do not remain indefinitely in
`dispatching`.

The new event is additive. Existing `interaction.resolved` behavior for
`ask_user` and `exec` remains gated by the database transition and is not
changed to stream-local delivery.

## Logging and safety

`mode_transition.resolved` uses the same structured log allowlist as the other
interaction events. Logs may include structural identity and lifecycle fields,
but must not dump the raw payload or option labels.

Malformed payloads fail at the typed Engine boundary and do not emit SSE.
Converter warnings remain structural and do not include business content.

## Verification

Tests cover:

- the actual Engine `mode_transition.resolved` payload shape, including raw
  `phase = proceeded`, converts to BCN `phase = resolved` with
  `decision = proceed`;
- connect and reconnect both register the event handler;
- a mode request followed by an RPC fallback and then the Engine event emits
  one resolved chunk even when the database transition returns `False`;
- replayed terminal events do not emit duplicate chunks;
- an unexposed interaction, a non-mode kind, or an invalid payload does not use
  the compatibility delivery path;
- the existing accepted-RPC fallback remains covered for older Engines; and
- interaction logging remains sanitized.
