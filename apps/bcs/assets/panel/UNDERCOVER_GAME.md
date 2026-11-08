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

The current viewer's pending HumanInput instruction may append a delimited `UNDERCOVER_UI_CONTEXT_V1` JSON block. The panel parses that authenticated, viewer-private boundary to show the owning player's word, speech limits, round/seat context, or vote action. Once received, the viewer's word remains available in component memory after submission while the panel stays mounted; it is scoped to the session, game and viewer and is masked by default. The running-node API cannot provide this word before the viewer's HumanInput becomes active, or recover it after a page reload when no such node is active. Unsupported or malformed recognized blocks produce a safe recoverable error. Private values are not placed in public params, storage, URLs, console output, errors, or `onInteraction` records.

Pending speech nodes may also provide ordered `upstream_artifacts`; sanitized cross-round context comes from public params. The word is hidden by default and cleared whenever the run/action identity changes.

## Player actions

The persistent Action Dock is independent of actor details. Drag its top handle
up or down to resize it, or focus the handle and use the up/down arrow keys. The
input and confirmation controls remain in the dock:

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

## Night-banquet view layer

The side panel renders the delivered 夜宴 (midnight banquet) art: an embedded 836×470 WebP hall backdrop, a judge/host portrait, and six art-calibrated seat slots around the oval table. Every image is embedded in the single UMD bundle; the panel makes no runtime request to an asset host.

### Real table data only

The view is a presentation layer over the existing panel parameters and the BCS state-machine graph. It never invents game data:

- **Seats:** `seatOrder` order plus each player's public `seatNumber`, `displayName`, and state. The six-seat ring is art layout only; other player counts receive a deterministic ring.
- **Public speech:** `publicHistory` and completed `nodeActorMap` node-detail outputs fetched by the existing refresh loop. Player speech is hidden while votes are collected.
- **Current action and private word:** the pending HumanInput node and its viewer-private `UNDERCOVER_UI_CONTEXT_V1` block. The word is hidden until the viewer reveals it and is never placed in public params, storage, URLs, or the rendered public graph.
- **Candidates:** `voteCandidates` / `pkCandidates` with the existing eligibility, no-self-vote, PK restriction, and abstention rules.
- **Timing:** only server deadlines (`pending.timeout_deadline_ms`, `currentAction.deadlineAt`, `params.deadlineAt`) produce a `mm:ss` countdown. Without a deadline the panel shows guidance text; there is no fabricated countdown.
- **Results:** the authenticated public result file feeds `GameOverDialog`; its published V1 summary can reveal roles and words after the game.

No static demo roster, demo word, sample speech, fixed room number, or `location.href` navigation is used.

### Static pages as view states

The six supplied static pages map onto the one live panel:

| static page | live view |
| --- | --- |
| `lobby.html` | seat ring plus the "第一章 · 夜宴" dialogue in the Action Dock while waiting for the host |
| `word.html` | parchment word card in the Action Dock, hidden until the viewer reveals it |
| `describe.html` | speaker dialogue in the Action Dock, speech bubble, private-word input, and the existing validation |
| `discuss.html` | public history and upstream speech details in the Action Dock |
| `vote.html` | candidate cards with portraits, selection reticle, explicit confirmation, and abstain when allowed |
| `result.html` | night-themed `GameOverDialog` with the public winner and summary |

The HUD step row (发词 · 描述 · 讨论 · 投票) is derived from `phase` plus real completed/turn counts. `display.showTimer === false` hides the deadline box.

### Layout

- **wide (≥960px):** two-column layout — the night-banquet stage sits on the left, with the workflow summary and Action Dock on the right.
- **wide/medium stacked (460–959px):** the same stage with smaller portraits; per-seat plates are wide-only and a labeled roster sits below. The dock uses a reduced height so the table remains large.
- **narrow (<460px):** stage vignette plus the labeled roster; the fixed Action Dock still owns dialogue, submit, confirmation, and recovery. While an action is pending on a narrow panel the workflow board starts collapsed to a one-line run summary (the table stage and action entry stay visible) with a keyboard-reachable「查看流程/收起流程」toggle; the step row hides, and the dock uses a compact height so 320×600/360×640 do not need two nested scrolls. Expanding the board shows the same real nodes/edges; switching runs resets it to the default collapsed state.

### Provenance and visual verification

Night-banquet source pixels, crop boxes, and retained licenses are recorded in `assets/THIRD_PARTY_NOTICES.md`, `assets/SELECTION.md`, `assets/ASSET_GUIDE.md`, and `assets/source/night-banquet/README.md`. The committed daily build work is:

