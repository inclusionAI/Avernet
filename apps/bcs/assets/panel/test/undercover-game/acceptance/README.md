# Phase-4 acceptance evidence — undercover game panel (#2608)

**Scope: synthetic preview only. This is not live-game evidence.**
Every screenshot, measurement and recording below was produced from the repository's own
synthetic preview harness (`apps/bcs/assets/panel/test/undercover-game/visual-preview.tsx`)
running in a real headless Google Chrome. The six players, run/session ids, host artifact
and vote payload are fetch mocks; no BCS service was connected. A live BCS run with real
bots/groups is still required for the issue's live acceptance. At capture time the local
BCS health endpoint responded but had **0 bots / 0 groups**, so a real game could not be
started (the capture script itself never calls BCS).

- Branch: `codex/issue-2608-jrpg-ui`
- Captured: 2026-11-04 (Asia/Shanghai), Google Chrome `156.0.8078.80` headless
- Environment: Node `v26.12.1`, CDP viewport emulation (`Emulation.setDeviceMetricsOverride`, DPR 1), Pillow `12.3.0`

## Files

| File | Size | Synthetic label | What it shows |
| --- | --- | --- | --- |
| `capture.mjs` | 208 lines | n/a | Single entry point: launches Chrome, orchestrates layout/keyboard/screenshot/recording steps and writes the results JSON |
| `acceptance-results.json` | 152 KB | `synthetic: true` | All measured rects, checks, keyboard steps, privacy scan, recording metadata |
| `01-speech-synthetic.png` | 760×1000, 308 KB | `-synthetic` filename + visible bottom watermark | Wide speech turn at 760×1000: masked word card (••••), textarea, submit footer |
| `02-vote-synthetic.png` | 760×1000, 233 KB | `-synthetic` filename + visible bottom watermark | Wide vote turn: candidate radios, 弃权 (abstain) card, 确认 (confirmation) control, 确认并投票 submit |
| `03-pk-vote-synthetic.png` | 760×1000, 222 KB | `-synthetic` filename + visible bottom watermark | Wide PK vote turn: PK badge, single PK candidate, confirmation, **no abstain card** |
| `04-eliminated-synthetic.png` | 760×1000, 404 KB | `-synthetic` filename + visible bottom watermark | Wide eliminated state: 6号 is rendered as 已淘汰 (✕) while the room stays live |
| `05-finished-synthetic.png` | 760×1000, 162 KB | `-synthetic` filename + visible bottom watermark | Wide finished state: opening `mode=finished` directly opens the real `GameOverDialog` with synthetic finale + 回到圆桌 |
| `narrow-320-speech-synthetic.png` | 320×600, 99 KB | `-synthetic` filename + visible bottom watermark | Exact 320×600 speech: board collapsed with visible **查看流程** toggle, stage visible, focused textarea + dock footer visible |
| `narrow-320-speech-expanded-synthetic.png` | 320×600, 70 KB | `-synthetic` filename + visible bottom watermark | Same 320×600 page after activating 查看流程: real node cards/edges expanded, **收起流程** toggle focused, dock footer still visible |
| `narrow-320-vote-synthetic.png` | 320×600, 96 KB | `-synthetic` filename + visible bottom watermark | Exact 320×600 vote: dock body scrolled so the 弃权 card + 确认 control + submit footer are all in view |
| `narrow-360-speech-synthetic.png` | 360×640, 118 KB | `-synthetic` filename + visible bottom watermark | Exact 360×640 speech: collapsed board + 查看流程, stage visible, focused textarea + dock footer |
| `walkthrough-synthetic.gif` | 360×640, 1.74 MB | Watermark on every frame | Synthetic screencast of the narrow keyboard walkthrough, incl. expand/collapse |

Alt text: each image above is described by its "What it shows" column; e.g. `02-vote-synthetic.png` alt: "Synthetic 760×1000 preview of the undercover game vote dock showing five candidate cards, a 弃权 abstain card, a 确认：尚未选择 confirmation row and a 确认并投票 button."

