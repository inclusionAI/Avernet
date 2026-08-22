# Public API — Adopt the Collaborator Authorization Seam

## Summary

The seam that answers *"may this person do this to this bot?"* shipped in
#1323 with no adopter: every row in its table records what some service
enforces elsewhere, and no row is enforced by the seam itself. This moves 88 of
those operations onto the seam and deletes the service-side check each one was
relying on, so that for those operations the answer is decided in exactly one
place.

This is a behaviour change, not a mechanism change. Each group's bar is
re-derived from the code being deleted rather than copied from the table, and
where the seam's answer differs from the service's, the difference is a decision
recorded in this feature — not a side effect.

## Motivation

**The seam has no production caller.** #1323 built the table, the route class
and the gate, and deliberately adopted none of it (`spec.md` *Decisions* 4). The
`Check` path is exercised only by fixture routers. Every month it stays that way
is a month the mechanism is unproven against real handlers, real levels and real
callers, while the drift it was built to stop continues.

**The table records bars it cannot prove.** #1323 says so outright: the levels
on `ServiceChecked` rows "were read off the modules they cite, and nothing here
can prove them." The inventory test only checks that a citation resolves to a
module performing *some* permission check — not that the number beside it is the
number that module enforces. Ninety-three recorded bars are currently
unverified, and they read exactly like verified ones.

**Six shapes still decide the same question.** A relay-resolve gate, a
service-layer hook, a publication facade, two router-local helpers and a
repository access check each carry their own bar and their own view of whether
the edit lock or the audit record applies. Consolidating removes five of them.

**The retiring addresses are covered only by accident.** Twenty-two of the
`deprecated/` addresses are checked today purely because the check sits *inside*
the handler they share with their replacement. Deleting those checks would leave
them bot-scoped and unguarded, which makes the twins this feature's problem
rather than the deprecation schedule's.

## User Stories

- As a backend engineer, I want an operation's collaborator bar enforced by the
  same declaration that documents it, so that reading the table tells me what
  actually happens at runtime.
- As a backend engineer deleting a service's permission code, I want the
  operation to keep refusing the same callers it refused before, so that a
  consolidation is not a silent grant.
- As a security reviewer, I want every enforced bar to cite the code it was
  derived from, so that "MEMBER" in the table is a finding rather than an
  assumption.
- As an integrator still calling a retiring address, I want it to refuse me
  exactly as its replacement does, so that a deprecated URL is not a way around
  a check.

## Acceptance Criteria

- [ ] 88 of the 90 in-scope operations carry `Check(level)` and the seam
      attaches the gate to each — verified structurally, not by sampling. The
      remaining 2 (bot chats) carry `NoCheck` with the reason below.
- [ ] For each operation that gains `Check`, the service-side collaborator check
      it previously relied on is deleted, and no remaining code path performs
      that check for that operation.
- [ ] The 2 bot-chat operations are recorded as having no collaborator
      dimension, because they scope their records to the acting user and never
      read the addressed owner. Their citation today names a module that
      performs no collaborator check; the row must state what is true rather
      than preserve the claim.
- [ ] Every enforced level is derived from the code being deleted and recorded
      with the evidence it was derived from. Where the derived level differs from
      the level #1323 recorded, the difference is called out explicitly and
      argued, not silently adopted — **the rule is that the code decides and the
      row is corrected to match, never the reverse.**
- [ ] Each of the 16 old addresses under `deprecated/` whose replacement is
      migrated still admits and refuses exactly the callers its replacement
      does, proven by test rather than by reading the code. The other six — the
      old skills addresses — are untouched, because this feature no longer
      changes the code that checks them.
- [ ] The check and the handler use the same bot and the same owner. Both read
      the bot from the URL path and the owner from the query string, so a caller
      cannot have permission checked against one bot while the handler changes a
      different one.
- [ ] If a handler gets its bot from somewhere the check cannot read — from the
      request body, for example — then that operation does **not** get a `Check`
      row. `Check` means "the seam enforces this", and here the seam cannot, so
      the row would be a false claim. Adding one makes the application refuse to
      start. This is permanent, not a gap to close later: the check runs before
      the handler, so it can only read what the request itself carries.
- [ ] Every edit lock enforced today is still enforced afterwards, on the same
      operations, refusing the same callers.
- [ ] No in-scope operation ends up unaudited, and none ends up audited twice for
      one request.
