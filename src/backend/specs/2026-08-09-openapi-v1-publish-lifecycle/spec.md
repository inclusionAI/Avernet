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

This adds a **publish** category to the public surface with four operations:
one that starts a verify release, one that promotes a validated release online,
and two read-only operations that report a release's state and a bot's release
history. The two writes are the only two user-driven advance points the publish
flow has, and each advances from exactly one state; everything else the internal
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
state machine — it publishes those two transitions, each with the single
precondition that makes it legal, plus a way to see where a release is.

**Progress is the third thing, and it is not optional.** Both writes are
asynchronous by design: they win a compare-and-set, enqueue a durable task, and
return. A caller that cannot then ask "where is my release?" has been handed a
fire-and-forget API. The publish flow already answers that question internally
from the record's own status, and that answer is what tells an integrator
whether to keep waiting, to promote, or to stop.

**The states that look like errors are the ones an integrator most needs.** A
release can be superseded by a newer version
(`2026-07-28-online-bot-supersede-cleanup`) or taken offline; a failed release
can be retried into a fresh attempt (`2026-07-23-online-release-retry-fix`).
Internally these are just statuses. If the public surface maps them to errors,
an integrator polling a superseded release sees a 409 and assumes breakage.
They are outcomes, and this spec publishes them as such.

## User Stories

- As a partner integrating the public API, I want to start a verify release for
  my service bot, so that building the bot and shipping it are one workflow on
  one surface.
- As a partner, I want to promote a validated release online once I have
  checked the verify runtime, so that the gate between pre-production and
  production is mine to hold.
- As a partner, I want to poll a release and see how far it has got, so that an
  asynchronous publish is observable rather than a guess.
- As a partner, I want a write that cannot legally run right now to say so
  plainly, so that I never have to guess whether my call did something.
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

- [ ] An operator can start a verify release for a service bot whose newest
      release is a draft, and the call advances that draft.
- [ ] The bot's newest release being a draft is the operation's **precondition**.
      When it is not — the release is already building, already validating,
      already online, superseded, retired, or failed — the call is refused with
      one fixed answer that says the release is not in a state that can be
      started. It never starts a second release, and it never mints one.
- [ ] Two concurrent calls advance the release exactly once. The one that loses
      the compare-and-set is refused by the same precondition answer as any
      other caller who asked at a moment when the release was not a draft.
- [ ] Starting a release is refused for any bot that is not a service bot.
- [ ] A successful call reports that the release has been accepted for
      processing, and returns the release in the state the advance produced.

### Promoting a release online

- [ ] An operator can promote a release that is awaiting promotion, and the
      promotion is the only thing that opens the online publish.
- [ ] The release being at the awaiting-promotion state is the operation's
      **precondition**, on exactly the same terms as starting a release: any
      other state — including a release already promoted or already online — is
      refused with one fixed answer, and the loser of a concurrent promotion is
      refused the same way.
- [ ] A successful call reports that the promotion has been accepted for
      processing, and returns the release in the state the advance produced.

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
- [ ] Reading a release is how a caller learns why a write was refused: the two
      writes disclose only that the precondition failed, and the read says what
      the state actually is.

### Who may publish

- [ ] The bot's owner may start and promote releases.
- [ ] A collaborator holding the platform's publish-capable role may start and
      promote releases; a collaborator below it may not.
- [ ] Reading a release and the release list is held to the same bar the access
      expansion already applies to operating the bot's runtimes, so anyone who
      can watch a runtime can see why it is what it is.
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
      endpoints, its preconditions, its role bar, and the published state set —
      in the same change, including the Chinese mirror.
- [ ] The known limitation below is recorded there too, not only in this
      directory, so the next reader of the surface finds it.

## In Scope

- A new public category with four operations: start a verify release, promote a
  release online, read a release, list a bot's releases.
- The published projection of `ac_bot_publish` (release state).
- The precondition model for the two writes.
- The role adjudication for publishing, and its reuse of the platform's existing
  role policy.
- Tests: the endpoint contract, the role matrix, the precondition behaviour from
  every internal status, cross-tenant and cross-bot isolation, and the release
  payload's published/withheld field sets.
- The surface's documentation, as listed above.

## Out of Scope

- **The operation ledger.** `ac_publish_operation` stays internal. Its rows
  carry provider-side identifiers and free-form internal payloads, and
  publishing even a projection of them would make the pipeline's internal step
  structure an external contract. A release's own state is what this surface
  publishes.
