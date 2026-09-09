# `bcsPanel.UndercoverGamePanel`

A React side panel in `@avernet-assets/bcs-panel` for the existing BCS 谁是卧底 state-machine workflow. The exported component remains `bcsPanel.UndercoverGamePanel`.

## Stable game-session panel

The referee opens one non-closable tab per game session and reuses it for speech, vote, later rounds, and retries. Phase/round remain in the title, while every submission keeps its own run-specific parameter file:

```bash
bcs collaborate run ./phase.yaml \
  --session session-example \
  --panel-component bcsPanel.UndercoverGamePanel \
  --panel-params @phase-panel-params.json \
  --panel-tab-id undercover-game-session-example \
  --panel-tab-title '谁是卧底 · 第 1 轮发言' \
  --panel-tab-closable false
```

Public parameters include the complete original roster, explicit `seatNumber`, stable `seatOrder`, living `turnOrder`, referee node mappings, sanitized `publicHistory`, public rules, and eligible non-self vote candidates. They never include words, roles, raw speech, raw votes, or private reasoning.

## Vote opening announcement

`openingAnnouncement?: string` is an optional public, script-generated notice for
new vote runs. It contains only phase/round guidance and, on retry, invalidation
of earlier votes. The referee authorizes voting by submitting the run and ends
that activation without an extra opening speech.

The single entry `vote_start` is a neutral acknowledgement by a living player
Bot, followed by parallel `vote_N` nodes and the referee's `tally`. Keep
`vote_start` out of `nodeActorMap`: its output is neither speech nor a vote and
must not affect player completion counts. The panel displays the announcement
only for the matching running run after `vote_start` completes. Before that it
shows preparation; failed/aborted runs show failure rather than completion.
Existing runs without this optional field retain their previous presentation.
No BCS Service or Plugin API changes are required. Deploy the updated panel and
referee profile together for the new notice; old running graphs are unchanged.

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

Game-generated panel parameters omit `apiBaseUrl` and `baseUrl` so the host can
inject its deployment API base (currently `/api/v1/collaboration` in the UMD
host). Without host injection, the game component uses `/bcnproxy` for local
development. Explicit `apiBaseUrl` overrides remain supported. Do not pin a
deployment path in the referee's generated parameters: it prevents host injection.
Existing panel parameters containing the old explicit `/bcnproxy` must be
regenerated with the updated referee script and reopened. A successful HTTP
response containing HTML instead of JSON is reported as an API routing error.

The game panel uses only existing BCS state-machine endpoints:

- `GET /state-machine-runs/{run_id}/graph`
- `GET /state-machine-runs/{run_id}/pending-human-nodes`
- `GET /state-machine-runs/{run_id}/nodes/{node_id}`
- `POST /state-machine-runs/{run_id}/nodes/{node_id}/respond`

No backend endpoint, graph topology, generic frontend behavior, or runtime dependency is added.

## Real-run output and bounded-layout behavior

The panel does not assume that manager-worker one-shot node outputs are copied into ordinary session messages. For completed nodes explicitly listed in `nodeActorMap`, it incrementally reads the existing authenticated node-detail endpoint, caches by `(runId, nodeId, attempt)`, and uses those results as the sole source of current-phase public outputs. It does not request session messages: the deployed session-message API belongs to `/openapi/v1/collaboration`, while state-machine endpoints belong to `/api/v1/collaboration`. Output appears after node completion and the next successful refresh; in-progress message previews are not displayed. Sanitized cross-round history still comes from `publicHistory`. Node-detail failures surface a refresh error, preserve the last good room, and are retried on refresh instead of being silently discarded. Speech artifacts become phase-local player bubbles; vote artifacts become only `已投票` until an authorized host tally/result is public. Host and unknown artifacts are never inferred as player speech.

The panel root is its own bounded viewport. The room scrolls inside the scene region, while the Action Dock uses a fixed header, internally scrollable context body, and non-scrolling footer for submit/confirmation and recovery controls. Actor details use an absolute panel-local overlay, close with Escape, and restore focus to the invoking seat or bubble. Automatic refresh is run-owned and continues after unchanged non-terminal snapshots; it stops on terminal state, run replacement, disabled refresh, or unmount.

## Asset-backed pixel room

The component renders a local, atlas-backed indoor game room. The wall/floor, rug, oval table, directional chairs, host podium, window, lamp, shelf, plant, frames, clock, character variants, portraits, bubbles, cards, and lifecycle markers are embedded into the single UMD bundle; the panel makes no runtime image request to an asset host.

The room has three container-driven compositions:

- **wide (≥680px):** landscape room, host, full oval table, and six depth-sorted seats;
- **medium (460–679px):** portrait room retaining the host, table, all seats, labels, and current action;
- **narrow (<460px):** indoor vignette followed by a sprite-backed participant roster.

Crossing a threshold changes presentation only. Speech drafts, vote selection and confirmation, focus restoration, polling, and the fixed Action Dock footer remain owned by the persistent panel component.

### Public state legend