- [ ] Every row left as `ServiceChecked` cites the module that really performs
      its check. Three skills rows cite one that does not, and correcting them is
      part of this feature even though those rows do not migrate — a wrong
      citation already sent this plan looking for a bar in the wrong module.
- [ ] Every operation whose bar is enforced publishes the parameter that names
      the bot owner it adjudicates.
- [ ] `scaffolding_row_count()` falls from 181 to 75. The remaining
      `ServiceChecked` rows are exactly 9: the 6 harness rows and the 3 skills
      rows deferred below.
- [ ] No operation outside the 90 in-scope rows changes which callers it admits
      or refuses.

## In Scope

- 90 of the table's 99 `ServiceChecked` operations (the table is 246 rows today:
  99 `ServiceChecked`, 65 `NoCheck`, 40 `OWNER_SCOPED`, 42 `INHERITED`, 0
  `Check`): 26 engine-runtime gating, 19 skill-centre
  authorization hook, 16 service-publication facade, **7** bot-skill asset
  service, 6 channels, 5 collaborator service, 3 authorized apps, 3 render
  screens, 2 bot chats, 2 diagnostics, 1 engine-runtime connection.
- Correcting the citation on the 3 skills rows that name a module which does not
  check them, even though those rows stay `ServiceChecked`.
- Deleting the service-side collaborator check each of those relied on.
- The 16 retiring `deprecated/` addresses that twin a migrated operation and
  carry the bot on the path, which become adjudicated rather than inheriting.
- **No change to the seam, and no new authorization code anywhere.** Every
  operation this feature adjudicates is adjudicable by the seam exactly as it
  stands today: the 88 current addresses and the 16 retiring twins that carry
  the bot on the path.
- Adding the owner parameter to the 3 authorized-apps handlers, which cannot
  carry an enforced row without it.
- Correcting the 2 bot-chat rows to the mode that matches their code.

## Out of Scope

- **Three skills operations** — `GET` and `POST /bots/{bot_id}/skills`, and
  `POST /bots/{bot_id}/skills/upload-folder`. Their checks live in
  `local_skill_query_service` and `local_skill_upload_service`, **not** in the
  module their rows cite. Those same two modules keep the six retiring skills
  addresses checked, four of which the seam could never adjudicate because the
  skill id resolves its own bot inside the handler. Migrating these three would
  mean deleting those checks and writing replacement authorization inside the
  retiring package — new code, in the one place scheduled for deletion.
  Deferred whole, so the retiring addresses keep exactly the checks they have
  and nothing new is written.
- **The 6 harness operations**, and not only because of the defect filed in
  #1323 — the deeper reason is that they are the one group the seam *cannot*
  adjudicate as they stand. Their handlers act on a bot named in the request
  **body** while the bar would be decided from the one on the path, so the check
  and the action are keyed on different things by construction. Adjudicating
  them means first changing what they address, which is a bug fix with its own
  blast radius (ownership also resolves through a method documented as
  performing no owner check, and one bot id skips the check entirely).
  **`ServiceChecked` therefore does not reach zero in this feature**; it reaches
  9 — these 6 plus the 3 skills rows above.
- **The 40 `OWNER_SCOPED` operations.** Blocked on #906 / #907, and a policy
  change rather than a consolidation: collaborators start getting through.
- **The 26 remaining `INHERITED` operations** — 20 twin `OWNER_SCOPED`
  addresses and follow their replacements whenever those are decided; 6 are the
  legacy skills addresses, untouched along with the three current rows they
  share their checks with.
- **Introducing an edit lock where none exists today.** #1323 *Decisions* 1
  stands: locks stay exactly where they are, and this feature must preserve them
  rather than extend or remove them.
- **Changing what is audited.** Reads stay unaudited, mutations stay audited.
- Retiring or deleting any `deprecated/` address ahead of its schedule.

## Open Questions

- ~~Where a derived level differs from the level #1323 recorded, which wins?~~
  **Settled by the first divergence found.** The two bot-chat rows cite a module
  containing no collaborator check at all, while the handler discards the
  addressed owner and scopes to the acting user. The rule taken from it: the
  code decides, the row is corrected to describe it, and a bar is never invented
  to justify a citation. Applying that rule made those two `NoCheck` rather than
  `Check`.
- Do any of the 90 operations have a caller that is not a human collaborator
  (an application acting under a grant), for which the collaborator bar is the
  wrong question entirely? Admission is a separate seam, but the interaction is
  untested while no row is enforced.
