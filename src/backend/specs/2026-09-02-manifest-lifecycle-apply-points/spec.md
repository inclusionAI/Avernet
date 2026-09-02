# Lifecycle Apply Points for the Bot Config Manifest (W8)

Work item W8 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1476.
Plan: `plan.md` in this directory.

## Summary

A bot that carries a configuration manifest configures itself. Today the only
thing that applies a stored manifest is an explicit `POST …/config-manifest/apply`,
or the creation job W13 runs for bots born through `POST /openapi/v1/bots/with-manifest`.
Everything else the design promised — a `PUT` that takes effect, a republish that
re-converges, a restart that re-resolves a moving ref, a teclaw bot created with
its manifest — still needs a human to remember to call apply afterwards.

This item wires the apply engine into the four remaining lifecycle points:

| Point | What happens |
| --- | --- |
| `PUT …/config-manifest` on an existing bot | The document is stored and an apply is **started**, in the same request. The response carries the apply's id. |
| A bot's container comes up (first boot, in-place restart, rebuild restart) | An apply is **started** against the freshly active container, so a manifest stored while the bot was `PENDING` lands, and a moving ref is re-resolved under D2's rules. |
| Publish / republish (first release included) | An apply runs against the draft stage and **finishes before the build snapshot is taken**, so the artifact carries the converged state. |
| Creation on **teclaw** through W13's endpoint | Accepted. The same job runs the same two phases; the refusal W13 shipped is lifted. |

It also makes the legacy `/startup-script` endpoints an alias view of the
manifest's `script` field on bots that have a manifest, so an edit through the
old surface can no longer be reverted by the next apply (§2.2).

## Motivation

Design §3.1 promises that "the first configuration a bot receives already
contains the manifest result" and that every lifecycle boundary re-converges.
W4–W7, W11 and W13 built the engine, the materialisers, the sources and the
creation path; W8 is where the business ask is finally delivered — a bot comes
up configured, with nobody calling apply.

Three consequences of earlier decisions land here and are restated rather than
re-derived:

- **§2.6 — `PUT` takes effect immediately, without a restart.** Restarting was
  never what made configuration effective; the categories are written directly.
  The one deferred category is `script`, delivered now and executed at the next
  device provisioning.
- **§2.7 — apply records delivery, not execution, and never writes to the bot
  record.** A lifecycle apply that fails leaves the lifecycle it rode on
  untouched: a republish still builds, a restart still restarts, a creation
  still creates. The report is what says what did not land.
- **§3.2 (D2) — a moving ref is re-resolved at every apply point**, in `strict`
  or `non_strict` mode per source. Lifecycle points are exactly the restarts
  "nobody associated with a configuration change"; making them applies is what
  gives the mode something to enforce.

## What the code allows, checked before writing this

Two facts constrain the shape, and both were verified against the tree rather
than assumed:

