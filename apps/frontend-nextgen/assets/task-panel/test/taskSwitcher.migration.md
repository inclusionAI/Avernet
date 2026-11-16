# Task-switcher assertion migration ledger

This ledger accompanies the bounded removal of the unused nextgen switcher/nav
implementation. It records test responsibility, not a new public protocol.
The shared package owns business logic; nextgen owns SDK presentation and route
adaptation. No compatibility re-export or copy of retired source is retained.

## Call-chain evidence

CodeGraph was attempted first (`explore` for all three names and `node` for the
hook). Its index still used historical `src/frontend-nextgen/...` paths and did
not resolve these files. Current-worktree reference search then established:

- `TaskPanelSwitcherSlot` had no production importer.
- `useTaskSwitcherTasks` was imported only by that unused component and its test.
- `taskPanelNavBinding` was imported only by its test.
- Remaining mentions in historical specs and comments are not import edges.

## Destinations

- **D**: `data/taskSwitcher.test.ts` (pure helper assertions).
- **V**: `panel/TaskLoopView.switcher.test.tsx` (real TaskLoopView, TaskPanel and controller).
- **N**: `panel/taskSwitcher.navigation.test.ts` (real shared controller).
- **H**: nextgen `test/services/tasks/panel/taskPanelRuntime.switcher.test.ts`
  (real runtime and shared controller; SDK boundary mocked).
- **O**: nextgen `test/services/tasks/panel/taskPanelRuntime.origin-regression.test.ts`
  (real runtime, controller, navigation adapter and default capability; router mocked).

No old store navigation commands are imported by these tests. Runtime's existing
output projections and the real adapter's route-readiness bookkeeping remain
part of the implementation under test, not a second tested policy implementation.

## Original `test/hooks/useTaskSwitcherTasks.test.tsx`

| Original case | New direct assertion |
| --- | --- |
| Hidden tab does not poll; visibility resumes; unmount removes listener | V: `pauses while hidden, resumes immediately on visibility, and removes timers/listener on unmount` |
| list maps three buckets and Chinese labels | D: parameterized `maps %s to %s / %s`; V verifies actual list query |
| Same modern main_session_id only | D: `keeps modern main_session siblings...`; V: conversation owner/relay filtering |
| by_session association with different main_session_id | D: `includes by_session relay tasks...`; V: owner/relay filtering |
| Deduplicate by task_id | D: modern siblings + anonymous sessions cases, including first-record-wins |
| Historical teamclaw_context.main_session_id | D: `falls back to historical...`, also verifies modern precedence |
| No session means current only | D: `never aggregates anonymous sessions...` |
| Missing current means no items | D: `has no switcher without the current task...` |
| API failure is silent empty items/records | V: `list failure hides supplemental switcher...` (after a successful load, stronger than initial failure) |
| Baseline/no attention, changed background task gets badge, selection clears it | V: `first load sets a baseline...`; active task remains unchanged and badge changes cause no extra list query |
| Disabled/no user/no current makes no query | V: `does not query supplemental lists without viewer/task/controller or with a custom switcher`; enabled is no longer a host business-hook API |
| First node bot session with node time bounds | D: `uses the first step bot session...`; V: `selects explicitly...` verifies real navigator invocation |
| Cooperation group session/group id | D: `preserves a cooperation group session and group id` |
| Dashboard failure never blindly uses whole-task time window | V: `dashboard failure never falls back...`; D additionally covers no first node |

## Original `test/components/Workspace/TaskPanelSwitcherSlot.test.tsx`

| Original case | New direct assertion |
| --- | --- |
| Query scoped by panel taskId | V: owner/relay filtering + this-panel-not-global-focus; D: conversation filtering |
| Cross-user session owner + by_session task ids | V: `queries the conversation owner and includes by_session relay associations...` |
| Multi-task first frame only floating control, correct current item, dropdown closed | V: `starts collapsed, toggles the bar, uses this panel task rather than global active focus` |
| Second click hides bar | Same V case |
| Changed params remounts task panel | V: `resets the control on task params changes and on panel remount` |
| Manual selection + rebuilt panel not default-open | Same V case |
| Single task still has floating control | V: `retains the single-task switcher...` |
| No remembered width -> 420 | H: `width memory null opens at 420` |
| Remembered 350 -> 420 minimum | H: `width memory 35% opens at 420` |
| Remembered 560 -> 560 | H: `width memory 56% opens at 560` |
| Switch siblings inherits original host conversation | N: `root selection restores the opaque original host, including after switching sibling tasks` |
| Selection opens target id/title and navigates to first step/time window | V: `selects explicitly...`; H verifies exact SDK openTab parameters and authenticated viewer injection |

## Original `test/hooks/taskPanelNavBinding.test.tsx`

