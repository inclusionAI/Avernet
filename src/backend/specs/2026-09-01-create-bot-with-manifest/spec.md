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

> **Revision 2** — reworked after review on PR #1791. The creation is now carried
> by a **task-queue job with a wall-clock deadline** rather than by the caller's
> polling and a device-activation listener; the poll takes only a `bot_id`; the
> feature switch is gone, replaced by the job cleaning up after itself. What
> changed and why is recorded per decision below.

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
consumed, and no stored manifest.

## Acceptance Criteria

### The create operation

- [ ] A public endpoint accepts a manifest document **plus** the ordinary
      creation attributes (engine, cluster, name, description, bot type, space,
      engine properties) and returns immediately with the allocated `bot_id`, the
      authorization handles (`iframe_url` / `redirect_url`), and the state the
      creation starts in — so a caller knows where it stands without a second
      call. Everything after that comes from the poll.
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
      the apply — the worst possible moment to discover it, because the bot now
      exists. The refusal names the construct and says what would apply it.
- [ ] That gate is derived from **what is actually registered**, not from a list
      written by hand. When W5 registers `skills` and `identity`, or W6
      `resources`, this endpoint accepts them with no edit here.
- [ ] The manifest is persisted **before Passport**, keyed by the allocated
      `bot_id`. No schema change: the existing
      `(avernet_tenant, sha256(env, entity_id, bot_id))` key has all three parts
      in hand at that point.
- [ ] The storage key's `entity_id` is resolved by the **same rule** `create_bot`
      will use when it writes the record, so the document stored before the bot
      exists is found afterwards. A second, drifting derivation of that value is
      the defect this criterion exists to prevent.
- [ ] **The creation attributes are materialised at submission**, alongside the
      manifest, so nothing about the creation has to be supplied again.

### The job that carries the creation

- [ ] Submission enqueues **one durable job** on the existing task queue, and that
      job — not the caller's polling — drives the creation to a terminal state:
      it waits for authorization, creates the bot, runs phase A before the start
      command is composed, waits for the container, runs phase B, and finishes.
- [ ] A caller who stops polling still gets a fully configured bot. Polling
      observes; it never drives.
- [ ] The job has a **configurable wall-clock deadline**, defaulting to ten
      minutes, measured from submission. Past it the creation is terminal and
      reported as expired — the bounded answer to "the user never clicked".
- [ ] The job is **safe to re-run**: the queue guarantees a single claimer but
      at-least-once invocation across crashes, so every step checks what is
      already done rather than assuming it is the first attempt. Creating twice,
      applying twice, or minting a second Passport application is a defect.
- [ ] The job is enqueued with an idempotency key derived from the bot id, so a
      resubmission cannot start a second creation for the same bot.

### The poll

- [ ] The poll is addressed by **`bot_id` alone**. `entity_id` is resolved
      server-side from the authenticated caller, exactly as it is at submission
      and everywhere else in this group; it is never a request parameter.
- [ ] The poll **never accepts the manifest, and never accepts creation
      attributes.** There is nothing for a caller to re-send, so there is no way
      for the applied document or the created bot to differ from what was
      validated and submitted.
- [ ] It reports:

      ```text
      AWAITING_AUTHORIZATION   waiting for the user to open the Passport link
              │                (the response carries iframe_url / redirect_url)
              ├──► AUTHORIZATION_REJECTED   terminal — the user declined
              ├──► AUTHORIZATION_EXPIRED    terminal — Passport expired, or the
              │                             job's deadline elapsed unclicked
              ▼
      CREATING                 authorized; the bot record is written, the
              │                container is being provisioned
              ▼
      APPLYING                 the manifest apply is running
              ├──► READY       terminal — success; carries the apply report
              └──► FAILED      terminal — carries which entries did not land
      ```

- [ ] `AUTHORIZATION_EXPIRED` is **new in this revision** and exists because the
      deadline does. "Never clicked" and "declined" are different things to a
      caller deciding whether to retry, and folding an expiry into
      `AUTHORIZATION_REJECTED` would report a decision the user never made.
- [ ] An apply result of `PARTIAL` reports **`FAILED`**, per the decision recorded
      on #1696 and in work-items §5: under §3.2's category overwrite a category is
      written all-or-nothing, so a declared category that was not written whole is
      not a success. **The apply record itself still says `PARTIAL`** — the
      mapping is this poll's summary, not a rewrite of the apply's own status, and
      the `PUT` + apply path on a running bot is unaffected.
- [ ] Because `FAILED` here must never read as "no bot was created", the terminal
      response **carries the bot** alongside the report. The bot record is not
      touched by a failing apply (§2.7) — no deletion, no status change, no
      deactivation — and the response makes that visible rather than leaving the
      caller to infer it.
- [ ] Both terminal states carry a report complete enough to answer "did my
      manifest take effect?" without a second call — **including the entries from
      both phases**. A terminal report that names only the post-container
      categories, silently dropping `script`, does not satisfy this.