```bash
node scripts/generate-atlases.mjs
npm run test:visual
npm run scan:public
npm run verify
```

The scan enforces complete provenance, no tracked source-pack archives, a 120 KiB combined atlas budget, no third-party runtime image URLs, and a 250 KiB minified UMD budget.

### Local design preview

Run `npm exec vite -- --host 127.0.0.1 --port 4178`, then open `http://127.0.0.1:4178/test/undercover-game/visual-preview.html`. The preview uses synthetic players and local mock responses; submissions do not affect a real game. Use `width=320&height=600&mode=speech`, `width=560&height=900&mode=vote`, `width=760&height=900&mode=speech`, `width=1024&height=768&mode=speech`, `width=1300&height=900&mode=speech`, `mode=observe`, `mode=bubbles`, `mode=markers`, `mode=finished`, `mode=pk-vote`, or `mode=pk-speech` in the query string to inspect the view states (the preview accepts widths up to 1400). Every mode also renders the real workflow-board mock: `mode=vote`/`mode=pk-vote` show the parallel `vote_start → vote_N → tally` graph, while speech modes show the serial `speak_N → collect` graph.

### 15-minute local preview and acceptance evidence

The local preview is the fastest way for another contributor to see all five
acceptance states without a BCS deployment. It is a **synthetic preview**
(simulated players and local mock responses), not a live game.

1. `cd apps/bcs/assets/panel` (dependencies must be installed with `npm ci` in
   this worktree).
2. Start the dev server: `npm exec vite -- --host 127.0.0.1 --port 4178`.
3. Open these five URLs (each is a real rendering of the panel; no BCS needed):
   - `...visual-preview.html?width=760&height=1100&mode=speech` — 发言 + 私有词卡（默认 `••••`）+ 真实 workflow 看板；
   - `...visual-preview.html?width=760&height=1100&mode=vote` — 投票候选、弃权项与二次确认；
   - `...visual-preview.html?width=760&height=1100&mode=pk-vote` — 平票 PK 投票（无弃权）；
   - `...visual-preview.html?width=760&height=1100&mode=eliminated` — 正常回合中的出局座位状态（合成 fixture；非终局，workflow 继续显示）；
   - `...visual-preview.html?width=760&height=1100&mode=finished` — 终局弹窗与公开复盘。
4. Narrow checks: `?width=320&height=600&mode=speech`, `?width=320&height=600&mode=vote`,
   `?width=360&height=640&mode=speech`. Expect: no page horizontal overflow,
   the night-banquet stage visible, the action title/input/submit in the dock,
   and the workflow board collapsed to its summary line with a「查看流程」
   button; Tab to it and press Enter to expand the real graph nodes, then Enter
   again to collapse (focus stays on the button).
5. Keyboard smoke test: Tab to the word-reveal button/textarea; submit speech
   with Tab→Enter; in vote use arrow keys to pick a candidate, Tab to the
   confirmation checkbox, Space, Tab to submit, Enter; repeat with 弃权;
   PK vote must not offer abstain. On the finale, Tab cycles inside the dialog
   and Escape returns to the table; `查看终局` reopens it.
6. Privacy spot check: the private word is masked until explicitly revealed,
   and the DOM/visible text must not contain `target_actor_id`, raw vote JSON,
   artifact text, instructions, or node errors.

Review screenshots and machine checks are committed under
[`test/undercover-game/acceptance/`](test/undercover-game/acceptance/):

| State | Evidence | Provenance |
| --- | --- | --- |
| 发言 | `01-speech-synthetic.png` | synthetic visual-preview, 760×1100 |
| 投票（含弃权二次确认） | `02-vote-synthetic.png` | synthetic visual-preview, 760×1100 |
| 平票 PK | `03-pk-vote-synthetic.png` | synthetic visual-preview, 760×1100 |
| 出局 | `04-eliminated-synthetic.png` | synthetic visual-preview, 760×1100 |
| 终局 | `05-finished-synthetic.png` | synthetic visual-preview, 760×1100 |
| 窄屏 320/360（默认收起） | `narrow-320-speech-synthetic.png`, `narrow-320-vote-synthetic.png`, `narrow-360-speech-synthetic.png` | synthetic visual-preview |
| 窄屏展开流程 | `narrow-320-speech-expanded-synthetic.png` | synthetic visual-preview, after activating「查看流程」 |
| 走查动画 | `walkthrough-synthetic.gif` (if generated) | synthetic state sequence |

