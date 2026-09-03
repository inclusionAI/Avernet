# Lifecycle Apply Points for the Bot Config Manifest (W8)

Work item W8 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1476.
Plan: `plan.md` in this directory.

> **Revision 3 (2026-09-02).** Adds the engine-family delivery seam and the
> teclaw platform-managed delivery behind a switch, on the owner's decision
> that the platform is the source of truth for what a manifest applies on both
> families. Revision history at the end.

## Summary

A bot that carries a configuration manifest configures itself. Today the only
things that apply a stored manifest are the explicit `POST …/config-manifest/apply`
and the creation job W13 runs for ARCA bots. This item delivers the rest of the
business ask for iteration 1, and does so through one abstraction that names
how the two engine families differ:

| Point | What happens |
| --- | --- |
| `PUT …/config-manifest` on an existing bot | The document is stored and an apply is **started** in the same request. The response carries the apply's id. |
| Creation through W13's endpoint, on **teclaw** | Accepted. The bot record is created, the whole manifest is materialised into platform state, and the **first artifact carries it**. |
| Creation on ARCA | Unchanged: phase A before the start command, phase B once the container is up. |
| Restart and publish / republish | **Not apply points in this iteration** (owner decision). Nothing previously applied is lost on them. |

Underneath, a **delivery strategy** per engine family owns three things the
engine cannot decide for itself: which phase each category belongs to, what
the write target is, and what the creation sequence looks like. ARCA's strategy
is today's behaviour. teclaw's strategy writes to the platform's own store and
database and delivers by artifact, which is the shape the design always
intended for it.

The artifact gains an **ownership map** so the engine can tell which categories
the platform is asserting and which it leaves to the engine. Until the teclaw
engine supports that map, a deployment switch keeps teclaw on today's per-file
path.

The legacy `/startup-script` endpoints are untouched (revision 5): the
manifest is a layer the startup script does not know about, and a manifest
that declares `script` materialises into the same row on apply.

## Motivation

Design §3.1 promises that "the first configuration a bot receives already
contains the manifest result". Design §4.1 says that on teclaw fetched content
is materialised into the OSS store and the artifact carries `{store, path}`
refs. The W12 contract's A4 says the engine applies the whole artifact before
reporting ready. Put together: on teclaw there is one phase, it runs before
provisioning, its output is platform state, and the artifact is the delivery.

W5, W6 and W13 built the engine and the materialisers on ARCA's shape — write
into a live container, then project — and that shape was carried onto teclaw
unchanged: an empty phase A, a post-ACTIVE phase B writing per file into the
running container and pushing MCP and skills through activation, which
recomposes and redelivers the whole artifact once per category. It converges,
but the first artifact never carries the manifest, a re-apply costs one engine
round trip per path, and the platform never holds the state it claims to have
applied.

Three earlier decisions are restated rather than re-derived:

- **§2.6 — `PUT` takes effect immediately, without a restart.** The one
  deferred category is `script`, delivered now and executed at the next
  device provisioning.
- **§2.7 — apply records delivery, not execution, and never writes to the bot
  record.** A lifecycle apply that fails leaves the lifecycle it rode on
  untouched.
- **§3.2 — a declared category is overwritten to equal the declaration; an
  undeclared category is untouched.** On teclaw the ownership map carries
  this to the engine per *operation* (revision 5): a manifest apply's
  artifact is the platform's for every category, a runtime edit's is the
  engine's.

## What the code allows, checked before writing this

1. **On the current materialisers, nothing but `script` can be delivered
   before a container exists**, on either family: identity and resources raise
   `DeviceNotBoundError` when unbound, MCP and skills go through activation
   whose first act refuses any bot that is not `ACTIVE`, and the teclaw draft
   composer returns empty identity and resource lists because the container
   owns those files. Delivering before the container therefore needs new write
   targets, not a re-ordering — which is what the teclaw strategy adds.
2. **Nothing applied earlier is lost on a restart or a republish.** The script
   row is re-read on every payload the platform composes; the ARCA workspace
   is on NAS; skills and MCP are database state; a teclaw build gathers the
   running container's files. A re-apply there would only re-resolve a moving
   `ref` and correct manual drift, which the owner has deferred.
3. **The teclaw create and update calls carry the artifact in the same field**
   (`deploy_config.teclaw_bot_config`), and publish artifacts already carry
   identity and resource refs through it. Refs on a create artifact are the
   existing contract.
4. **A draft artifact redelivered to a live teclaw bot carries empty identity
   and resource lists today, and the bot keeps its files.** So the engine
   treats an empty list as "leave alone", which is not the literal A1 rule.
   Without an ownership marker the platform cannot express "this category is
   empty" for files. That is the gap the map closes.
