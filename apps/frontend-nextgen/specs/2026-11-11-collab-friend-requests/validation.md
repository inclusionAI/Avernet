# Validation — 2026-11-11

Base: `3c6bdb9ad1`. Local implementation, not committed/pushed or deployed.

## Executed

- New service/Hook/component tests were introduced before their implementation;
  entry integration tests reproduced the missing button before assembly wiring.
  An added business-403 detail-lookup regression failed before fixing the
  authorization fallback (1 failed / 17 passed), then passed.
- Targeted controller/service/Hook/modal/real-page integration: **5 suites,
  49 tests passed**.
- `npm run check:open-core`: passed.
- `npm run ci`: passed, including source guardrails, typecheck, lint,
  **826 suites / 6783 tests**, **21 closure fixtures**, closure guard and Open build.
- Collab closure: **13 entries / 604 files / 0 violations**; existing source-slice
  typecheck conformance tests pass as part of full CI.
- `npm run build:collab`: passed. Named page-chunk allowlist inspection confirms
  no additional route/page outside the retained collab surface.
- `git diff --check`, changed source line-count checks (all below 1000), and new
  documentation relative-link existence checks passed.

## Covered behavior

- Default/Open and public group pages have no entry; collab public Bot page
  places it after the identity picker. No inbox requests while the modal is closed.
- Real page + shell + BCS controllers/services at a mocked HTTP boundary exercise
  actor-filtered pending requests, a single full-id Bot batch across both pages,
  avatar/name presentation, accept/reject, catalog refresh and zero navigation.
  Request allowlist excludes Backend/private sessions/tasks/space/message center.
- Human queries use authenticated user id; changing discovery actor destroys the
  open modal. Hook tests cover A→B→A, stale reads, close during mutation, duplicate
  click prevention, read errors/retry and recipient/state restrictions.
- Mutation failures do not report success or change the pending row. Confirmed
  outcomes update status without discarding enriched Bot presentation.
- Detail enrichment ordinary failures retain rows with warnings/default avatars;
  auth errors and aborts are propagated. Empty requests skip Bot queries.
- Contract-matched pagination, invalid envelopes/data, wrong confirmation ids and
  encoded request path segments are tested. No creation timestamp is fabricated.

## Gaps and knowledge

No live BCS/browser E2E, private OCB source export, isolated exported-tree install,
or Docker image validation was run. These remain deployment/host gates.
The app README links `docs/collab-friend-requests.md`, the owning flow and failure
contract; existing discovery/BCS API ownership is unchanged. No BCS code changed.
