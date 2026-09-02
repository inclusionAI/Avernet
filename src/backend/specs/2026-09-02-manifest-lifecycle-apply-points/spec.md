# Lifecycle Apply Points for the Bot Config Manifest (W8)

Work item W8 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1476.
Plan: `plan.md` in this directory.

## Summary

A bot that carries a configuration manifest configures itself. Today the only
thing that applies a stored manifest is an explicit `POST …/config-manifest/apply`,
or the creation job W13 runs for bots born through `POST /openapi/v1/bots/with-manifest`.
A `PUT` that takes effect and a teclaw bot created with its manifest still need
a human to remember to call apply afterwards, and the legacy startup-script
endpoint can silently diverge from the manifest it now shadows.

This item wires the apply engine into the two lifecycle points that still
need it in this iteration, and records why the other two are deferred:

| Point | What happens |
| --- | --- |
| `PUT …/config-manifest` on an existing bot | The document is stored and an apply is **started**, in the same request. The response carries the apply's id. |
| Creation on **teclaw** through W13's endpoint | Accepted. The same job runs the same two phases; the refusal W13 shipped is lifted. |
| Restart (in-place or rebuild) and publish / republish | **Not apply points in this iteration** (owner decision, 2026-09-02). Nothing applied earlier is lost on either path — see *What the code allows* — and what a re-apply there would add is only the re-resolution of a moving git ref and correction of manual drift. Both converge at `PUT` and at explicit apply. |

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
  or `non_strict` mode per source. In this iteration the apply points are
  `PUT`, explicit apply and creation; restart and republish are deferred
  (D-1), so a ref never moves on them.

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

2. **Nothing applied earlier is lost on a restart or a republish.** The script
   row is re-read by `_build_create_bot_payload` on every payload it composes
   (`upgrade_bot` for the in-place restart, the device service for the rebuild
   restart, `create_bot` for a first release). The workspace is on NAS. Skills
   and MCP are DB state, and the existing skill listener re-syncs symlinks on
   every activation. An ARCA build snapshots the draft workspace plus DB state;
   a teclaw build gathers the running draft container's files — where the
   manifest's per-file writes landed — and takes MCP and skills from DB. A
   re-apply at those points would therefore change nothing a previous apply
   delivered; it would only re-resolve a moving `ref` and correct manual drift.
   Those two are the whole payload of "apply at restart / republish", and the
   owner has deferred them.

## User Stories

- As a bot owner, I `PUT` a manifest and the running bot reflects it, without a
  restart and without a second call; the response tells me an apply started and
  where to read its result.
- As a bot owner, I `PUT` a manifest onto a bot that is still `PENDING`; the
  response tells me which part could not land yet and what to do once the bot
  is `ACTIVE`, and the apply report records the same per entry.
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
- [ ] When the bot is **not `ACTIVE`** at `PUT` time, `warnings` also says that
      the categories needing a live container (`identity`, `resources`,
      `skills`, `mcp`) will be recorded as failed in this apply and that the
      caller should `POST …/config-manifest/apply` once the bot is `ACTIVE`.
      Apply itself does **not** branch on the bot's status (§2.7): both phases
      are started, and the report says per entry what did not land.
- [ ] No restart is issued on either family. `BotService.restart_bot` is not
      reachable from anything this item adds; a test on the manifest layer pins
      that it names no restart, republish or payload-rebuild call.
- [ ] `DELETE …/config-manifest` is unchanged: it clears the declaration and
      applies nothing.

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

- [ ] The `PUT` apply is started through `start_apply`, so it takes the lock,
      re-validates, writes a `RUNNING` record, and — because the source session
      is built from the newest recorded resolutions — enforces the source's
      `mode` on a moved ref: `strict` refuses the entry and the category
      aborts, `non_strict` delivers and notes the move. No second enforcement
      path exists. In this iteration a moving ref is re-resolved **only** at
      `PUT` and at explicit apply.
- [ ] The trigger vocabulary is `explicit`, `put`, `create:pre_container`,
      `create:on_container`. `last-apply` and
      `GET …/applies/{id}` return the same per-entry detail for every trigger.
- [ ] Apply writes nothing to the bot record and does not branch on first boot,
      on either family (§2.7). The orchestrator-stays-generic test still holds;
      nothing added here reads or writes `ac_bots.status`.
- [ ] The user manual §7 states that restart and republish do not re-apply in
      this iteration, that nothing previously applied is lost on them, and that
      a moved ref or manual drift converges at the next `PUT` or explicit
      apply.