5. **The teclaw poll flips the binding and the bot record to `ACTIVE` in one
   transaction and publishes no event.** With restart and republish out of
   scope nothing here needs the activation moment, so no hook is added.

## User Stories

- As a bot owner, I `PUT` a manifest and the running bot reflects it without a
  restart; the response tells me an apply started and where to read its result.
- As a bot owner on teclaw, I create a bot with its manifest and the first
  artifact the container receives already carries every declared category.
- As a bot owner on teclaw, I `PUT` a new manifest version and the container
  receives one artifact that is exactly the new desired state, rather than a
  series of per-file edits.
- As an operator, I can turn the teclaw platform-managed path off until the
  engine supports the ownership map, and nothing regresses while it is off.
- As the teclaw engine owner, I can read one contract section that says what
  the ownership map means and what the engine must do with it.
- As an operator who still uses `PUT …/startup-script`, the endpoint behaves
  exactly as before; on a bot whose manifest declares `script`, the manifest
  is the source of truth and the next apply restores it.
- As a bot owner, a manifest problem at any point costs me entries in the
  apply report, never the lifecycle operation itself.

## Acceptance Criteria

### The delivery seam

- [ ] A `DeliveryStrategy` exists per engine family, selected by the same
      `is_teclaw` authority the capability resolver uses. It answers: the
      phase of each construct, the write ports the materialisers use, the
      creation sequence, and the "make effective" step that closes an apply.
- [ ] **ARCA's strategy is today's behaviour, unchanged**: `script` in
      `PRE_CONTAINER`, everything else in `ON_CONTAINER`, device-backed ports,
      activation with projection, no explicit closing step (the owning
      services project as they write). Every existing apply, creation and
      materialiser test passes with assertions untouched.
- [ ] **teclaw's strategy, with the switch on**: `script` unsupported, every
      other construct in `PRE_CONTAINER`, store-backed ports for identity,
      resources and skill packages, record-only activation for MCP and skills,
      and one whole-artifact redeliver as the closing step when the bot has a
      live binding (none when it does not — provisioning composes the first
      artifact instead).
- [ ] **teclaw's strategy, with the switch off**: today's shape — every
      non-script construct in `ON_CONTAINER`, the per-file ports, activation
      with projection, no closing step. The switch lives in one place and is
      read once per apply.
- [ ] The orchestrator, the order table and the materialisers do not learn the
      family: the materialisers see ports, the orchestrator sees phases. The
      orchestrator-stays-generic test still passes, and a new test pins that
      no materialiser module names an engine.
- [ ] The strategy is injected into the apply service and the creation job;
      nothing else selects behaviour by engine string.

### The ownership map (artifact contract)

- [ ] `BotConfigArtifact` gains an optional top-level `ownership` map, category
      name to `platform` or `engine`. `to_dict` omits it when unset, so every
      artifact built before this change is byte-identical; `from_dict` does not
      manufacture it. The JSON schema admits it. `SCHEMA_VERSION` stays 4, per
      the `cli_tools` precedent and A5.
- [ ] Semantics, stated in the contract and pinned by the schema's
      description: `platform` means the list in this artifact is the complete
      desired state for that category's area (W12 §5), an empty list meaning
      remove everything; `engine` means the engine owns the category and
      ignores the list; absent means today's behaviour.
- [ ] The teclaw composer emits the map on every artifact it composes for a
      bot, and ownership follows the operation (revision 5): every category
      is `platform` on the closing redeliver of a manifest apply and on the
      first artifact of a bot that carries a manifest, when the switch is on;
      every category is `engine` on any other compose — a skill or resource
      upload, an MCP edit, a channel change, a publish build — and while the
      switch is off. `mcp` is `platform` on every occasion (today's semantics
      made explicit). A bot without a manifest gets `engine` for every other
      category, which is today's artifact plus the map.
- [ ] ARCA artifacts do not carry the map; nothing on ARCA reads it.
- [ ] A contract addendum for the teclaw owner is added to
      `engine-convergence-contract.zh-CN.md`: the map and its semantics, file
      refs in a redeliver to a running container, and a store-backed `SkillRef`
      for a local skill, each with an example and an acceptance line.

### teclaw platform-managed delivery (switch on)

- [ ] The platform keeps a durable record of the manifest-delivered file set
      per bot and category — bytes in the bot-data object store under a
      per-bot key layout that is itself the record (no index table, as of
      plan rev 4) — and that record is what the teclaw composer reads for
      the three file categories when the map says `platform`.
- [ ] The identity and resource materialisers converge that record: `plan`
      reads the store, `write` puts and deletes objects. Convergence is
      observed from the store (an unchanged file writes nothing), never from
      the container.
- [ ] A manifest skill is unpacked into the store under the engine's
      local-skills directory layout, stored like any other files, recorded
      as a skill with a `local://` locator, and emitted in the artifact both
      as files and as a `SkillRef` naming the package's store prefix.
