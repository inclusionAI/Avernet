# Validation — 2026-11-11

Base: `02f6fb7ce6` (rebased onto REL20261112). Evidence is for the local public
source tree; changes have not been committed or pushed.

## Automated evidence

- Initial assembly/route/Human-action regression: **9 failed / 10 passed** before
  implementation. Failures included existing/owned Bot conversation entry and
  automatic private-session creation on accepted friendship.
- Assembly, route and Human-action regression after implementation: **19 passed**.
- Full `npm run ci`: **822 suites / 6742 tests passed**; guardrails, typecheck,
  lint, **21 closure-parser fixtures**, runtime closure and Open build passed.
- `npm run check:open-core`: passed.
- `npm run build:collab`: passed.
- Runtime closure: **13 entries / 597 files / 0 violations**; the existing
  source-slice positive/negative TypeScript tests also passed in full CI.
- Final targeted tests (after tightening the new test's import to the public
  runtime): **3 suites / 10 tests passed**, and typecheck passed again.
- Production output inspection: **7 named page chunks**, all in the retained
  page allowlist; public Bot/group chunks present, experts/tasks/chat absent.
  The shared collaboration index can be merged into shared/Focused chunks by
  granular splitting; generated Umi routes still register its lazy entry.
- `git diff --check`: passed; all new/modified source files stay below 1000 lines.

## Behavioral coverage

Real collab capabilities and real Bot/group route components mounted within
AppShell, with only the HTTP boundary mocked. Runtime Backend configuration was
explicitly true to prove collab cannot accidentally restore Backend enhancement.

- Bot cold entry, candidate list, profile fallback and approved friendship use
  only allowed BCS endpoints; no Backend/task/space/private-session requests.
- Human existing-friend and managed Bot cards have no conversation action.
- Direct action invocation for those Bots does not create a session or navigate;
  accepted friendship updates state without automatically opening private chat.
- Public group listing, member details and session creation use BCS; successful
  creation navigates to `/workspace/collaboration` with group/session identifiers.
- Open expert navigation and existing discovery/task behavior retained by the
  full regression suites. Extraction comparison confirmed 10 adapter and 14
  service retained method bodies unchanged.

The full-regression static UI/layering scan was expanded to include the new
public shell and hook; its existing assertions were not weakened. Closure tests
also reject experts/tasks and full task facades imported from allowed discovery
entries.

## Not executed

No live browser/backend-stack E2E, private OCB export/install/build, or Docker
image test was run. OCB must update the public source revision, inherit the
collab capabilities, update artifact navigation assertions, and re-run its
isolated export and deployment gates. Historical export evidence does not
validate the newly expanded artifact.
