# Public API — One Collaborator Authorization Seam

## Summary

The public API has no single place that answers *"may this person do this to
this bot?"*. Every group answers it its own way — five distinct shapes across
84 operations, and 38 more that never ask at all. This introduces one seam that
carries the whole answer (permission level, edit lock, audit record), one table
that says which answer each operation wants, and a build that refuses to start
when an operation is missing from that table.

Nothing about who may reach what changes for any group that already checks. The
one operation group whose check is currently wrong — harness — is corrected
through the new seam, which is what proves the seam works.

## Motivation

**The internal API has an AOP layer; the public API does not.** Internally,
`CollaboratorPermissionInterceptor` decorates ~76 routes across 16 routers and
does three jobs in one place: adjudicate the caller's collaborator level, refuse
a mutation when someone else holds the bot's edit lock, and write an audit
record for every non-owner action. On `/openapi/v1` that interceptor is used
zero times.

**What replaced it is five different shapes.** Authorization on the public
surface is now performed by a relay-resolve gate, a service-layer hook, a
publication facade, two router-local helper functions, and a repository access
check — each with its own permission bar and its own idea of whether the edit
lock or the audit record applies. Only one group (channels) reproduces the full
internal contract; its own comment says it is deliberately *"matching the
internal mutation policy"*, which is the tell that the policy has no home.

**The drift already produced a defect.** The harness group adjudicates the bot
named in the path, but every handler acts on an owner taken from the request
body, which is never checked. It also resolves that bot with a repository method
documented as *"no owner check … for permission checks use `get_by_id_and_owner`"*,
and it skips authorization entirely for one bot id. The check and the action are
keyed on different things — the exact failure the surface's own grant dependency
was written to make structurally impossible.

**Two known gaps are waiting on this.** Issues #906 and #907 defer collaborator
access for the bots, identity, resources, routines and MCP groups. Both are
blocked less by policy than by there being nowhere to put the policy: today
each would hand-roll a sixth shape. With a seam and a table, each becomes a
table edit.

**The convention is already settled and already shipped.** All 122 bot-scoped
operations address the bot on the path; `user_id` is a required query parameter
surface-wide; `owner_id` is a published query parameter with a written
contract. What is missing is only the shared enforcement that reads them.

## User Stories

- As a backend engineer adding a public operation on a bot, I want the
  authorization for it to be decided in one declaration and applied for me, so
  that I cannot ship an operation that forgot to check.
- As a backend engineer, I want to see every public operation and what it
  requires of a caller in one list, so that "what governs this endpoint?" is a
  question with one answer.
- As a reviewer, I want an operation added without an authorization decision to
  fail loudly and immediately, so that omission is never mistaken for a
  deliberate "no check needed".
- As a security reviewer, I want the check and the action to read the same
  values off the request by construction, so that no operation can adjudicate
  one bot and act on another.
- As a collaborator on a team bot, I want the surface to refuse me the same way
  whether I am not permitted or the bot does not exist, so that the API is not
  an oracle for other people's bots.
- As an operator investigating an incident, I want every non-owner mutation on
  a bot to leave the same audit record whichever public group it came through.

## Acceptance Criteria

### The table

- [ ] Every operation on the public surface appears in the authorization table
      exactly once, and every table entry corresponds to a live operation.
- [ ] Each entry states what the operation requires: a minimum collaborator
      level and whether it mutates, or an explicit named reason it is not
      adjudicated at this seam.
- [ ] An operation absent from the table is a **build failure** — the public
      router refuses to assemble, naming the method and path. It is never
      admitted, and never silently left unchecked.
- [ ] A route reachable on the public surface that was not built through the
      seam's route type is likewise a build failure.
- [ ] The authorization table and the existing app-caller admission table
      describe the same set of operations; a disagreement fails a test.

### The seam

- [ ] For an operation the table marks as adjudicated, authorization is applied
      automatically at router assembly. The operation's own module declares
      nothing and cannot opt out.
