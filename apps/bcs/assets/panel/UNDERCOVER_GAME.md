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

### Tie-break PK contract

`pk_speaking` and `pk_voting` are subphases of the existing `round`.
`attempt` remains a retry counter within the current subphase. PK is never a retry
of the ordinary vote and does not invalidate the first ballot.

- `pkCandidates?: string[]` contains the tied players' actor IDs. PK phases require
  at least two distinct living candidates. The original `seatOrder` is unchanged.
- In PK speech, `turnOrder` and player `nodeActorMap` entries contain only PK
  speakers. A non-PK Human watches without an input task. PK voting uses all living
  players; `voteCandidates` contains only PK candidates other than the voter.
- The private vote context has `allowAbstain: false`; PK never displays an
  abstention option. The referee independently rejects abstention, self-votes and
  non-PK targets, without requesting replacement votes.
- `publicHistory[].stage?: 'regular' | 'pk'` distinguishes two speech groups in
  the same round. Omitted stage means regular. Both histories remain inspectable.
- `voteHistory?: Array<{ round, stage, votes, counts }>` carries **already tallied**
  ballots only. `stage` is `regular` or `pk`; each vote is
  `{ seatNumber, displayName, text }`, where text is the referee's normalized
  ballot, and each count is `{ seatNumber, votes }`. These records contain no raw
  vote reasoning, words or roles. The next phase carries previous tallies; the
  current tally is visible through the host broadcast. Terminal summaries include
  both ballots. In-flight player artifacts still display only “已投票”.

PK final files use `undercover-result-v1-r<round>-pk-a<attempt>.json` and include
`stage: 'pk'`. The parser matches stage as well as session, host, round and attempt;
a missing stage means regular for existing results. The ordinary file name and
existing v1 fields stay compatible. Publish the updated panel before activating
this profile for new games; hot migration of an in-flight old collaboration is
not validated. No BCS server, shared collaboration protocol or database change
is required.

Preview PK with `?width=560&height=800&mode=pk-vote` or
`?width=360&height=600&mode=pk-speech`; these fixtures use simulated API responses.

## 可换肤主题：作战指挥台（Ops 终端风副屏）

仓库自带一套与像素俱乐部房并列的备用副屏主题 **`bcsPanel.UndercoverGameOpsPanel`**（作战指挥台/终端风）。它复用同一套数据层 —— `api.ts`、`viewModel.ts`、`actionContext.ts`、`contracts.ts`（不改动任何数据层、主持脚本或 BCS 接口），只替换展示/交互视图。

### 主题定位与视觉

- 高对比“指挥终端”排版，配色全部取自工作台共享语义 token（`--background`、`--card`、`--foreground`、`--border`、`--primary`、`--success`、`--warning`、`--destructive`、`--info`、`--muted-foreground`），随亮/暗主题自动切换，不再自带独立字面色板。居右圆桌换成一个**左右分层的工作流图**，玩家状态用“座位表”式行卡呈现。
- 左边是**本轮实际运行的 workflow 图**：发言 run 呈现为按回合顺序连号的串行传递闭包链（后手可读齐前序原话）；投票 run 呈现为「入口 → 各玩家并行 `vote_N`（彼此无边）→ 主持人 `tally`」的末节点计票结构。
- 全部通过 CSS 自定义属性（`var(--bg/--panel/--accent/--run/--done/--bad/…)`）驱动，这些属性只是上面共享语义 token 的别名，因此调色跟随工作台主题；`prefers-reduced-motion` 关闭动画。
- `showHostOutput` 开启时保留**主持人公开播报**区（最新一条直接展示，更早的折叠收纳），计票、出局/PK 公告与阶段交接不会在 Ops 视图中消失。
- 独立保留**历轮公开发言**区，按轮次/PK 阶段分组折叠展示 `publicHistory`；某玩家投完票后行卡只剩“已投票”时，此前描述仍可从历史区回读。

### 覆盖的 5 个状态

