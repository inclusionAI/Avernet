# Group chat activity controls

Status: implemented and locally verified. The user approved the designs and
layout thresholds in this chat on 2026-10-14.

Scope: next-gen group chat presentation only. Existing BCS abort and delivery
cancellation contracts, routing, and scheduling remain authoritative.

## Accepted design

- Each Bot has one current reply and may have several queued messages.
- A compact activity bar above the composer summarizes replying Bots and queued
  deliveries. A source message addressed to two Bots contributes two deliveries.
- The processing list opens above the bar. One active Bot uses a single column;
  two use parallel columns; three or more use a vertical Bot list and the
  selected Bot's queue. Count includes Bots with only queued deliveries.
- Layout responds to the panel's actual width. Narrow panels stack vertically.
- Replying messages and replying Bots show a small motion-sensitive activity
  icon instead of repeated progress text or elapsed time.
- Stop sits beside each Bot's identity. Queued messages retain individual cancel
  actions. Repeated waiting explanations and the explanatory footer are removed.

## Acceptance

1. Opening the list does not stop a Bot. Stopping a selected Bot invokes the
   existing Bot-scoped callback; cancelling invokes the selected message and
   delivery IDs without cancelling other recipients or deleting the message.
2. Pending stop/cancel actions are disabled independently. Replies and queue
   entries remain visible until their authoritative state changes.
3. Selecting another Bot reveals only its queue. If that Bot leaves the activity
   list, selection falls back to an existing Bot. Switching sessions resets the
   open panel and selection. An empty activity list occupies no space.
4. Processing deliveries count as replying even before streaming messages
   arrive; streaming and processing records for one Bot are counted once.
5. Every currently streaming assistant message shows its icon independently of
   its position in history. Completed/aborted/user messages show no activity icon;
   aborted replies retain their content and the `已停止` terminal label.

## Validation

- All 494 frontend test suites passed (3,570 tests), including page integration,
  independent stop/cancel, selected-Bot removal, and per-message activity icons.
- TypeScript, frontend lint, production build, and Open Core artifact checks
  passed. Modified/added production components remain below 300 lines.
- The real React components and production CSS were verified in Chromium with
  simulated delivery/control state: 1/2/5 active Bots, narrow containers, dark
  mode, Bot selection, and independent stop/cancel. No horizontal overflow.
- Live BCS/plugin execution was not exercised; backend contracts and scheduling
  were not changed. No new dependencies or environment configuration were added.