- [ ] The seam resolves the addressed bot from the bot id on the path and the
      owner from the request's owner parameter, defaulting to the caller. It
      reads no other source, so the values it checks are the values the
      operation acts on.
- [ ] A caller below the required level is answered exactly as if the bot did
      not exist. A caller who is the bot's owner always passes the level check.
- [ ] Any failure to resolve the bot, the owner, or the caller's level refuses
      the request. There is no path on which an unresolvable check proceeds.
- [ ] For a mutating operation on a bot that has collaborators, a caller who
      does not hold the bot's edit lock is refused, and the response names the
      current holder.
- [ ] A mutating operation that succeeds for a non-owner writes one audit
      record naming the bot, its owner, the actor and the operation. An owner's
      action writes none, matching existing behaviour.
- [ ] A failure to write the audit record never fails the request.
- [ ] The permission, lock and audit behaviours are three independent settings.
      Turning off one does not silently turn off another.

### Documented contract

- [ ] Every adjudicated operation publishes its owner parameter in the API
      document without its handler declaring it.
- [ ] An operation's published error set states the refusals the seam can
      produce.

### Harness, corrected

- [ ] The harness operations adjudicate the bot addressed on the path against
      the owner named in the request's owner parameter, through the seam.
- [ ] A harness request that also names an owner in its body is accepted only
      when that value agrees with the authorized owner; disagreement is
      refused. A request that omits it behaves as before.
- [ ] No bot id bypasses harness authorization.
- [ ] Harness mutations obey the same lock and audit rules as every other
      adjudicated mutation.

### Nothing else moves

- [ ] For every group other than harness, the caller admitted before this
      change is admitted after it, and the caller refused before is refused
      after, with the same status code.
- [ ] The app-caller grant rules are untouched.

## In Scope

- One authorization seam carrying permission level, edit lock and audit.
- One table covering every public operation, and the modes it can take.
- Automatic application of the seam at router assembly, and the build-time
  refusal that makes omission impossible.
- Recording, per operation, what each group enforces today — including the
  groups whose enforcement stays where it is for now.
- Correcting the harness group through the seam.
- The tests that pin all of the above, and the surface documentation.

## Out of Scope

- **Moving the groups that already adjudicate.** Channels, skills, skill sets,
  MCP, editors, render screens, containers, lifecycle, diagnostics, chats and
  the engine-runtime groups keep their current enforcement, recorded in the
  table as such. Migrating them to the seam is per-group follow-on work with
  its own behaviour risk, and doing it here would hide the seam's introduction
  inside a dozen behaviour changes.
- **Giving collaborators access to the owner-scoped groups** — bots, identity,
  resources, routines, local. That is #906 and #907, and it is a policy
  decision per operation, not a mechanism. After this change each becomes a
  table edit.
- **The app-caller admission table and grant dependencies.** Untouched.
- **The internal API's interceptor.** It stays exactly as it is; nothing on the
  internal surface changes.
- **Retiring the edit-lock or audit concepts**, or changing what a permission
  level means.
- **The deprecated group's self-checked addresses.** Named in the table as
  self-checked and left alone; they are removed when their addresses retire.

## Open Questions

1. **Read operations and the edit lock.** The internal interceptor couples them
   — turning off the audit record also turns off the lock check — so reads
   there are unlocked as a side effect rather than by decision. This spec
   separates the two settings and proposes reads never take the lock. Confirm
   that is the intended rule and not an artefact worth preserving.
2. **Audit scope for reads.** Today no read writes an audit record on either
   surface. Keeping that is assumed. If any public read should become audited,
   it should be named now rather than discovered later.
3. **Table granularity for the follow-on work.** The table records a single
   minimum level per operation. If any operation ever needs a bar that depends
   on the request's content rather than the operation, that operation must stay
   self-checked. No current operation appears to need this; confirm before the
   follow-on PRs start filling in rows.
