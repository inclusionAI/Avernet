# V1 Session Human Participant Mode Design

## Context

`PATCH /openapi/v1/collaboration/sessions/{session_id}/participants/{bot_uuid}`
currently accepts only `auto` and `muted` because its request DTO uses
`BotParticipantMode`. The domain participant model already supports the four
actor-aware modes:

- Bot: `auto`, `muted`
- Human: `present`, `absent`

The legacy `PATCH /sessions/{sid}/members/{actor_id}` endpoint accepts all four
modes and, when a `human_*` target is not yet in the Session, adds that Human as
an Observer before applying the requested mode.

## Goal

Expose the Human mode behavior through OpenAPI V1 without copying the legacy
endpoint's weak authorization model.

## Contract

Define the V1 update input as the union of the Bot and Human mode vocabularies:

- `BotParticipantMode`: `auto | muted`
- `HumanParticipantMode`: `present | absent`
- `SessionParticipantMode`: `oneOf(BotParticipantMode, HumanParticipantMode)`

Rust uses the existing domain `ParticipantMode` as the application command
type. The union limits the wire vocabulary; the application service still
validates the selected mode against the target participant's authoritative
`actor_kind` with `ParticipantMode::is_valid_for`.

The existing URL and path parameter remain unchanged for compatibility. The
path value is treated as an Actor identifier internally even though the
published parameter is still named `bot_uuid`.

## Authorization

The endpoint remains Human-caller-only through `require_human`.

Human targets are strictly self-service:

- The target must equal `human_{caller.user.id}`.
- The caller must be able to read the Session under the existing V1 detail
  visibility rule.
- A Session manager cannot update or auto-add a different Human participant.

Bot targets preserve the current V1 behavior:

- The target must already be a Session participant.
- The caller must pass `can_manage_session`.
- Only `auto` and `muted` are accepted.

This intentionally differs from legacy authorization. Legacy requires only an
authenticated caller and does not bind that caller to the target Human.

## Application Flow

1. Require an authenticated Human caller.
2. Load the Session and its parent Group.
3. Look for the target in the Session roster.
4. For an existing Human target:
   - require exact self identity;
   - require Session read access;
   - reject Bot-only modes;
   - update the stored mode.
5. For a missing `human_*` target:
   - require exact self identity;
   - require Session read access;
   - reject Bot-only modes;
   - idempotently add a Human `Observer` with the Human default mode
     (`absent`);
   - always apply the requested mode after the add.
6. For an existing Bot target:
   - require Session management authority;
   - reject Human-only modes;
   - update the stored mode.
7. For a missing non-Human target:
   - preserve the current authorization ordering;
   - return `participant_not_found` to an authorized manager.

The Human first-insert intentionally uses the legacy add-then-update sequence.
The unconditional second write matters when another request inserts the same
Human between the initial read and the idempotent add: this request still
applies its requested mode instead of returning the concurrent insert's mode.
As with the legacy endpoint, a storage failure between the two writes can leave
an `absent` Observer that a retry will update.

## Errors

- Unknown mode string: `400 invalid_request` at JSON decoding.
- Mode incompatible with actor kind: `400 invalid_participant_mode`.
- Human target does not match the authenticated Human: `403 forbidden`.
- Caller cannot read the Session for self-service: `403 forbidden`.
- Existing Bot update without management authority: `403 forbidden`.
- Missing Bot participant for an authorized manager: `404 participant_not_found`.

## Testing

Add contract, HTTP adapter, Service API, and application tests for:

- the Bot/Human input mode union;
- HTTP acceptance and forwarding of `present`;
- existing Human self mode changes;
- missing Human self first-insert as Observer;
- rejection of another Human even for a Session manager;
- rejection of actor/mode mismatches;
- preservation of Bot `auto/muted` management behavior;
- preservation of missing-Bot behavior.

Regenerate the Gateway-owned `src/gateway/configs/schemas/bcn.openapi.json`
snapshot from the validated source contract and verify deterministic output.

System-message parity with the legacy adapter and path-parameter renaming are
outside this change.
