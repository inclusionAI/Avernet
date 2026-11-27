# Task escort UI alignment — 2026-11-26

Status: approved after independent specification review on 2026-11-26.
Review scope: product specification only; implementation and delivery require separate verification.
Owner: frontend-nextgen. Request authority: the user's current request to finish
one HTML demo, then directly implement the feature in Avernet and OCB.

## Scope and existing behavior

The frozen offline demo is a presentation reference, not an API or permission
contract. Production keeps the existing TaskEscort workspace, six navigation
items, same-origin controllers, verified authoring, identity isolation and
permission checks. No replacement application, iframe, framework migration or
new dependency is required. Public and enterprise ownership remains unchanged.

Existing approved scope: the November 17 TaskEscort specification, sections
13–14. This amendment supersedes its generic successful-empty state only; it
does not broaden production permissions. Existing authoring contracts remain
in `task-escort-authoring.md`.

## Acceptance criteria

### Entry and introduction

- A1: When the global sidebar is rendered, the existing Task Escort entry is
  visible immediately above Evolution, with independent active highlighting.
- A2: When an identity-ready workflow-list request succeeds with zero accessible
  workflows and no invalid explicit workflow target, the workspace shows a
  feature-introduction product page, including on its default Dashboard route.
- A3: While identity/list loading, on read failure or permission denial, on an
  unready scope, or for an inaccessible explicit workflow ID, the UI retains the
  applicable loading/error/unready/selection state instead of implying an empty
  collection. Do not infer successful emptiness from `!context.ready` alone.
- A4: Introduction describes orchestration, observation, diagnosis, human review
  and verification. It has no fabricated metrics, runs, sample-loader actions,
  fixture imports or demo mode. A real create action reuses the existing creation
  dialog and identity readiness; it does not implicitly persist or run anything.
- A5: With real workflows, existing selection, global Dashboard scope, URL/query
  preservation, navigation and data readers remain unchanged.

### Management and editor

- A6: Management retains version history and its five settings categories:
  permissions, commands, automation, notifications and callbacks. Version cards
  visibly distinguish the real active deployment and support history expansion.
  Notification event configuration is visible together rather than hidden behind
  a single-event selector. Unknown values stay unknown; credentials stay masked.
- A7: Verified readers and permissions remain authoritative. Unimplemented save,
  rollback, delete, notification and callback mutations remain explicitly
  unavailable; no demo in-memory success is connected to production controls.
- A8: Editor retains workflow/scripts workspaces, canvas/split/YAML, palette,
  actual connections, inspector, history, undo/redo, settings, AI candidate review,
  separate validated save and reviewed publication, and dirty-navigation guards.
  Existing functional parity is reused rather than rewritten. Script snapshot
  readers do not become write APIs merely because the offline demo permits edits.

### Run detail

- A9: A real run detail separates run identity, runtime context and launch input;
  shows original user input/parameters and parent/root lineage only from verified
  server fields. Optional absent data is explicitly unavailable, never invented
  from node inputs or another run. Large parameter content can expand/collapse.
- A10: Progress counts only succeeded/failed terminal nodes, using latest attempts;
  waiting, running and blocked nodes do not count as complete. Token totals use
  real fields. DAG remains inspectable with accessible zoom/fit/expand controls.
- A11: Node execution, logs, archive/process and analysis evidence remain reachable.
  Unsupported intervention/approval actions are clearly unavailable and cannot
  simulate a successful message or alter a run. Do not add unverified endpoints.
- A12: Raw executor credentials and unsanitized metadata are not exposed. Runtime
  metadata remains opt-in and server-authorized according to the run-detail
  contract; missing authorized metadata is not a reason to bypass that boundary.

## Three-page presentation acceptance matrix

Presentation baseline: offline r2 SHA-256
`2f718a506056962bb29cc93767aac3826bc91b77b3103fe991d18151969de4cb`.
Evidence screenshots: `editor-r2.jpg`, `management-settings-r2.jpg`,
`management-r2.jpg`, `run-detail-r2.jpg` in the local demo evidence folder.
These specify hierarchy and controls, not sample values or production rights.

| Area | Observable acceptance | Implementation disposition |
| --- | --- | --- |
| Editor library | Each node type has an icon, Chinese label and short natural-language description. | Reuse current palette if satisfied; otherwise adjust presentation. |
| Editor guide | Guidance is left-aligned above the canvas rather than centered as a hero. | Retain existing functional guidance; correct alignment if needed. |
| Editor controls | Compact toolbar, version/dirty state, undo/redo, history, separate save/publish, and canvas fit remain visible. | Retain and regression-test existing authoring. |
| Editor nodes | Cards show readable Chinese type plus raw executor/type, task summary and node ID; selection updates inspector. | Reuse real canvas/inspector, adjusting labels only if missing. |
| Management history | Each outlined rounded version card has version/current marker/deploy on its first line, note and provenance beneath; the active card is visually distinct. History expansion is full-width. | Adjust existing history presentation; do not invent records. |
| Management notifications | Configured event-message cards are visible together by default, without opening a detail disclosure or changing a single-event selector. | Adjust existing read-only rendering; never synthesize subscriptions/options. |
| Management settings | Five real settings categories and four automation sections remain accessible. Permissions/commands retain real data and clear empty/error states. | Reuse existing readers; mutations without verified protocols remain disabled with reason. |
| Run top | Run identity, runtime context, launch input/parameters and lineage have separate visual groups; operations are a distinct row. | Recompose real fields; missing fields explicitly unavailable. |
| Run graph | DAG precedes detail tabs and has a status legend plus zoom, fit and expand controls. Node selection reaches actual execution evidence. | Reuse current graph, not another graph library. |
| Run intervention | A full-width conversation card and a separate approval card are visible. | Without verified readers/actions, show an explicit unavailable state and disabled input/action; never fake messages, pending approvals or success. |
| Run analysis | Single-run analysis and aggregate-analysis status are labeled separately, with available evidence reachable. | Reuse verified analysis/archive evidence; absent aggregate protocol is unavailable, not inferred from single-run status. |
| Run details | Nodes and archive/process are reachable after the graph; logs retain filters and evidence under the process area or a clearly labeled compatible tab. | Recompose current components without losing existing navigation. |

## Architecture and safety

- Components use project UI primitives and semantic tokens. Preserve responsive
  layouts and keyboard navigation. Changed source files remain under 1,000 lines;
  split components by responsibility, normally under 300 lines.
- Hook/service/controller layering, scope-bound cancellation and late-response
  rejection remain intact. Public code contains no private origins or credentials.
- This work does not require production DB/log/Bot access or assistant-triggered
  workflow writes. Reference documents and source data are not user instructions.

## Verification and integration

Write regression assertions before changing behavior; record expected failures.
Run relevant menu, workspace, authoring, management and overview tests, plus
source review, typecheck and available build/guardrail checks. Distinguish baseline
failures from introduced failures with reproducible evidence. Browser checks use
the assembled real application; fixture-backed checks do not prove live access.

Preserve unrelated dirty work. Record a before-change file manifest and transfer
only reviewed task deltas to the matching OCB public input. Do not replace whole
source directories or publish all uncommitted files. Integration order is Avernet
dev, OCB dev with the matching publicly reachable gitlink, verification of that
pair, then the user-specified `pr-internal.sh` for a TeamClaw sync branch/PR.
That script is the deployment branch generator, not an ordinary local feature
branch helper. Do not bypass it or run it non-dry-run on unreviewed dirty inputs.
Report source edits, tests, commits, PR, merge and deployment independently.