- [ ] MCP and skill activation for a bot that is not `ACTIVE` records the
      desired state and skips projection, through an explicit entry point on
      the activation service; the artifact is the projection. On an `ACTIVE`
      teclaw bot the same entry point is used and the strategy's single
      redeliver replaces the per-mutation projections.
- [ ] The closing redeliver composes through the same composer the runtime
      device-sync plugin uses, so the redelivered artifact and the first
      artifact are produced by one path.
- [ ] The objects for a bot are deleted when a W13 creation ends
      without a bot, together with the manifest and the script row it already
      deletes. They are otherwise retained, like publish snapshots.

### Creation on teclaw (lifting W13's refusal)

- [ ] `POST /openapi/v1/bots/with-manifest` accepts a teclaw engine. A teclaw
      document declaring `script` is still refused by the capability resolver
      with the existing reason.
- [ ] With the switch on, the creation job runs teclaw's sequence: create the
      bot record **without provisioning**, run the single pre-container phase
      against that record, provision (which composes the first artifact from
      the platform state just written), wait for `ACTIVE`, finish. No phase B.
- [ ] With the switch off, the job runs today's sequence on teclaw: an empty
      phase A, create and provision, wait for `ACTIVE`, phase B.
- [ ] `create_bot` gains a deferred-provision option; a separate call
      provisions a record created that way. Every existing creation path
      passes the default and is unchanged.
- [ ] The poll reports `READY` on teclaw once the bot is `ACTIVE` and the
      single phase is terminal; it does not wait for a phase B that never
      comes. `APPLY_FAILED` when the phase ended `PARTIAL` or `FAILED`;
      `CREATE_FAILED` when provisioning failed.
- [ ] **First-artifact guarantee, with the switch on:** a test on the creation
      job pins that provisioning is called only after the pre-container phase
      is terminal, and a test on the provisioner pins that the artifact handed
      to the container carries the store's refs and the ownership map.

### `PUT` takes effect (§2.6)

- [ ] `PUT …/config-manifest` stores and validates exactly as today, **then
      starts an apply** of both phases with trigger `put`. Storing never
      depends on whether the apply can start.
- [ ] The response carries the apply in a new `apply` field — `apply_id` and
      `RUNNING`, or `NOT_STARTED` with `apply_in_progress` / `not_started` —
      and is `200` in every case.
- [ ] When the document declares `script`, `warnings` carries the delivery
      note. When the bot is not `ACTIVE` and its strategy has `ON_CONTAINER`
      constructs, `warnings` says those will be recorded as failed and names
      the apply call to make once the bot is `ACTIVE`. On a teclaw bot with the
      switch on there is no such warning: nothing needs the container.
- [ ] No restart is issued on either family. A test on the manifest layer pins
      that it names no restart, republish or payload-rebuild call.
- [ ] `DELETE …/config-manifest` is unchanged.

### The legacy `/startup-script` endpoints are untouched (revision 5)

- [ ] All three endpoints behave byte-for-byte as today on every bot; their
      existing tests pass unedited. The manifest service exposes nothing to
      them, and they inject nothing from the manifest layer.
- [ ] A manifest that declares `script` materialises into the same row on
      apply, as before; the manifest is the source of truth for what it
      declares.

### Ordering, records, and what apply never does

- [ ] A test pins iteration 1's ARCA ordering: `script` is the only
      `PRE_CONTAINER` construct for ARCA, and on a first boot it runs before
      any other category. The test names #1508 as the change that deletes it.
      On teclaw the same test pins the opposite: with the switch on every
      construct is `PRE_CONTAINER` and there is no first-boot ordering rule.
- [ ] The user manual states the `script` rule for ARCA and that it does not
      apply on teclaw, where `script` is unsupported anyway.
- [ ] With no script stored, the composed start command is byte-identical to
      today's; #935's assertion is kept and not edited.
- [ ] The trigger vocabulary is `explicit`, `put`, `create:pre_container`,
      `create:on_container`; on teclaw with the switch on only the first three
      occur.
- [ ] Every apply is started through `start_apply`, so D2's `strict` and
      `non_strict` are enforced on a moved ref at `PUT`, explicit apply and
      creation. No second enforcement path exists.
- [ ] Apply writes nothing to the bot record and does not branch on first boot
      (§2.7). The strategy branches on the family and the switch, which are
      configuration, not lifecycle state.

### Nothing else moves

- [ ] `POST …/config-manifest/apply`, its `202` and its `409`, are unchanged.
- [ ] W13's endpoint, poll and job behave exactly as today on ARCA.
- [ ] No restart, publish or device-activation path is modified.
- [ ] With the switch off, every teclaw behaviour is what it was before this
      change, including the artifact bytes apart from the ownership map.