The preview harness adds 12 px padding around the panel, so a screenshot's browser viewport is the exact target size but the panel surface is 24 px smaller (760×1000 viewport → 736×976 panel; 320×600 → 296×576; 360×640 → 336×616). The harness also clamps its internal frame height to 1000 px, which is why the wide captures use **760×1000** instead of 760×1100 (1100 renders an 88 px empty band; the task allowed 760×1000 in that case).

## How to reproduce

Requirements: a Vite dev server serving `apps/bcs/assets/panel` at `http://127.0.0.1:4178`, Google Chrome, Node ≥ 22 (global `WebSocket`), Python with Pillow (only for the GIF and for optimizing any PNG over 600 KB). Verified with Chrome 156, Node 26, Pillow 12.3.

```bash
# terminal 1 — preview server (do not start a second server if one already runs on 4178)
cd apps/bcs/assets/panel
npx vite --host 127.0.0.1 --port 4178

# terminal 2 — capture everything (~2.5 min; same single entry point/output contract)
cd apps/bcs/assets/panel
node test/undercover-game/acceptance/capture.mjs
```

The entry script is split by responsibility so every source file stays under the
repository's 1,000-line rule (all are < 350 lines):

| Module | Lines | Responsibility |
| --- | --- | --- |
| `capture.mjs` | 208 | single CLI entry; orchestrates the run and aggregates `acceptance-results.json` |
| `lib/runtime.mjs` | 59 | constants, optional `python3+Pillow` detection, git info, sleep/hash helpers |
| `lib/cdp.mjs` | 181 | raw CDP WebSocket client, headless Chrome launch/targets, page open/ready helpers |
| `lib/audit.mjs` | 230 | in-page measurement expression, layout assertions, privacy scan |
| `lib/screenshot.mjs` | 59 | viewport screenshot + optional Pillow PNG optimization |
| `lib/keyboard.mjs` | 326 | real CDP key events, per-scenario keyboard walkthrough, toggle state probes |
| `lib/layout.mjs` | 338 | narrow/wide layout sweep, toggle evidence, five wide screenshots, narrow screenshots |
| `lib/recording.mjs` | 166 | CDP screencast capture, frame merging, synthetic GIF assembly |

Environment overrides: `PREVIEW_BASE` (default `http://127.0.0.1:4178`), `CHROME_PATH` (default macOS Google Chrome app), `PYTHON` (default `python3`; the script probes `import PIL` and, if Pillow is missing, clearly warns and skips GIF/PNG optimization instead of failing), `SKIP_GIF=1` to skip the recording.

Every captured PNG gets a visible `SYNTHETIC PREVIEW · NOT A LIVE GAME` bar in the page's 12px bottom padding (annotation only; it does not modify mock data). The script writes the nine screenshots, `walkthrough-synthetic.gif` and `acceptance-results.json` next to itself. It launches its own headless Chrome with a temporary `--user-data-dir` and a random CDP port and exits non-zero if any check fails.

Notes on the preview-only behavior (no repository files outside `acceptance/` are touched):

- `visual-preview.html?...&mode=finished` is self-contained: its mock now serves the real vote graph plus a synthetic public host finale, so opening that URL directly opens the real `GameOverDialog`. The capture script does **not** wrap or inject business data; the dialog, focus trap and closing logic are the real panel source.
- All other captures use the untouched harness mock.

## Layout evidence (measured with `getBoundingClientRect`, DPR 1)

All checks below pass; full per-check detail with every rect is in `acceptance-results.json`
(`wideLayoutChecks`, `layoutSweep`, `toggleEvidence`).

### Wide (760×1000 viewport, 736×976 panel surface)