All images are explicitly labelled synthetic in
`test/undercover-game/acceptance/README.md` and carry a visible
`synthetic preview` watermark; they are not evidence of a live BCS game. Machine-readable checks (overflow/stage/dock/board-collapsed +
keyboard walkthrough) live in `acceptance-results.json` in the same directory.
The reproducible capture tool is `node test/undercover-game/acceptance/capture.mjs`
(Chrome headless + CDP; requires the Vite server above).

**Live-game boundary.** At the time of this phase the local BCS process answered
`GET /health` (`status: ok`, version `0.1.4-beta.3`) but `GET /bots` returned
`[]` and `GET /groups` returned zero items, so no referee/player Bots or game
session exist to start a real undercover round. No live run ID, live screenshot,
or live recording is claimed here; the issue's live acceptance still requires a
deployed BCS with the referee profile and onboarded players. The panel makes no
new server/API/referee changes for this acceptance work.

### Host portrait and speech status


The host/judge appears as a framed portrait above the table with a public nameplate. During speech collection a running player shows only the waveform marker, even if partial public text is already available. Completed, non-pending public speech can expand into a speech bubble; other completed speakers keep clickable small bubbles. Lifecycle markers (vote, eliminated, retry, action required) remain distinct. Preview `?width=760&height=900&mode=markers` to inspect a running speaker alongside completed speeches in the wide composition.

### Player actions

The persistent Action Dock is independent of the actor details:

- speech: private word reveal/hide, real deadline, upstream speeches, public history, Unicode count, empty/length/own-word validation, and one HumanInput submission;
- vote: eligible seat/name cards with portraits, an explicit selection reticle, explicit abstain when the private context allows it, confirmation, and canonical content:
  - `{"kind":"vote","target_actor_id":"player-b"}`
  - `{"kind":"vote","abstain":true}`
- recovery: confirmed `onAction({type:'send_message', content:'卡住了'})` through the existing host bridge.

Server-side HumanInput validation remains authoritative. Expired or conflicting submissions become stale and trigger a refresh. Vote targets and raw structured vote output remain hidden during active collection, including structured ballots that arrive as host node artifacts; host tally prose and the authorized phase-end result remain public.

### State-machine workflow board

The panel shows the current phase's real BCS state-machine graph through the
existing `GET /state-machine-runs/{run_id}/graph` response. The board is a
structural projection only:

- **Read fields:** `run.run_id`, `run.status`, `nodes[].node_id`,
  `nodes[].status`, `nodes[].attempt`, and `edges[].source/target`. It never
  reads `artifact_text`, `instruction`, `display_name`, `error`, words, ballots,
  or vote targets. Raw error values are never rendered; a failed node shows only
  its public `失败` state.
- **Node identity:** `nodeActorMap` maps `speak_N`/`vote_N` to the player's public
  seat nickname and `collect`/`tally` to the host. `vote_start` is a known
  protocol confirmation node that is intentionally absent from `nodeActorMap`,
  so it renders as a neutral "预备/投票开始确认" node instead of a player.
  Nodes missing from the map are shown as `未映射节点` with a degrade notice.
- **Topology:** lanes are derived from the actual graph edges by longest-path
  rank. Regular speech therefore draws the real serial chain of `speak_N`
  nodes and its final `collect`; vote draws `vote_start` → parallel `vote_N` →
  `tally`; PK uses the same graph shapes for the PK candidates/voters that the
  run actually contains. The board never hardcodes node counts or guesses a
  node's status from `phase`.
- **States:** `pending`, `ready`, `running`, `completed`, `failed`, `aborted`,
  `retry_scheduled`, and `skipped` are rendered with distinct labels/tones (`aborted`
  has its own tone even though it shares the terminal semantics of `failed`);
  `attempt > 1` or `retry_scheduled` adds an attempt badge. The run status is
  shown separately from node status. Unrecognized values show `状态未知` rather
  than being inferred.
- **Degradation:** a missing graph shows "尚未获取本阶段运行图"; a graph whose
  `run.run_id` differs from the panel `runId` invalidates and clears the board;
  missing `edges` renders the real node list without inferred order; unknown
  nodes or node kinds, unknown edges, self-loop/cyclic graphs, duplicate edges
  (deduplicated), and unknown statuses show explicit notices and never
  fabricate progress.
- **Layout/privacy:** the board sits above the table stage with horizontally
  scrolling lanes (also in narrow panels) and connector lines/arrowheads drawn
  from the real edges. In every layout and game phase the board starts
  collapsed behind a focusable「查看流程」button (`aria-expanded`/`aria-controls`);
  expanding renders the same real graph and「收起流程」collapses it again. A run
  switch remounts the board and restores the default collapsed state. The board body is height-capped (`min(240px, 30vh)`) and
  scrolls both axes so the night-banquet stage stays reachable on first paint
  even for the taller parallel-vote graph. It contains no live input, so speech/vote submission,
  confirmation, timer, keyboard focus, and recovery behavior are unchanged.

