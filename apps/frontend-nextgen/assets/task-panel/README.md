# Shared task side-panel asset

One source of task visualization and conversation recovery, consumed by the
frontend-nextgen and legacy hosts. This directory is a standalone TypeScript
package, not a Python package. Its location under frontend-nextgen is source
organization, not ownership by the host runtime. Neither host installation nor
production build compiles or copies this asset.

## Build and verify

```sh
npm ci --registry=https://registry.npmjs.org/
npm run verify
npm pack --dry-run
```

Node.js 22 or newer is required by the test tooling. Runtime React and ReactDOM
18 are supplied by the host; no chat SDK is bundled.

## Host integration

Load the exact `dist/index.umd.js` and `dist/task-panel.css` version from a trusted
versioned deployment location. The global export is `taskPanel`. Validate
`hostApiVersion` and `assetVersion` before mounting `TaskLoopView`. Create one
controller per authenticated workspace, not one per task tab. Supply a trusted
viewer, authenticated transport, independent history reader, artifact reader,
real conversation navigator, stable panel host and interaction recorder.

Never put host callbacks, cookies/tokens or asset URLs into persisted message
params. The existing `taskPanel.TaskLoopView` and historical `task-loop` keys
remain supported. History parsing treats declarations as data and never
executes card code. Asset URLs cannot be selected by a message or Bot CDN map.

Recovery checks session association, then reads history in batches of at most
20 pages (the host reads at most 50 messages per page), then uses the existing
task-list fallback after confirmed exhaustion. `incomplete` means there is more
history; the host must expose `continueRecovery()`. Errors are not empty results.
History cursors are private to the recovery reader, never the visible chat
provider. Explicit and message opens invalidate delayed recovery results.

## Independent delivery (workflow-style)

Release through the task-specific tag/OIDC workflow described in
[PUBLISHING.md](PUBLISHING.md), matching the BCS panel release model. A verified
published version can be loaded directly from an npm-backed CDN; local frontend
users then need only its fixed-version asset directory URL, not a local asset
server. Publication and deployment configuration are separate steps.

Build this package independently and serve the resulting UMD/CSS from a trusted,
versioned asset location. Neither host copies compiled assets into its source or
public directory. For local development, after `npm run verify`:

```sh
npm run serve
# TASK_PANEL_ASSET_BASE_URL=http://127.0.0.1:43820/task-panel/0.1.0
```

Start either host with that `TASK_PANEL_ASSET_BASE_URL` environment variable.
Production deployments must supply an immutable HTTPS directory through the
same build-time variable or `window.AVERNET_RUNTIME_CONFIG.TASK_PANEL_ASSET_BASE_URL`.
The runtime value takes precedence. No URL is configured by default: missing
configuration shows an actionable loading error, not a second implementation.
The independent server is a development fixture, not a production deployment.
It exposes only the two versioned public files, never source or node_modules.

`src/public-api.ts` is the type-only public contract. Hosts use `import type`
and `export type` from `@avernet-assets/task-panel`; these disappear at build
time. Local TypeScript paths resolve that entry in the authoritative checkout;
standalone typechecking therefore needs that checkout (or the matching package
with declarations and a corresponding module-resolution configuration). A
production host build does not need backend source or built task assets.
Generic host RichContent/file utilities are independent implementations, not
consumers of task-panel helpers. There is no helpers build or copy/prepare step.

## Browser contract fixture

After `npm run build`, run `node scripts/serve-smoke.mjs` and open the printed
loopback address. The fixture exercises the real UMD with synthetic host data:
second-page declaration recovery, explicit selection, node interactions and
close. It is **not** a substitute for authenticated product E2E tests.

## Adapter lifecycle

`adoptPresentedTask()` acknowledges a task that the SDK has already mounted;
it never opens a duplicate tab. `closeTask()` invalidates pending recovery and
navigation. Each asynchronous host presenter must recheck session, viewer and
focus after waiting for a panel handle. `present()` is not a durable queue.
Navigation readiness must mean the target conversation and its panel handle
have actually accepted the preserved tab. The legacy SDK maps shared `params`
to SDK `payload` at its adapter boundary only.