| Original case | New direct assertion |
| --- | --- |
| Empty selected node and drill stack | N: `defaults to an empty task slice...` |
| Selection + drill + pending target + back | N: `select/drill invokes the host navigator...`; real navigation replaces the retired pendingNavigation command |
| Default node creates no navigation/scroll signal | N: `default node selection changes only that task...`; no host navigation, preservation or user-interaction call |
| Root binds correct task and exits linked session | N: sibling-origin restore; O: linked-group -> exact original host route. No new linkedSession state is introduced |
| Root restores exact original URL | O: `restores the exact original host route after a linked group conversation` |
| Root on already-current normalized URL leaves content/URL alone | **O: ordinary passing regression after main-thread restore adapter fix**, `keeps an already-current normalized host URL unchanged...`; preserved by the current host restore adapter |
| Callbacks do not cross task slices | N: `defaults to an empty task slice and keeps instance callbacks isolated`; includes independent drill stacks/back |

## Behavior decisions and remaining gap

- Single-task control is intentionally preserved. TaskLoopView previously required
  `items.length > 1`, although the shared control documented/supports single-task
  viewing. The new direct UI test locks in `> 0` rather than deleting the old case.
- Supplemental list failure hides the switcher, never the displayed task. No
  host getHuman/list business hook remains. Visibility polling stays at 60 seconds.
- Titles use the existing shared `recordTitle` precedence (context, goal, `任务 id`),
  rather than resurrecting the old host-only bare-id fallback.
- Normalized-origin restoration is now fixed by the main thread's
  `restoreTaskPanelOrigin`. O retains the original assertion as an ordinary test.
- Extra existing coverage: `test/controller.test.ts` already covers viewer reset,
  opaque-origin session handoff and default-selection interaction exclusion.
- The follow-up removal of the dead host store/observer is mapped below. The
  shared controller and host adapter remain the sole implementation path.

## Follow-up: retire host domain-policy store and observer

### Production boundary after removal

- `taskSidePanelStore` now has output-only `readonly activeTaskId`, one-shot
  explicit-open intent, and the host's linked-session rendering container.
  No active-task setter, nav slice/algorithm, pending-navigation queue or scroll
  counter remains. Production projection is owned by taskPanelRuntime.
- `taskUpdateAttentionStore` had no remaining consumers beyond the retired host
  store. It is removed; live switcher attention remains in shared TaskLoopView.
- `app.tsx` only loses the obsolete task observer import/component/assembly.
- `useTaskExecution` marks explicit intent before message submission; it does not
  claim a displayed active task. A rejected synchronous send consumes that intent.
- Force-close uses `peekTaskPanelController()?.leaveConversation()`, which clears
  focus and aborts navigation through the real snapshot. The main thread's SDK-X
  `onTaskPanelTabClosed` acknowledgement is preserved unchanged.
- Page tests use `store.setState` exclusively for read-only projection fixtures;
  no compatibility setter or second nav policy is retained.
- After observer removal, `originsByTask/rememberOrigin` in
  taskConversationNavigationStore has no production reader/writer outside its own
  definition/reset. It is reported, not edited. In contrast,
  `isCurrentTaskConversationOrigin` is live in restoreTaskPanelOrigin.

### Original host store assertions

Destination **C** is `test/controllerHostMigration.test.ts` in this shared package.
Destination **S** is nextgen `test/stores/taskSidePanelStore.test.ts`.
Destination **A** is nextgen
`test/services/tasks/panel/taskPanelConversationAdapter.test.ts`.
Destination **K** is nextgen
`test/services/tasks/panel/taskPanelHandleAdapter.test.ts`.

| Original store case | New responsibility and assertion |
| --- | --- |
| Empty active/nav/pending/scroll/linked state | C empty shared snapshot; S empty projection/intent/container and explicit absence of retired fields/commands |
| selectNode selects/navigates and increments scroll signal | C selects once, invokes real host navigator once and records node interaction; A real DOM scroll on node time window replaces synthetic counter |
| kind/groupId/botId/nodeId/time fields survive pendingNavigation | C exact target + nodeId interaction; A exact capability route/time target + real message DOM scroll |
| Null target still selects, without navigation | C empty-snapshot/null-target case |
| Root already in host only selects | C root without origin; A semantic same-session no-op; O normalized URL ordinary regression |
| Root restores exact original URL | A opaque-origin restore; O real runtime/default capability exact route |
| Root exits linked session and cancels old pending jump | C root aborts previous navigation; A actual route restoration. Host linkedSession container close behavior remains S, not controller state |
| Drill deduplicates and caps at two without changing selection | C dedup/cap test. Main-thread integration preserves the original cap: third defensive drill does not evict either of the two existing levels; only the shared controller implements it |
| Drill also navigates | C drill/overview/isolation case |
| Overview clears drill stack, preserving selected node | Same C case |
| consumePendingNavigation clears queue | Queue removed; C cancel/close abort test asserts actual in-flight signal and focus rather than a dead command mailbox |
| Task-A nav does not affect Task-B | C independent slices/overview case |
| setActiveTask resets that task's navigation | Setter retired. C asserts new task starts empty but reopening an existing shared task preserves real selection; no fake active/reset path is reinstated |
| resetAll empties state | S resets projection/intent/container; C leave clears focus/associations and viewer change clears nav |
| Linked group container open/close, linked Bot container open, reset clears container | All three original S cases retained |

