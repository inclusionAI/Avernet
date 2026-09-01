# Creating a Bot With Its Configuration — the Async Create API (W13)

## Summary

A public, asynchronous API that creates a bot from a **configuration manifest**
plus the ordinary creation attributes, so the bot's **first** container comes up
already carrying its configuration. One `POST` starts it, one poll drives it to
a terminal state, and the manifest the caller submitted once is the manifest that
gets applied — it is never re-submitted.

This is the item that delivers the business ask. W4 made applying a stored
manifest possible on a bot that already exists; every other work item feeds that.
None of them can put configuration into a bot's *first* boot, because until the
bot exists there is no `/bots/{bot_id}/config-manifest` to write to. That gap is
what this closes.

Work item **W13** · issue #1696 · `work-items.zh-CN.md` §2.11, §2.12, §5.

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
around it: the manifest is in hand before the bot record is written, so apply
runs inside creation — phase A before the start command is composed, phase B once
the container is up.

**One fixed constraint, not a design choice.** Passport authorization requires a
human click, and that is AgentPass's limitation, not ours. "Create a bot" is
therefore inherently one-at-a-time and human-in-the-loop; nothing here may assume
otherwise, and "create N bots from one manifest" is not a thing this API can ever
grow into (§2.11). Applying *one* manifest to *many existing* bots needs no
authorization at all and remains open.

## User Stories

**As a developer standing up a new bot**, I submit its manifest with its creation
attributes, complete one authorization, and poll one endpoint until it says
`READY` — at which point the bot exists, its container is up, and its MCP servers,
skills, identity files and startup script are what my manifest declared. I never
learn the platform's internal sequencing.

**As the same developer whose manifest has a typo**, I am told at submission time
— before I am sent to Passport, before a bot exists, before anything external is
spent. The `422` names every violation at once.

**As a developer whose manifest was valid but whose apply partly failed**, the
poll reaches `FAILED`, the response names which entries did not land and why, and
my bot is nonetheless running. Nothing silently reports success for a category
that was not written.