### Nothing else moves

- [ ] `POST …/config-manifest/apply`, its `202` and its `409`, are unchanged.
- [ ] W13's endpoint, poll and job behave exactly as they do today on ARCA;
      their tests pass with assertions untouched.
- [ ] No restart, publish, device-activation or provisioning path is modified.
      `BotService.restart_bot`, `PublishFlowService`, `DeviceService`,
      `TeclawProvisionService` and the teclaw publish poll are untouched.

## Decisions

**D-1 — Restart and republish are not apply points in this iteration.** Owner
decision (2026-09-02), on the finding that nothing previously applied is lost on
either path and that a re-apply there would only re-resolve a moving `ref` and
correct manual drift. The work item's restart and republish criteria are
recorded as deferred, not met. The mechanisms designed for them — a
`DeviceActivatedEvent` listener for "the container came up", and a durable
wait before the publish build — are kept in this spec's revision history for
when they are wanted, and none of their code lands.

**D-2 — A `PUT` on a non-ACTIVE bot warns rather than defers.** With no
activation hook, a manifest stored while the bot is `PENDING` has nothing to
re-apply it when the container comes up. Rather than applying phase A only and
inventing a "deferred" state, `PUT` starts both phases exactly as on an ACTIVE
bot — §2.7 forbids apply from branching on bot state — and the response warns
that container-bound categories will be recorded as failed and names the call
to make once the bot is `ACTIVE`. The report carries the same information per
entry.

**D-3 — teclaw creation is the same job, not a different step order.** The
refusal W13 shipped protected against a mechanism that would "change under" a
teclaw bot once W8 landed. Checked against the code, no other mechanism is
available in iteration 1: nothing can be delivered before the container exists
on either family. The honest outcome is to lift the refusal and state that the
first-artifact guarantee is #1508's on both families.

**D-4 — `PUT` starts an apply; it does not run one.** Apply is a durable task
(W13); the request returns the id. A `PUT` that could not start an apply still
stores: the alternative, a `409`, would refuse a valid document because of
apply's own serialisation, which §2.6 forbids.

**D-5 — The legacy endpoint writes the row itself on the manifest arm, rather
than starting an apply.** Its contract is synchronous — the row exists when
`200` returns — and its tests read the repository straight after. Starting an
apply would make the row eventually consistent and would `409` under a running
apply. Writing the row with the substituted body is exactly what the
materialiser writes, so the next apply plans `unchanged`.

**D-6 — The document rewrite is a textual splice, not a re-serialisation.** W1
stores the document verbatim because `script.body` is a shell body whose bytes
are its meaning. Re-dumping the YAML would reformat every other section. The
splice replaces the top-level `script` section only and proves itself by
parsing the result back.

## In Scope

- `PUT …/config-manifest` starting an apply and reporting it, with the
  non-ACTIVE warning.
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
- **Applying at restart (in-place or rebuild) and at publish / republish**
  (D-1). Deferred with the mechanisms recorded below.
- **Applying when a container comes up** for a manifest stored while the bot
  was `PENDING` (D-2). Deferred with D-1; it shares the same hook.
- **Applying at scale-out** — explicitly not wanted, and moot under D-1.
- **Deleting a manifest when its bot is deleted** — a standing gap, unchanged.
- **Cleaning stale `ac_bot_startup_script` rows** — unchanged.

## Open Questions

1. **Bot-health surface (§2.7).** Deferred here (see *Out of Scope*). If the
   owner wants a minimal signal in this item, the cheapest is a
   `config_manifest: {last_apply_status, last_applied_at}` block on the bot
   detail payload of `GET /openapi/v1/bots/{bot_id}`.

## Follow-ups

- #1508 — deliver every category before start; removes the `script` rule and
  the `APPLYING` window, and makes the first artifact carry the manifest.
- Restart / republish as apply points, when moving-ref re-resolution at those
  points is wanted. The designed shape, so it is not re-derived: a
  `LifecycleBase` listener on `DeviceActivatedEvent` (plus a callback from the
  teclaw publish poll, which emits no event) starting a `start`-triggered apply
  for bots with a manifest, skipping stale bindings and live W13 creation
  jobs; and a `manifest_apply_before_build` step in `PublishVerifyFlowHandler`
  that starts a `republish`-triggered apply against the draft stage and
  reschedules until it is terminal, with the apply id kept in the publish
  record's `ext`.
- A manifest summary on the bot detail surface (open question 1).