### Original observer assertions

| Original observer case | New direct host/controller assertion |
| --- | --- |
| Group target fields forwarded, pending consumed, original route captured, committed lease | A captures exact route/forwards all fields/lease-before-capability; C captures original opaque handle once; queue no longer exists |
| Lease already committed before synchronous route capability | Same A group case, checked inside capability invocation |
| Same current conversation completes hydration without route lifecycle | A same-current-conversation readiness case |
| Bot id forwarded | A botId/real destination hydration case |
| Root exact URL, first origin never overwritten | A exact opaque origin restore + C capture-once after sibling switch + O real route restore |
| Unsupported capability does not throw/retry or keep lease | A unavailable typed outcome/cleared lease/one invocation; actionable outcome replaces retired observer-only console message |
| Time-window DOM hit scrolls; missing hit degrades safely | A real data-ts scroll and missing-message ready outcome; synthetic store scroll signal not used |
| Message mounted late is retried | A ordinary passing test `locates a message mounted after hydration using a bounded retry`; bounded retries and cancellation now live in the actual adapter |
| No time window creates no DOM-probe timer | A no scroll/no remaining timers after actual route readiness. Current adapter's bounded route-readiness wait is intentionally distinct from obsolete probe timing |

### Execution and SDK presentation assertions

- `test/hooks/useTaskExecution.test.tsx`: existing centralized/relay output and
  all skill/selection assertions retained; submission now explicitly asserts
  intent with no fabricated active task.
- `test/integration/taskPanel/useTaskExecution.presentation.test.tsx`: centralized
  and relay submission emit a real declaration, keep shared/projection focus null,
  then mount actual SDK TaskPanelAdapter -> TaskPanelHost -> shared TaskLoopView.
  Only the mounted card projects activeTaskId, with actual task UI in the DOM.
  Failed send clears its intent without claiming a display.
- K asserts ordinary force-close aborts via controller.leaveConversation,
  navigation preservation suppresses force-close, SDK-X and imperative closeTab
  use the same acknowledgement, remembered SDK aliases close correctly,
  collapse keeps node/task selection, and one-shot intent permits an SDK open
  without pretending it was already displayed.
- Existing page/recovery tests retain their route-preservation and SDK-open
  assertions. Only fixture task setters and the two nav projection lines change.

### Remaining bounded gap

The old uncalled observer retried DOM lookup when history messages mounted after
hydration. The current live host conversation adapter probes once. The original
late-mount assertion is retained against that real adapter as `it.failing`; its
source is outside the authorized edit list, so no replacement implementation was
added. Adapter ownership must decide/implement bounded retry and then turn this
into an ordinary test. The normalized-origin regression is separate and is fixed.

### Verification

Final commands/results are appended after the focused and full runs. No non-task
README, shared production module, controller, taskClient or runtime.ts is changed
by this follow-up. No dependency, publication, deployment, commit or push.

Verification results for this follow-up:

- Shared `npm run typecheck`: passed (production and test TypeScript).
- Shared `npm test`: 43 files / 334 tests passed, including all 9 new direct
  controllerHostMigration assertions. No shared production source was edited.
- Nextgen `npm run typecheck`: passed.
- Nextgen related Jest run: 25 suites / 262 tests passed, including 1 explicitly
  expected failure for late DOM mounting; this is not a claim that all 262 behavior
  assertions are currently satisfied. Scope includes panel services/integration,
  task store/execution/bootstrap, ChatLayoutPanelSlot, useGroupChat and appRuntime.
- The new actual SDK-adapter presentation cases cover both centralized and relay
  launches; the failure-to-send case and all preexisting hook cases pass.
- Running the late-DOM case as an ordinary test confirmed failure specifically
  at `expect(el.scrollIntoView).toHaveBeenCalled()` (zero invocations), not setup
  failure. The existing normalized-origin test remains ordinary and passes.
- Nextgen `npm run ci:guardrails`, shared `npm run scan:public` and
  `git diff --check`: passed. useTaskExecution remains below the hook-size limit;
  the host projection/container store is 38 lines.
- Production reference audit: no retired nav fields/commands/observer/attention
  store references remain in nextgen src. runtime.ts projects activeTaskId only
  (main-thread edit), as do the two updated test runtime seams.

### Final integration of retired-observer assertions

The former late-DOM expected failure is now an ordinary test. The actual host
adapter retries timestamp DOM lookup at a bounded 500 ms cadence, checks the
current route lease and abort signal before each attempt, and clears its waits on
cancellation. No message hit degrades to a ready conversation after the budget;
missing hydration still reports timeout. The original depth-cap assertion also
remains: a third defensive drill does not evict either retained level. These are
not assertions against a second host algorithm. Earlier expected-failure and
rolling-stack descriptions in the sidecar handoff are superseded by this section.
