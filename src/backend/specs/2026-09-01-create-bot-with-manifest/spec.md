# Creating a Bot With Its Configuration — the Async Create API (W13)

## Summary

A public, asynchronous API that creates a bot from a **configuration manifest**
plus the ordinary creation attributes, so the bot's **first** container comes up
already carrying its configuration. One `POST` starts it; a durable background
job carries it to completion; one `GET` reports where it stands. The manifest the
caller submitted once is the manifest that gets applied, and the poll asks for
nothing but the bot's id.

This is the item that delivers the business ask. W4 made applying a stored
manifest possible on a bot that already exists; every other work item feeds that.
None of them can put configuration into a bot's *first* boot, because until the
bot exists there is no `/bots/{bot_id}/config-manifest` to write to. That gap is
what this closes.

Work item **W13** · issue #1696 · `work-items.zh-CN.md` §2.11, §2.12, §5.

> **Revision 5** — all open questions closed. Applying runs as a **task-queue
> task on every path**, replacing the daemon thread W4 shipped; the creation job
> waits only for the pre-container phase; submission never creates a bot inline;
> the poll is a pure read; the terminal states name **which** thing failed; and
> the endpoint is **ARCA-only** — teclaw creation is W8's. Revision history at the
> end of `plan.md`.

## Motivation

Design §3.1 promises that "the first configuration a bot receives already
contains the manifest's results". With only `PUT`, that promise is unreachable
for a genuinely new bot:

1. `POST /openapi/v1/bots` allocates a `bot_id` and applies for a Passport.
2. The user clicks the authorization link.
3. The poll sees `ISSUED` and *only then* writes the `ac_bots` row and
   provisions a container.

The earliest a caller can `PUT` a manifest is after step 3 — the container is
already up, already unconfigured, and the start command has already been composed
without the manifest's `script`. Every bot's first boot is empty, and the caller
has to notice, `PUT`, apply, and in the `script` case restart to get the boot they
asked for in the first place.

Creating *with* a manifest removes the ordering problem rather than working
around it: the manifest is in hand before the bot record is written, so apply runs
inside creation. It runs in **two phases**, and the names are used throughout this
document:

- **Phase A — the pre-container phase.** `script` alone. It materialises as a row
  in `ac_bot_startup_script` and needs no container, and
  `BaasService._build_create_bot_payload` reads that row while composing the start
  command. So phase A must land *before* the start command is composed, or the
  first boot cannot carry the script at all.
- **Phase B — the post-container phase.** `identity`, `resources`, `skills`,
  `mcp` — everything that resolves a device and therefore cannot run until the
  container is up.

Both are W4's, not new here: `ApplyPhase.PRE_CONTAINER` / `ON_CONTAINER` exist
already, and `start_apply` already takes the phase set. What W13 adds is calling
them at the two moments creation makes available.

**One fixed constraint, not a design choice.** Passport authorization requires a
human click, and that is AgentPass's limitation, not ours. "Create a bot" is
therefore inherently one-at-a-time and human-in-the-loop; nothing here may assume
otherwise, and "create N bots from one manifest" is not a thing this API can ever
grow into (§2.11). Applying *one* manifest to *many existing* bots needs no
authorization at all and remains open.

## User Stories

**As a developer standing up a new bot**, I submit its manifest with its creation
attributes, complete one authorization, and poll one endpoint with nothing but the
bot's id until it says `READY` — at which point the bot exists, its container is
up, and its MCP servers, skills, identity files and startup script are what my
manifest declared. I never learn the platform's internal sequencing, and I never
re-send anything I already sent.

**As the same developer whose manifest has a typo**, I am told at submission time
— before I am sent to Passport, before a bot exists, before anything external is
spent. The `422` names every violation at once.

**As a developer who gets distracted and never clicks the link**, the creation
expires on its own within a bounded, configurable window, the poll tells me so,
and nothing is left behind.

