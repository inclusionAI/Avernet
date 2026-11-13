# Collab assembly and independent internal-site export

Scope amended on 2026-11-11 by `../2026-11-11-collab-discovery/spec.md`: retain
public Bot/group discovery, no experts, and no Human private-chat actions.
Historical validation records below remain evidence for their original scope,
not for the expanded exported artifact.

Status: implementation plan revised on 2026-11-03. Review fixes cover
task-execute group creation, nullable avatar consumers, space initialization,
dynamic import closure checks, and attachment controls. plan.md Tasks 1–16
were implemented on 2026-11-04 and validated by automated gates only: full
`npm run ci` green (guardrails, typecheck, lint, jest, collab-closure
fixtures, closure guard 11 entries / 509 files / 0 violations, Open build),
`npm run build:collab` green (emitted page chunks match `config/routes.collab.ts`
exactly), `check:open-core` green, and
the repo file-size guardrails green. Null-slot state-machine group creation and
image/file upload are covered by the automated collab suites
(`CreateGroupModal.collabSlots.test.tsx`, `GroupChatComposerCollabSlots.test.tsx`).
export-plan.md Tasks 17–20 were then implemented and recorded on 2026-11-04
as follows:

- Full public CI re-run at `53d9ea5f57` (Task 20): `check:open-core`,
  `npm run ci` (guardrails, typecheck, lint, **jest 680 suites / 5278 tests**,
  collab closure guard `11 entries, 509 files, 0 violations`, Open build),
  `npm run build:collab`, and the a2a-chat-client package job all green. The
  export did not alter full public routes or the source inventory.
- Full-source Singlebox regression (acceptance criterion 10) **verified live**:
  `FRONTEND_VARIANT=nextgen FRONTEND_FORM=open bash singlebox/singlebox.sh
  start all` → rc=0 with the complete standalone stack (baas, backend, bcsfuse,
  BCS, 5 OpenClaw bots, demo bot, Gateway, nextgen frontend). The launcher's
  readiness contract passed (`apps/frontend-nextgen` via `npm run dev:local`,
  root element + `/umi.js`, Gateway health 200); the Gateway dev-cookie login
  armed, `/openapi/v1/auth/*` answered the documented local
  "OAuth is not configured" contract, a real chat-strategy group was created
  and re-listed through the frontend origin → Gateway → BCS with the dev
  principal (`bcs_grp_10dbeb93d89b40069e1e84a8f020cae4`), the external-bot
  `mine` listing returned the onboarded `bot_*` profiles, and open-only
  surfaces answered (`/bot-workshop` page, BBS task list). `dev:collab` serves
  only the reduced collab route table (collab markers present; the single
  `bot-workshop` string occurrence in the dev entry bundle `/umi.js` is a
  verified source comment — `config/routes.collab.ts` references none of the
  cut pages as routes or components (its only `BotWorkshop` mention is the
  404-fallback comment), and lazy chunks a production build carries hold
  inert open-nav metadata rather than emitted excluded-page chunks).
  A disposable checkout omitting only `apps/frontend` started nextgen green
  and failed `FRONTEND_VARIANT=legacy` loudly at its own prerequisites; both
  singlebox verity suites pass in that copy. Evidence:
  `docs/singlebox-nextgen-local.md` "Full-source validation record (2026-11-04)".