1. **Nothing but `script` can be delivered before a container exists, on either
   family.** Every `ON_CONTAINER` materialiser touches the bot's live state:
   `identity` and `resources` resolve a device and raise `DeviceNotBoundError`
   when unbound; `mcp` and `skills` go through `DirectActivationService`, whose
   first act is `is_bot_ready`, which refuses any bot not `ACTIVE`. The teclaw
   draft artifact is composed from platform DB state that a manifest cannot
   populate before the bot is `ACTIVE`, and its `identity_files` / `resources`
   are gathered from the running container at promotion. So "materialise into
   platform state before `provision()`" — the shortcut the work item hoped for —
   is not available. This is D4 (#1508), deferred by decision; W8 inherits its
   consequence on teclaw exactly as on ARCA: the first container comes up and is
   then configured.

2. **The platform already has one signal for "this bot's container just came
   up".** `DeviceService.report_device_alive` publishes `DeviceActivatedEvent`
   on every binding transition `PENDING → ACTIVE`, and two listeners
   (`SkillSymlinkListener`, `CronAutoSetupListener`) already subscribe to it to
   reconcile a freshly active container. It fires for an ordinary create's
   first boot, for the BaaS in-place restart (the binding is flipped back to
   `PENDING` and re-reported), and for the ARCA rebuild restart (a new binding).
   It does **not** fire for publish-stage containers as far as the bot is
   concerned — `BotRepository.get_by_binding_id` resolves only the bot's own
   draft binding — and scale-out replicas have no binding row of their own.
   teclaw's activation is the one gap: its durable publish poll flips the
   binding terminal directly and publishes nothing.

## User Stories

- As a bot owner, I `PUT` a manifest and the running bot reflects it, without a
  restart and without a second call; the response tells me an apply started and
  where to read its result.
- As a bot owner, I `PUT` a manifest onto a bot that is still `PENDING`; when
  its container comes up it configures itself.
- As a bot owner, I republish a service bot and the new version's snapshot
  contains what the manifest declares, including a moved branch ref resolved
  under the source's mode.
- As a bot owner, I restart a bot and the manifest is re-converged against the
  new container; a `strict` source that moved is refused and the bot keeps
  running what it had, and the report says so.
- As a bot owner on teclaw, I create a bot with its manifest through the same
  endpoint ARCA owners use, and it comes up configured.
- As an operator who still uses `PUT …/startup-script`, my edit on a bot that
  has a manifest becomes the manifest's `script` and is not silently undone by
  the next apply.
- As a bot owner, a manifest problem at any lifecycle point costs me an entry in
  the apply report, never the lifecycle operation itself.

## Acceptance Criteria

### `PUT` takes effect (§2.6)

- [ ] `PUT …/config-manifest` stores and validates the document exactly as
      today (all-or-nothing, byte-verbatim, `422` with every reason), **and then
      starts an apply** of both phases with trigger `put`. Storing never depends
      on whether the apply can start.
- [ ] The response carries the apply that was started — its `apply_id` and
      `RUNNING` — in a new `apply` field, alongside the stored document. A caller
      polls `GET …/config-manifest/applies/{apply_id}` or `…/last-apply` exactly
      as after an explicit apply.
- [ ] When the apply **cannot** be started — another apply holds the lock, or
      the queue is unreachable — the document is still stored and the response
      still answers `200`; `apply` says it was not started and why
      (`apply_in_progress` / `not_started`). A valid manifest is never refused
      because of where the bot sits in its lifecycle, and never because of
      apply's own serialisation.
- [ ] When the document declares `script`, the response's `warnings` say that
      the script is delivered now and executes at the bot's next device
      provisioning. The apply report's `script` entry carries the same note it
      does today.
- [ ] No restart is issued on either family. `BotService.restart_bot` is not
      reachable from anything this item adds; a test on the manifest layer pins
      that it names no restart, republish or payload-rebuild call.
- [ ] `DELETE …/config-manifest` is unchanged: it clears the declaration and
      applies nothing.

### The container comes up

- [ ] When a bot's own binding transitions to `ACTIVE` and the bot has a stored
      manifest, an apply of both phases is started with trigger `start`. This
      covers an ordinary create's first boot, the BaaS in-place restart and the
      ARCA rebuild restart with one mechanism.
- [ ] On teclaw, the same happens when the durable publish poll persists the
      binding `ACTIVE` — the one activation path that publishes no
      `DeviceActivatedEvent` today.
- [ ] A bot without a manifest is untouched: no apply, no record, no lock.
- [ ] **Scale-out does not re-apply.** A scale-out adds BaaS devices under the
      bot's existing container identity and raises no activation for the bot's
      binding; instances stay identical because they share one platform state
      (#926's actual requirement). A test pins that an activation the bot
      repository cannot resolve to a bot starts nothing.
- [ ] **Publish-stage containers do not re-apply.** A verify or online container
      redeploys a frozen artifact; the manifest addresses the draft stage, which
      is what the republish point below converges before that artifact is
      frozen. Their activations resolve to no bot and start nothing.
- [ ] **A bot whose W13 creation job is still live is left to the job.** The job
      owns creation-time apply and recognises its own phases by trigger on the
      newest record; a `start` apply landing between "container up" and "phase B
      started" would make it start phase B twice and blur the poll. The listener
      checks for a live creation job and yields. Once the job is terminal — a
      later restart of a W13-created bot — the listener applies like for any
      other bot.
- [ ] A stale activation (the bot's current binding is no longer the one that
      fired) starts nothing, per the precedent the skill listener sets.
- [ ] The listener writes nothing to the bot record and never raises into the
      device-alive path; a failure to start an apply is logged and the container
      is unaffected.

### Publish / republish

- [ ] When a publish enters its build phase and the bot has a stored manifest,
      an apply of both phases is started against the draft stage with trigger
      `republish`, and **the build waits for it to reach a terminal status**
      before producing the artifact. The verify-flow task reschedules itself
      while the apply is `RUNNING`, exactly as W13's creation job waits for its
      pre-container phase.
- [ ] The wait is durable and re-entrant: the apply's id is recorded on the
      publish record's `ext`, so a re-claimed task resumes waiting on the same
      apply rather than starting another. An apply that cannot be started
      because the lock is held is retried on the next tick; any other failure to
      start is recorded on the publish record and the build proceeds.
- [ ] A `PARTIAL` or `FAILED` republish apply **does not fail the publish**. The
      build proceeds with whatever converged; the apply report says what did
      not (§2.7).
- [ ] A bot without a manifest builds exactly as today, with no apply, no
      record and no extra tick.
- [ ] The first release of a service bot is a publish and gets the same
      treatment.
- [ ] On teclaw the promotion gather runs after the apply, so the per-file
      writes the `identity` and `resources` materialisers made into the running
      draft container are what the artifact carries.

### Creation on teclaw (lifting W13's refusal)

- [ ] `POST /openapi/v1/bots/with-manifest` accepts a teclaw engine. The
      `engine_not_supported_for_creation` refusal is removed from the preflight
      and from the route's documentation.
- [ ] A teclaw document that declares `script` is still refused — by the
      capability resolver, as at `PUT`, with the existing reason. Nothing
      teclaw-specific is added to the preflight.
- [ ] The creation job runs the **same** sequence on teclaw as on ARCA: the
      pre-container phase (which delivers nothing on teclaw, and records that),
      creation, wait for `ACTIVE`, the post-container phase. No teclaw-shaped
      step order exists, because there is nothing for one to do — see *What the
      code allows* above.
- [ ] **The first-artifact guarantee is not delivered here, and the spec says so
      where W13's poll and the work item say the opposite.** On teclaw, as on
      ARCA, the first container comes up and is then configured, inside the
      `APPLYING` window the poll already reports. #1508 closes it on both
      families at once. The work-items W8 entry is updated to record this as a
      consequence of D4's deferral rather than left as an unmet criterion.

### The legacy `/startup-script` endpoints write through (§2.2)

- [ ] On a bot **with** a stored manifest, `PUT …/startup-script` rewrites the
      manifest document's top-level `script` section to the submitted body —
      leaving every other byte of the document as it was — stores it through
      the same validation `PUT …/config-manifest` uses, and then writes the
      `ac_bot_startup_script` row with the body the materialiser would write
      (placeholders substituted). `GET …/config-manifest` and `GET …/startup-script`
      agree afterwards, and the next apply plans the script `unchanged`.
- [ ] On a bot **with** a manifest, `DELETE …/startup-script` removes the
      `script` section from the document, stores the result, and clears the row.
- [ ] On a bot **with** a manifest that declares `script`, `GET …/startup-script`
      returns the manifest's body. When the manifest does not declare it, the
      row is returned as today.
- [ ] On a bot **without** a manifest all three endpoints behave byte-for-byte
      as today. Their existing tests pass unedited.
- [ ] The document rewrite is exact: the spliced `script` section round-trips
      through the manifest parser to the submitted body, byte for byte,
      including trailing newlines. If a body cannot be expressed as a YAML block
      scalar it is written as a quoted scalar; either way the parse equals the
      body, and a test says so for bodies with quotes, `$(…)`, `{token}`,
      leading spaces and no trailing newline.
- [ ] A write-through that fails validation (the manifest would no longer be
      accepted for this bot) is a `422` naming the reasons, and neither the
      document nor the row changes.

### Ordering and the `script` rule (§2.12)

- [ ] A test pins iteration 1's ordering: the only `PRE_CONTAINER` construct is
      `script`, every other construct is `ON_CONTAINER`, and on a first boot the
      script row exists before the start command is composed while nothing else
      has been delivered. The test names #1508 as the change that deletes it.
- [ ] The rule "a manifest's `script` must not depend on anything else the
      manifest declares" is stated in the user manual's `script` section and in
      the `PUT` route's documentation.
- [ ] With no script stored, the composed start command is byte-identical to
      today's (design §10.4); #935's existing assertion is kept and is not
      edited.

### Records, D2, and what apply never does

- [ ] Every lifecycle apply is started through `start_apply`, so it takes the
      lock, re-validates, writes a `RUNNING` record, and — because the source
      session is built from the newest recorded resolutions — enforces the
      source's `mode` on a moved ref: `strict` refuses the entry and the
      category aborts, `non_strict` delivers and notes the move. No second
      enforcement path exists.
- [ ] The trigger vocabulary is `explicit`, `put`, `start`, `republish`,
      `create:pre_container`, `create:on_container`. `last-apply` and
      `GET …/applies/{id}` return the same per-entry detail for every trigger.
- [ ] Apply writes nothing to the bot record and does not branch on first boot,
      on either family (§2.7). The orchestrator-stays-generic test still holds;
      nothing added here reads or writes `ac_bots.status`.
- [ ] `last-apply` is the reachable signal for "did my manifest land" on the
      paths that have no poll loop (republish, restart). The user manual §7 says
      so and no longer implies a real-time state on those paths.

### Nothing else moves

- [ ] `POST …/config-manifest/apply`, its `202` and its `409`, are unchanged.
- [ ] W13's endpoint, poll and job behave exactly as they do today on ARCA;
      their tests pass with assertions untouched.
- [ ] The skill symlink and cron listeners are not modified.
- [ ] The publish flow's phase sequence, statuses and existing tasks are
      unchanged for bots without a manifest.

## Decisions

**D-1 — One mechanism for "the container came up": the device-activated
event.** The alternatives were to hook each caller — `restart_bot`'s two legs,
`_allocate_device_async`, the BaaS publish poller, the restart poll task — or to
apply *before* the payload is rebuilt. Hooking callers is four seams for one
fact and misses the next one added; applying before the rebuild runs against a
container that is about to be destroyed (the ARCA leg releases the device), so
its device writes fail per entry and the report is noise. The event is the
platform's own definition of the moment, it already has two listeners, and it
naturally excludes publish-stage containers and scale-out.

**D-2 — Republish applies *before* the build, and waits.** The published stages
are frozen snapshots of the draft; an apply after the new container comes up
would target the draft and never reach the snapshot. So the republish point is
the build phase, and the build waits, the way W13's job waits for phase A. The
wait is bounded by the apply lock's TTL and the task's own deadline, and a
non-succeeding apply lets the build proceed (§2.7).

**D-3 — teclaw creation is the same job, not a different step order.** The
refusal W13 shipped protected against a mechanism that would "change under" a
teclaw bot once W8 landed. Checked against the code, no other mechanism is
available in iteration 1: nothing can be delivered before the container exists
on either family. The honest outcome is to lift the refusal and state that the
first-artifact guarantee is #1508's on both families.

**D-4 — The activation apply yields to a live W13 creation job.** The job
recognises its phases by trigger on the newest record and the poll derives its
state the same way; a foreign trigger landing mid-creation would make the job
start phase B twice and hide the creation's report from the poll. Checking for a
live job is a cheap indexed read; once the job is terminal the bot is an
ordinary bot.

**D-5 — teclaw's activation gets a direct hook, not a new event emission.**
Publishing `DeviceActivatedEvent` from the teclaw poll would also wake the skill
symlink listener (a whole-artifact redeliver on every teclaw boot) and the cron
listener — a behaviour change to two other features. The teclaw poll handler
gets an optional activation callback, wired by DI to the same listener logic.
Emitting the event is recorded as the cleaner long-term fix and left to its own
change.

**D-6 — `PUT` starts an apply; it does not run one.** Apply is a durable task
(W13); the request returns the id. A `PUT` that could not start an apply still
stores: the alternative, a `409`, would refuse a valid document because of
apply's own serialisation, which §2.6 forbids.

**D-7 — The legacy endpoint writes the row itself on the manifest arm, rather
than starting an apply.** Its contract is synchronous — the row exists when
`200` returns — and its tests read the repository straight after. Starting an
apply would make the row eventually consistent and would `409` under a running
apply. Writing the row with the substituted body is exactly what the
materialiser writes, so the next apply plans `unchanged`.

**D-8 — The document rewrite is a textual splice, not a re-serialisation.** W1
stores the document verbatim because `script.body` is a shell body whose bytes
are its meaning. Re-dumping the YAML would reformat every other section. The
splice replaces the top-level `script` section only and proves itself by
parsing the result back.

## In Scope

- `PUT …/config-manifest` starting an apply and reporting it.
- The device-activated listener, its DI wiring, and the teclaw activation hook.
- The publish build-phase wait and its `ext` marker.
- Lifting W13's teclaw refusal.
- The `/startup-script` write-through, both arms, with the splice helper.
- The §2.12 ordering test and its documentation.
- The trigger vocabulary and the module README, the user manual §4.6 / §5.5 /
  §7, and the work-items W8 entry (both languages).

## Out of Scope

- **Delivery before the container starts** (#1508), and with it the teclaw
  first-artifact guarantee and the removal of the `script` rule.
- **A bot-health surface for manifest state.** §2.7 asks that any surface
  showing a bot as healthy be able to show that a manifest entry is not. The
  bot list and detail payloads are UI contracts; adding a manifest summary to
  them is a product decision and its own change. `last-apply` remains the
  authority.
- **Emitting `DeviceActivatedEvent` from the teclaw poll** (D-5).
- **Applying at scale-out** — explicitly not wanted.
- **A restart of the publish record's stage containers** — they redeploy a
  frozen artifact and are not an apply point.
- **Deleting a manifest when its bot is deleted** — a standing gap, unchanged.
- **Cleaning stale `ac_bot_startup_script` rows** — unchanged.

## Open Questions

1. **Bot-health surface (§2.7).** Deferred here (see *Out of Scope*). If the
   owner wants a minimal signal in this item, the cheapest is a
   `config_manifest: {last_apply_status, last_applied_at}` block on the bot
   detail payload of `GET /openapi/v1/bots/{bot_id}`.
2. **teclaw activation** — D-5 chose the callback. If emitting the event is
   preferred despite waking the other listeners, it is a two-line change in the
   teclaw poll handler and the callback goes away.

## Follow-ups

- #1508 — deliver every category before start; removes the `script` rule and
  the `APPLYING` window, and makes the first artifact carry the manifest.
- Emit `DeviceActivatedEvent` from the teclaw publish poll once the skill and
  cron listeners are confirmed safe on teclaw.
- A manifest summary on the bot detail surface (open question 1).
