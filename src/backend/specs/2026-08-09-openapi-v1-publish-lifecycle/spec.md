# Public API — Publish Lifecycle for Service Bots

GitHub issue: [#909](https://github.com/inclusionAI/Avernet/issues/909)

## Summary

A service bot exists to be published. The public `/openapi/v1` surface can
create one, drive its draft workspace, and — since the access expansion
(`2026-08-09-openapi-v1-access-expansion`, PR #904) — watch its verify and
online runtimes once they exist. What it cannot do is *make* them exist. Every
transition between stages is internal: starting a verify release, promoting a
validated release online, and even asking how far a release has got all require
the internal `/api/service-bot/publish` surface.

This adds a **publish** category to the public surface: one operation that
starts a verify release, one that promotes a validated release online, and
three read-only operations that report a release's state, the bot's release
history, and the release's operation ledger. The two writes are the only two
user-driven advance points the publish flow has; everything else the internal
surface offers — retry, rollback, offline, restore-draft, scale, restart,
approval — stays internal.

## Motivation

**The surface stops exactly where the product starts.** An integrator can
create a service bot through the public API, upload its skills, write its
identity files, configure its MCP servers, and drive its draft runtime — and
then must leave the public API to publish any of it. PR #904 made the published
runtimes *observable* and named this gap in the same breath, filing it as this
issue. A publish API that can build everything but ship nothing is a partial
integration by construction: every partner ends up with a two-surface workflow,
and the internal surface's contract (Chinese messages, `ApiResponse`, staff-id
auth) is not one we can hand out.

**Only two transitions are actually driven by a person.** The publish flow's
`process()` reads as a large state machine, but it advances on exactly two
statuses: `DRAFT → BUILDING` (which starts the build and the verify release)
and `VALIDATING → ONLINE_PUB` (which promotes the validated release online).
Every other status is a side-effect-free status report, because the durable
task chain owns the rest. So the public surface does not have to reproduce a
state machine — it has to publish two buttons and a progress view. That is what
makes this a category-sized change rather than a project.

**Progress is the third of those, and it is not optional.** Both writes are
asynchronous by design: they win a compare-and-set, enqueue a durable task, and
return. A caller that cannot then ask "where is my release?" has been handed a
fire-and-forget API. The publish flow already answers that question internally
in two shapes — the record's own status, and the crash-safe operation ledger
built by `2026-07-15-publish-service-idempotency` — and the ledger is what turns
"still building" into something an operator can act on: which attempt is
running, at which stage, on whose behalf.

**The states that look like errors are the ones an integrator most needs.** A
release can be superseded by a newer version
(`2026-07-28-online-bot-supersede-cleanup`) or taken offline; a failed release
can be retried into a fresh attempt (`2026-07-23-online-release-retry-fix`).
Internally these are just statuses and ledger rows. If the public surface maps
them to errors, an integrator polling a superseded release sees a 409 and
assumes breakage. They are outcomes, and this spec publishes them as such.

## User Stories

- As a partner integrating the public API, I want to start a verify release for
  my service bot, so that building the bot and shipping it are one workflow on
  one surface.
- As a partner, I want to promote a validated release online once I have
  checked the verify runtime, so that the gate between pre-production and
  production is mine to hold.
- As a partner, I want to poll a release and see how far it has got, so that an
  asynchronous publish is observable rather than a guess.
- As an operator debugging a slow or failed publish, I want to see the release's
  operation ledger — which operation, which stage, which attempt, what state —
  so that "still building" becomes a diagnosis rather than a wait.
- As an operator of a bot that was re-published or taken offline, I want the
  older release to report that it was superseded or retired, so that my poller
  reads an outcome instead of an error.
- As a team member on a shared service bot, I want the surface to hold
  publishing to the same role bar the platform already defines for it, so that
  who may ship is not decided differently by which API they call.
- As a partner, I want a release I may not see to answer exactly like a bot that
  does not exist, so that the surface discloses nothing about other users' or
  tenants' bots.

## Acceptance Criteria

### Starting a verify release

- [ ] An operator can start a verify release for a service bot in one call,
      without first calling the internal surface to mint a release record.
- [ ] Starting a release on a bot that has never published produces its first
      release; starting one on a bot whose latest release is live online
      produces the next version.
- [ ] The call is safe to repeat. A second call while a release is already in
      flight starts nothing, and reports the in-flight release rather than
      failing.
- [ ] Two concurrent calls advance the release exactly once. The loser reports
      the release's state; it does not error, and it does not start a second
      build.
- [ ] A release that cannot be started from its current state — most notably a
      failed release, whose recovery is retry and stays internal — is refused
      with one fixed answer that says so.
- [ ] Starting a release is refused for any bot that is not a service bot.
- [ ] The answer distinguishes "this call started the release" from "a release
      was already running", so a caller knows whether it is the owner of the
      transition.

### Promoting a release online

- [ ] An operator can promote a release that is awaiting promotion, and the
      promotion is the only thing that opens the online publish.
- [ ] Promotion is safe to repeat and safe under concurrency, on the same terms
      as starting a release: one advance, no error for the loser.
- [ ] Promoting a release that is not awaiting promotion is refused with one
      fixed answer; promoting one that is already promoted or already online
      reports its state instead of failing.

### Reading release state

- [ ] An operator can read one release's current state and a human-readable
      message describing it.
- [ ] An operator can list a bot's releases, newest first, paged.
- [ ] Every internal publish status maps to exactly one published state, and the
      published set is closed, documented, and independent of the internal
      spelling.
- [ ] A superseded release and a retired (taken-offline) release each report
      their own state. Neither is an error, and neither is reported as failed.
- [ ] A failed release reports a failed state with a fixed message. Internal
      failure text is not published.
- [ ] Nothing in a release payload exposes device topology, binding ids, build
      artifact locations, provider workflow ids, or the internal record id.

### Reading the operation ledger

- [ ] An operator can read a release's operation ledger: which operations ran
      for that release, in order, paged.
- [ ] Each entry publishes what an operator can act on — the operation, the
      stage, the attempt number, its state, who ran it, and when.
- [ ] Each entry withholds the provider-side identifiers and the free-form
      internal payloads: the BaaS bot identity, the BaaS workflow id, the
      operation's parameters and results, its internal correlation id, its
      internal row id, and its last internal error text.
- [ ] A retried operation is visible as a later attempt rather than as a
      rewritten row, so a caller can see that a retry happened.
- [ ] Only operations belonging to the addressed release are returned.

### Who may publish

- [ ] The bot's owner may start and promote releases.
- [ ] A collaborator holding the platform's publish-capable role may start and
      promote releases; a collaborator below it may not.
- [ ] Reading a release, the release list, and the ledger is held to the same
      bar the access expansion already applies to operating the bot's runtimes,
      so anyone who can watch a runtime can see why it is what it is.
- [ ] Anyone else — including a caller who can reach the bot for other reasons
      — is answered byte-identically to naming a bot that does not exist.
- [ ] Which caller asked, and which owner's bot they asked for, are recoverable
      from the logs at the point of refusal; the response carries neither.

### Addressing and parameters

- [ ] The category is addressed by its own component literal, following the
      surface's addressing rule, and the literal is added to the published
      reserved-names list in the same change.
- [ ] Every operation takes the required `user_id` query parameter the surface
      settled on, and may name the bot's owner with the same optional `owner_id`
      parameter the access expansion introduced.
- [ ] A release is addressed by a caller-meaningful identifier scoped to the
      bot, not by an internal database id.
- [ ] A release identifier belonging to a different bot, a different owner, or a
      different tenant is answered as not found.

### Isolation and internal surface

- [ ] Every release lookup is keyed on the primary key of the bot row the
      caller's access was proven against, never on `bot_id` alone.
- [ ] The internal `/api/service-bot/publish` surface is unchanged: its routes,
      its responses, and its test suite are untouched.
- [ ] No schema change is required.

### Documentation

- [ ] The surface's handoff documentation records the new category — its
      endpoints, its role bar, the published state set, and what the ledger
      publishes and withholds — in the same change, including the Chinese
      mirror.
- [ ] The decisions this spec settles are recorded where the next reader of the
      surface will find them, not only in this directory.

## In Scope

- A new public category with five operations: start a verify release, promote a
  release online, read a release, list a bot's releases, read a release's
  operation ledger.
- The published projections of `ac_bot_publish` (release state) and
  `ac_publish_operation` (the ledger).
- The role adjudication for publishing, and its reuse of the platform's existing
  role policy.
- The composition that makes "start a verify release" a single call for a bot
  with no release record and for a bot whose last release is live.
- Tests: the endpoint contract, the role matrix, the state-machine behaviour
  from every internal status, cross-tenant and cross-bot isolation, and the
  ledger's published/withheld field sets.
- The surface's documentation, as listed above.

## Out of Scope

- **Retry, rollback, offline, and restore-draft.** Each is a recovery or
  teardown decision with its own preconditions and its own blast radius; each
  deserves its own decision about what an external operator may do, and none is
  an advance point in the publish flow. A failed release is therefore a terminal
  state on this surface, and this spec says so plainly rather than half-exposing
  a recovery path.
- **Scale, restart, and bot-type changes.** Runtime capacity and lifecycle
  operations on a published bot, not publish transitions. Restart already has a
  public form on the bots category for the bot record.
- **Human approval.** The approval path is delegated to BaaS server-side
  auto-approval and has no caller-driven step left to publish.
- **The eval stage.** It has no long-lived runtime, and its publish/teardown
  operations are an internal evaluation mechanism.
- **Creating the service bot, or making it public.** Bot creation is the bots
  category; visibility and collaborator management are
  [#910](https://github.com/inclusionAI/Avernet/issues/910).
- **A published failure taxonomy.** A failed release reports that it failed;
  turning the internal error text into a stable, enumerated public reason set is
  its own contract and its own change.
- **Any schema change.** Neither publish table gains a column, and neither gains
  a tenant axis — see Decision 8.
- **Delegation.** Who may *call* is unchanged: a verified end user, whose
  `user_id` must be the caller.

## Decisions

Settled here rather than left open:

1. **Starting a release is one call, and it mints the release record when
   there is none.** The issue names the `DRAFT → BUILDING` advance as the
   scope, but a bot that has never published has no draft to advance, and a bot
   whose last release is live online must first be upgraded to a new draft.
   Making the caller mint the record separately would mean either publishing a
   second write whose only purpose is bookkeeping, or leaving the public surface
   unable to start a first release at all. The public concept is *a release*;
   the draft record is how the platform stores one. So one operation resolves or
   creates the release record and advances it, and the underlying create is
   already idempotent, so repeating the call does not fork a version.
2. **Publishing is held to the platform's publish role, not to the operator
   bar.** The role vocabulary already draws this line: `ADMIN` is defined as
   edit *and publish*, `MEMBER` as edit only. The access expansion's member-level
   operator bar is about reaching a runtime that exists; starting a release
   creates or replaces one and changes what the bot's audience talks to. Reads
   in this category stay at the operator bar, because a member who can already
   watch the verify runtime learns nothing new from being told it is at
   `building`. Owner-only was rejected: it would make the platform's own
   definition of `ADMIN` untrue on the surface partners use.
3. **A lost race is not an error.** Both writes advance under an optimistic
   compare-and-set, and the loser of a double-submit is a caller who asked for
   a state the system is already in. It gets the release's current state, with
   the response distinguishing "you started this" from "this was already
   running". Publishing a 409 there would make correct client retry logic —
   the thing an asynchronous API most needs — read as failure.
4. **A release that cannot advance is refused with one fixed answer, and retry
   stays internal.** Starting a release from a failed record, or promoting a
   record that is not awaiting promotion, is a genuine precondition failure and
   answers 409 with a fixed message. It does not silently retry, and it does not
   describe what recovery would require, because recovery is not on this
   surface.
5. **Superseded and retired are published states, not errors.** They are the
   normal end of a release's life under re-publish and offline. A poller that
   was following a release must be able to see that a newer version took over,
   or that the service was taken down, and continue — not receive an error for a
   record that behaved exactly as designed.
6. **Internal failure text is not published.** A failed release publishes the
   fixed message for its state. The internal `error_message` is raw operational
   text — exception reprs, provider ids, internal-language strings — and the
   surface's standing rule is that failure messages are fixed and never
   `str(exc)`. The diagnosis stays in the logs, where the surface already
   records it.
7. **The ledger publishes the timeline, not the payloads.** An operator needs to
   know which operation ran, at which stage, on which attempt, in what state,
   by whom, and when — that is what makes a stalled publish diagnosable. The
   provider-side identifiers (the BaaS bot, the BaaS workflow id) and the
   free-form `params` / `result` / `last_error` blobs are internal plumbing that
   would become an unversioned contract the moment they were published; they are
   withheld. Every kind of operation is shown, including ones this surface
   cannot trigger: hiding rows would make the attempt numbering and the
   timeline lie.
8. **No tenant column, and no schema change — isolation comes from the bot
   resolve.** The publish tables carry no `avernet_tenant`, and adding one is
   not this change's job. Every operation here resolves the addressed bot
   through the tenant-guarded, owner-scoped bot read first, and every subsequent
   lookup is keyed on that row's **primary key** — the same argument the access
   expansion made for the engine-runtime groups, for the same reason: `bot_id`
   is not unique across owners.
9. **A release is addressed by its version, not by its record id.** The version
   is per-bot, monotonic, already stored, and already how the platform talks
   about releases. The record id is a global auto-increment whose value would
   leak cross-tenant volume and whose meaning is internal.

## Open Questions

1. **The read bar.** Decision 2 splits the category: publish-role for the two
   writes, operator-level for the three reads. The alternative is one bar for
   the whole category. Recommendation: keep the split — it is the platform's
   existing role semantics, and a single bar would either hide progress from
   members who can already watch the runtime, or let them ship.
2. **Whether a superseded release should name its successor.** The record
   carries the link internally. Publishing it on a list would cost a lookup per
   row; publishing it only on the detail read would make the field's presence
   depend on which endpoint you called. Recommendation: omit for now, and add it
   as a whole-payload decision if integrators ask.
3. **The category literal.** `publish` is proposed. It matches the internal
   surface's vocabulary and reads correctly in the address. It must be added to
   the reserved-names list, which makes any existing bot whose id is literally
   `publish` unreachable at the bare bot address — the same one-time cost every
   component literal carries.