- **Minting a release record.** Neither write creates one. Creating a service
  bot already creates its first draft, so a first release needs nothing extra —
  but taking a *live* bot to a new version does, and that draft is minted on the
  internal surface. See **Known limitation**.
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
  a tenant axis — see Decision 7.
- **Delegation.** Who may *call* is unchanged: a verified end user, whose
  `user_id` must be the caller.

## Known limitation

**Re-publishing a live bot still needs one internal call.** Because neither
write mints a release record, the public surface can take a bot from its
first draft all the way to online, but cannot start version *N+1* of a bot
already online: after a successful publish the newest release is `online`, not
`draft`, and the draft for the next version is created by the internal
upgrade operation.

This is a deliberate consequence of Decision 1, not an oversight. It is
recorded here, and in the surface's handoff documentation, so an integrator
meets it in the docs rather than in a 409. Closing it is a follow-up decision
about whether minting a release belongs on the public surface at all, and if so
whether it is a third operation or a widening of this precondition.

## Decisions

Settled here rather than left open:

1. **This surface advances releases; it does not mint them.** Both writes
   require the release to already be in the one state their transition starts
   from. The alternative — having "start a release" resolve-or-create the draft
   record — would fold two internal operations with their own preconditions
   (`create_first_publish_for_bot`, `upgrade_publish`) into one public call
   whose effect depended on state the caller could not see. Requiring the state
   makes the operation's meaning fixed: it advances the draft that is there. The
   cost is recorded above as a known limitation.
2. **One legal source state per write, and anything else is a fixed refusal.**
   Starting requires `draft`; promoting requires the awaiting-promotion state.
   This is uniform: a caller who is early, a caller who is late, a caller
   repeating themselves, and the loser of a concurrent race are all told the
   same thing — the release is not in a state this operation can act on — and
   they all find out what the state *is* by reading the release. Publishing four
   different flavours of "no" for the same condition would advertise timing
   detail the caller cannot act on differently.
3. **Publishing is held to the platform's publish role, not to the operator
   bar.** The role vocabulary already draws this line: `ADMIN` is defined as
   edit *and publish*, `MEMBER` as edit only. The access expansion's member-level
   operator bar is about reaching a runtime that exists; starting a release
   creates or replaces one and changes what the bot's audience talks to. Reads
   in this category stay at the operator bar, because a member who can already
   watch the verify runtime learns nothing new from being told it is at
   `building`. Owner-only was rejected: it would make the platform's own
   definition of `ADMIN` untrue on the surface partners use.
4. **The ledger stays internal.** An operator following a release wants to know
   where it is, which the release state answers. The ledger answers *how the
   pipeline got there* — which BaaS workflow, on which attempt, against which
   provider bot — and that is the pipeline's internal step structure. Publishing
   it, even projected, would turn a crash-safety mechanism into a contract we
   could not then restructure.
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
7. **No tenant column, and no schema change — isolation comes from the bot
   resolve.** The publish tables carry no `avernet_tenant`, and adding one is
   not this change's job. Every operation here resolves the addressed bot
   through the tenant-guarded, owner-scoped bot read first, and every subsequent
   lookup is keyed on that row's **primary key** — the same argument the access
   expansion made for the engine-runtime groups, for the same reason: `bot_id`
   is not unique across owners.
8. **A release is addressed by its version, not by its record id.** The version
   is per-bot, monotonic, already stored, and already how the platform talks
   about releases. The record id is a global auto-increment whose value would
   leak cross-tenant volume and whose meaning is internal.

## Open Questions

1. **The read bar.** Decision 3 splits the category: publish-role for the two
   writes, operator-level for the two reads. The alternative is one bar for the
   whole category. Recommendation: keep the split — it is the platform's
   existing role semantics, and a single bar would either hide progress from
   members who can already watch the runtime, or let them ship.
2. **Whether the known limitation should be closed in this change.** Adding a
   third write that mints the next version's draft would make the public surface
   complete for re-publish. It is deliberately not here. Recommendation: ship
   the four operations, and let a real integrator's need decide the shape of the
   fifth rather than guessing it now.
3. **The category literal.** `publish` is proposed. It matches the internal
   surface's vocabulary and reads correctly in the address. It must be added to
   the reserved-names list, which makes any existing bot whose id is literally
   `publish` unreachable at the bare bot address — the same one-time cost every
   component literal carries.