## Decisions

**D-1 — Restart and republish are not apply points in this iteration.** Owner
decision, on the finding that nothing previously applied is lost on either
path. The designed mechanisms (a `DeviceActivatedEvent` listener, a durable
wait before the publish build) are recorded in *Follow-ups* and not built.

**D-2 — A `PUT` on a non-ACTIVE ARCA bot warns rather than defers.** `PUT`
starts both phases exactly as on an ACTIVE bot; the response warns that
container-bound categories will be recorded as failed and names the call to
make later. On teclaw with the switch on the warning never applies.

**D-3 — The platform is the source of truth for what a manifest applies, on
both families.** Owner decision. On ARCA that is already so: device writes land
on platform-controlled NAS and the database. On teclaw it means the platform
keeps its own copy of the delivered files and composes artifacts from it, and
a live bot converges by one whole-artifact redeliver rather than by a per-path
diff against the container. This replaces the design's §3.1 note that a live
teclaw bot is converged through the per-file channel.

**D-4 — The ownership map is decided per operation.** The engine cannot
otherwise distinguish "the platform asserts this category is empty" from "the
platform has nothing to say", so it has to guess. The map removes the guess:
a manifest apply's artifact (and the first artifact of a bot with a manifest)
says the platform owns every category; any other operation's artifact says the
engine does. Its absence preserves today's behaviour, which is what lets it
ship ahead of the engine. (Revision 5; revision 3 keyed it on which categories
the stored manifest declared.)

**D-5 — A deployment switch gates the teclaw platform-managed path.** Off,
teclaw runs today's per-file shape. On, the store path. The switch is read by
the strategy factory and nowhere else. It is off by default until the engine
ships the map.

**D-6 — teclaw creation creates the record first, then applies, then
provisions.** The activation services and the composer read the bot record, so
the record must exist before the single phase; the artifact must be composed
after it. `create_bot` gets a deferred-provision option — the seam W13 avoided,
and the one W8's own scale note names.

**D-7 — The seam varies ports and phases, not materialisers.** The
materialisers stay engine-agnostic; the strategy hands them the ports for its
family. Adding a family is a strategy, not a fork of five materialisers.

**D-8 — `PUT` starts an apply; it does not run one.** A `PUT` that could not
start an apply still stores.

**D-9 — The legacy `/startup-script` endpoints do not know the manifest
exists.** Revision 3's write-through alias (and its splice, D-10) is withdrawn
in review: the manifest is the upper layer, and the startup script is one of
the entities it materialises into, not a view of it.

## In Scope

- The delivery strategy seam, both strategies, the switch, and the strategy
  awareness in the apply service, the creation job and the poll.
- The ownership map on the artifact, the schema, the composer, and the
  contract addendum.
- The managed-files store, the store-backed ports, record-only
  activation, the store-backed skill package, the closing redeliver.
- The deferred-provision option on `create_bot` and the provision call.
- Lifting W13's teclaw refusal.
- `PUT` starting an apply and reporting it.
- The ordering test, docs, the work-items W8 progress block.

## Out of Scope

- **Restart and republish as apply points** (D-1).
- **The publish gather preferring the platform copy for platform-managed
  categories.** Publish still gathers from the container; the gathered set
  equals what the platform delivered plus any manual drift. Natural follow-up
  when publish becomes an apply point.
- **ARCA delivery before the container** (#1508's ARCA half). The seam is
  where it lands; a store-backed or NAS-backed pre-binding port is its shape.
- **A bot-health surface for manifest state.** `last-apply` remains the
  authority.
- **`engine_config` and `cli_tools`** on either family.

## Open Questions

None for the platform side. One item is with the teclaw owner and gates only
the switch's default, never this work: support for the ownership map, file refs
in a live redeliver, and the store-backed `SkillRef`, per the contract addendum.

## Follow-ups

- Flip the switch's default once the teclaw engine ships the map.
- #1508's ARCA half: a pre-binding port for the ARCA strategy.
- Restart / republish as apply points (D-1's recorded mechanisms).
- The publish gather preferring the platform copy.
- A manifest summary on the bot detail surface.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | Four apply points: `PUT`, an activation listener, a publish-build wait, teclaw creation on the ARCA shape. |
| **rev 2** | Restart and republish deferred by the owner. |
| **rev 3** | The delivery-strategy seam; the platform as source of truth on teclaw (D-3); the ownership map (D-4); the switch (D-5); teclaw creation as record, apply, provision (D-6). The "same job on teclaw" statement of rev 1 and rev 2 withdrawn. |
| **rev 5** | Review of inclusionAI/Avernet#1836: ownership follows the operation, not the declared categories (D-4); the `/startup-script` write-through alias withdrawn (D-9, D-10). |