- [ ] `APPLYING` makes D4's interim cost **observable**. Post-boot delivery
      (§3.4) leaves a window where the bot is ACTIVE but not yet configured; a
      caller waiting for `READY` never observes it.
- [ ] A creation that never got a bot leaves the poll answering its terminal
      authorization state, not a 404.

### The two phases, in the creation sequence

- [ ] **Phase A** runs after the bot record exists and **before the start command
      is composed**, and creation does not proceed to provisioning until it has
      finished. A row written after the payload is composed cannot reach the first
      boot, so this ordering is the item, not an optimisation.
- [ ] **Phase B** runs once the container is up, and the job is what notices.
- [ ] A **failing phase A does not fail creation.** The bot is still created and
      provisioned; the failure is recorded and surfaces in the poll's terminal
      report. §2.7's boundary holds: apply records delivery, and a manifest-layer
      failure never mutates the bot record.
- [ ] `script`'s materialisation stays exactly what it is today: apply writes the
      `ac_bot_startup_script` row and stops. **No new execution machinery is
      built here** — the platform already composes that row into the start command
      (#926's mechanism, which W4's materialiser writes into). W13 only guarantees
      *when* the row is written relative to that composition.
- [ ] **Iteration 1's rule is stated where a caller will read it:** a manifest's
      `script` must not depend on anything else the same manifest declares,
      because on a first boot the script runs before any other category has been
      delivered (§2.12). #1508 removes the restriction in iteration 2.

### Tenancy

- [ ] The tenant reaches the code that actually performs the apply. The job runs
      on a worker with **no request behind it**, so the tenant is carried in the
      job's payload and re-established for the duration of the handler.
- [ ] This is pinned by tests, not by memory, and the failure mode is why:
      `get_current_avernet_tenant()` is a **total function that returns the
      default tenant** outside a request rather than raising. A handler that
      forgets the payload's tenant therefore does not crash — it quietly
      substitutes the wrong `${BOT_TENANT}` and reads and writes the manifest
      tables under the wrong tenant. That is an isolation failure that no
      exception announces.

### Cleaning up after itself

- [ ] When a creation ends **without a bot** — declined or expired — the job
      deletes the manifest it stored at submission. The rows this endpoint can
      create are bounded by their own jobs: each one has a deadline, and reaching
      it is what triggers the delete.
- [ ] No feature switch. The endpoint ships enabled; the unbounded-orphan-rows
      objection that would have required one is answered by the deadline above
      rather than deferred to a follow-up.

### Nothing else moves

- [ ] `POST /openapi/v1/bots`, the auth-status poll and every existing creation
      path behave **exactly** as they do today. Their tests pass unedited.
- [ ] The `PUT` path is unchanged: a bot created by any other means can still be
      given a manifest afterwards, and it still takes effect immediately, with no
      restart, by the same path as any other existing bot (§2.6).
- [ ] With no manifest supplied, the new endpoint creates a bot that is
      byte-for-byte the bot the existing endpoint creates — same preflight, same
      engine/cluster rules, same space resolution, same Passport flow.
- [ ] Creation with no manifest, or with a manifest declaring nothing, applies
      nothing and reports `READY`. An empty declaration is not a failure.

## Decisions

**D-1 — A dedicated endpoint pair, not an extra field on `POST /bots`.**
Adding an optional manifest to the existing create would change what its existing
answers *mean*: today a `201` says "created and done", and the auth-status poll's
`ISSUED` is terminal. With a manifest neither is true. Rather than overload two
established contracts with a conditional third meaning, the manifest flow gets its
own pair with its own state machine. It **reuses the implementation** —
`create_bot_with_authorization` and `complete_bot_authorization` — so the
preflight, engine/cluster bijection, space resolution and Passport handling are
the same code, not a copy.

**D-2 — The endpoint accepts a narrower vocabulary than `PUT`, and derives it.**
`PUT` may accept a category with no materialiser: the document sits inert, the
capabilities endpoint says so, and nothing has been created. On the creation path
that same acceptance costs a Passport application, a user's authorization click and
a live bot before the failure appears. Deriving the gate from the registry rather
than restating it means W5 and W6 widen this endpoint by landing.

**D-3 — A durable job carries the creation; the poll only observes.**
*Revised — this replaces a device-activation listener.* The queue already has the
exact shape this needs: a handler that reschedules itself until an external status
goes terminal, bounded by a wall-clock deadline, with `TIMED_OUT` as a status
distinct from failure. Three things fall out that the listener design had to
solve separately:

- **The restart guard disappears.** A listener on device activation fires on every
  activation — restarts and re-publishes included, which are W8's — so it needed a
  guard to recognise a creation. A job exists only for a creation, so there is
  nothing to disambiguate.
- **Abandonment becomes terminal.** The listener design had no answer for "the
  user never clicked"; the deadline is one, and it is configurable.
- **Work survives a restart.** A daemon thread does not; the queue is
  DB-backed and reclaims a crashed worker's task after its lease expires.

The cost, stated plainly because work-items §5 predicted it: **the task queue
carries no tenant.** Its model has `env`, `app` and `idempotency_key` and nothing
tenant-shaped, and no request context exists at handler time to capture. The
tenant therefore rides in the payload and is re-established with
`avernet_tenant_scope` in the handler. That is the whole of the cost, and it is
covered by an acceptance criterion above rather than left to be remembered.

**D-4 — The poll asks for a bot id and nothing else.**
*Revised.* The earlier draft had the poll echo the creation attributes, the way
today's auth-status poll does. It does not need to: the attributes are
materialised into the job at submission, so the server already has them. This also
retires the entire question of what happens when an echo disagrees with what was
submitted — there is no echo. (Engine swapping was never permitted in the first
place; the re-validation the earlier draft added to defend against it is gone with
it. W4's apply-time re-validation, which predates this item, stays as it is.)

**D-5 — A failed phase A does not abort creation.** Putting a manifest-layer
failure in charge of the bot record would contradict §2.7 and leave a half-created
bot to compensate for. The bot is created; the report says the script did not
land; the caller can fix it with `PUT` + apply.

**D-6 — `PARTIAL` reports `FAILED`, and the response shows the bot.**
The mapping is a standing decision (#1696, 2026-08-30, superseding an earlier
revision that mapped `PARTIAL` to `READY`; it rested on `on_fetch_failure: skip`,
which §3.2 removed). The objection it invites is real — "failed" can read as "no
bot was created" — so the terminal response carries the bot itself, and the apply
record keeps saying `PARTIAL`. Nothing about applying a manifest to an
already-running bot changes: there, `PARTIAL` is the report's status and the HTTP
call is a success. Only this poll's one-word summary maps it.

**D-7 — No feature switch.**
*Revised.* The switch existed for one reason: submission stores a manifest keyed
by a `bot_id` that may never become a bot, and nothing capped those rows —
deleting a bot never reaches them, and allocating a `bot_id` consumes no quota. The
deadline supplies the cap the switch was standing in for, so the job deletes the
manifest when a creation ends without a bot. #1698's general expiry sweeper is
still worth having for rows this path cannot account for, but it is no longer this
endpoint's gate.

**D-8 — teclaw is accepted, but "in the first artifact" is not claimed.**
The job waits for the container on either engine family, so phase B works for
both. The stronger teclaw guarantee — that the **first** artifact already contains
the manifest's results — requires reaching into artifact production and is W8's
criterion, not this item's. On teclaw, `script` is already unsupported by the
capability resolver, so phase A has nothing to do there.

## In Scope

- The create endpoint and the poll endpoint, with the state machine above.
- The creation job: its handler, its payload (attributes + tenant), its
  configurable deadline, its idempotency key, and its re-runnability.
- Persisting the manifest at submission against the allocated `bot_id`, and
  deleting it when the creation ends without a bot.
- The materialiser-backed acceptance gate, derived from the registry.
- Phase A before the start command is composed; phase B once the container is up;
  both phases' results in the terminal report.
- Tenant propagation through the job payload, with tests.
- Documentation: the creation flow, the poll states, the `script`-dependency rule.

## Out of Scope

- **Creation idempotency at the `bot_id` level (#1697).** A pre-existing gap:
  `generate_bot_id` mints an id platform-side with no idempotency key, so a
  retried *submission* makes a second bot. The job's idempotency key prevents a
  second job for the same bot; it cannot prevent a second bot id being minted.
- **#1698's general orphan sweeper.** Still worth having; no longer this
  endpoint's precondition.
- **W8's other apply points** — republish, rebuild-restart, `PUT` taking effect
  without a restart, and the legacy `/startup-script` write-through.
- **The teclaw first-artifact guarantee** (W8), per D-8.
- **Batch creation.** Structurally impossible: one authorization click per bot.
- Any change to `POST /openapi/v1/bots` or the existing auth-status poll.

## Open Questions

1. **Does the job drive, or does the poll?** The review said both "add a job with
   the task queue infra" and "that poll endpoint handles the end to end logic".
   This spec has the **job** drive and the poll observe, because a poll-driven
   creation stalls forever if the caller walks away — which is the thing a durable
   job is for. If the intent was the opposite, the state machine survives; the
   handler and the poll swap roles.
2. **`AUTHORIZATION_EXPIRED`** is added by this revision as the honest terminal for
   a deadline that elapsed. It is a seventh state on a machine #1696 specified with
   six; say the word and it folds into `AUTHORIZATION_REJECTED`.
3. **The default deadline** is ten minutes, as proposed, and configurable. It
   bounds how long a user has to click.

## Follow-ups

- #1697 — creation idempotency.
- #1698 — the general orphan-manifest sweeper.
- #1508 — deliver every category before the container starts, which removes both
  the `APPLYING` window and iteration 1's `script`-dependency rule.
