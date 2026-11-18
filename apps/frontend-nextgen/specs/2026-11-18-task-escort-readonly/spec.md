# Task Escort read-only workspace

Status: **approved scope; local source validation complete; source integration pending**

## Ownership and baseline

Owner: `apps/frontend-nextgen`. This change carries the already-reviewed Task
Escort user feature onto the public `dev` baseline
`89681bf7465c0d548ca9f9f10e6b23075628bdd4`; it does not expand its approved scope.
No server schema, authentication policy or existing Evolution behavior changes.
The [ownership manifest](ownership-manifest.json) records independent feature
files, minimal shared patches and documentation. The
[changed-file inventory](changed-files.txt) lists the complete delivery scope.
Repository [architecture rules](../../../../docs/arch/arch.rules.md) remain
binding; this frontend-only migration introduces no new architectural decision
and does not supersede an ADR.

## User behavior

- The independent **任务护航** entry opens
  `/evolution-factory/task-escort` under the existing AppLayout, immediately
  above **智能进化** in the management group. Ordinary extension navigation
  precedes the pair; explicit `placement: 'last'` items still follow it in
  their original order.
- The existing Evolution entry, label, icon, destination, controls and deep
  links remain unchanged. Other `dev` routes, navigation-guide markers and
  collapsed-sidebar grouping remain intact.
- Route metadata and longest slash-boundary prefix matching select one menu
  entry. Escort descendants select Escort, existing Evolution descendants
  select Evolution, and lookalike prefixes do not select unrelated entries.
- Dashboard, workflow execution overview, issues and repair evidence,
  experiences, workflow definitions/history and management records display
  real server responses. Workflow selection and query navigation stay within
  the independent workspace.

## Read-only contract

Task Escort controllers consume same-origin `GET` resources under
`/openapi/v1/harnessflow/api`. Requests reuse the existing authenticated request
adapter and backend authorization; the frontend is not an authorization boundary.
See the owning [controllers](../../src/services/backendApi/harnessFlow),
[read services](../../src/services/taskEscortParity) and
[workspace context](../../src/hooks/taskEscortParity/useTaskEscortWorkspaceContext.ts).
This change adds consumers, not backend endpoints or a new proxy target.

- No Task Escort action sends create/update/delete, apply, run, verification,
  publication, settings-save or notification-configuration writes. Unsupported
  actions remain disabled or return an explicit unsupported result, even if a
  backend capability advertises editing permission. Local selection and
  preview state do not imply a persisted change.
- Network, authorization, missing-resource and malformed-response failures
  remain visible as errors or explicit unavailable states. Missing evidence
  is not replaced by fabricated results, zero-valued assessments or successful
  write notifications.
- The formal entry always uses real reads. A legacy `demo` argument cannot
  enable synthetic runtime data. The legacy generic workspace loader remains
  disabled at the formal page (`loadWorkspace: false`); the dashboard uses its
  dedicated real-read resources.
- Fixture and demo modules are test support only. Neither the formal page nor
  its production dependency graph imports those modules, and production barrels
  do not re-export them. There is no URL switch or failure fallback to demo data.
- Identity, space and workflow changes isolate read state. Superseded reads
  are cancelled or ignored so old-scope evidence cannot populate the new view.
- Service locations and credentials are deployment configuration, not public
  feature content. This specification contains only relative API paths.

## Acceptance and regression evidence

1. Preserve every existing `dev` route and original Evolution source file;
   add only the independent Escort route and navigation metadata.
2. Keep Escort and Evolution adjacent after ordinary navigation extensions,
   while preserving explicit last placement and mutually exclusive highlighting
   in both expanded and collapsed navigation.
3. Preserve the reviewed independent implementation unless an existing `dev`
   file requires compatibility review. All 231 independent destination paths
   were absent at migration time. Runtime content is copied unchanged; one
   redaction test builds explicit dummy credential fields for the secret gate.
4. Reject writes and synthetic fallback, including explicitly requested legacy
   demo mode. Retain identity/workflow isolation and visible read failures.
5. Keep public content free of private service locations and credentials.

Regression suites: [shell navigation](../../test/shell),
[workspace](../../test/taskEscortWorkspace),
[read-only feature views and services](../../test/taskEscortParity), and
[dashboard HTTP contract](../../test/services/backendApi/harnessFlowDashboardController.test.ts).
These are retained tests, not evidence that they ran during migration.

## Compatibility and validation handoff

The `dev` request adapter adds optional inline-error/server-message options;
its existing GET signatures are unchanged. The identity service adds an optional
`alreadyHandled` error flag. Keep these newer implementations; the migrated
feature does not replace them.

The configured backend must expose the existing HarnessFlow read resources.
A public deployment without those resources shows honest unavailable/error
states, never fixture data. Authorization and real-data integration still need
validation in the host's configured environment.

Static migration checks completed on 2026-11-18:

| Check | Result |
| --- | --- |
| `npm run check:open-core` | Passed; dependency manifests and forbidden overlay paths unchanged. |
| `git diff --check` | Passed; an additional check covers whitespace and conflict markers in all new files. |
| Independent source integrity | 230 files match the reviewed source byte-for-byte; the remaining file only makes dummy URL credentials explicit in its unchanged redaction contract. |
| Ownership inventory | Exactly 243 changed files: 231 independent, 8 shared paths and 4 documentation files. |
| Existing routes | Removing the single added Escort route yields the exact original dev route table. |
| Compatibility seams | Evolution source, request/identity adapters and package manifests unchanged; last placement, guide markers and keyed Fragment retained. |
| Formal-entry local import graph | 264 local files checked conservatively, including type imports/re-exports; no unresolved local imports or fixture/demo reachability. |
| New domain HTTP controllers | All 7 use GET only; no direct raw-fetch bypass. |
| File hygiene | No pending implementation markers, skipped/only tests or private-location patterns found in this change; largest source/test file is 713 lines. |

These checks establish static migration integrity, not an independent approval or
runtime/backend integration result.
Additional local validation completed on 2026-11-18 in outside-repository
runtime copies, without changing either source dependency manifest:

| Environment | Validation |
| --- | --- |
| Public dependencies | Installed from the public lockfile and registry; setup, typecheck, lint, public guardrails, Open Core check and production build passed. |
| Public feature regression | 72 suites / 711 tests passed for Task Escort, shared shell and the related HTTP contracts. |
| Enterprise assembly | Both source `dev` worktrees assembled successfully; setup, typecheck, lint, production build and 70 related suites / 699 tests passed. |
| Enterprise existing limitation | The guard reports an unrelated 251-line capability-set Hook against its 250-line limit; the file and guard report match the clean `dev` baseline exactly. |

This table is not a full-repository or live-backend acceptance claim. Broader
suite results belong in the source PR validation record. No configured backend,
production database or write API was exercised. Source PR merge, mirror
synchronization, enterprise gitlink integration and deployment remain separate
follow-up states.

### Pre-push test-fixture adjustment

The repository secret gate rejected a synthetic URL literal in the redaction
test as credential-like content. That test now constructs a URL using explicit
dummy fields and retains its password/query/fragment redaction and no-network
assertions. Runtime implementation, tests selected for execution and the secret
checker are unchanged. The ownership manifest records this single test-only
adjustment and an explicitly defined content checksum.
