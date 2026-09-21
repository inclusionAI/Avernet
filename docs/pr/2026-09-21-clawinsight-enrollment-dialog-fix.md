# fix(clawinsight): center the monitoring enrollment notice

## Problem

When a user selected a Bot that was not yet enrolled in monitoring and clicked
`加入监控`, the placeholder notice `加入监控暂未开放` could render at the
upper-left corner of the page in the deployed ClawWeb host. The native
`<dialog>` element was relying on browser default modal positioning, while the
host page's global styles could override that default behavior.

This made the notice appear detached from the user's action and obscured the
left navigation area.

## Solution

- Keep the existing native modal dialog, user-facing copy, focus return, and
  `::backdrop` behavior unchanged.
- Explicitly position `.join-dialog` at the center of the viewport with
  `position: fixed`, `top: 50%`, `left: 50%`, and a translate transform.
- Reset the dialog's default margin so host-level dialog styles cannot move it
  back to the page origin.
- Bound the dialog height and allow internal scrolling on small viewports.
- Do not change the monitoring API, enrollment behavior, header, sidebar, or
  other monitoring layout.

## Validation

- `MonitoringSelector.test.tsx`: **12 passed, 0 failed**.
- `npm run check --workspace @avernet/clawinsight`: **passed**.
- `git diff --check`: **passed**.
- The patch changes only the monitoring selector stylesheet and this PR
  description document.

## Compatibility and risk

- CSS-only UI correction; no API, database, permission, or persistence changes.
- The enrollment endpoint and its not-yet-available response remain unchanged.
- The dialog remains a native modal and retains keyboard focus handling and the
  existing backdrop.
- Rollback is limited to reverting the stylesheet change.

## Spec

This is a follow-up fix for the ClawInsight monitoring Bot enrollment
placeholder introduced by the monitoring Bot search and status UI.

## Related issues

- Follow-up to the ClawInsight monitoring Bot search and monitoring status
  change shipped in the parent branch.