1. **发言**：公开文本框 + 词隐藏/显示 + 上游发言 + 轮次/字数约束；窄屏(≤360px)自动压缩图与表。
2. **投票（含弃权二次确认）**：候选玩家 `vote_N` 卡片 + 「弃权」；需勾选“我已确认”二次确认后才可提交，**改选候选人或切换到弃权会立即重置确认**，不会把确认带给新的票；PK 阶段根据 `allowAbstain:false` 隐藏弃权。
3. **平票 PK**：`pk_speaking / pk_voting` 阶段；候选人仅限 `pkCandidates` 且不可自投；已公布的两轮票面（常规 + PK）以“已公布票面”区展示。
4. **出局**：被淘汰玩家保留原始座位号，行卡加删除线 + 红边 + “出局”徽标。
5. **终局**：结构化 `resultFile`（或旧版主持人判词兼容）展示平民/卧底阵营获胜，并在判词旁显示公开复盘总结（词对与复盘，`result.summary`），作为独立结果卡；所有终局信息仅在公开 `showPublicReveal`（总结另受 `showHostOutput`）开启时展示。

### Workflow 视图接入方式

面板通过既有 `GET /state-machine-runs/{run_id}/graph` 拿到当前 run 的 `nodes + edges`，用 `nodeActorMap` 将节点映射到座位/主持人，再做跨栏 `Kahn` 分层：
- 发言 run：`speech_{i}` 依次从左到右排列，`collect`（聚焦）作为末列最后一次汇合；
- 投票 run：`vote_start` 为入口列，`vote_N` 同一列并行（彼此无依赖边），`tally` 强制放到**最终列**，让“计票最后完成”在图上直观可见。
- 图例区分运行中/完成/失败/等待，节点按状态着色；该视图只展示节点与公开标签，不暴露发言原文或票向（详见下方隐私边界）。

### 隐私边界（与像素房一致）

- 词、身份、原始发言、原始票面只通过本人 HumanInput 的 `UNDERCOVER_UI_CONTEXT_V1` 认证上下文进入副屏；**不写入 localStorage、URL、console、`onNode` 等任何可回溯位置**（由新增 `ops-theme.mjs` 测试断言）。
- 投票进行中，房间视图与 workflow 视图都**不显示票向**和未遮蔽的发言原文；节点详情仅显示“已投票”或公开主持判词。
- 复用现有 `visual-preview` 的 mock 数据层，可在浏览器直接切换 `?theme=ops&mode=…` 查看五种状态。

### 如何复现（≤15 分钟）

```bash
cd apps/bcs/assets/panel
npm exec vite -- --host 127.0.0.1 --port 4178
# 打开 http://127.0.0.1:4178/test/undercover-game/visual-preview.html?theme=ops&mode=speech&width=760&height=760
# 右上角可一键切换 theme=club / theme=ops
```

状态速览：`mode=speech` 发言、`mode=vote` 投票（含弃权确认）、`mode=pk-vote` 平票 PK、`mode=eliminated` 出局、`mode=finale` 终局，`narrow` 窄屏用 `width=320`。

`npm run verify` 会额外执行新增的 `test/undercover-game/ops-theme.mjs`（主题生命周期 + 隐私 + workflow 布局）。

### 主题说明与答案（给评委）

- **为什么发言图用传递闭包而不是链？** 发言 run 中每个玩家节点直接连向其后所有尚未发言的玩家（而非仅相邻节点）。这样后发言的玩家能拿到本轮**全部前序原话**（而不仅是上一位），证据图里呈现为逐步变宽的“扇出”，而不是线性顺序——体现的是“每个新发言人看到的信息增量”这一多智能体语义。
- **为什么主持人必须是末节点、不能在汇总节点里直接开投？** BCS 契约要求恰好一个零入度入口与一个 final_output 末节点，且单个 run 内部不允许并行入口。主持人若在 `collect/tally` 汇总里直接“开投”，就会把两轮阶段塞进同一个 run，破坏“发言一次收齐 → 结束 → 再开投票 run”的槽位隔离与并发契约。因此必须让汇总/计票作为本 run 的**末节点**结算，再把结果回灌给主持人会话去启动下一个投票 run。