**As a developer whose manifest was valid but whose apply partly failed**, the
poll reaches a terminal state that names which entries did not land — and shows me
the bot, running, so there is no doubt it was created.

**As an operator**, an abandoned creation leaves no bot, no container, no quota
consumed, and no stored manifest — and a pod restarting mid-creation does not
strand it.

## Acceptance Criteria

### Applying is durable work

- [ ] **Applying a manifest runs as a task on the existing task queue, on every
      path** — the pre-container phase, the post-container phase, and the explicit
      apply on an already-running bot. The daemon thread W4 spawns is replaced.
- [ ] **An apply survives the process that started it.** A pod that dies
      mid-apply does not lose the work: the task's lease expires and another
      worker finishes it. This is what makes a stored apply status correct without
      anyone reading it — the apply *completes*, rather than being retroactively
      reported as failed.
- [ ] **Re-running an apply is safe, and the reason is convergence, not
      configuration.** The queue is at-least-once *structurally* — a crashed
      worker's task is re-claimed whether or not the handler ever asks for a retry
      — so safety must come from apply itself: re-applying an unchanged document
      performs no writes (W4's convergence criterion), and the apply lock
      serialises attempts. Any statement that re-runs are safe "because retry is
      off" is wrong and must not appear in the code or the docs.
- [ ] **The public contract of the existing apply route does not change.**
      `POST …/config-manifest/apply` still answers `202` with an `apply_id`,
      still refuses a concurrent apply, still surfaces a validation failure
      synchronously, and its poll route still returns the same report. Only what
      executes the work changes.
- [ ] **One task type**, not one per case. The three cases differ only in
      arguments the engine does not branch on; the apply record's `trigger`
      column already distinguishes them for anyone querying.
- [ ] The task is enqueued so the worker picks it up **immediately** rather than
      waiting out its idle interval.
- [ ] **Operational preconditions are stated, not assumed.** The worker runs only
      where it is enabled and its table is provisioned. With applying — and
      therefore bot creation — riding the queue, a deployment with the worker off
      does not merely run slower: creations never complete.
      `task_queue_worker.enabled=true` is confirmed to hold in the target
      deployments; it is written down anyway, where an operator will find it,
      because it is now load-bearing.

### The create operation

- [ ] A public endpoint accepts a manifest document **plus** the ordinary
      creation attributes (engine, cluster, name, description, bot type, space,
      engine properties) and returns immediately with the allocated `bot_id` and
      the authorization handles (`iframe_url` / `redirect_url`).
- [ ] **The submit response carries no state.** The state vocabulary belongs to
      the poll and appears nowhere else, so no terminal value can ever be returned
      by submission. A caller that has just submitted is, by construction,
      awaiting authorization.
- [ ] **Only ARCA-family engines are accepted.** A teclaw creation is refused, by
      the same rule as an unbacked construct — never accept what this path cannot
      deliver. teclaw configures a bot by composing its artifact at provision
      time, which is a different mechanism from the pre/post-container split here
      and belongs to W8, whose scope names it ("teclaw 在第一份 artifact 组装之前")
      and whose first acceptance criterion is the first-artifact guarantee. The
      refusal says so and names W8.
- [ ] The manifest is **validated before Passport is applied for**, in the same
      preflight as quota, name and engine checks. A caller with an invalid
      manifest is never sent to authorize, and no Passport application is spent
      on a request that cannot succeed.
- [ ] Validation answers from the **request's** engine type and bot type, not
      from a bot record — there is no record yet. It uses the resolver W1 already
      exposes for exactly this (`resolve_capabilities`), so this path and
      `PUT`/`GET …/capabilities` cannot disagree about what a document may
      contain.
- [ ] A manifest declaring a construct with **no materialiser in this build** is
      refused **here**, at submission. This is stricter than `PUT`, deliberately:
      `PUT` may accept a category that sits inert, but accepting one here means
      taking the user through authorization, creating the bot, and *then* failing
      the apply — the worst possible moment to discover it. The refusal names the
      construct and says what would apply it.
- [ ] That gate is derived from **what is actually registered**, not from a list
      written by hand. When W5 registers `skills` and `identity`, or W6
      `resources`, this endpoint accepts them with no edit here.
- [ ] The manifest is persisted **before Passport**, keyed by the allocated
      `bot_id`. No schema change: the existing
      `(avernet_tenant, sha256(env, entity_id, bot_id))` key has all three parts
      in hand at that point.
- [ ] The storage key's `entity_id` is resolved by the **same rule** `create_bot`
      will use when it writes the record, so the document stored before the bot
      exists is found afterwards.
- [ ] **The creation attributes are materialised at submission**, so nothing about
      the creation has to be supplied again.
- [ ] **Submission never creates the bot.** This endpoint always goes through
      user consent, so it applies for the Passport and stops; the job owns
      creation on every path. If AgentPass ever returns a token immediately, the
      job's first run simply sees `ISSUED` and proceeds — no special case, and the
      phase-A-before-creation ordering holds regardless.

### The job that carries the creation

- [ ] Submission enqueues **one durable job**, and that job — not the caller's
      polling — drives the creation: it waits for authorization, runs phase A,
      creates the bot, waits for the container, starts phase B, and finishes.
- [ ] A caller who stops polling still gets a fully configured bot. Polling
      observes; it never drives.
- [ ] **The job waits for phase A, and does not wait for phase B.** Phase A has a
      downstream dependency — creation must not begin until the script row exists.
      Phase B has none: nothing in the platform is blocked on it, exactly as
      nothing is blocked on an apply against a running bot. The job starts phase B
      and finishes; phase B is then observed the same way any other apply is.
- [ ] The job has a **configurable wall-clock deadline**, defaulting to ten
      minutes, measured from submission. Past it the creation is terminal and
      reported as expired.
- [ ] The job is **re-entrant**: every step asks "is this already done?" against
      durable state, because the queue guarantees a single claimer but
      at-least-once invocation.
- [ ] The job is enqueued with an idempotency key derived from the bot id, so a
      resubmission cannot start a second creation for the same bot. This is the
      **first adoption** of that mechanism. The queue requires adoption to ship
      strictly later than the mechanism itself, which is satisfied: it is
      implemented and released, and has simply had no call site until now.

### The poll

- [ ] The poll is addressed by **`bot_id` alone**. `entity_id` is resolved
      server-side from the authenticated caller, exactly as it is at submission;
      it is never a request parameter.
- [ ] The poll **never accepts the manifest, and never accepts creation
      attributes.** There is nothing for a caller to re-send.
- [ ] **The poll is a pure read.** It reads durable rows — the job record, the bot
      record, the apply records — and maps them to a state. It calls no external
      service (it never queries AgentPass; the job does that), starts no work, and
      writes nothing. If it needs the authorization handles, they come from
      durable state or are not returned at all.
- [ ] It reports:

      ```text
      AWAITING_AUTHORIZATION   waiting for the user to open the Passport link
              │
              ├──► AUTHORIZATION_REJECTED   terminal — the user declined
              ├──► AUTHORIZATION_EXPIRED    terminal — Passport expired, or the
              │                             job's deadline elapsed unclicked
              ▼
      CREATING                 authorized; the bot record is written, the
              │                container is being provisioned
              ├──► CREATE_FAILED   terminal — the bot could not be created or
              │                    never came up. Nothing to do with the manifest
              ▼
      APPLYING                 the post-container apply is running
              ├──► READY         terminal — the bot is up and the manifest landed
              └──► APPLY_FAILED  terminal — the bot is up; part of the manifest
                                 did not land, and the report says which
      ```

- [ ] **The three failure modes are distinguishable without reading prose.** An
      invalid manifest is a `422` at submission with no bot and no state at all; a
      bot that could not be created or never came up is `CREATE_FAILED`; a bot
      that is running with an incomplete manifest is `APPLY_FAILED`. A caller
      never has to parse a message to tell "you have no bot" from "you have a bot
      that is missing some configuration".

- [ ] An apply result of `PARTIAL` reports **`APPLY_FAILED`**, not `READY`, per
      the decision recorded on #1696: under §3.2's category overwrite a category is
      written all-or-nothing, so a declared category that was not written whole is
      not a success. **The apply record itself still says `PARTIAL`** — the mapping
      is this poll's summary, not a rewrite of the apply's status, and the `PUT` +
      apply path on a running bot is unaffected.
- [ ] `APPLY_FAILED` states in its own name that the bot exists — that is what the
      earlier `FAILED` spelling could not do, and the objection it invited ("people
      will think the bot was never created") is answered by the vocabulary rather
      than by a note in the payload. The response **also carries the bot**, and the
      bot record is not touched by a failing apply (§2.7).
- [ ] Both terminal states carry a report complete enough to answer "did my
      manifest take effect?" without a second call — **including the entries from
      both phases**.
- [ ] A creation that never got a bot leaves the poll answering its terminal
      authorization state, not a 404.

### The two phases, in the creation sequence

- [ ] **Phase A runs before the bot record is created at all.** It needs nothing
      from the bot: the startup-script row is keyed by `(entity_id, bot_id)`,
      both known at submission, and the only placeholders the schema admits
      (`BOT_ENGINE_TYPE`, `BOT_ENV`, `BOT_TENANT`, `BOT_ARCH` — there is
      deliberately no `BOT_ID` or `BOT_NAME`) all resolve from the creation
      request. Ordering it ahead of creation makes "the row exists before the
      start command is composed" true **by construction**.
- [ ] **Applying behaves identically in all three cases.** The lock, the
      re-validation, the record, the orchestrator's walk of `APPLY_ORDER`, the
      per-entry outcomes and the status derivation are one code path; only the
      arguments differ, and nothing in the engine branches on them.
- [ ] A **failing phase A does not fail creation.** The bot is still created and
      provisioned; the failure is recorded and surfaces in the poll's terminal
      report (§2.7).
- [ ] A creation that ends without a bot takes the **startup-script row** with it
      as well as the manifest — phase A can write that row before anyone knows the
      creation will complete.
- [ ] `script`'s materialisation stays exactly what it is today: apply writes the
      `ac_bot_startup_script` row and stops. **No new execution machinery is built
      here** — the platform already composes that row into the start command
      (#926's mechanism, which W4's materialiser writes into). W13 only guarantees
      *when* the row is written relative to that composition.
- [ ] **Iteration 1's rule is stated where a caller will read it:** a manifest's
      `script` must not depend on anything else the same manifest declares,
      because on a first boot the script runs before any other category has been
      delivered (§2.12). #1508 removes the restriction in iteration 2.

### Tenancy

- [ ] The tenant reaches the code that actually performs the apply. Both the
      creation job and the apply task run on a worker with **no request behind
      them**, so the tenant is carried in each payload and re-established for the
      duration of each handler.
- [ ] This is pinned by tests, not by memory, and the failure mode is why:
      `get_current_avernet_tenant()` is a **total function that returns the
      default tenant** outside a request rather than raising. A handler that
      forgets the payload's tenant therefore does not crash — it quietly
      substitutes the wrong `${BOT_TENANT}` and reads and writes the manifest
      tables under the wrong tenant. That is an isolation failure that no
      exception announces.

### Cleaning up after itself

- [ ] When a creation ends **without a bot** — declined or expired — the job
      deletes the manifest and any startup-script row phase A wrote. The rows this
      endpoint can create are bounded by their own jobs.
- [ ] No feature switch. The endpoint ships enabled; the unbounded-orphan-rows
      objection that would have required one is answered by the deadline above.

### Nothing else moves

- [ ] `POST /openapi/v1/bots`, the auth-status poll and every existing creation
      path behave **exactly** as they do today. Their tests pass unedited.
- [ ] The `PUT` path is unchanged: a bot created by any other means can still be
      given a manifest afterwards, and it still takes effect immediately, with no
      restart (§2.6).
- [ ] Every existing config-manifest, apply and startup-script test passes
      **unedited**. Moving apply onto the queue changes what executes the work,
      not what the work does or what any caller sees.
- [ ] Creation with no manifest, or with a manifest declaring nothing, applies
      nothing and reports `READY`.

## Decisions

**D-1 — A dedicated endpoint pair, not an extra field on `POST /bots`.**
Adding an optional manifest to the existing create would change what its existing
answers *mean*: today a `201` says "created and done", and the auth-status poll's
`ISSUED` is terminal. With a manifest neither is true. The manifest flow gets its
own pair with its own state machine, reusing the implementation beneath it.

**D-2 — The endpoint accepts a narrower vocabulary than `PUT`, and derives it.**
`PUT` may accept a category with no materialiser: it sits inert, the capabilities
endpoint says so, and nothing has been created. On the creation path that same
acceptance costs a Passport application, a user's click and a live bot before the
failure appears. Deriving the gate from the registry means W5 and W6 widen this
endpoint by landing.

**D-3 — A durable job carries the creation; the poll only observes.**
A poll-driven creation stalls forever if the caller walks away, which is what a
durable job is for. The queue already has the shape: a handler that reschedules
itself until an external status goes terminal, bounded by a wall-clock deadline,
with `TIMED_OUT` distinct from failure.

**D-4 — The poll asks for a bot id and nothing else, and only reads.**
The creation attributes are materialised into the job at submission, so the server
already has them. That retires the question of what happens when an echo
disagrees with what was submitted — there is no echo. The poll also makes no
external call: the job polls AgentPass, and the poll reads what durable state
says.

**D-5 — A failed phase A does not abort creation.** Putting a manifest-layer
failure in charge of the bot record would contradict §2.7 and leave a half-created
bot to compensate for.

**D-6 — The terminal states name what failed.**
`PARTIAL` still reports a failure rather than `READY` — a standing decision
(#1696, 2026-08-30, superseding an earlier revision that rested on
`on_fetch_failure: skip`, which §3.2 removed). What changed is the spelling: three
distinct outcomes a caller must be able to tell apart — an invalid manifest (a
`422`, no bot), a bot that could not be created (`CREATE_FAILED`), and a running
bot with an incomplete manifest (`APPLY_FAILED`) — now have three distinct
answers. A single `FAILED` covering the last two was the real source of the "did I
get a bot or not?" ambiguity. The apply record keeps saying `PARTIAL`; only this
poll's summary maps it.

**D-7 — No feature switch.** The deadline supplies the cap the switch was
standing in for, and the job deletes what it wrote when a creation ends without a
bot. #1698's general sweeper is still worth having but is no longer this
endpoint's gate.

**D-8 — ARCA only; teclaw creation is W8's.**
*Reversed in rev 5.* Earlier revisions accepted teclaw with a narrower guarantee.
That was the wrong shape: this item's entire pre/post-container split exists
because `BaasService._build_create_bot_payload` reads the startup-script row while
composing a start command, and teclaw has no analogue — it configures a bot by
**composing its artifact at provision time**. Delivering a teclaw manifest
post-container would be both a worse fit and a different mechanism from the one
that lands once W8 does the artifact work, so a teclaw bot created here would get
semantics that change under it.

The work items already assign it: W8's scope covers creation-time apply for both
families and names teclaw's as "before the first artifact is assembled", its first
acceptance criterion is the first-artifact guarantee, and its scale note lists
`TeclawProvisionService` among the three things it touches. (W8's second criterion
reads as though W13 covered creation for both engines; against its own scope line
and first criterion, the reading above is the coherent one, and stating it here
retires the ambiguity.)

So the endpoint refuses a teclaw engine, by the same rule that refuses an unbacked
construct. `script` was already unsupported on teclaw anyway, so nothing is lost
that this path could have delivered.

**D-9 — Applying becomes a task, on every path.**
`start_apply` runs its work on a daemon thread today. The task queue's own README
names that as the pattern it exists to replace: it "loses work on restart and
double-runs across pods". Three things follow, and the third is why this is in
W13's scope rather than a nice-to-have:

- A pod restart no longer loses an apply.
- A stranded `RUNNING` record stops being a thing to sweep or reinterpret — the
  work finishes instead.
- **Creation depends on an apply completing.** Phase A must land before the bot is
  created; a thread that dies takes that guarantee with it, and the bot would boot
  without its script. A durable task is what makes the ordering survive a restart,
  not just a happy path.

The running-bot path gets the same treatment, because a second execution
mechanism for the same operation is exactly the divergence W4's single code path
exists to prevent.

**D-10 — One task type for all three cases.**
The three differ only in `phases`, the `trigger` label, and whether a previous
report is carried — none of which the orchestrator branches on. Three task types
would be three registry keys for one behaviour, and the apply record's `trigger`
column already carries the distinction for anyone querying. `wake_on_enqueue` is
per-type, but all three want immediacy, so that does not argue for splitting
either.

**D-11 — Re-runs are safe by convergence, never by "retry is off".**
Worth its own decision because the wrong reason is load-bearing: at-least-once is
structural, so a handler that never asks for a retry is still re-invoked when its
lease expires. Safety comes from apply's own convergence and its lock. Writing it
down the other way would mislead whoever adds a materialiser that is not
convergent.

## In Scope

- Moving apply execution onto the task queue, for all three cases, with the
  existing apply API contract unchanged.
- The create endpoint and the poll endpoint, with the state machine above.
- The creation job: its handler, its payload (attributes + tenant), its
  configurable deadline, its idempotency key, and its re-entrancy.
- Persisting the manifest at submission, and deleting it — with any phase-A
  startup-script row — when the creation ends without a bot.
- The materialiser-backed acceptance gate, derived from the registry.
- Phase A before creation; phase B once the container is up; both phases' results
  in the terminal report.
- Tenant propagation through both payloads, with tests.
- Documentation, including the operational precondition that the worker must be
  enabled.

## Out of Scope

- **Creation idempotency at the `bot_id` level (#1697).** `generate_bot_id` mints
  an id platform-side with no idempotency key, so a retried *submission* makes a
  second bot. The job's key prevents a second job for the same bot; it cannot
  prevent a second id being minted.
- **#1698's general orphan sweeper.** Still worth having; no longer a gate.
- **W8's other apply points** — republish, rebuild-restart, `PUT` taking effect
  without a restart, and the legacy `/startup-script` write-through.
- **The teclaw first-artifact guarantee** (W8), per D-8.
- **Batch creation.** Structurally impossible: one authorization click per bot.
- Any change to `POST /openapi/v1/bots` or the existing auth-status poll.

## Open Questions

**None.** All four closed in review on 2026-09-01:

1. **Failure vocabulary** — the requirement is that an invalid manifest, a bot
   that could not be created, and a running bot with an incomplete manifest are
   all distinguishable. Answered by `422` / `CREATE_FAILED` / `APPLY_FAILED`
   (D-6).
2. **`AUTHORIZATION_EXPIRED`** stays — comprehensiveness preferred — and the
   state vocabulary is confined to the **poll**; the submit response carries no
   state at all, so no terminal value can appear there.
3. **teclaw** is out (D-8); the endpoint is ARCA-only and teclaw creation is W8's.
4. **Preconditions confirmed by the owner:** `task_queue_worker.enabled=true`
   holds in the target deployments, and enqueue idempotency is implemented and
   released — this is simply its first call site. The key stays in the design.

## Follow-ups

- #1697 — creation idempotency.
- #1698 — the general orphan-manifest sweeper.
- #1508 — deliver every category before the container starts, which removes both
  the `APPLYING` window and iteration 1's `script`-dependency rule.
