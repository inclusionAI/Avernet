# Task trajectory replay

## Problem

The trajectory service exposes a persisted event timeline, but consumers cannot obtain
an explicit playback clock. Rebuilding timing independently in every client risks
inconsistent ordering, speed scaling, and pagination behavior.

## Scope and semantics

Replay is a **read-only projection of persisted trajectory events**. It does not rerun
bots, tools, callbacks, dispatch, verification, or any task state transition. It also
does not trigger trajectory analysis. Both centralized-planning and Relay events are
supported because replay consumes the common canonical `TaskTrajectory.timeline`.

The service returns frames in the canonical timeline order produced by the trajectory
assembler. Each frame contains:

- `sequence`: its zero-based position in the complete timeline;
- `offset_ms`: `(event.gmt_create - first.gmt_create) / playback_rate`, rounded to the
  nearest millisecond;
- `delay_ms`: the difference between this frame's scaled offset and the preceding
  source frame's offset (zero for sequence 0);
- the original trajectory event projection.

The response also reports the source start/end timestamps, scaled full duration,
requested page start, and total frame count. `from_sequence` and `limit` slice the
returned frames without renumbering sequences or resetting offsets. An empty timeline
returns no frames, duration zero, and null source timestamps.

## Validation

- `playback_rate`: 0.1 through 10.0 inclusive;
- `from_sequence`: zero or greater;
- `limit`: optional, 1 through 1000 inclusive.

The core projection validates the same constraints as the HTTP boundary.

## Service API

`TaskContextServiceProtocol.replay_trajectory(...)` is the external Service API. The
facade reads the canonical trajectory with `do_analysis=False` and applies the pure
replay projection.

## HTTP API

Additive endpoints:

- `GET /api/v1/collaboration/tasks/trajectory/replay`
- `GET /openapi/v1/collaboration/tasks/trajectory/replay`

Query parameters are `task_id`, `playback_rate`, `from_sequence`, and `limit`. The
public endpoint uses the existing principal authentication contract. Both return the
standard envelope containing the same replay DTO.

## Contract propagation and compatibility

- Consumer: internal and public HTTP task adapters.
- Contract: `TaskContextServiceProtocol.replay_trajectory`.
- Implementation: `TaskContextService` plus the transport-agnostic replay projection.
- Persistence: existing trajectory timeline; no schema or migration change.
- Compatibility: additive method and endpoints; existing trajectory reads and event
  writes are unchanged.