| Capture | mode | panel | stage `[data-room-layer="night-hall"]` | workflow board | action dock | dock footer / submit | overflow |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `01-speech` | speech | 736×976 @ (12,12) | 705×396.6 @ (26,308.1) | present, expanded (not collapsed) | `data-action-active="true"` 708×380 @ (26,594) | footer 704×57 @ (28,915); submit 82.9×38 @ (42,925) | 760 ≤ 760+1 |
| `02-vote` | vote | 736×976 @ (12,12) | 705×396.6 @ (26,449.1) | present, expanded | `true` 708×380 @ (26,594) | footer 704×57 @ (28,915); submit 95.6×38 @ (42,925) | 760 ≤ 760+1 |
| `03-pk-vote` | pk-vote | 736×976 @ (12,12) | 705×396.6 @ (26,449.1) | present, expanded | `true` 708×380 @ (26,594) | footer 704×57 @ (28,915); submit 95.6×38 @ (42,925) | 760 ≤ 760+1 |
| `04-eliminated` | eliminated | 736×976 @ (12,12) | 705×396.6 @ (26,308.1) | present, expanded | `false` (no pending action) | footer 704×57 @ (28,915) | 760 ≤ 760+1 |
| `05-finished` | finished | 736×976 @ (12,12) | 705×396.6 @ (26,365.9) | present, expanded | `false` | dialog `role="dialog" aria-label="游戏结束"`, focus inside, 3 background regions `inert` | 760 ≤ 760+1 |

Wide screenshots are "wide composition" (`data-layout="wide"`). In the vote/PK-vote captures the 8-node workflow board is taller, so the Scene scroll container clips the lower part of the stage; the stage rect is still full height (396.6 px) and intersects the viewport, and the vote inputs are fully visible. This is the real layout, not a capture artefact.

### Narrow action modes (exact viewport emulation)

| Viewport | mode | panel | stage | board | toggle | dock footer / submit | overflow |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 320×600 | speech | 296×576 @ (12,12) | 273×119.4 @ (22,222.1) | `data-workflow-collapsed="true"`, 0 node cards | `查看流程`, `aria-expanded="false"`, rect x33 y178.1 w61.6 h26 | footer 272×57 @ (24,519); submit 82.9×38 @ (38,529) | 320 ≤ 320+1 |
| 320×600 | vote | 296×576 @ (12,12) | 273×119.4 @ (22,222.1) | collapsed, 0 nodes | same toggle rect (x33 y178.1) | footer 272×57 @ (24,519); submit 95.6×38 @ (38,529) | 320 ≤ 320+1 |
| 320×600 | pk-vote | 296×576 @ (12,12) | 273×119.4 @ (22,222.1) | collapsed, 0 nodes | same toggle rect | footer 272×57 @ (24,519); submit 95.6×38 @ (38,529) | 320 ≤ 320+1 |
| 360×640 | speech | 336×616 @ (12,12) | 313×136.9 @ (22,222.1) | collapsed, 0 nodes | `查看流程`, rect x33 y178.1 w61.6 h26 | footer 312×57 @ (24,559); submit 82.9×38 @ (38,569) | 360 ≤ 360+1 |
| 360×640 | vote | 336×616 @ (12,12) | 313×136.9 @ (22,222.1) | collapsed, 0 nodes | same toggle rect | footer 312×57 @ (24,559); submit 95.6×38 @ (38,569) | 360 ≤ 360+1 |
| 360×640 | pk-vote | 336×616 @ (12,12) | 313×136.9 @ (22,222.1) | collapsed, 0 nodes | same toggle rect | footer 312×57 @ (24,559); submit 95.6×38 @ (38,569) | 360 ≤ 360+1 |
| 320×600 | finished | 296×576 @ (12,12) | 273×119.4 @ (22,436.7) | expanded, 7 nodes | not rendered (no pending action) | result dialog open, `role="dialog" aria-label="游戏结束"` | 320 ≤ 320+1 |
| 360×640 | finished | 336×616 @ (12,12) | 313×136.9 @ (22,436.7) | expanded, 7 nodes | not rendered | result dialog open | 360 ≤ 360+1 |

At 320×600 the action dock keeps the footer visible (`dock-footer` bottom 576 < 600) while the dock body is internally scrollable (`scrollHeight 397 > clientHeight 116` in vote). `narrow-320-vote-synthetic.png` scrolls the dock body to `scrollTop 281` so the abstain card and confirmation control are both in view (`abstainCardInView=true`, `confirmationControlInView=true`).

