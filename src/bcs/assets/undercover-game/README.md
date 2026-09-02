# `undercoverGame.UndercoverGamePanel`

A standalone UMD side panel for a public, phase-level 谁是卧底 (Undercover)
game projection. It keeps the generic `bcsPanel.StateMachineRunView`
unchanged and consumes only the existing BCS state-machine/session contracts.

## Usage

Each speech or vote phase is a bounded one-shot state-machine run. Open one
new panel instance for each phase:

```bash
bcs collaboration run ./phase.yaml \
  --session <session-id> \
  --panel-component undercoverGame.UndercoverGamePanel \
  --panel-params @phase-panel-params.json
```

`phase-panel-params.json` is a public JSON object. Use placeholders in public
examples; never put real Bot IDs, credentials, or private endpoints in a
published parameter file.

```json
{
  "runId": "run-example",
  "groupId": "group-example",
  "sessionId": "session-example",
  "gameSessionId": "game-example",
  "phase": "speaking",
  "round": 1,
  "host": { "actorId": "host-example", "displayName": "主持人" },
  "seatOrder": ["player-a", "player-b", "player-c"],
  "players": [
    { "actorId": "player-a", "displayName": "玩家甲", "isHuman": true },
    { "actorId": "player-b", "displayName": "玩家乙" },
    { "actorId": "player-c", "displayName": "玩家丙" }
  ],
  "nodeActorMap": {
    "speech-player-a": "player-a",
    "speech-player-b": "player-b",
    "speech-player-c": "player-c"
  },
  "voteCandidates": [
    { "actorId": "player-b", "displayName": "玩家乙", "eligible": true },
    { "actorId": "player-c", "displayName": "玩家丙", "eligible": true }
  ],
  "apiBaseUrl": "/api/v1/collaboration",
  "currentViewerActorId": "player-a",
  "display": {
    "showTimer": true,
    "showPublicReveal": false,
    "showVoteResults": false
  }
}
```

The caller must supply `runId`, `groupId`, `sessionId`, `phase`, `round`, a
host descriptor, an ordered `seatOrder`, public player descriptors, and the
stable node-to-actor mapping. Missing identity data produces a visible,
recoverable error instead of fabricated state.

## Existing BCS contracts consumed

The asset uses these existing endpoints relative to `apiBaseUrl`:

- `GET /state-machine-runs/{run_id}/graph`
- `GET /state-machine-runs/{run_id}/pending-human-nodes`
- `GET /state-machine-runs/{run_id}/nodes/{node_id}` (lazy selected detail)
- `GET /sessions/{session_id}/messages?include_pending=true`
- `POST /state-machine-runs/{run_id}/nodes/{node_id}/respond` with `{ "content": "..." }`

Responses may be raw JSON or the existing `{ code, message, data, request_id }`
envelope. HTTP failures are surfaced with their status and server message. A
vote is submitted through the existing HumanInput route using a canonical
structured string, for example:

```json
{"kind":"vote","target_actor_id":"player-b"}
```

The state-machine workflow remains responsible for authorization and semantic
validation. A display name is never submitted as a vote identity.

## Public/privacy boundary

Only explicit public panel parameters and explicitly tagged state-machine output
messages are rendered. The panel does not parse natural-language output to
derive phase, actor identity, elimination, roles, words, or votes. Hidden roles,
hidden words, private host reasoning, and unrevealed vote targets/results stay
absent until each field is explicitly marked as publicly revealed.

The example above contains no real UUIDs, tokens, private URLs, hidden game
facts, or remote image references. The package contains only local CSS pixel-art
primitives, with integer scaling and `image-rendering: pixelated`; there is no
runtime remote asset dependency.

## Runtime behavior

The panel polls the current phase run while it is active and stops polling when
the run is completed, failed, or aborted. A manual refresh remains available.
A refresh failure keeps the last successful public snapshot visible. Selecting a
player, host, or bubble opens a detail surface; selected node detail loads lazily
and failure is shown inline without closing the game scene.

Human speech is validated as non-empty and by UTF-8 byte length before submit.
Human votes show only explicit eligible public candidates and require a separate
confirmation click. Stale/conflicting actions are disabled, explained, and
followed by a state refresh.
