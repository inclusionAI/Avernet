# Public API — One Collaborator Authorization Seam

## Summary

The backend public API (`/openapi/v1`) has no single place that answers *"may
this person do this to this bot?"*. Every group answers it its own way — five
distinct shapes across 84 operations, and 38 more that never ask at all. This
introduces one seam that carries permission level and audit record, one table
that says which answer each operation wants, and an assembly step that refuses
to build the router when an operation is missing from that table.

Nothing about who may reach what changes for any group. The seam ships with its
table rows recording what each group enforces today, so this is inert on
arrival: it is the mechanism, and the policy decisions that use it (#906, #907)
become table edits afterwards.

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
was written to make structurally impossible. **Fixing it is not in this scope**;
it is handed to that group's owner as its own change.

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

- [ ] Every operation on the backend public surface appears in the
      authorization table exactly once, and every table entry corresponds to a
      live operation.
- [ ] Each entry states what the operation requires: a minimum collaborator
      level, or an explicit named reason it is not adjudicated at this seam.
- [ ] An entry that names another module as the enforcer cites a module path
      that really exists and really performs a permission check.
- [ ] An operation absent from the table makes the public router **fail to
      assemble** — `build_public_router()` raises, naming the method and path,
      so the application does not start. This is stronger than a test
      assertion: CI catches it because every test that builds the app fails,
      and a deploy of such a build fails too. The operation is never admitted,
      and never silently left unchecked.
- [ ] A route reachable on the public surface that was not built through the
      seam's route type likewise fails assembly.
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
- [ ] An adjudicated operation that succeeds for a non-owner and whose request
      method is not a read writes one audit record naming the bot, its owner,
      the actor and the operation. An owner's action writes none, matching
      existing behaviour.
- [ ] A failure to write the audit record is logged at error level with the
      bot, owner, actor and operation, and does not fail the request. The
      reason is stated in *Decisions* below.
- [ ] Permission and audit are two independent behaviours. Turning off one does
      not silently turn off the other.

### Documented contract

- [ ] Every adjudicated operation publishes its owner parameter in the
      **backend's** published API document without its handler declaring it.
      This criterion is about the backend service's own document only; no other
      surface's published contract is in scope.
- [ ] An operation's published error set states the refusals the seam can
      produce.

### Nothing moves

- [ ] For every group, the caller admitted before this change is admitted
      after it, and the caller refused before is refused after, with the same
      status code.
- [ ] Every edit lock enforced today by a service — channels, service
      publications — still behaves exactly as it does now. The seam not
      carrying a lock does not remove the locks that already exist.
- [ ] The app-caller grant rules are untouched.

## Decisions

Settled here rather than left open:

1. **No edit lock in the seam.** The internal AOP refuses a mutation when
   another collaborator holds the bot's edit lock. The public seam does not,
   and that is deliberate for this iteration: it is a behaviour the public
   surface has never had for most groups, adding it would change answers for
   every group that adopts the seam later, and the lock's semantics deserve
   their own decision rather than riding along with a mechanism change. Locks
   that services enforce today stay exactly where they are.

2. **An audit-write failure does not fail the request.** The request already
   succeeded when the record is written — the handler has mutated state and the
   response is about to say so. Failing at that point would report an error for
   an action that really happened, and a client retrying it would apply the
   mutation twice; a missing log line is the smaller harm. This is not the same
   direction as the seam's fail-closed permission check, and the two are
   consistent under one rule: **refuse before acting; never lie about what you
   did after acting.** Fail-closed prevents an action that has not happened
   yet, and after the fact there is nothing left to prevent. The cost is a
   silently incomplete audit trail, which is why the failure must be loud in
   logs. If audit completeness ever becomes a hard requirement, the answer is a
   durable outbox, not a synchronous write that can fail a request — named here
   so it is a decision rather than an omission.

3. **Which actions are audited follows the request method**, which the table
   key already carries: reads are not audited, matching both surfaces today. An
   operation that reads but is expressed as a non-read method would be audited
   wrongly; none of the adjudicated operations is such a thing today, so no
   per-row override is introduced until one exists.

## In Scope

- One authorization seam carrying permission level and audit.
- One table covering every backend public operation, and the modes it can take.
- Automatic application of the seam at router assembly, and the assembly-time
  refusal that makes omission impossible.
- Recording, per operation, what each group enforces today — including the
  groups whose enforcement stays where it is.
- The tests that pin all of the above, and the surface documentation.

## Out of Scope

- **The edit lock**, per *Decisions* 1. Existing service-level locks are
  untouched.
- **The harness group's defect.** Real, described in *Motivation*, and handed
  to that group's owner as a separate change. Its table rows record today's
  behaviour like every other group's.
- **Moving the groups that already adjudicate.** Channels, skills, skill sets,
  MCP, editors, render screens, containers, lifecycle, diagnostics, chats and
  the engine-runtime groups keep their current enforcement, recorded in the
  table as such. Migrating them is per-group follow-on work with its own
  behaviour risk, and doing it here would hide the seam's introduction inside a
  dozen behaviour changes.
- **Giving collaborators access to the owner-scoped groups** — bots, identity,
  resources, routines, local. That is #906 and #907, and it is a policy
  decision per operation, not a mechanism. After this change each becomes a
  table edit.
- **The app-caller admission table and grant dependencies.** Untouched.
- **The internal API's interceptor.** It stays exactly as it is; nothing on the
  internal surface changes.
- **Any surface other than the backend's own `/openapi/v1` document.**
- **The deprecated group's self-checked addresses.** Named in the table as
  self-checked and left alone; they are removed when their addresses retire.

## Open Questions

1. **The adjudicated path ships with no production caller.** With harness out
   of scope, every table row is "enforced elsewhere" or "no collaborator
   dimension yet", so the seam's own code path is exercised only by its tests
   until #906/#907 land. That is normal for infrastructure that precedes
   policy, but an unused branch is exactly what rots. Two ways to avoid it, if
   preferred: have one group that a service already checks *also* declare the
   seam at the identical level — redundant, zero behaviour change, one extra
   bot resolve per request — or accept the gap and rely on tests. Proceeding on
   the second unless told otherwise.

2. **Audit scope for reads.** No read writes an audit record on either surface
   today, and *Decisions* 3 keeps that. If any public read should become
   audited, name it now rather than discovering it later.