## Workflow board toggle (narrow + pending action)

Observed live DOM contract at 320×600 `mode=speech` (all six narrow action combinations 320/360 × speech/vote/pk-vote pass):

| State | `data-workflow-collapsed` | `data-workflow-expanded` | toggle | node cards | edges | focus | dock footer / submit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| default | `"true"` | `"false"` | `查看流程`, `aria-expanded="false"`, `aria-controls="wf-content-_r_1_"` (React `useId`, value varies), target exists, rect x33 y178.1 w61.6 h26, inside viewport, `tabIndex=0` | 0 | 0 | textarea auto-focused | visible (footer y519–576, submit y529–567) |
| after Enter/Space activation | `"false"` | `"true"` | `收起流程`, `aria-expanded="true"`, same `aria-controls` target, rect x35 y181.1 w61.6 h26 | 7 (`[data-workflow-node="true"]`: `speak_1…speak_6`, `collect`) | 21 | stays on the toggle | visible (footer y519–576, submit y529–567) |
| activated again | `"true"` | `"false"` | `查看流程`, `aria-expanded="false"` | 0 | 0 | stays on the toggle | visible |

- Keyboard reachability: from the auto-focused textarea, `Tab` × 4 reaches the toggle in DOM order (`提交发言` → `告诉主持人卡住了` → header `↻ 刷新` → `查看流程`); `Enter` expands, `Space` collapses (both real CDP key events in the walkthrough below).
- With the board collapsed **and** expanded, the dock footer/submit button still intersect the 320×600 viewport (`footerVisible=true`, `submitVisible=true`), because the dock is outside the scrollable Scene; `documentElement.scrollWidth` stays 320.
- Run switch reset: the preview page uses one fixed `runId`, so the browser cannot switch runs in place. The repository's jsdom test was run and passes: `cd apps/bcs/assets/panel && node test/undercover-game/workflow-board.mjs` → "Workflow board tests passed: … run switching …", including `run switch does not reuse the old expanded state` (`data-workflow-expanded="false"` afterwards). A fresh page load also remounts the board collapsed (covered by the layout sweep).
- Wide layout / no pending action: the toggle is not rendered (`workflowToggle.present=false`) and the board shows the full real graph.

## Keyboard walkthrough (real CDP `Input.dispatchKeyEvent`, no jsdom)

45 steps total, all pass, 0 console errors after ignoring the pre-existing `favicon.ico` 404. "Submitted" means the dock rendered `✓ 已提交，等待其他玩家` / `提交成功，等待其他玩家和主持人。` after the real POST mock accepted.

| # | Scenario | Viewport | Exact keys (after page load auto-focus) | Result |
| --- | --- | --- | --- | --- |
| 1 | speech | 760×1000 | `Input.insertText("在忙碌的一天结束之后才会用到")` → `Tab` → `Enter` | PASS — textarea 14/25 字; Tab focuses 提交发言; Enter submits |
| 2 | vote | 760×1000 | `ArrowDown` → `Tab` → `Space` → `Tab` → `Enter` | PASS — candidate 3号 selected; Tab→checkbox; Space checks; Tab→确认并投票; Enter submits |
| 3 | abstain | 760×1000 | `ArrowUp` → `Tab` → `Space` → `Tab` → `Enter` | PASS — `ArrowUp` wraps to `__abstain__`, label 确认：本轮弃权; confirm + submit succeed |
| 4 | pk-vote | 760×1000 | `Space` → `Tab` → `Space` → `Tab` → `Enter` | PASS — Space selects the single PK candidate; **no `input[value="__abstain__"]` exists**; confirm+submit succeed |
| 5 | finished | 760×1000 | `Tab` → `Tab` → `Tab` → `Escape` → `Enter` | PASS — dialog auto-opens, focus starts on 回到圆桌; Tab cycles 终局公开复盘 ↔ 回到圆桌 and never leaves the dialog; 3 background regions inert; Escape closes and restores focus to 查看终局; Enter reopens |
| 6 | narrow speech + toggle | 320×600 | `Tab`×4 → `Enter` (expand) → `Space` (collapse) → `Shift+Tab`×5 → `Input.insertText(...)` → `Tab` → `Enter` | PASS — toggle focused at Tab 4; 0→7 node cards→0; focus stays on toggle; focus returns to textarea; speech submitted |

