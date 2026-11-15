# Space management: Figma alignment

## Layout

Measurements read from the space-management design source, not from a scaled screenshot:

- At a 1440px desktop viewport: 200px sidebar, 20px content inset, 64px page header.
- Three-column grid, 16px gaps; reference cards are 376 × 188px with 12px corners.
- Card content: 20px horizontal / 16px vertical padding, 40px identifier, three statistics: members, last update, and creator (per the revised request).
- Separate 56px card footer contains membership, current-space state, or join action.
- Create and add-member dialogs: 480px maximum width; member drawer: 680px maximum width.
- Member table order: member, identity (role), permissions, and administrator-only actions.
- Narrow screens wrap the toolbar, collapse the card grid, and clamp the drawer to the viewport.

## Contract and behavior

No new endpoint or permission model is introduced. Existing search, pagination, creation,
join approval, directory search, batch addition, role changes and removal remain in use.
A space card exposes a details action only for joined spaces or the owned personal space.
Personal spaces never expose a join action.

The revised card uses `memberCount`, `gmtModified` (relative time), and `creatorUserName`,
not Bot/admin counts. Missing update time or creator displays as `—`. Member permission
counts remain optional; missing permission counts display as `—`, while numeric zero remains visible. Member subtitles use the existing user ID because the
current domain model has no department field; no department is inferred from display names.

Failed join submissions keep the form open. Failed creation keeps the entered name;
canceling creation clears it. Creation and join forms release their loading state in
`finally`. Search resets pagination to page one. Creator and last-administrator guards
remain enforced in member actions; the last administrator has no remove confirmation trigger.

## Verification

- Regression tests: `test/components/Admin/SpaceManagement.test.tsx`,
  `test/pages/Admin/Spaces.test.tsx`, existing member/batch and mapper tests.
- Public frontend: `npm run check:open-core` and `npm run ci`.
- Browser validation should cover list, create dialog, personal/team member drawer,
  add-members dialog, and narrow screens. An isolated fixture preview is UI evidence,
  not proof of authenticated backend integration.
