# Collab general configuration isolation implementation plan

**Goal:** General configuration in BCN-only collab must load and save without engine or organization-directory services.

**Design source:** `~/Desktop/collab-form-裁剪通用配置-problem-and-plan.md`, sections 3.1–3.6. Apply all three proposed decisions: hide restricted visibility, hide historical department viewers, and omit engine enrichment (preserve any engine already supplied by BCN).

**Architecture:** Add independent organization-directory and engine-enrichment capabilities. Open defaults disable the directory; collab additionally disables engine enrichment. Full internal explicitly enables both. Resolve capabilities at call time, after extension registration. Keep service contracts and persistence unchanged.

## Steps

- [x] Add failing capability, production-wiring, editor, and historical-viewer regressions. Exercise load, refresh, synchronization, and search with unavailable organization/engine endpoints.
- [x] Add capability signatures/defaults and collab/full-internal composition. Guard all runtime department hydration paths and searches; build identity from the authenticated identity when the directory is unavailable. Gate optional engine dependency in production composition without eager capability capture.
- [x] Require directory availability for organization visibility selection; hide historical organization viewers while preserving summaries and allowing users to replace unsupported settings. Keep friend approval's existing two-option behavior.
- [x] Run focused public tests, typecheck, collab closure checks, and internal assembly contracts using `assemble-internal.sh --open-dir <Avernet frontend> --collab`. Verify full internal capability overrides separately. Check changed source line counts and whitespace.

No commits, pushes, submodule pointer changes, or deployment PRs are part of this local fix. Browser/live-backend validation must be reported separately from automated tests.

## Implementation notes

- Both load and refresh retain department names embedded in DTOs, even when remote resolution is disabled. Skipping the entire mapper would discard valid historical labels.
- The production engine dependency uses a call-time getter so module imports before extension registration cannot capture the Open Core default.
- Browser testing exposed an additional engine-catalog call from `src/services/workspace/identityService.ts` during shell startup. Its optional metadata enrichment now honors the same capability, with separate disabled/enabled regressions.
- Capability regression coverage includes full internal opt-in, collab-internal private identity presentation with the directory disabled, preservation of BCN-supplied engine metadata, historical viewers, and replacement of restricted visibility.

## Validation boundaries

The assembled collab-internal production build was exercised in headless Chrome with local HTTP fixtures (not a deployed backend). Opening configuration and saving both friend policies and both supported visibility values produced only collaboration mine/PATCH requests, no organization or engine-catalog requests, and no page errors.

Broader OCB tests contain five unrelated failing suites (stale bot-workshop import paths, native-title expectations, and shell visibility expectations). The same failures reproduce after restoring baseline source/test snapshots in a separate temporary assembly. Full assembled typecheck has 43 diagnostics; the baseline and patched diagnostic sets match exactly after excluding line positions. No existing gates were weakened and no unrelated migration fixes were included.

## Verified results

- Public Jest: 697 suites / 5,429 tests passed.
- Assembled internal targeted Jest: 9 suites / 69 tests passed.
- Public typecheck, lint, guardrails, collab closure and its 12 self-tests, Open Core checks, and collab build passed.
- Internal collab production build and headless Chrome configuration save workflow passed.
- Modified and added source files remain below 1,000 lines; both worktrees pass `git diff --check`.