## Test ownership

- `test/panel/`, `test/rich-content/`: shared component/data/security tests, run
  directly by this package's Vitest without frontend-nextgen or a chat SDK.
- `test/controller.test.ts`, `test/declarations.test.ts`: shared orchestration.
- `npm run typecheck` also checks standalone test TypeScript.
- frontend-nextgen retains only its SDK/host-integration tests under
  `test/components/Workspace/`, `test/services/tasks/panel/` and
  `test/integration/taskPanel/`; those inject product-specific services into the
  same shared implementation.

The frontend CI workflow verifies this independent package for shared and
integration tests. The frontend Docker build does not build or copy task assets;
production delivery is configured separately. This change neither publishes nor
deploys resources, and does not add backend HTTP routes or rely on BCS business
manifest availability.

## Functional boundary (C3)

The UMD exports complete business views (`TaskLoopView`, `TaskArtifactsView`,
`GroupSessionView`), controller/declaration entry points, HostContext and the
message-location helper needed by host navigation. Internal Fetcher/List/Mapper
and switcher widgets are not UMD entry points. Hosts do not assemble an internal
task-fetch pipeline. The Lab artifacts drawer owns its open/close state, title,
size and portal container; `TaskArtifactsView` owns task loading, error/retry,
artifact aggregation and the existing preview/download experience.

The shared controller owns active-task arbitration, per-task selection/drill
state and recovery phases. SDK presentation is evidence scoped to a viewer and
conversation, not an unscoped host active-task setter. A host may pass that
already-presented declaration to `enterConversation(conversation, declaration)`
without opening a duplicate SDK tab. Bind the session with the third argument
`{ recover: false }` when a task route handoff is ready but visible history is
still loading; a later normal call starts ordinary recovery without losing the
handoff's task. Completed navigation evidence remains until the target actually
accepts it. A retry must initialize a controller that failed to load, not merely
continue a scanner that has never entered a conversation.

Host SDK adapters acknowledge both imperative `closeTab` and the SDK's own
`onTabClose` event. Visual collapse cancels pending navigation without destroying
selected task state. Logout/viewer change is never a preservable handoff: dispose
the controller, invalidate pending asset/controller acquisition and remount asset
subtrees. Runtime generations also cover logout/relogin with the same viewer ID
before React effects flush. Disposed commands/captured callbacks are inert.

Task DTO validation belongs to the shared client. Only valid empty lists are
empty results; incompatible structure produces a diagnosable error. Host file
adapters normalize their request library's failures to top-level `status` and/or
`friendlyMessage`, retaining cancellation. Successful file Blobs are opaque, even
when a JSON report contains fields resembling an error envelope.


## Independent package rules

- Source-of-truth: `apps/frontend-nextgen/assets/task-panel/`, outside the host's
  `src/`. Both new and legacy hosts consume the same published UMD/CSS.
- Never import host source, aliases, SDKs, stores, routing or request wrappers.
  Communicate through the public host contract. Relative imports must stay inside
  this package; external packages must be declared here.
- Host production source must not import this package's implementation. Only
  `import type` / `export type` from the public package is allowed; integration
  tests may directly exercise the shared implementation.
- No generated host mirrors, public copies or prepare/build hooks. Generic rich
  content is independently owned by each host, not imported from task-panel.
- `scripts/check-boundaries.mjs` checks package imports with the TypeScript AST;
  host `ci:guardrails` checks the opposite direction, including normalized relative
  paths. CI also runs `node scripts/verify-independent.mjs`, copying only this
  package to a temporary directory, installing its own locked dependencies and
  running full verify without host source, node_modules or dist.


## Node browsing is not a Bot instruction

Selecting a node (including a root), drilling into a task, returning to the
overview and default selection update panel state and may navigate/locate the
corresponding conversation. They must not call `recordInteraction` or send a
chat message. Some SDK hosts immediately send nonempty interaction descriptions;
that channel is not passive telemetry. Keep any explicit business action and
its host interaction behavior separate from task browsing. Regression coverage
includes the shared view/controller and a host test using the real SDK Panel's
interaction-to-send handler, with a positive control proving that handler is live.