The board is fixture-tested from graphs derived from the actual referee
`render_speak_yaml`/`render_vote_yaml` output: serial speech, parallel vote with
`tally` last, PK speech/vote, failed/retry states, missing graph/edges, unknown
nodes, run switching, and a privacy scan for forbidden artifact/error/word/vote
fields.

### Club room and game finale

The room uses the night-banquet hall, oval table, banners, night windows, and candle lighting delivered for this view, with generated portrait medallions for the six seats. All art remains embedded in the UMD bundle.

New vote panels may specify `resultFile: "undercover-result-v1-r<round>-a<attempt>.json"`. The referee's `reveal` command generates and uploads that immutable public JSON using the existing Session File API. This is a versioned game-to-panel payload, not a new BCS Service API or Plugin API. BCS owns file storage and membership; the game script owns verdicts and disclosure; the panel owns presentation.

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

Only an exact-name, ready file owned by the current host Bot in the current session is consumed. Session, host, round, and vote-render attempt must match panel params. The filename is scoped by the authenticated session and changes on every new round or re-render; no result filename or private result is carried by speech panels. Legacy `Bot`/`Ready` and V1 `bot`/`ready` metadata are supported. Other owners and other sessions are ignored before reading content. Malformed, unsupported, mismatched, or conflicting results show a recoverable error without falling back to prose. Files are limited to 64 KiB. Duplicate identical results are accepted for upload-response recovery.

Both list and content use the configured `apiBaseUrl` and the existing `/sessions/{sessionId}/files` routes. Content is read with same-origin credentials: BCS receives the current login, while redirected presigned storage reads do not send cookies. Presign backends must allow browser reads from the application's origin via their existing CORS configuration; no new proxy endpoint is assumed. Transient network failures display an error and retain conservative prose compatibility. A successfully parsed structured result takes precedence.

A published result represents a finished game independently of the run and chat session closing. A failed `finish` does not remove it. Polling continues for up to five terminal snapshots when a result file is absent or unavailable, then stops; manual refresh can recover a later publication. Completed outputs and a successfully loaded immutable result are cached per mounted run. Panels without `resultFile` make no file requests.

For old games, a completed run still needs an explicit final phase or a non-pending mapped host output containing both an end declaration and a victory verdict. Compatibility includes `终局揭晓`, `本局结束——平民赢了`, and verdicts followed by a separate explanatory sentence, such as `平民胜利！卧底在第一轮就被精准揪出。`. Ordinary phase completion, player claims, unmapped artifacts, conditional rules, quoted verdicts, and contradictory winners do not trigger the dialog.

The finale is a full-panel night-banquet result scene modelled on the static
`ui/result.html` design: chapter kicker, the real winner title, six seat cards
with portraits, two word boxes, the public reason, and the public recap. It
renders only fields that are already public:

- `title`/`winner` come from the structured result (or the legacy prose verdict);
- the winner banner is derived from that public winner and reads 「公开结果未提供获胜方」 when reveal is disabled;
- seat cards and word boxes use the referee's V1 published summary format only after the result file passes session, host, round and attempt checks. Each roster name, seat and word must match exactly; missing, conflicting or unrecognized information remains `未公开`;
- the public `reason` and `summary` are shown verbatim, separate from any
  inferred identity/word data.

The terminal presentation hides the workflow board: it is not rendered while a
finale result is present, and the dialog marks the remaining underlying
`data-game-content` regions inert while focus is trapped. The dialog honors
`showPublicReveal` and `showHostOutput`, traps focus, supports Escape and
"回到圆桌", and can be reopened via "查看终局". Dismissal is scoped to the
mounted game session and survives refreshes and phase updates.

Release the compatible panel first, then activate the updated referee profile for new games. Existing games keep using prose compatibility. This change does not require a BCS server or database migration. The final review can still be read in the host broadcast; the structured dialog summary comes directly from script facts, independently of the model's prose.

Preview `?width=1300&height=900&mode=finished` (wide result scene) or
`?width=320&height=600&mode=finished` (scrollable narrow result), and
`?width=900&height=950&mode=observe` for the club room.

### Eliminated seats vs. terminal presentation