Exact per-step evidence (active element descriptors, key sequence array, DOM snapshots) is in
`acceptance-results.json` → `keyboardWalkthrough`. The only console/network error observed on
every page is the pre-existing `GET /favicon.ico 404`; it is excluded by name and recorded
by name (`isIgnoredConsoleNoise`) and every captured page records 0 filtered console
errors. No Vite/React dev warnings surfaced (the harness builds with
`process.env.NODE_ENV=production`).

Narrow speech focus evidence (320×600): the textarea (38,452.1, 240×84) is focused and
intersects the dock body (24,403.5, 272×115.5, scrollTop 140) while the dock footer (24,519,
272×57) remains inside the viewport.

## Privacy scan

Needles scanned in `document.body.innerText`, `document.documentElement.outerHTML` **and every
DOM attribute** on all nine captured pages:

the preview's synthetic private word (not reproduced here), `target_actor_id`, `"kind":"vote"`, `artifact_text`, `instruction` — **0 hits**.

- The speech word card renders only `••••` (`wordCardHasMaskedWord=true` on all speech pages) and the 显示词语 button was never activated (`aria-pressed="false"`).
- Raw ballots stay inside the harness's in-memory fetch mock; the panel sanitizes non-host vote artifacts to `已投票`, which is what the room roster shows. Published vote history (PK mode, collapsed `<details>`) contains only the synthetic players' public statements, no `target_actor_id`/node artifact payloads.
- This is a DOM/visible-text scan, not OCR; pixel-level leakage was reviewed visually on the nine PNGs above.

## Recording (synthetic, not a live game)

`walkthrough-synthetic.gif` — 360×640, **40 GIF frames**, 16.4 s loop, 1.74 MB, effective
**2.44 fps**, minimum frame delay 130 ms (≤ 8 fps), Pillow-assembled from real
`Page.startScreencast` JPEG frames captured while driving the preview in Chrome. Every frame
carries the watermark **"SYNTHETIC PREVIEW - not a live game"**.

Sequence: narrow speech default (查看流程 visible) → Enter expands the real graph nodes → Space
collapses → type safe speech → submit → vote turn (select, confirm, submit) → finished dialog →
Escape → Enter reopens. It is a screen recording of the synthetic preview, so it shows layout,
focus behaviour and state transitions; it is **not** evidence of a live BCS game and has no
audio.

## Bounds, gaps and known harness quirks

- **No live BCS evidence.** This capture never contacts BCS. A separate local health check found 0 bots and 0 groups, so no live game could be started; live acceptance still requires a real BCS run and a real-game recording.
- Screenshots are 760×1000 rather than 760×1100 because the preview harness clamps its frame height to 1000 px; the target viewports for narrow checks are exact 320×600 and 360×640 via CDP device-metrics emulation.
- Panel surface is 24 px smaller than the screenshot viewport due to the harness's `#root` padding; measured rects above account for it.
- The workflow board / night hall / dock live in a scrollable Scene; in action modes with a tall board the stage is partially clipped by the Scene scroll viewport (stage rect still full height and visible), and expanding the board at 320 scrolls the stage out of view while the dock stays fixed. Both are intentional layout behaviour, and the screenshots show it.
- `finished` opens the real `GameOverDialog` directly from the preview mock (vote graph + synthetic public host finale); the capture script does not inject business data.
- `git diff --check` is clean. The acceptance capture is part of the same panel change as the narrow-layout, workflow-toggle, preview, test, and documentation updates.

## Result

`acceptance-results.json` → `summary.pass = true`:
9/9 screenshots, 8/8 narrow layout rows + 5/5 wide layout rows, 6/6 toggle evidence blocks
(66 individual toggle assertions), 45/45 keyboard steps, privacy scan 0/0 hits,
recording assembled. No failed checks or blockers.
