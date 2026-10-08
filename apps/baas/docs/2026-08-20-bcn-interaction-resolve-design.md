# BaaS BCN Interaction Resolve Design

## Context

BCS sends every Provider 2.0 human-interaction answer to the Provider webhook
as `POST /bcn/downlink` with `method = interaction.resolve`. BaaS already
persists Engine `interaction.requested` events and has an owner-worker queue
that delivers a decision to the Engine WebSocket. The downlink router does not
currently register `interaction.resolve`, and the persisted dispatch command
currently carries only a decision, so `ask_user` answer data cannot survive the
HTTP-to-owner-worker boundary.

This change adds the missing BaaS adapter without changing Engine source or
Engine behavior.

## Goals

- Register `interaction.resolve` on `POST /bcn/downlink` for JSON transport.
- Accept the full BCS Provider webhook envelope and validate kind-specific
  resolution data.
- Persist a normalized, transport-independent resolution with the existing
  interaction state machine before acknowledging BCS.
- Deliver the normalized resolution through the existing owner worker and
  build the Engine-native request for `ask_user`, `exec`, and `mode_switch`.
- Preserve Provider idempotency across HTTP retries.

## Non-goals

- Do not change any file under `src/engine`.
- Do not add `interaction.resolve` to SSE transport; it is a finite JSON
  request returning a JSON acknowledgement.
- Do not infer whether an `ask_user` value is a predefined option or a custom
  value. Every value is treated as an ordinary value in this version.
- Do not synthesize or forward `fromMode`, `targetMode`, BCS run IDs, or BCS
  idempotency metadata to Engine.

## Chosen architecture

The BCN adapter normalizes the request before persistence. A typed interaction
resolution is stored with the existing interaction record and returned by
`claim_for_dispatch`. The owner `AsyncChatClient` passes it to the existing
Engine WebSocket client, which selects the Engine method and builds the exact
wire frame.

This preserves the current durable owner-worker flow. Parsing the raw BCN
envelope in the owner worker was rejected because it would leak a transport
contract into Engine delivery. Sending directly from the router was rejected
because the HTTP worker may not own the Engine WebSocket and because it would
bypass the interaction state machine.

## BCN request contract

The outer request is the existing BCS Provider webhook envelope:

```json
{
  "type": "req",
  "id": "bcn-resolve-1",
  "method": "interaction.resolve",
  "session_id": "session-1",
  "bcn_group_id": "group-1",
  "to_bot": {
    "provider_id": "provider-1",
    "provider_bot_ref": "bot-1"
  },
  "params": {
    "bcsRunId": "bcs-run-1",
    "runId": "provider-run-1",
    "interactionId": "interaction-1",
    "kind": "ask_user",
    "idempotencyKey": "idem-1",
    "action": "submit",
    "answers": {}
  },
  "timeout_ms": 3600000
}
```

`params.interactionId` is the BaaS-owned public interaction ID emitted on the
requested SSE. It is the sole interaction lookup and transition key. The outer
`session_id` remains part of the BCN envelope for protocol compatibility and
other routing metadata, but BaaS does not compare it with an Engine session key
or use it to resolve an interaction. After the public-ID lookup, the stored row
supplies the original Engine `session_key` and `interaction_id` for owner-worker
dispatch.

For new interactions, the public ID is
`BAAS-INTERACTION-<sha256-prefix>`, where the suffix is the first 32 lowercase
hex characters of SHA-256 over the compact UTF-8 JSON array
`[engine_session_key, engine_interaction_id]`. Existing rows retain the ID that
was already exposed before migration so in-flight resolves remain compatible.

## Kind-specific normalization

### `ask_user`

BCN requires `action = submit | cancel`. A submit requires one or more answers.
Each answer is keyed by `questionId` and contains a non-empty `question` plus a
required ordered list of string `values`. The list may be empty and its strings
may be empty or whitespace-only; these shapes represent a skipped question.

Given:

```json
{
  "action": "submit",
  "answers": {
    "deploy_target": {
      "values": ["staging"],
      "question": "what's your deploy target?"
    },
    "components": {
      "values": ["web", "worker"],
      "question": "whats' the components?"
    }
  }
}
```

the normalized Engine fields are:

