# `undercoverGame.UndercoverGamePanel`

A dependency-free React UMD side panel for the existing BCS 谁是卧底 state-machine workflow. The exported component remains `undercoverGame.UndercoverGamePanel`.

## Stable game-session panel

The referee opens one non-closable tab per game session and reuses it for speech, vote, later rounds, and retries. Phase/round remain in the title, while every submission keeps its own run-specific parameter file:

```bash
bcs collaborate run ./phase.yaml \
  --session session-example \
  --panel-component undercoverGame.UndercoverGamePanel \
  --panel-params @phase-panel-params.json \
  --panel-tab-id undercover-game-session-example \
  --panel-tab-title '谁是卧底 · 第 1 轮发言' \
  --panel-tab-closable false
```

Public parameters include the complete original roster, explicit `seatNumber`, stable `seatOrder`, living `turnOrder`, referee node mappings, sanitized `publicHistory`, public rules, and eligible non-self vote candidates. They never include words, roles, raw speech, raw votes, or private reasoning.

## Private HumanInput context

The current viewer's pending HumanInput instruction may append a delimited `UNDERCOVER_UI_CONTEXT_V1` JSON block. The panel parses that authenticated, viewer-private boundary to show the owning player's word, speech limits, round/seat context, or vote action. Unsupported or malformed recognized blocks produce a safe recoverable error. Private values are not placed in public params, storage, URLs, console output, errors, or `onInteraction` records.

Pending speech nodes may also provide ordered `upstream_artifacts`; sanitized cross-round context comes from public params. The word is hidden by default and cleared whenever the run/action identity changes.

## Player actions

The persistent Action Dock is independent of actor details:

- speech: word reveal/hide, deadline, upstream speeches, public history, Unicode count, empty/length/own-word validation, and one HumanInput submission;
- vote: eligible seat/name cards, explicit abstain, confirmation, and canonical content:
  - `{"kind":"vote","target_actor_id":"player-b"}`
  - `{"kind":"vote","abstain":true}`
- recovery: confirmed `onAction({type:'send_message', content:'卡住了'})` through the existing host bridge.

Server-side HumanInput validation remains authoritative. Expired/conflicting submissions become stale and trigger refresh. Vote targets and raw structured vote output remain hidden during active collection.

## Runtime and accessibility

The panel polls non-terminal runs, preserves the last good room on refresh failure, keeps eliminated players in their original seats, maps multiple referee nodes to the host area, and shows neutral speech-to-vote and vote-to-tally/next-round bridge states. Layout follows the panel container using `ResizeObserver` with a resize fallback. Controls have accessible names/states, turn changes use `aria-live`, details restore focus, and reduced-motion mode removes nonessential motion.

## Existing contracts

The asset uses only existing BCS endpoints:

- `GET /state-machine-runs/{run_id}/graph`
- `GET /state-machine-runs/{run_id}/pending-human-nodes`
- `GET /state-machine-runs/{run_id}/nodes/{node_id}`
- `GET /sessions/{session_id}/messages?include_pending=true`
- `POST /state-machine-runs/{run_id}/nodes/{node_id}/respond`

No backend endpoint, graph topology, generic frontend behavior, or runtime dependency is added.

## Real-run output and bounded-layout behavior

The panel does not assume that manager-worker one-shot node outputs are copied into ordinary session messages. For completed nodes explicitly listed in `nodeActorMap`, it incrementally reads the existing authenticated node-detail endpoint, caches by `(runId, nodeId, attempt)`, and merges those results with the session-message compatibility path. Speech artifacts become phase-local player bubbles; vote artifacts become only `已投票` until an authorized host tally/result is public. Host and unknown artifacts are never inferred as player speech.

The panel root is its own bounded viewport. The room scrolls inside the scene region, while the Action Dock uses a fixed header, internally scrollable context body, and non-scrolling footer for submit/confirmation and recovery controls. Actor details use an absolute panel-local overlay, close with Escape, and restore focus to the invoking seat or bubble. Automatic refresh is run-owned and continues after unchanged non-terminal snapshots; it stops on terminal state, run replacement, disabled refresh, or unmount.

## Asset-backed pixel room

Version 1.1 renders a local, atlas-backed indoor game room. The wall/floor, rug, oval table, directional chairs, host podium, window, lamp, shelf, plant, frames, clock, character variants, portraits, bubbles, cards, and lifecycle markers are embedded into the single UMD bundle; the panel makes no runtime image request to an asset host.

The room has three container-driven compositions:

- **wide (≥680px):** landscape room, host, full oval table, and six depth-sorted seats;
- **medium (460–679px):** portrait room retaining the host, table, all seats, labels, and current action;
- **narrow (<460px):** indoor vignette followed by a sprite-backed participant roster.

Crossing a threshold changes presentation only. Speech drafts, vote selection and confirmation, focus restoration, polling, and the fixed Action Dock footer remain owned by the persistent panel component.

### Public state legend

- `（你）` plus the framed portrait: current viewer identity;
- `!` triangle: Human action required;
- waveform: Bot speaking;
- check: speech completed;
- sealed envelope: private vote submitted (never a target);
- cross: eliminated, with the original seat retained;
- circular arrow / warning: retry or error.

Every state includes readable text or a distinct shape and remains understandable with color removed. `prefers-reduced-motion: reduce` stops attention animation while retaining the static marker.

### Provenance and visual verification

Selected CC0 source pixels and retained licenses are under `assets/source/`. Exact source files/cells, transformations, rejected references, and bridge-art rules are documented in `assets/THIRD_PARTY_NOTICES.md`, `assets/SELECTION.md`, and `assets/ASSET_GUIDE.md`.

```bash
node scripts/generate-atlases.mjs
npm run test:visual
npm run scan:public
npm run verify
```

The scan enforces complete provenance, no tracked source-pack archives, a 120 KiB combined atlas budget, no third-party runtime image URLs, and a 250 KiB minified UMD budget.

### Local design preview

Run `npm exec vite -- --host 127.0.0.1 --port 4178`, then open
`http://127.0.0.1:4178/test/visual-preview.html`. The preview uses synthetic
players and local mock responses; submissions do not affect a real game.
Use `width=320&height=600&mode=speech` or `width=560&height=600&mode=vote`
in the query string to inspect input layouts; `mode=observe` shows the waiting
state. The panel preserves pixel room art while using readable typography,
a round progress bar, independent host broadcasts, and a persistent action footer.

### Club room and game finale

The room uses original paneled walls, parquet, brass-trimmed green felt, a geometric rug and warm window lighting with the existing CC0 characters. All art remains embedded in the UMD bundle.

The game-over dialog opens after a completed run only when the phase explicitly denotes a whole-game finish (`complete`, `completed`, `finished`, `game_over`), or the current run has a non-pending mapped host output with an explicit Chinese game-end declaration and a civilian/undercover victory verdict. Ordinary phase completion, player claims, private messages and conditional rule explanations do not trigger it. This is a conservative compatibility path for the existing host prose, not an inference from player count or round number. Other wording remains available in the host broadcast without an automatic popup.

The dialog shows only the existing public host summary and honors `showPublicReveal` and `showHostOutput`. It traps keyboard focus, supports Escape and “回到圆桌”, and can be reopened via “查看终局”. Dismissal is scoped to the mounted game session and survives refreshes and phase updates; no private data is persisted.

Preview `?width=760&height=660&mode=finished` for the finale fixture, or `?width=900&height=950&mode=observe` for the club room.
