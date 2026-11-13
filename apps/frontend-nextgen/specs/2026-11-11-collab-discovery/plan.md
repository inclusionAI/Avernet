# Collab Discovery Implementation Plan

**Goal:** Export BCS-only public Bot/group discovery in collab without expert,
task or Human single-chat behavior.

**Architecture:** Reuse public discovery shells, hooks and services; retain
legacy full facades for task consumers. Configure differences at the collab
composition root. Preserve the strict runtime/source-slice closure guard.

**Tech Stack:** TypeScript, React, Umi, Zustand, Jest/Testing Library, AST guard.

## 1. Pin assembly and behavior (RED)
- [x] Extend `test/routes/collabRoutes.test.ts` with Bot/group routes and expert exclusion.
- [x] Update `test/extensions/collab.test.ts` to expect discovery navigation;
  assert `getPublicBotBackendEnabled() === false` even when runtime config is true.
- [x] Add `test/components/CollaborationSquare/CollabDiscovery.test.tsx` for expert
  tab omission and Human owned/friend/no-auto-chat behavior under real collab capabilities.
- [x] Run those tests and record failures before implementing.

## 2. Isolate retained discovery from task-inclusive facades
- [x] Extract `PublicDiscoveryApiAdapter` (Bot/group methods) and
  `PublicDiscoveryService`/shared public singleton assembly. Existing
  `CollaborationSquareApiAdapter` and `CollaborationSquareService` extend these
  with task methods and preserve public exports. Remove broad backend barrels.
- [x] Extract `usePublicDiscovery` from `useCollaborationSquare`; keep the latter
  composing the public hook and task hook for legacy full consumers.
- [x] Extract `PublicDiscoveryPageShell` with shared Bot/group rendering;
  `SquarePageShell` remains the full wrapper, with legacy task rendering separate.
- [x] Retained hooks and group-view types import exact public modules, never
  the full service/hook facade. Run typecheck and existing discovery tests.

## 3. Enable the collab assembly (GREEN)
- [x] `config/routes.collab.ts`: add discovery redirect and two page entries.
- [x] `src/extensions/collab.ts`: add discovery nav; hard-disable optional Backend;
  `getDiscoveryExpertsEnabled: () => ({ status: 'available', value: false })`.
- [x] Add default true capability in types/defaults; `SquareNavigation` filters
  only the expert item by that capability. Leave Open ordering unchanged.
- [x] `scripts/check-collab-closure.cjs`: allow exactly Bot/group page entries,
  explicitly forbid legacy task shell/hook/service/adapter and BBS transport.
  Add positive/negative closure assertions; do not exempt broad directories.
- [x] Run the red tests, closure guard and source-slice typecheck to green.

## 4. Regression and delivery evidence
- [x] Add HTTP-boundary Bot/group integration tests with collab capabilities;
  assert no Backend/private-session/task/space requests and no chat navigation.
- [x] Update active collab export docs/spec and README; preserve historical evidence.
- [x] Run `npm run check:open-core`, `npm run ci`, `npm run build:collab`;
  inspect emitted page chunks and source-file sizes; `git diff --check`.
- [x] Report public validation and private-export limitations; do not push unless requested.
