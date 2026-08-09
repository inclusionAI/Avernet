# Public API — Operate Shared Bots and Published Stages

## Summary

The public engine-runtime surface — the sessions, engine, models and approvals
groups, and the connection endpoint — currently refuses every bot that more
than one person can reach: a personal bot that has been made public, any bot
with collaborators, and a service bot anywhere but its pre-publication draft
workspace. This lifts that restriction along both axes at once. A bot's
**operators** — its owner and its collaborators — can operate it whether or not
it is public or has collaborators, and a service bot's **verify** and
**online** runtimes become addressable alongside its draft.

Who counts as an operator is decided in one place. The target — whose bot, and
which of its runtimes — is named in the request, the same way the parallel
explicit-user-id change made the end user a named part of the request. A
request that names neither behaves byte-for-byte as it does today.

## Motivation

**The restriction was a first cut, not a policy.** The engine-runtime surface
shipped serving private personal bots only, with collaborator access recorded
as an open question and the widening explicitly anticipated: "widening this
later breaks nothing." A later change extended it to a service bot's draft —
and stopped there. What remains refused is not a hazard class; it is the
backlog of that question, and this spec is its answer.

**The refused bots are the interesting ones.** A service bot exists to be
published: its verify and online runtimes are the product, and the draft is
the workshop. Today a caller can create a service bot through the public API,
drive its draft, publish it — and then not observe the runtime they just
published. A coding bot is built by a team, and adding the first collaborator
cuts the entire team, the owner included, off from the surface with a 501.
Making a personal bot public does the same to its owner. In each case the
surface withdraws exactly when the bot starts to matter.

**The internal surface already answers the question this spec asks.** In the
workbench, a bot's operator channel — the device connection — is granted to
the bot's owner and to its member-level collaborators, and an owner watching
their published bot sees that runtime's sessions, whoever started them. The
public surface refuses the same people the same data. This spec brings the two
surfaces to the same answer, rather than inventing a new one.

**The parallel explicit-user-id change gives this its shape.** That change
split "which user is this request for?" into *acquisition* (the request names
the user) and *adjudication* (one seam decides whether the caller may act for
them). This spec applies the same split to "which bot, whose, at which
stage?": the request names the owner and the stage, and one place decides
whether the caller may operate that bot. Handlers learn nothing; the rule
lives once.

## User Stories

- As a collaborator on a team-built coding bot, I want to list and drive the
  bot's sessions from my own account, so that working on the team's bot does
  not mean borrowing the owner's identity.
- As a service-bot operator, I want to observe the sessions, engine state,
  models and approval mode of the verify and online runtimes I published, so
  that publishing a bot does not mean losing sight of it.
- As the owner of a personal bot, I want making it public to leave my API
  access exactly as it was, so that visibility is a property of the bot, not a
  penalty on its owner.
- As a partner integrating the public API, I want a bot I may not operate to
  answer exactly like a bot that does not exist, so that the surface discloses
  nothing about other tenants' or users' bots.
- As an engineer extending this surface, I want "may this caller operate this
  bot?" answered in one place, so that a later widening — delegation, new
  roles — is a reviewable change to that place rather than a sweep across
  every handler.

## Acceptance Criteria

### Which bots are served

- [ ] A public personal bot is served to its operators, exactly as a private
      one is.
- [ ] A bot with collaborators — a service bot, or a personal bot whose engine
      takes collaborators — is served to its operators.
- [ ] A service bot's verify runtime is addressable while a verify release is
      live, and its online runtime while an online release is live.
- [ ] A service bot's draft workspace is served exactly as it is today.
- [ ] Bot types the surface has never heard of are still refused; the
      allowlist itself does not widen.

### Who may operate

- [ ] The bot's owner may operate it.
- [ ] A collaborator at member level or above may operate it — one bar for
      every operation on the surface, the same bar the internal operator
      channel applies.
- [ ] Public visibility grants operation to no one: a public bot's audience
      can talk to it, not operate it.
- [ ] Any other caller receives an answer byte-identical to naming a bot that
      does not exist.
- [ ] The end-user contract from the explicit-user-id change is untouched: the
      request's `user_id` still names the verified caller, and naming anyone
      else is still refused the same way.

### Naming the target

- [ ] An operation can name the bot's owner when the owner is not the caller.
      The parameter is optional and defaults to the caller, so operating one's
      own bot names nobody extra.
- [ ] An operation can name the stage it addresses. The parameter is optional,
      defaults to the draft, and its value set is closed and published.
- [ ] Both parameters follow the placement rule the explicit-user-id change
      settled: the query string, never a body field, never a path segment. No
      path changes.
- [ ] A request that names neither owner nor stage behaves byte-for-byte as
      today, for every operation on the surface.
- [ ] The published API description documents both parameters on exactly the
      engine-runtime operations, and nowhere else.

### Stage semantics

- [ ] Naming a stage with no live runtime — a verify that is not validating,
      an online that has not released, any stage but draft on a personal bot —
      is refused with one fixed answer that says the stage is not live, and
      that answer is distinguishable from "no such bot".
- [ ] There is no fallback between stages: a request for verify is never
      answered by the online runtime, or the reverse.
- [ ] The draft remains the pre-publication workspace only; a draft-addressed
      request can never reach a published runtime.

### What an operator sees