- Export gate (acceptance criterion 11's offline half, OCB side): export
  fixtures (28/28), failure-atomicity rejection probes, a real export (629-file
  manifest, both source revisions recorded) and the output-tree-only
  `npm ci` / `typecheck` / artifact tests (**2 suites / 26 tests**, the private
  collab assembly + auth suites) / `build` all pass under
  OCB `apps/frontend-nextgen/scripts/ci-collab-export.sh`, which is wired into
  `scripts/ci-internal.sh` (private PR CI) after the full-form gates without
  replacing them. Source trees stayed read-only throughout.

The feature remains **not implementation complete**. Still UNVERIFIED, owned by
the private deployment environment: the browser-rendered collab-form smoke
(login → `/workspace/collaboration`, sidebar exactly 协作群 and 外部 Bot with
no search/bell entries, create-group flow + external-bot participant +
messaging + side panel, `/external-bots` panel + registration dialog,
invite/focus deep links, removed routes such as `/workspace/chat` rendering
NotFound — criteria 2–5), the sanitized runtime network trace asserting zero
task-execute / workflows / spaces / work-orders requests (criterion 8) over a
collab-form session, the full internal assembled-tree regression
(`ci-internal.sh` end-to-end on the private pipeline — additionally blocked
until the `submodules/avernet` pointer is advanced to the collab-form Avernet
baseline), the exported image build and startup (no Docker CLI on the dev
machine), and the private-stack runtime acceptance of the exported site.
Neither the automated gates above nor the local HTTP-level records complete the
independent internal-site requirement; per-boundary evidence lives in
`docs/singlebox-nextgen-local.md`, `docs/deployment.md`, and OCB
`apps/frontend-nextgen/deploy/collab/README.md`.

Owned externally and not modified: BCS, gateway, all backend contracts and
endpoints. This spec only re-assembles existing frontend behavior into a
third build form; it adds no new business feature and changes no Open Core
default behavior.

Owner: frontend next-gen. Scope: build-time form switch, a dedicated route
table, a new `extensions/collab` assembly, capability additions, component
slots for in-page pruning, build/CI wiring, tests, and docs. The private OCB
nextgen assembly owns the internal deployment exporter and internal-only
authentication/branding adapters; no private implementation enters Avernet.

## Required delivery boundaries

User-confirmed on 2026-11-03:

1. **Avernet keeps the complete `apps/frontend-nextgen` public source.**
   Existing public pages, full Open assembly, public dependencies and tests
   remain maintained here. Collab extraction writes only a separate output
   tree; it never replaces this directory or its full `config/routes.ts`.
2. **The complete public frontend remains runnable with Singlebox.**
   `FRONTEND_VARIANT=nextgen FRONTEND_FORM=open` selects this directory and
   its `dev:local` script; validate it through `singlebox/singlebox.sh` and
   the Gateway, without requiring the internal checkout or legacy frontend.
   This change does not switch Singlebox's global default from legacy.
3. **The internal collaboration site is an independent nextgen deliverable.**
   Assemble the full Avernet public nextgen source plus the OCB nextgen
   private overlay, select a dedicated internal-collab entry, then export only
   its collaboration source closure and required build/runtime support files.
   The exported tree builds and deploys without either source checkout mounted.
4. **`apps/frontend` is not an input or dependency.** No legacy pages,
   components, scripts, configuration, package/lock files, Docker layers or
   startup command may be copied, invoked or used as a fallback. Historical
   BCN export scripts are design references only.

“Complete” means the complete existing public nextgen product, not copying
private OCB features into Avernet. “Independent site” means independent frontend
source/build/deployment; the existing gateway, BCS and authentication services
are still runtime upstreams. Export success must not be described as a fully
standalone backend stack.

Implementation is ordered by `plan.md` Tasks 1–16 and then `export-plan.md`.
The feature is complete only when both the full-source Singlebox gate and the
isolated internal-export gate have passed.

## Background

`apps/frontend-nextgen` ships two forms today: Open Core (this public tree,
capabilities defaults, `@/extensions -> src/extensions/empty`) and internal
(TeamClaw, assembled in the internal repo with overlays). Deployments that
only want the BCS-backed collaboration-group pages plus external-Bot
onboarding must currently ship the whole workbench, including BotWorkshop
(TeamClaw-managed bots), single-bot chat, Lab, EvolutionFactory, squares and
admin pages.

Static import-closure analysis (2026-11-03, entries =
`pages/Workspace/Collaboration/*` + `pages/Workspace/GroupWorkspaceArea.tsx`)
identified the following reuse candidates (not proof of bundle isolation):

- 449 files / ~53.5k lines (~36% of `src`); it does **not** import
  `pages/BotWorkshop`, `components/BotWorkshop` (169 files / 19.5k lines),
  `services/botWorkshop`, `services/engine`, or any single-chat page code
  except three small hooks/components reverse-used by the group history
  sidebar (pruning resolves them as: `taskSessionNavigation` moves into the
  Open assembly's slot registration; `SessionHistorySearchPanel` and
  `sidePaneContent` become the closure guard's allowlist).
- External bots (`bot_` prefix) are BCS-side actors. Group membership,
  candidates and management flow exclusively through
  `/openapi/v1/collaboration/**`; adding an external bot to a group never
  touches the TeamClaw bot-management stack.
- Residual couplings to TeamClaw gateway endpoints (`/openapi/v1/bots/*`)
  are limited and mostly soft: identity badge enrichment (already
  catch-guarded), service-bot routing classification in the create-group
  wizard, task skill lookup, task-linked single-bot sessions, bot render
  screens for the side panel, and the 10-line `resolveUserId` utility.
- The full app entry also initializes admin spaces, and `groupService`
  statically imports task execute through `groupExecuteService`. These are
  mandatory cuts in this plan, not optional deployment degradation.

Precedents this design follows: umi only bundles pages referenced by the
route table (`codeSplitting.granularChunks` amplifies this); the legacy
`apps/frontend` BCN-only slice swaps the route table wholesale at assembly;
focused collaboration routes already render chrome-free via
`routeMeta.navigationVisibility`; side-panel wiring already lives in
`src/extensions/empty.ts` (`registerSidePanelWiring`).

## Accepted design

### 1. Form switch

A third assembly form named `collab`, selected at build time by
`FRONTEND_FORM` (default `open`), read in `config/config.ts` using the same
env-injection pattern as `BCS_ENDPOINT_*` / `__A2A_CLIENT_DEFAULT__`:

- `routes`: `collab` → `config/routes.collab.ts`; default → `config/routes.ts`.
  The two tables are independent whole tables; the collab form does not use
  the internal overlay's conflict-override semantics.
- alias `@/extensions`: **exact-match** key `'@/extensions$'` →
  `src/extensions/collab` for the collab form, default →
  `src/extensions/empty` (internal builds keep their own repointing, with
  the same `$` requirement). The `$` is load-bearing: webpack
  `resolve.alias` prefix-matches non-`$` keys, and components import the
  shared registry/contract via `@/extensions/slots` /
  `@/extensions/contract`; without `$` those subpath imports would be
  rewritten to `<assembly>/slots` and fail `max build` (jest stays green
  because `moduleNameMapper` maps `^@/(.*)$` into `src/`).
- `package.json` gains `build:collab` and `dev:collab`
  (`cross-env FRONTEND_FORM=collab max build|dev`).
- `scripts/check-open-core.cjs` stays unchanged: it only checks fixed
  forbidden internal paths plus package.json registry patterns, and every
  new file here is Open Core content, so it passes as-is.
- Components never branch on the form. Pruning happens only through (a) the
  route table, (b) extension assembly differences, (c) capability overrides.
- The public switch defaults to `open` even after an export. Exported private
  configuration is fixed to the collaboration routes and the dedicated
  internal-collab assembly; it must not import the full route tables or select
  them through a fallback. The exporter never edits the public config in place.

### 2. Route table (`config/routes.collab.ts`)

| Route | Component | Note |
| --- | --- | --- |
| `/` | redirect → `/workspace/collaboration` | default entry |
| `/workspace/collaboration` | `pages/Workspace/Collaboration` | full collaboration page |
| `/workspace/collaboration-only`, `/workspace/collaboration/group`, `/workspace/collaboration/session` | `pages/Workspace/Collaboration/Focused` | focused/embeddable deep links (unchanged) |
| `/workspace/invite/:type/:token` | `pages/Workspace/InviteAcceptPanel` | invite acceptance must keep working |
| `/workspace/bcn/chat/detail` | `pages/Workspace/BcnChatDetail` | external BCN landing entry |
| `/collaboration-square` | redirect → `/collaboration-square/bots` | public discovery entry |
| `/collaboration-square/bots` | `pages/CollaborationSquare/Bots` | BCS-only public Bot discovery and friendship |
| `/collaboration-square/groups` | `pages/CollaborationSquare/Groups` | public group browsing and session creation |
| `/external-bots` | new thin page `pages/ExternalBots` | PageHeader + reused `ExternalBotPanel`, bypassing `BotWorkshop/index.tsx` entirely |
| `*` | `pages/NotFound` | |

Cut (physically absent from the bundle): `/workspace/chat`, `/bot-workshop*`,
`/collaboration-square/experts*`, `/collaboration-square/tasks`,
`/collaboration-square/community`, `/lab*`, `/evolution-factory*`, `/work/my-task`,
`/space-admin`, `/ticket-center`, `/welcome`, `/components`,
`/design-system`, `/private-chat/chat`, the legacy `/work` `/manage`
redirects.

All routes stay under the existing `AppLayout` (auth boot, invite gate) and
`AppShell`; no layout change.

### 3. Navigation and shell

- New capability `getPrimaryNavigationItems()`: a form-level override of
  the static baseline, not a move. The baseline array **stays** in
  `src/shell/navigation.tsx`; the capability default returns `null`
  ("keep the module baseline"), so `defaultCapabilities.ts` never imports
  the shell module (that would create a capabilities → shell import
  cycle). `getMergedNavigationItems()` falls back to the in-module array
  when the capability yields null — Open behavior is zero-change — and the
  collab assembly overrides it to exactly three items: **协作群**
  (`/workspace/collaboration`), **发现** (`/collaboration-square`) and
  **外部 Bot** (`/external-bots`).
  Internal overlay inherits the null default through its `...defaults`
  spread and stays unchanged.
  `getInternalNavigationItems()` keeps its additive role.
- A `/external-bots` entry joins the baseline `src/shell/routeMeta.ts`
  table (inert in the Open form: no matching route exists there).
- `getShellVisibility()` gains a `globalSearch: boolean` field (Open
  default `true` = current behavior). The collab assembly overrides
  `globalSearch: false` (search results would deep-link into pages absent
  from this form) and `notificationBell: false` (TicketCenter absent).
  `AppSidebar` reads the capability instead of hardcoding the search mount.
- Auth boot, invite-code gate, identity init and brand/notice defaults stay.
  New capability `getSpaceContextInitializationEnabled()` defaults to `true`
  for Open/internal and is `false` for collab. `AppShell` gates both
  `ensurePersonalSpaceOnAppEntry()` and Bot-section `initSpaceContext()` on
  it. Hiding `spaceSwitcher` alone is insufficient. Neither first entry nor
  visiting `/external-bots` may call the spaces API; catching a failed request
  is not a substitute (the HTTP layer can already have queued an error toast).

### 4. In-page pruning via extension slots

Kept in the collaboration page: group list and the create-group wizard's
BCS-backed modes (definition YAML editing, participant binding and loop preview
included). The “是否以任务执行” option is absent in collab; state-machine groups
use the ordinary BCS creation API. Open/internal retain the option and its
existing behavior. Also kept: group chat messages, image selection/paste and
file upload, question panels, exec
approvals, speaking lease, bot mute/auto control, members/invite/share,
session files and file-preview side pane, `CollabPanel`, and the side
panel family: AixUI `<AixUI type="panel">` messages, BCS manifest UMD
panels, FUSE. The group-chat WebSocket protocol surface is unchanged.

Physically excluded from the collab bundle (wrap-not-fork: slots live in
`GroupChatPane` / composer / `CollabPanel`; Open/internal assemblies inject
the real implementations, the collab assembly injects inert ones; the
static import chain breaks, so the code never enters collab chunks):

| Cut | Location | Note |
| --- | --- | --- |
| Task composer actions | composer slot (`TaskComposerMenu`, workflow list) | removes `/api/workflows` usage; an independent attachment menu retains file upload and image selection when the task slot is null |
| Task-execute group creation | `groupTasks.createGroupViaExecute` slot | Open/internal register the existing executor; collab has no executor and rejects explicit execute requests without network I/O |
| Task message rendering | message-area slot (`GroupTaskRow`, `ManagerWorkerTasks`) | WS-level run status/abort stays |
| Task side panel | already behind `registerTaskPanel` in `extensions/empty.ts` `registerSidePanelWiring` | collab assembly simply does not register it; `assets/TaskPanel` (~3.5k lines + styled-components) drops out |
| Task hooks/services | `useTaskExecution*`, task panel/engine/skill/mapper layers, `backendApi/workflows`, ... | fall out with the UI slots. Accepted residue (documented, not a leak): `services/tasks/taskConversationNavigation` (a 114-line navigation leaf) stays via kept `AppShell` / `ChatLayoutPanelSlot` / header-hook edges, and `taskConfig` via the collaboration controller; no task engine or task panel code enters the bundle |
| Service-bot routing control | create-group wizard slot (`botClassificationService` → `/openapi/v1/bots/{id}/classification`) | external bots have no classification service |

Small surgeries, keeping Open behavior identical:

- Split the BCS read helpers out of `groupExecuteService` into
  `groupBcsReadService`; shared `groupService` must not import or re-export the
  task executor at runtime. Its existing execute method delegates through the
  slot and fails clearly when unavailable. The real execute implementation is
  imported only by Open/internal assembly. Group/session reads remain usable
  for existing `bcs_grp_` identifiers in every form.
- The nullable `AppExtension.avatarEditor` contract propagates to
  `AvatarSettings`: after its existing hook call, return null when no editor
  is registered. Typecheck still includes BotWorkshop in collab builds; route
  pruning does not remove this consumer from TypeScript's source set.
- `resolveUserId` (10-line utility currently inside the single-chat stack's
  `botSessionService`) moves into a dependency-free module under
  `services/workspace/`; existing call sites update imports.
- `services/workspace/index.ts` barrel re-exports of the single-chat stack
  (`botChatProvider`, `botSessionService`, `supportProvider`) are replaced
  with precise imports at call sites.
- `useGroupChatHeaderPanels` loses its runtime edge to
  `useSessionTaskPanelBootstrap` (which pulls `useTaskSwitcherTasks` and
  `services/myTask`, i.e. the MyTask/task-engine chain, and would violate
  the §5 "task engine is never called" constraint): the bootstrap hook
  becomes extension slot `taskPanelBootstrap` (Open/internal = real hook;
  collab = null, with a locally typed noop keeping the call site
  hooks-safe). Its `reopenSidePaneTabsFromMessages` /
  `sessionHasSidePaneContent` import retargets from the
  `useChatHeaderPanels` re-export to the pure `sidePaneContent` module, so
  `pages/Workspace/Chat/hooks/useChatHeaderPanels` leaves the collab
  closure entirely.
- The external-Bot service chain moves out of the forbidden
  `services/botWorkshop/` (`/external-bots` page → `ExternalBotPanel` →
  `useExternalBots` → `externalBotService` + `mapBotDto`): `externalBotService`
  relocates to `services/externalBots/`, and the mapper subgraph it needs
  (`botMapper`, `botLifecycleResolver`, and the pure
  `getBcnCapability`/`getServiceBotCapability` getters) relocates to
  `services/bots/`. The `services/botWorkshop` barrel re-exports from the
  new homes so all remaining bot-management importers stay byte-identical.
  Pure relocation; Open behavior unchanged.
- Identity badge enrichment via `/openapi/v1/bots` stays as-is: it is
  already catch-guarded and preserves identity loading without that upstream.
  It remains an explicit optional request; catch alone does not guarantee
  suppression of the HTTP layer's global error notification.

Slot inventory is finalized during implementation (estimated 6–10 points)
and recorded in the plan; every slot ships with the current real
implementation as its Open/internal default.

### 5. Runtime API surface (collab form)

The table below is the public collab profile. The independent internal export
keeps these collaboration APIs and adds only its explicitly selected existing
private authentication/identity bootstrap contract. That private contract and
deployment configuration are documented and tested in OCB; it does not permit
task/admin/legacy-frontend dependencies to return.

| Kept | Served by |
| --- | --- |
| `/auth/*` (BCS OAuth login) | BCS |
| `/openapi/v1/collaboration/**` (groups, sessions, participants, invitations, messages, external-bot mine/query/candidates, friend connections) | gateway → BCS |
| `/api/v1/collaboration/**` (definitions, templates, session files, manifest) | gateway → BCS |
| group-chat WS `/openapi/v1/collaboration/messages/ws` | gateway |
| `/openapi/v1/bcsfuse/**` (FUSE side screen) | gateway |

Never called from the collab form: task engine, `/api/workflows`, admin
(spaces/work-orders), private-chat, aixcore, and `/openapi/v1/bots/*`
management endpoints (classification, skills, render-screens, sessions).
Exceptions with explicit degradation: identity badge enrichment
(`GET /openapi/v1/bots`, already catch-guarded, stays per §4), and public
bot catalog discovery (`/openapi/v1/bots/catalog/discover`), which is
verified during implementation; if unavailable on the target stack, the
existing discovery-tab degradation absorbs it.

Deployment: same `Dockerfile` plus a `FRONTEND_FORM` build-arg
(`docker build --build-arg FRONTEND_FORM=collab`). The nginx template and
entrypoint stay unchanged in this iteration. After the runtime-request checks
pass, unused upstreams can point at a syntactically valid, resolvable local
placeholder, e.g. `http://127.0.0.1:9`; arbitrary unresolved hostnames can prevent
nginx startup. In particular, admin spaces must be disabled, not contacted and
then ignored. A slimmed collab nginx
template with a reduced env-var contract is an explicit follow-up, out of
scope here.

## Non-goals

- No runtime `if (form === 'collab')` branches in components.
- No backend, BCS, gateway, or protocol changes.
- No change to Open Core default behavior or internal-form behavior; the
  task stack keeps shipping in both.
- No new external-Bot functionality; the page reuses
  `ExternalBotPanel`/`BotRegistrationDialog`/`BotGeneralConfigDialog`.
- No second nginx template / reduced env contract in this iteration.
- No pruning or replacing Avernet's complete nextgen source tree.
- No legacy frontend migration or dependency; do not invoke legacy OSS export.
- No publication of private collab artifacts to the Avernet repository.

## Independent internal export contract

The source ownership observed in OCB is already bidirectional assembly, not a
new internal-to-public export: `apps/frontend-nextgen/scripts/assemble-internal.sh`
copies Avernet's `src/`, baseline routes and the A2A package, then overlays
private nextgen files. Keep this source ownership intact. Its generated tree
is a staging input for an **internal deployment export**, never a replacement
for Avernet's public sources.

- Private implementation entry: OCB
  `apps/frontend-nextgen/src/internal/collab/extension.ts`. It uses the public
  collab assembly and explicitly selected private auth/identity/brand adapters.
  It must not import `extensions/internal.ts` or `extensions/empty.ts`: today's
  internal entry imports the full Open assembly and registers task panels.
  Reuse private leaf adapters by extracting shared definitions in OCB as needed;
  do not duplicate full internal capabilities or use unrestricted object spreads
  that restore full navigation, space initialization, task hooks or old-site links.
- The export contains the collaboration route entries, app/layout/bootstrap
  entries, all required runtime and type dependencies, styles/assets, ambient
  declarations, selected tests, build/deployment files and the local A2A package.
  Runtime bundle closure and buildable source closure are distinct checks.
  In particular, replace the plan's initial `typeof` references to omitted
  task/avatar implementations with pure shared contracts before exporting.
  Do not solve missing types by exporting the excluded implementations.
- Output keeps the required geometry:
  `apps/frontend-nextgen/` plus `packages/a2a-chat-client/` under a new output
  root. Generate config/tsconfig/Jest aliases consistently for that geometry;
  OCB's flat staging-tree package paths must not leak into the result.
- Use the open nextgen package/lock pair with registry injection from the
  deployment environment. Revised 2026-11-04 from the original "private
  package/lock pair": the sliced collab closure contains no
  private-registry-only dependencies (proven by the isolated export gate —
  `npm ci` over the public registry passes, and the slice typechecks/tests/
  builds green), so the exported tree ships the public-registry lock and
  the internal mirror is supplied at build/install time via
  `NPM_REGISTRY`/`COLLAB_EXPORT_NPM_REGISTRY` (never exported, never defaulted
  to any private address). The exporter copies no `node_modules`, generated Umi
  trees, local credentials, machine paths or symlinks reaching the source
  checkouts. Registry credentials and mirror addresses stay private.
  Removing unused npm dependencies is not required for this source-slicing
  change; never edit dependency versions without updating the lock coherently.
- The exporter requires an explicit, new output directory outside both source
  repositories. It rejects overlap, nonempty outputs and path traversal, does
  not run git restore/clean or rsync --delete against sources, and never commits
  or publishes. A manifest records both source revisions, dirty-state indicators,
  selected assembly, emitted relative paths and hashes, with no secrets.
- Validate in the exported tree with the original checkouts and `apps/frontend`
  absent: install, typecheck, selected tests, build and image build using only
  the output root as Docker context. Then smoke-test the independent site
  against configured gateway/BCS/auth upstreams. Public Singlebox remains a
  separate required gate, not an internal-profile fallback.

## Acceptance criteria

1. `npm run build:collab` succeeds; `scripts/check-collab-closure.cjs`
   proves the collab route entry closure contains no
   `pages/{BotWorkshop,Lab,EvolutionFactory,Admin,SpaceAdmin,TicketCenter,Welcome,MyTask,ScheduledTasks,Demo}`,
   no `pages/CollaborationSquare` except `Bots/index.tsx` and `Groups/index.tsx`,
   no `pages/Workspace/Chat` except the explicit allowlist
   (`Chat/components/SessionHistorySearchPanel`,
   `Chat/hooks/sidePaneContent`), and no
   `services/{engine,botWorkshop,botHealthCheck}`, task controllers, or
   `services/workspace/{groupExecuteService,groupTaskAdapter}`. This boundary
   covers static imports and bundled dynamic imports from app/layout and route
   entries; type-only edges and installed dependency internals are excluded.
2. `npm run dev:collab` (or the collab Docker image) against the singlebox
   gateway: login works, `/` lands on `/workspace/collaboration`, the
   sidebar shows exactly 协作群, 发现 and 外部 Bot, with no global-search entry
   and no notification bell.
3. Group flows work end to end: create group via the retained BCS wizard, add an
   external-bot participant, send/receive messages and questions, exec
   approvals, speaking lease, side panels (AixUI panel, BCS UMD panel,
   FUSE, session-file preview). State-machine creation preserves
   YAML/bindings/preview but has no
   execute checkbox. Image selection, image paste and file upload remain
   operable with every task slot null.
4. `/external-bots` lists `bot_` external bots; the registration dialog
   offers the OpenClaw / DeepSeek Harness / QwenWork panels; visibility
   config saves through the external-bot direct route.
   Discovery shows public Bots/groups only. Human existing friends and managed
   Bots cannot open private chat; acceptance cannot auto-create a private session.
   Bot/profile/friendship and public group flows issue only BCS requests.
5. Deep links work: invite acceptance, `/workspace/bcn/chat/detail`, the
   three focused routes; removed routes render `NotFound`.
6. Open form regression: `npm run ci` fully green, and the Open-form
   merged-navigation snapshot is identical to today's.
7. `scripts/check-open-core.cjs` passes; every new/modified source file
   stays ≤ 1,000 lines.
8. From authenticated cold entry through `/external-bots` and state-machine
   group creation, record requests and assert zero calls to task execute,
   workflows, spaces or work-orders. Test an explicit execute call with null
   slots too: it returns an unavailable error without network I/O.
9. Before and after export, the full public nextgen source inventory and hashes
   are identical. No legacy frontend files are read, copied or referenced by
   the exporter or output. Full Open routes and the full Open assembly remain.
10. `FRONTEND_VARIANT=nextgen FRONTEND_FORM=open` starts the complete public
    frontend through Singlebox; root/JS readiness, login, collaboration and an
    existing non-collaboration public route work. Use the existing local service
    readiness contract; do not require private services to satisfy this gate.
11. The internal collab export installs, typechecks, tests, builds and creates
    a deployable image from its own directory with no access to either source
    checkout or `apps/frontend`. Its private auth/identity and retained group,
    external-Bot and side-panel flows pass on the target stack; excluded pages
    are absent from the source artifact and routes, not merely hidden.

## Validation

Document revision evidence (2026-11-03): the Task 14 script and fixture code
were extracted from the plan into a temporary directory; all 10 Node tests
passed, including the lazy forbidden-import counterexample. An in-memory
TypeScript check of the proposed nullable editor plus consumer guard reported
zero diagnostics for `AvatarSettings`. These validate the proposed snippets,
not the completed feature. No application source was changed, and neither full
application builds nor target-stack E2E were run during this document revision.
The later export/Singlebox requirements are design changes only; their gates
have not yet been implemented or executed.

- Unit (jest, under `test/`):
  - `test/routes/collabRoutes.test.ts` — collab table whitelist and `/`
    redirect target.
  - `test/extensions/collab.test.ts` — assembly overrides (nav items,
    shell visibility, space initialization disabled, slots inert).
  - navigation capability tests — Open default snapshot matches today's
    merged nav; collab override merges correctly.
  - `test/pages/ExternalBots.test.tsx` — thin page renders the reused panel.
  - Avatar consumer regression — null editor renders no editing controls;
    Open editor still renders; full typecheck includes the consumer.
  - Shell initialization regression — collab makes neither personal-space
    initialization nor space-list requests; Open keeps both existing triggers.
  - Group creation regression — BCS YAML creation works without an executor;
    Open execute still works; unavailable execute calls fail without requests.
  - Composer regression — with null task slots, exercise image file selection,
    image paste and file upload, not just registry values.
  - Existing capability/route/shell conformance tests remain passing; update
    full-object assertions for the additive capability fields.
- Closure guard: `scripts/check-collab-closure.cjs`, a TypeScript-AST crawler
  from the collab route entries and app/layout roots asserting the negative list in
  acceptance criterion 1 (forbidden prefixes plus a prefix allowlist for
  the two reverse-used Chat files). Follow runtime imports/re-exports, literal
  `import()` (including React.lazy), and literal `require()`; erase type-only
  edges. Unresolved local code imports and nonliteral bundled imports fail
  closed. Explicit webpack-ignored runtime URL imports are outside the emitted
  module graph and must be identified separately. Include negative fixtures
  for lazy forbidden imports, missing local modules and indirect re-exports,
  plus positive fixtures for type-only imports and allowed modules. The guard
  is wired into `npm run ci` and
  `.github/workflows/frontend-nextgen.yml` (plus one `build:collab` step —
  tests are not re-run for the second build).
- Manual smoke (singlebox gateway, collab dev server): login → group list →
  create group + add external bot → chat → side panel open/close →
  `/external-bots` registration dialog (all three panels) → visibility
  config save, with recorded network evidence for acceptance criterion 8.
- Implementation-time verification items:
  - `/openapi/v1/bots/catalog/discover` availability on the Avernet stack.
  - `BotRegistrationDialog` flows (registration token via BCS;
    `QWENWORK_PROVIDER_SLUG` define at build) against the target backends.
  - Final slot inventory and `GroupChatActiveRuns` classification (WS-level
    run status stays).

## Required paired OCB nextgen changes

The public source remains authoritative and is consumed by OCB's nextgen
assembly. A paired OCB change must adapt its configuration and private assembly
to the new public contracts, add the internal collab exporter described in
`export-plan.md`, and pass both internal full-form and exported-site validation.
It is required for this delivery, not a deferred post-merge carry-back task.
Shared changes consumed from Avernet include `config/routes.collab.ts`,
`config/config.ts` switch, `src/extensions/collab.ts`, capability contract
additions in `src/capabilities/types.ts` + `defaultCapabilities.ts` (with
internal overlay overrides mirroring the new `getShellVisibility` field),
capability-driven navigation refactor, `GroupChatPane` slot introductions
(including `taskPanelBootstrap`, the `useGroupChatHeaderPanels` rewiring and
the group-create executor slot), the nullable avatar consumer,
the space-initialization capability, attachment fallback menu,
`groupBcsReadService` extraction, `services/bots` + `services/externalBots` relocations,
`resolveUserId` move, barrel-import tightenings, scripts/tests, and this
spec. Internal behavior stays identical because every slot/capability
default carries today's implementation; validate this in the actual private
assembly as well. Update the module documentation's historical export wording
to reflect the inspected source ownership rather than introducing another
independently maintained copy of the public source.

## Risks and mitigations

1. Slot surgery touches shared components (`GroupChatPane`, composer,
   `CollabPanel`) — Open/internal regression risk. Mitigation: every slot
   defaults to the current real implementation; existing suite plus the
   internal acceptance run cover it.
2. Two route tables can drift (a future collaboration route forgets to join
   the collab table). Mitigation: whitelist unit test on the collab table
   plus a PR-description reminder in this spec's module docs.
3. Physical exclusion depends on the import graph staying cut. Mitigation:
   `check-collab-closure.cjs` runs in CI on every change.
4. External-bot registration / public discovery availability on the Avernet
   stack is unverified. Mitigation: verified first during implementation;
   worst case the existing degradation paths cover it and the gap is
   documented, not blocking the form merge.

## Docs to update with the change

- `apps/frontend-nextgen/CLAUDE.md` — describe the third assembly form and
  its seams.
- `apps/frontend-nextgen/docs/deployment.md` — `FRONTEND_FORM=collab` build,
  minimal upstream set, placeholder guidance for unused upstreams.
- This module's spec index if one is added by convention.
- `docs/singlebox-nextgen-local.md` — complete public nextgen startup remains
  supported; internal collab export is a separate artifact.
- OCB nextgen deployment docs — private collab export and output-only image
  build, with the required private CI/runtime evidence.
