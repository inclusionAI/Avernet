# Public API — Adopt the Collaborator Authorization Seam

## Summary

The seam that answers *"may this person do this to this bot?"* shipped in
#1323 with no adopter: every row in its table records what some service
enforces elsewhere, and no row is enforced by the seam itself. This moves 93 of
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

- [ ] 91 of the 93 in-scope operations carry `Check(level)` and the seam
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
- [ ] Each of the 22 retiring `deprecated/` twins of an in-scope operation
      refuses and admits the same callers as its replacement, and this is
      asserted by test rather than by inspection.
- [ ] For every adjudicated operation, the bar is decided from the same two wire
      values the handler acts on: the bot the request addresses, read from the
      same part of the request the handler declares it in, and the same resolved
      owner. No arrangement of request parameters can aim the check at one bot
      while the handler acts on another.
- [ ] An operation whose handler takes its bot from anywhere else — a body
      field, say — **cannot** be adjudicated and must be refused an enforced row
      rather than given one that does not match. This is a structural limit of
      deciding the bar before the handler runs, not a gap to close later.
- [ ] Every edit lock enforced today is still enforced afterwards, on the same
      operations, refusing the same callers.
- [ ] No in-scope operation ends up unaudited, and none ends up audited twice for
      one request.
- [ ] Every operation whose bar is enforced publishes the parameter that names
      the bot owner it adjudicates.
- [ ] `scaffolding_row_count()` falls by 93; the remaining `ServiceChecked` rows
      are the 6 harness rows and nothing else.
- [ ] No operation outside the 93 changes which callers it admits or refuses.

## In Scope

- The 93 `ServiceChecked` operations outside the harness group, across 11
  modules: 26 engine-runtime gating, 19 skill-centre authorization hook, 16
  service-publication facade, 10 bot-skill asset service, 6 channels, 5
  collaborator service, 3 authorized apps, 3 render screens, 2 bot chats, 2
  diagnostics, 1 engine-runtime connection.
- Deleting the service-side collaborator check each of those relied on.
- The 22 retiring `deprecated/` addresses that are twins of an in-scope
  operation, which become adjudicated rather than inheriting.
- Letting the seam read the addressed bot from the query string as well as the
  path. **This is the only change the seam itself needs, and only six rows force
  it** — the retiring skills addresses, which predate bot-first addressing and
  still publish `bot_id` as a query parameter. Every other in-scope operation,
  and the sixteen path-addressed twins, are adjudicable by the seam exactly as
  it stands today.
- Adding the owner parameter to the 3 authorized-apps handlers, which cannot
  carry an enforced row without it.
- Correcting the 2 bot-chat rows to the mode that matches their code.

## Out of Scope

- **The 6 harness operations**, and not only because of the defect filed in
  #1323 — the deeper reason is that they are the one group the seam *cannot*
  adjudicate as they stand. Their handlers act on a bot named in the request
  **body** while the bar would be decided from the one on the path, so the check
  and the action are keyed on different things by construction. Adjudicating
  them means first changing what they address, which is a bug fix with its own
  blast radius (ownership also resolves through a method documented as
  performing no owner check, and one bot id skips the check entirely).
  **`ServiceChecked` therefore does not reach zero in this feature**; it reaches
  6.
- **The 40 `OWNER_SCOPED` operations.** Blocked on #906 / #907, and a policy
  change rather than a consolidation: collaborators start getting through.
- **The 20 remaining `INHERITED` operations** — twins of `OWNER_SCOPED`
  addresses, which follow their replacements whenever those are decided.
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
- Do any of the 93 operations have a caller that is not a human collaborator
  (an application acting under a grant), for which the collaborator bar is the
  wrong question entirely? Admission is a separate seam, but the interaction is
  untested while no row is enforced.