- [ ] The surface remains an operator console: an admitted operator reaches
      the addressed runtime's device-wide state, including sessions created by
      other operators and by end users conversing with a public or published
      bot. The published documentation says this plainly, before an integrator
      discovers it.
- [ ] A session created through this surface records the operator who created
      it as its user, so that on a shared runtime, sessions remain
      attributable.

### Refusals and disclosure

- [ ] A refused non-operator learns nothing: not whether the bot exists, is
      public, is published, or has collaborators. Two refused callers get
      byte-identical answers.
- [ ] Which caller asked, and which owner's bot was asked for, are recoverable
      from the logs at the point of refusal.
- [ ] The "not supported" refusal (501) now means only what it says — a bot
      type the surface cannot serve — and no longer stands in for "shared".

### Documentation

- [ ] The surface's handoff documentation and the engine-surface reference
      record the widened rule — who may operate, which stages, what an
      operator sees — in the same change, including the Chinese mirrors.
- [ ] The prior widening to draft service bots, which shipped without a spec
      or a doc entry, is recorded retroactively so the doc's history is whole.
- [ ] Prose that misstates how published runtimes are separated from the draft
      is corrected where it stands.

## In Scope

- The five engine-runtime groups — sessions, engine, models, approvals, and
  the connection endpoint (16 operations).
- The operator adjudication, the stage addressing, and the connection
  composition that serves them.
- The tests that pin today's refusals, deliberately flipped, and new tests
  pinning the widened contract.
- The surface's documentation, as listed above.

## Out of Scope

- **Collaborator access to the rest of the surface** — bots, identity,
  resources, MCP, routines. Skills already ships owner-versus-actor semantics;
  each remaining category needs its own decision about what a collaborator may
  do to *data* (edit a bot's record? delete its resources?), which is not the
  same decision as admitting them to an operator channel. Follow-up work,
  named in the handoff doc.
- **The routines group's stage pin.** Routines drive the draft runtime by an
  explicit pin today. The internal cron machinery already understands stages,
  so widening it is real work with its own semantics (what does a routine on a
  verify runtime mean during validation?) — its own track.
- **Delegation.** Who may *call* is unchanged: a request still requires a
  verified end user, the named `user_id` must still be the caller, and
  App-on-behalf-of remains the delegation workstream. This spec widens which
  bots a caller reaches, not which callers exist.
- **The eval stage.** It has no long-lived runtime to address.
- **Per-caller containers.** Bots whose callers each get their own runtime are
  end-user chat machinery; their operator surface remains the draft workspace
  addressed here.
- **Per-caller session scoping on shared runtimes.** Rejected, not deferred —
  see Decisions.
- **Publish and visibility lifecycle.** Making a bot public, publishing it,
  and managing collaborators stay internal operations; this spec only makes
  their results operable.
- **The chat path and Bot Logs.** End users converse over the messages
  channel, and the logs group has its own contract; neither moves.

## Decisions

Settled here rather than left open:

1. **One operator bar: owner, or collaborator at member level or above.** The
   same bar the internal operator channel applies, applied uniformly to every
   operation in scope — reads, writes, and the connection socket alike. A
   finer per-operation split (say, admin-only approval writes) would be new
   policy this platform has nowhere else; if it is ever wanted, the seam is
   the place to add it.
2. **Public visibility is not authorization.** Internally, holding a public
   bot's device connection is possible for any caller; the public surface
   deliberately does not inherit that. An audience talks to a bot; operators
   operate it.
3. **A refused non-operator gets "no such bot", not "forbidden".** The 403 of
   the explicit-user-id change keeps its single meaning — the request named an
   end user the caller may not act for. Naming another owner's bot without
   being its operator is answered exactly like naming a bot that does not
   exist, because anything else confirms the bot exists.
4. **Operator surfaces stay device-wide; per-caller scoping is rejected.** The
   runtime cannot filter its session collection by user — a per-user filter
   upstream is silently ignored — and building a backend-owned per-caller
   index would recreate the chat product inside the operator console. The
   internal workbench already shows an owner their published bot's runtime
   whole; this surface documents the same exposure instead of half-hiding it.
5. **Defaults preserve today's contract.** Owner defaults to the caller, stage
   defaults to draft; every request that is valid today keeps its exact
   behavior. The expansion is reachable only by naming what it adds.
6. **A dead stage refuses; it never falls back.** Answering a verify request
   from the online runtime would be wrong twice — wrong data, and a hidden
   dependency on release timing.

## Open Questions

1. **The owner parameter's name.** The skills group already ships an owner
   locator named `owner_entity_id`; this spec's plan proposes `owner_id` for
   the engine-runtime groups, which reads with `user_id` and `bot_id` and
   matches the domain. That leaves one concept with two spellings on one
   surface — the exact drift the explicit-user-id change settled for `bot_id`.
   Recommendation: adopt `owner_id` here and reconcile skills to it before its
   pending release; needs the skills owner's ack.
2. **Routines' stage pin.** Kept out of scope above; flagged here so the
   review can pull it in if "other stage service bot" was meant to include
   routine management on published runtimes.
3. **Member versus admin for approval-mode writes.** Decision 1 applies one
   member-level bar everywhere, matching the internal channel. If review wants
   configuration writes held to admin, that is a one-line change at the seam —
   but it should be decided now, not drifted into.