An eliminated player is a normal continuing-round seat state: the seat portrait
is dimmed and marked 「已淘汰」, but the round, later speeches, votes, and the
live workflow graph continue. The public contract has no dedicated "elimination
transition" parameter or elimination screen; deriving one from history would
incorrectly hide the graph for every later round once anyone had been
eliminated. Therefore the panel intentionally keeps the workflow board for
rounds that merely contain eliminated players, and only suppresses it for an
explicit terminal result. Preview `mode=eliminated` is a synthetic seat-state
fixture (it is not a production phase); it keeps the graph for that reason and
must not be read as a production "out display"

### Character speech bubbles

The stage has no baked text overlay. A single expanded pixel speech bubble follows its speaker, using the original nine-slice frame and directional tail SVG assets. Placement uses measured player/host bounds and chooses a nearby clear position, shrinking the frame when needed. Latest output expands automatically; older output stays in small speech markers that can be clicked to review in place. Clicking the expanded bubble opens the full public speech. Text previews are limited to two lines. A new latest output resets manual review; unchanged refreshes retain it. Voting continues to hide player speech bubbles and vote targets. The floating bubble and clickable speech markers are used in the wide composition; medium and narrow panels surface the same public speech through the Action Dock dialogue and the actor detail view, where the stage has no room for a clear bubble. Reduced-motion preferences suppress bubble entrance animation.

When no player input is required, the Action Dock shows the latest public speech or host broadcast with a portrait, nameplate, and public-recipient note so the static description page remains readable inside the live panel.

Use `?width=760&height=900&mode=bubbles&bubbleSeat=5` to preview all speech markers in the wide composition; `bubbleSeat=1` through `6` selects the newest speaker for layout checks. Medium and narrow widths intentionally render no floating bubble or markers and surface speech in the Action Dock dialogue instead.

### Tie-break PK contract

`pk_speaking` and `pk_voting` are subphases of the existing `round`. `attempt` remains a retry counter within the current subphase. PK is never a retry of the ordinary vote and does not invalidate the first ballot.

- `pkCandidates?: string[]` contains the tied players' actor IDs. PK phases require at least two distinct living candidates. The original `seatOrder` is unchanged.
- In PK speech, `turnOrder` and player `nodeActorMap` entries contain only PK speakers. A non-PK Human watches without an input task. PK voting uses all living players; `voteCandidates` contains only PK candidates other than the voter.
- The private vote context has `allowAbstain: false`; PK never displays an abstention option. The referee independently rejects abstention, self-votes, and non-PK targets without requesting replacement votes.
- `publicHistory[].stage?: 'regular' | 'pk'` distinguishes two speech groups in the same round. Omitted stage means regular. Both histories remain inspectable.
- `voteHistory?: Array<{ round, stage, votes, counts }>` carries **already tallied** ballots only. `stage` is `regular` or `pk`; each vote is `{ seatNumber, displayName, text }`, where text is the referee's normalized ballot, and each count is `{ seatNumber, votes }`. These records contain no raw vote reasoning, words, or roles. The next phase carries previous tallies; the current tally is visible through the host broadcast. Terminal summaries include both ballots. In-flight player artifacts still display only "已投票".

PK final files use `undercover-result-v1-r<round>-pk-a<attempt>.json` and include `stage: 'pk'`. The parser matches stage as well as session, host, round, and attempt; a missing stage means regular for existing results. The ordinary file name and existing v1 fields stay compatible. Publish the updated panel before activating this profile for new games; hot migration of an in-flight old collaboration is not validated. No BCS server, shared collaboration protocol, or database change is required.

The night-banquet view renders PK seats with a `PK` badge, keeps the original seat order, and suppresses abstention; the ordinary vote/tally logic, target secrecy, and confirmation behavior are unchanged.

Preview PK with `?width=560&height=800&mode=pk-vote` or `?width=360&height=600&mode=pk-speech`; these fixtures use simulated API responses.

### Scope limits in this phase

- The night-banquet view is verified through jsdom component tests, deterministic atlas checks, and the local Vite preview; it has not been visually regression-tested in a real browser against a live BCS deployment here.
- PK uses the existing contract and is covered by the focused/component fixtures, but only limited PK visual QA was performed in this phase.
- Narrow layout keeps the vignette/roster fallback; the full stage is optimized for wide/medium containers.
- The workflow board is verified from real referee-script graph fixtures and the local preview; it has not yet been validated against a live BCS deployment/run. The phase-4 acceptance screenshots/recording are synthetic previews; no live BCS round could be started locally because the running BCS has zero bots/groups. No BCS Service API, Plugin API, referee script, database, or migration change is introduced.