```json
{
  "decision": "submit",
  "answer": "deploy_target: staging；components: web，worker",
  "message": "deploy_target: staging；components: web，worker",
  "values": {
    "deploy_target": "staging",
    "components": "web，worker"
  },
  "answers": {
    "what's your deploy target?": "staging",
    "whats' the components?": "web，worker"
  },
  "selectedOptions": [
    ["staging"],
    ["web", "worker"]
  ]
}
```

Within one question, values are joined with the Chinese comma `，`. Question
summaries are joined with the Chinese semicolon `；`. JSON object and array
order follows the incoming answer order. `selectedOptions` is a two-dimensional
array: each inner array is an unchanged copy of one answer's `values`.

Skipped values use the same projections without rewriting. For example,
`values: []` produces an empty string in the summary, `values`, and `answers`
maps and an empty inner array in `selectedOptions`. Empty and whitespace-only
strings are likewise preserved exactly.

No option lookup, membership validation, trimming, or `other` substitution
occurs. Custom strings are ordinary values. Cancel produces only `decision =
cancel`.

### `exec`

BCN supplies a non-empty `decision` that BCS has already checked against the
offered options. BaaS still validates it against the decision set persisted
with the requested interaction. The Engine request is:

```json
{
  "type": "req",
  "id": "<BaaS-generated request ID>",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "interaction-exec-1",
    "decision": "allow-once"
  }
}
```

The same shape supports `allow-always` and `deny` when offered.

### `mode_switch`

BCN also supplies a non-empty `decision`, normally `proceed` or `stay`. Unlike
the other kinds, Engine uses its mode transition RPC and transition identity:

```json
{
  "type": "req",
  "id": "<BaaS-generated request ID>",
  "method": "mode_transition.resolve",
  "params": {
    "transitionId": "interaction-mode-1",
    "decision": "stay"
  }
}
```

## Persistence and idempotency

The interaction payload stores both the original BCN request envelope and the
normalized resolution. `decision` remains explicit for state validation and
backward compatibility. Older queued records without a normalized resolution
continue to dispatch as decision-only interaction requests.

For Provider requests, BaaS persists `idempotencyKey` with the accepted
resolution. A retry with the same key and identical normalized resolution
returns success without a second state transition or Engine dispatch, including
after the accepted record reaches a terminal `failed` or `expired` state. If a
concurrent request wins the `requested -> queued` transition, the loser rereads
the record before deciding whether the request is an identical replay. Reusing
the same key for different content, or resolving the same interaction with a
different key after it has left `requested`, is a conflict.

Engine emits a top-level `interaction.resolved` event for `ask_user` and `exec`,
and current Engine versions emit `mode_transition.resolved` for `mode_switch`.
BaaS preserves the raw mode-transition envelope and converts it to the common
BCN `interaction` event with `phase = resolved`. Because the terminal event can
arrive after the accepted RPC response, its one-time SSE delivery is gated by
the active stream's previously exposed mode-switch request rather than by a
second database state transition.

BaaS still persists the mode RPC exchange first and marks the record resolved
from an accepted response. This remains a database-terminalization fallback for
older Engine versions that do not emit `mode_transition.resolved`; the response
itself does not synthesize a terminal SSE.

## Response and errors

After the record is durably moved to `queued`, `/bcn/downlink` returns the BCS
Provider acknowledgement:

```json
{"ok": true}
```

An interaction validation, lookup, expiry, or conflict failure returns a
finite acknowledgement with `ok = false`, `retryable = false`, and a sanitized
error string. Unexpected infrastructure failures remain server errors so BCS
can treat them as retryable transport failures. Answer content must never be
written to logs at any level. Pydantic failures for `interaction.resolve` are
caught inside the downlink route so rejected `input_value` data never reaches
the global validation logger or a non-2xx transport response.

## Verification

Tests cover:

- Pydantic validation for all three kinds;
- router registration and exact domain handoff;
- the exact `ask_user`, `exec`, and `mode_switch` Engine frames;
- two-dimensional `selectedOptions` and ordered ordinary-value conversion;
- persistence and claim across the HTTP-to-owner-worker boundary;
- same-key idempotent retries and conflicting retries;
- idempotent replay after terminal failure/expiry and concurrent transition
  races;
- sanitized invalid-request ACKs and interaction logs;
- mode-switch terminalization from the accepted Engine RPC;
- decision-only backward compatibility for existing queued records;
- focused BaaS router, service, protocol, interaction, and async client suites;
- Ruff formatting/checking and `git diff --check`.