**As an operator**, a creation the user abandoned at the authorization step
leaves no bot, no container, and no quota consumed — only a manifest row that a
follow-up (#1698) expires.

## Acceptance Criteria

### The create operation

- [ ] A public endpoint accepts a manifest document **plus** the ordinary
      creation attributes (engine, cluster, name, description, bot type, space,
      engine properties) and returns immediately with the allocated `bot_id`, the
      poll state, and the authorization handles (`iframe_url` / `redirect_url`).
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
- [ ] The manifest is persisted in the **first leg**, keyed by the allocated
      `bot_id`. No schema change: the existing
      `(avernet_tenant, sha256(env, entity_id, bot_id))` key has all three parts
      in hand at that point.
- [ ] The storage key's `entity_id` is resolved by the **same rule** `create_bot`
      will use when it writes the record, so the document stored in leg one is
      found in leg two. A second, drifting derivation of that value is the defect
      this criterion exists to prevent.
- [ ] **The validated manifest is the applied manifest.** The caller submits it
      once. No polling call accepts a manifest, so no caller can validate one
      document and have another applied.

### The poll, and its states

- [ ] One poll endpoint drives the creation to a terminal state and reports:

      ```text
      AWAITING_AUTHORIZATION   waiting for the user to open the Passport link
              │                (the response carries iframe_url / redirect_url)
              ├──► AUTHORIZATION_REJECTED    terminal
              ▼
      CREATING                 authorized; the bot record is written, the
              │                container is being provisioned
              ▼
      APPLYING                 the manifest apply is running
              ├──► READY       terminal — success; carries the apply report
              └──► FAILED      terminal — carries which entries did not land
      ```

- [ ] The bot is created on `ISSUED` by the poll, as it is today — this rides on
      the existing two-leg Passport flow rather than forking it.
- [ ] An apply result of `PARTIAL` reports **`FAILED`**, not `READY`. Under
      §3.2's category overwrite a partial set is destructive, so a declared
      category that was not written whole is not a success a caller should see as
      ready. The per-entry records say which entries and why.
- [ ] `FAILED` is a **manifest-layer** terminal state (§2.7). **The bot record is
      not touched** — no deletion, no status change, no deactivation. A caller
      that polls `FAILED` has a running bot whose manifest did not fully land, and
      the report says exactly which parts did not.
- [ ] Both terminal states carry a report complete enough to answer "did my
      manifest take effect?" without a second call — **including the entries from
      both phases**. A terminal report that names only the post-container
      categories, silently dropping `script`, does not satisfy this.
- [ ] `APPLYING` makes D4's interim cost **observable**. Post-boot delivery
      (§3.4) leaves a window where the bot is ACTIVE but not yet configured; a
      caller waiting for `READY` never observes it. The window is no longer an
      invisible trap on this path.
- [ ] The poll never requires the manifest. It may require the creation
      attributes to be echoed, exactly as the existing auth-status poll does.
- [ ] Because those attributes are echoed, the stored manifest is
      **re-validated against the engine creation will actually use** before the
      bot record is written. An engine swapped between submission and completion
      changes what the manifest may contain, and the check that catches it must
      run while nothing has been created yet.

### The two phases

- [ ] Apply is invoked in **two phases** (W4's `phases` argument, built for this):
      - **Phase A — `script`** runs after the bot record exists and **before the
        start command is composed**. `BaasService._build_create_bot_payload` reads
        the startup-script row while composing; a row written after that point
        cannot reach the first boot.
      - **Phase B — everything else** runs once the container is up.
- [ ] Phase A is **synchronous** with respect to provisioning. Creation does not
      proceed to provisioning until phase A has finished, or the ordering above is
      a coincidence rather than a guarantee.
- [ ] Phase B is started by the **platform**, on the signal that the container
      became available — not by the caller's polling. A caller who stops polling
      still gets a configured bot.
- [ ] Phase B runs **once** for a creation. The activation signal also fires on
      restarts and re-publishes; those apply points belong to W8, and this must
      not quietly become them.
- [ ] A **failing phase A does not fail creation.** The bot is still created and
      provisioned; the failure is recorded and surfaces in the poll's terminal
      report. §2.7's boundary holds: apply records delivery, and a manifest-layer
      failure never mutates the bot record.
- [ ] `script`'s materialisation stays exactly what W4 made it — a write to
      `ac_bot_startup_script` and nothing else. No restart, no republish, no
      forced payload rebuild.
- [ ] **Iteration 1's rule is stated where a caller will read it:** a manifest's
      `script` must not depend on anything else the same manifest declares,
      because on a first boot the script runs before any other category has been
      delivered (§2.12). #1508 removes the restriction in iteration 2.

### Tenancy

- [ ] The tenant reaches the code that actually performs the apply, on **every**
      path that starts one — including the platform-driven phase B, which runs on
      a thread with no request behind it.
- [ ] This is pinned by tests, not by memory. A wrong tenant here substitutes the
      wrong `${BOT_TENANT}` **and** reads and writes the manifest tables under the
      wrong tenant: an isolation failure, not merely a correctness one.

### Nothing else moves

- [ ] `POST /openapi/v1/bots`, the auth-status poll and every existing creation
      path behave **exactly** as they do today when no manifest is involved.
      Their tests pass unedited.
- [ ] The `PUT` path is unchanged: a bot created by any other means can still be
      given a manifest afterwards, and it still takes effect immediately, with no
      restart, by the same path as any other existing bot (§2.6).
- [ ] With no manifest supplied, the new endpoint creates a bot that is
      byte-for-byte the bot the existing endpoint creates — same preflight, same
      engine/cluster rules, same space resolution, same Passport flow.
- [ ] Creation with no manifest, or with a manifest declaring nothing, applies
      nothing and reports `READY`. An empty declaration is not a failure.

### Availability

- [ ] The endpoints ship behind a switch that is **off by default**, and the
      reason is written where the switch is defined: the first leg persists a
      manifest row keyed by a `bot_id` that may never become a bot, nothing caps
      those rows, and the ordinary delete-bot path never reaches them because no
      bot record was ever written. Allocating a `bot_id` consumes no quota, so the
      tenant limit that bounds every other creation path does not bound this one.
      #1698 (expiry) is the precondition for turning it on — not an optional
      second-phase tidy-up.

## Decisions

**D-1 — A dedicated endpoint pair, not an extra field on `POST /bots`.**
Adding an optional manifest to the existing create would change what its existing
answers *mean*: today a `201` says "created and done", and the auth-status poll's
`ISSUED` is terminal. With a manifest neither is true — `ISSUED` is the middle of
the flow, and creation continues through `APPLYING`. Rather than overload two
established contracts with a conditional third meaning, the manifest flow gets its
own pair with its own state machine. It **reuses the implementation** —
`create_bot_with_authorization` and `complete_bot_authorization` — so the
preflight, engine/cluster bijection, space resolution and Passport handling are
the same code, not a copy.

**D-2 — The endpoint accepts a narrower vocabulary than `PUT`, and derives it.**
`PUT` may accept a category with no materialiser: the document sits inert, the
capabilities endpoint says so, and nothing has been created. On the creation path
that same acceptance costs a Passport application, a user's authorization click and
a live bot before the failure appears. So this path additionally requires a
registered materialiser. Deriving that from the registry rather than restating it
means W5 and W6 widen this endpoint by landing, with no edit here and no window
where the two lists disagree.

**D-3 — Phase B is driven by the container-activation signal, not by the poll.**
Letting the poll start phase B would be less plumbing and would make the apply
hostage to a client's loop: a caller who stops polling would leave a bot ACTIVE
and permanently unconfigured. The platform already publishes a signal when a
device becomes available and already carries listeners that act on it, so phase B
joins that pattern. Consequence to hold: the signal fires on every activation, so
the listener must recognise a **creation's** phase B and leave every other
activation to W8.

**D-4 — Two applies, one story.** Phase A and phase B are separate applies with
separate ids: they are separated by the whole of container provisioning, and a
single record held `RUNNING` across it would mean holding an apply lock across
provisioning and reporting `APPLYING` during what is really `CREATING`. To keep
the terminal report complete, phase B's report **carries phase A's results
forward**, so the report a caller reads at `READY` or `FAILED` accounts for every
declared category. Phase A's own record stays readable in its own right.

**D-5 — A failed phase A does not abort creation.** The alternative — refusing to
create a bot whose startup script could not be written — would put a
manifest-layer failure in charge of the bot record, contradicting §2.7 and leaving
a half-created bot to compensate for. The bot is created; the report says the
script did not land; the caller sees `FAILED` and can fix it with `PUT` + apply.

**D-6 — Re-validate at completion, against the engine completion will use.**
The poll echoes creation attributes, so the engine at completion may differ from
the engine the manifest was validated against — and capabilities are
engine-dependent (`script` on teclaw, for one). Re-validating before the record is
written keeps "the validated manifest is the applied manifest" true in the only
sense that matters: the document is checked against the bot that will actually
receive it, while there is still nothing to roll back.

**D-7 — teclaw is accepted, but "in the first artifact" is not claimed.**
Both engine families reach the same activation signal, so phase B works for both.
The stronger teclaw guarantee — that the **first** artifact already contains the
manifest's results — requires reaching into artifact production and is W8's
criterion, not this item's. On teclaw, `script` is already unsupported by the
capability resolver, so phase A has nothing to do there.

## In Scope

- The create endpoint (manifest + creation attributes) and the poll endpoint with
  its six states.
- Persisting the manifest in leg one against the allocated `bot_id`.
- The materialiser-backed acceptance gate, derived from the registry.
- Phase A invoked before the start command is composed; phase B invoked on
  container activation, once per creation, carrying phase A's results into the
  terminal report.
- Tenant propagation onto every thread that applies, with tests.
- The default-off switch, with its reason recorded.
- Documentation: the user manual's creation flow, the `script`-dependency rule,
  and the module README's boundary.

## Out of Scope

- **Orphan manifest cleanup (#1698).** Tracked as the precondition for enabling
  the endpoint, not built here.
- **Creation idempotency (#1697).** A pre-existing gap: `generate_bot_id` mints an
  id platform-side with no idempotency key, so a retried creation makes a second
  bot. Unrelated to manifests and unchanged by this.
- **W8's other apply points** — republish, rebuild-restart, `PUT` taking effect
  without a restart, and the legacy `/startup-script` write-through.
- **The teclaw first-artifact guarantee** (W8), per D-7.
- **Batch creation.** Structurally impossible: one authorization click per bot
  (§2.11).
- Any change to `POST /openapi/v1/bots` or the existing auth-status poll.

## Open Questions

None blocking. Two settled by assumption and worth naming at review:

1. **Endpoint spelling and shape** — a dedicated pair (D-1). If the team would
   rather grow the existing create endpoint, the state machine and every other
   criterion here survive that change; only the routing does.
2. **Which signal phase B hangs on** (D-3) — the device-activation event, chosen
   because listeners already do exactly this. If teclaw's provisioning turns out
   not to reach it, teclaw's phase B needs its own trigger, and the fallback is
   the publish poll that provisioning already runs.

## Follow-ups

- #1698 — expire unclaimed first-leg manifests; the gate on enabling this.
- #1697 — creation idempotency.
- #1508 — deliver every category before the container starts, which removes both
  the `APPLYING` window and iteration 1's `script`-dependency rule.