- `（你）` plus the framed portrait: current viewer identity;
- `!` triangle: Human action required;
- waveform: Bot speaking;
- clickable small speech bubble: completed public speech; no separate check marker;
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
`http://127.0.0.1:4178/test/undercover-game/visual-preview.html`. The preview uses synthetic
players and local mock responses; submissions do not affect a real game.
Use `width=320&height=600&mode=speech` or `width=560&height=600&mode=vote`
in the query string to inspect input layouts; `mode=observe` shows the waiting
state. The panel preserves pixel room art while using readable typography,
a round progress bar, independent host broadcasts, and a persistent action footer.

### Club room and game finale

The room uses original paneled walls, parquet, brass-trimmed green felt, a geometric rug and warm window lighting with the existing CC0 characters. All art remains embedded in the UMD bundle.

New vote panels may specify `resultFile: "undercover-result-v1-r<round>-a<attempt>.json"`.
The referee's `reveal` command generates and uploads that immutable public JSON
using the existing Session File API. This is a versioned game-to-panel payload,
not a new BCS Service API or Plugin API. BCS owns file storage and membership;
the game script owns verdicts and disclosure, and the panel owns presentation.

The V1 payload has these required fields:

```json
{
  "kind": "undercover.game-result",
  "version": 1,
  "status": "finished",
  "gameSessionId": "example-session",
  "hostActorId": "example-host",
  "round": 1,
  "attempt": 1,
  "winner": "civilian",
  "reason": "卧底已经全部出局",
  "summary": "游戏结束！平民阵营获胜。\n公开的词对和身份清单……"
}
```

Only an exact-name, ready file owned by the current host Bot in the current
session is consumed. Session, host, round and vote-render attempt must match
panel params. The filename is scoped by the authenticated session and changes
on every new round or re-render; no result filename or private result is carried
by speech panels. Legacy `Bot`/`Ready` and V1 `bot`/`ready` metadata are supported.
Other owners and other sessions are ignored before reading content. Malformed,
unsupported, mismatched or conflicting results show a recoverable error, without
falling back to prose. Files are limited to 64 KiB. Duplicate identical results
are accepted for upload-response recovery.

Both list and content use the configured `apiBaseUrl` and existing
`/sessions/{sessionId}/files` routes. Content is read with same-origin credentials:
BCS receives the current login, while redirected presigned storage reads do not
send cookies. Presign backends must allow browser reads from the application's
origin via their existing CORS configuration; no new proxy endpoint is assumed.
Transient network failures display an error and retain conservative prose
compatibility. A successfully parsed structured result takes precedence.

A published result represents a finished game independently of the run and chat
session closing. A failed `finish` does not remove it. Polling continues for up
to five terminal snapshots when a result file is absent or unavailable, then
stops; manual refresh can recover a later publication. Completed outputs and a
successfully loaded immutable result are cached per mounted run. Panels without
`resultFile` make no file requests.

For old games, a completed run still needs an explicit final phase or a
non-pending mapped host output containing both an end declaration and a victory
verdict. Compatibility includes `终局揭晓`, `本局结束——平民赢了`, and verdicts
followed by a separate explanatory sentence, such as
`平民胜利！卧底在第一轮就被精准揪出。`. Ordinary phase completion, player claims,
unmapped artifacts, conditional rules, quoted verdicts and contradictory winners
do not trigger the dialog.

The dialog honors `showPublicReveal` and `showHostOutput`, traps focus, supports
Escape and “回到圆桌”, and can be reopened via “查看终局”. Dismissal is scoped to
the mounted game session and survives refreshes and phase updates.

Release the compatible panel first, then activate the updated referee profile
for new games. Existing games keep using prose compatibility. This change does
not require a BCS server or database migration. The final review can still be
read in the host broadcast; the structured dialog summary comes directly from
script facts, independently of the model's prose.

Preview `?width=760&height=660&mode=finished` for the finale fixture, or `?width=900&height=950&mode=observe` for the club room.

### Character speech bubbles

The table has no text overlay. A single expanded pixel speech bubble follows its
speaker, using original nine-slice frame and directional tail SVG assets. Placement
uses measured player/host bounds and chooses a nearby clear position, shrinking
the frame when needed. Latest output expands automatically; older output stays in
small speech markers that can be clicked to review in place. Clicking the expanded
bubble opens the full public speech. Text previews are limited to two lines. A new
latest output resets manual review; unchanged refreshes retain it. Voting continues
to hide player speech bubbles and vote targets. Narrow panels retain the labeled
roster, and reduced-motion preferences suppress bubble entrance animation.

Use `?width=560&height=950&mode=bubbles&bubbleSeat=5` to preview all speech
markers; `bubbleSeat=1` through `6` selects the newest speaker for layout checks.

### Host sculpture and speech status

The host is an original stone knight centered against the north wall, with a
pedestal and a compact plaque beneath the club sign. Narrow layouts use a smaller
sculpture alongside the plaque. The host still opens the same public detail view.

During speech collection, a running player has only the waveform marker, even if
partial public text is already available. Completed, non-pending public speech can
expand into a bubble; other completed speakers have clickable small bubbles. The
completed-speech check sprite and check prefix are removed. Other lifecycle markers
(vote, eliminated, retry, action required) remain distinct. Preview `mode=markers`
to inspect a running speaker alongside completed speeches.
