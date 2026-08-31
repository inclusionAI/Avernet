# Applying a Bot Config Manifest — the Engine, the Record, and the Two Fetch-Free Categories

## Summary

A stored manifest does nothing today. This makes it *apply*: on demand, a bot's
real configuration is converged toward what its document declares, and the caller
gets a per-entry account of what happened. The engine is built whole — ordering,
serialization, category-scoped overwrite, all-or-nothing writes, the per-entry
record, `dry_run` — while only the two categories that need no network are wired
into it: `mcp` and `script`.

This is W4 (#1472). It is the first work item where a manifest changes anything
about a bot, and it is the piece W5, W6, W8 and W13 all plug into rather than
route around.

## Motivation

**Everything upstream of this is inert.** W1 (#1469) stores, validates and
describes a document; W10 (#1509) put the five categories' rules where a
non-HTTP caller can reach them. Neither writes anything to a bot. A caller can
`PUT` a perfectly good manifest today and observe no difference in their bot,
which is the whole feature's value proposition still undelivered.

**The engine has to exist before the interesting categories do.** `skills`,
`identity` and `resources` are what customers asked for, and every one of them
needs content fetched from somewhere — the guarded fetcher (W2, #1470), source
credentials (W3, #1471), platform-side materialisation (W11, #1510). Building
the orchestrator *and* the fetch pipeline in one item would mean debugging two
unproven things against each other. `mcp` and `script` are the two categories
whose materialisation needs no fetched bytes at all — an MCP entry is a registry
reference, a script is an inline body — so they are the cheapest possible
end-to-end proof that the path *manifest → existing service → real change on a
bot* works. W5 and W6 then add categories to a mechanism that already runs.

**The orchestrator's shape is load-bearing for two later items and cannot be
retrofitted.** Apply is not one ordered pass. `script` is a plain row write that
needs no container, and on the creation path it must land *before* the start
command is composed or the first boot carries no script. `identity`,
`resources` and `skills` all resolve a device filesystem and raise if unbound,
so they can only run once a container is up. That is two phases separated, on
the creation path, by the whole of container provisioning. An orchestrator
written as a single call is one W13 (#1696) has to bypass, and bypassing it means
a second apply implementation on the most expensive path in the feature.

**Overwrite is destructive if it is done by halves.** D2's revision (#1467) made
a declared category overwrite its area to equal the declaration. Under that rule
a partially-materialised category is not "partly applied" — it is a *deletion*:
declaring `{A, B}` and writing only `{A}` removes B. So a category is written
all-or-nothing or not at all, and a transient failure has to leave a working
entity untouched. This is the property most worth testing and the easiest to get
subtly wrong.

**Apply's authorization bar is where a manifest could become a privilege
escalation.** Three of the six categories are owner-only through their own
endpoints. A manifest that could be applied at a lower bar would be the way
around them. W10's spec settled the bar and handed this item the test that keeps
it true.

## User Stories

- As a bot owner, I want to apply my stored manifest on demand, so that the bot's
  MCP servers and startup script match what I declared without my configuring
  each one by hand.
- As a bot owner, I want to ask what an apply *would* do before it does it, so
  that I can see a destructive change coming rather than discovering it.
- As a bot owner, I want a per-entry account of the last apply, so that "did my
  manifest take effect?" has an answer I can read rather than infer from the
  bot's behaviour.
- As a bot owner whose source is briefly broken, I want the entries that failed
  to leave my working configuration alone, so that a bad five minutes upstream
  does not delete something that was working.
- As a bot owner, I want applying an unchanged document to be a no-op, so that
  re-applying is safe and I can do it whenever I am unsure.
- As the engineer building W13, I want the orchestrator split at the point where
  a container becomes necessary, so that creating a bot with a manifest calls
  this engine instead of reimplementing it.
- As the engineer building W5 and W6, I want to add a category by writing a
  materialiser, so that ordering, locking, recording, overwrite and reporting are
  not mine to rebuild.
- As a security reviewer, I want apply's bar proven to be at least every bar it
  can reach through, so that a manifest cannot become a route around an
  owner-only operation.
- As an operator, I want the apply record to name credentials but never carry
  their values, so that reading an audit trail is not a way to read a secret.

## Acceptance Criteria

Grouped by the property each set holds. Every one traces to `work-items.zh-CN.md`
§5 W4, #1472's body, or one of its three amendment comments.

### The apply operation

- [ ] **`POST /openapi/v1/bots/{bot_id}/config-manifest/apply` does not block.**
      It accepts the request, mints an `apply_id`, starts the work, and answers
      `202` with that id. Applying is device I/O today and fetching over the
      network from W5 — a caller must never be holding an HTTP connection open
      while it happens.
- [ ] The `apply_id` is the caller's handle: `GET
      …/config-manifest/applies/{apply_id}` returns that apply's report, in
      progress or finished, and `GET …/config-manifest/last-apply` returns the
      newest one for the bot. A caller who lost the id is not stranded.
- [ ] A report carries a **status of its own** — `RUNNING` until the work
      finishes, then the derived `SUCCEEDED` / `PARTIAL` / `FAILED`. A poller can
      tell "still working" from "finished, partially", which is the distinction
      W13's `APPLYING` state needs.
- [ ] **A crashed or restarted process must not strand a report at `RUNNING`
      forever.** The serialization lock's staleness rule is what bounds it, and a
      report whose lock has gone stale reads as `FAILED` rather than as a poll
      that never terminates.
- [ ] A bot with **no stored manifest** applies nothing and reports nothing
      applied — not an error, the same rule that makes an absent manifest read as
      an empty document rather than a 404.
- [ ] `dry_run=true` **stays synchronous** and returns the plan in the response —
      a preview whose answer arrives by `apply_id` later is not a preview. It
      performs **no write of any kind**: not to a bot's configuration, and not to
      the report storage either, so it mints no `apply_id` and appears in no
      history. This is safe to keep synchronous only while nothing is fetched;
      W5 must revisit it when `resolve` starts making network calls, and the
      spec says so rather than letting W5 discover it.
- [ ] `GET /openapi/v1/bots/{bot_id}/config-manifest/last-apply` returns the most
      recent report. A bot that has never been applied answers with an empty
      report, not a 404.
- [ ] Two applies against the same bot **serialize**. The second either waits or
      is refused, never interleaves. The lock follows the existing
      `BotRestartLockRepository` pattern rather than introducing a new mechanism,
      and it is not the restart lock's own row — applying a manifest must not
      collide with restarting a bot.

### Convergence

- [ ] **Applying an unchanged document a second time reports `unchanged` for
      every entry and performs no write.** This is the criterion the whole
      engine is judged on: convergence is observable as the absence of writes,
      not merely as an equal-looking result.
- [ ] Convergence is decided against the value that would actually be
      materialised, **after** `${BOT_*}` substitution — never against the raw
      document text. A body containing `${BOT_ENV}` must not report `updated` on
      every apply because the stored text and the written text differ by
      construction.

### Per-entry outcomes and the summary

- [ ] Every declared entry gets exactly one outcome: `created`, `updated`,
      `unchanged`, `skipped` or `failed`.
- [ ] `skipped` means **"not written because its category was aborted"**. It no
      longer means an author permitted the entry to be missing — `on_fetch_failure:
      skip` does not exist.
- [ ] The apply result is `SUCCEEDED` / `PARTIAL` / `FAILED`, **derived** from
      the per-entry outcomes. Nothing reads it and then acts on it.
- [ ] **Apply writes nothing to the bot record.** No status change, no
      activation, no readiness gate, and no branch anywhere on whether this is
      the bot's first boot.

### Category-scoped overwrite (§3.2)

- [ ] A **declared** category is overwritten to equal the declaration. An
      **undeclared** category is untouched.
- [ ] A category declared **empty** (`skills: []`) empties that area. This is
      the reverse of the earlier "stop managing but do not delete" reading.
- [ ] **`DELETE` of the manifest deletes nothing**, and this is derived from the
      same rule rather than standing beside it: `[]` is a declaration that the
      set is empty; absence is not a declaration at all. One test pins both
      behaviours together, because they look opposite and are one rule.
- [ ] **The area is scoped per category, not globally.** `resources` overwrites
      only the declared `path` subtrees; a file the bot created elsewhere in its
      workspace survives. A test pins that survival.
- [ ] **A category is written all-or-nothing.** If any declared entry in it
      cannot be materialised, the category is not overwritten at all, every other
      entry in it reports `skipped`, and the entries in *other* categories are
      unaffected. The test that matters: declaring `{A, B}` where B fails leaves
      B's existing content intact — a transient failure must never delete a
      working entity.
- [ ] **`MEMORY.md` and `IDENTITY.md` are never written and never removed**,
      whether or not a manifest mentions them. The single exception to overwrite,
      with a test of its own.

### The two phases

- [ ] The orchestrator is **two-phase**, and its shape carries this whether or
      not a caller can currently observe the split:
      - **Phase A — no container required.** `script` only.
      - **Phase B — container required.** `identity → resources → skills → mcp`,
        in that order.
- [ ] Phase A is callable **without** Phase B, and Phase B without Phase A, by a
      caller that is not an HTTP request. This is what W13 needs and what it
      would otherwise bypass the orchestrator to get.
- [ ] On a running bot the two phases run back to back and the split is invisible
      — one call, one report, the ordering above preserved.
- [ ] This **reverses design §3.4's order**, which put `script` last. The reason
      is recorded where a reader of the design will find it.

### The two materialisers

- [ ] `script` materialises as a write to the bot's startup script through
      `BotStartupScriptService` and nothing else.
- [ ] **The response states when it takes effect, in the terms the mechanism
      actually has.** The row is delivered now and executes at the bot's next
      **device provisioning** — not "next start" loosely, and not "only the
      first container". `BaasService._build_create_bot_payload` re-reads
      `ac_bot_startup_script` on every payload it composes and appends it to
      `after_create_cmd_hook`, and it is reached from `create_bot` *and*
      `upgrade_bot`; `BotService.restart_bot` "releases the current device and
      allocates a new one", so a restart composes a fresh payload and re-reads
      the row. What it does **not** do is re-execute inside a container that is
      already running. So a script written by a later manifest version does take
      effect — at the next create, restart or republish.
- [ ] `mcp` converges the bot's **enabled-server set** — the area §3.2 names —
      through the existing per-bot activation service: a declared server not yet
      active is activated, one active and no longer declared is deactivated, one
      already active reports `unchanged`. It **reuses the existing tenant
      permission check** for a `server_code` rather than carrying a second copy,
      and an entry naming a server the tenant may not enable reports `failed`.
- [ ] **`mcp[].config` is removed from schema v1** and refused by name, with
      `manifest-schema.zh-CN.md` §3.1 corrected in the same change. An `mcp`
      entry is a bare `server_code`. See *Decisions* 10.
- [ ] **No apply path writes account-scoped MCP configuration.** A test pins
      that the `mcp` materialiser cannot reach `update_user_unified_config` or
      `sync_mcp_detail_to_all_bots` — the write that would fan a per-bot apply
      out across every bot its owner has.
- [ ] Both materialisers reach their category's rules through **W10's seam**,
      not through a hand-written second set of checks.
- [ ] `${BOT_*}` placeholders are substituted at materialisation time using the
      existing resolver — the write path's whitelist and the apply path's
      substitution stay one module.

### Categories with no materialiser yet

- [ ] A declared category with **no registered materialiser** — `skills`,
      `identity`, `resources` until W5/W6, `engine_config` and `cli_tools`
      indefinitely — reports every one of its entries `failed` with a stable,
      readable reason, and the category is therefore **not overwritten**. Nothing
      existing is destroyed, exactly as with any other unmaterialisable entry.
- [ ] The remaining categories still apply. A document declaring both `script`
      and `skills` delivers the script and reports `PARTIAL`.
- [ ] Adding a materialiser is the *only* change W5 and W6 need for this
      behaviour to stop occurring. No branch anywhere names the three categories
      by hand.

### The apply record

- [ ] Each apply is recorded with enough per-entry detail to answer *what was
      materialised, and what happened to everything that was not*.
- [ ] The record puts **no mark on any materialised entity**. A manifest-created
      MCP activation is indistinguishable from a hand-made one, by design (§2.3).
- [ ] **The record never disagrees with reality after a mid-way failure.**
      Nothing is recorded as materialised that was not.
- [ ] The record names credentials **by name only**. No credential value is ever
      written to it — and the record is the artifact a support engineer reads, so
      this is a security criterion, not a tidiness one.

### Authorization

- [ ] `POST …/apply` declares `AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT` and
      `Check(PermissionLevel.OWNER, EDIT_LOCK)`, per W10's *Apply Declares Its Own
      Bars*. The bar is decided on apply's own shape, not derived as a maximum
      over the categories it touches.
- [ ] `GET …/last-apply` is readable at the same bar as reading the manifest
      itself. Reading how a bot is configured is part of working on it.
- [ ] **The dominance test.** For every category apply can materialise, apply's
      declared bar is at least that category's own write bar, and its admission
      mode is no wider in the set of callers it admits. Adding a category whose
      endpoints require more than apply does fails this test by name.
- [ ] `engine_ext` is unreachable from a manifest on every path.

### Nothing else moves

- [ ] No existing endpoint changes behaviour. `PUT`, `GET` and `DELETE` of the
      manifest answer exactly as they do today, and their existing tests pass
      unmodified.
- [ ] Applying does not restart a bot on any engine, and `BotService.restart_bot`
      is not called from any path this change adds.

## Decisions

Settled here rather than left open.

1. **`mcp` and `script` are the two categories, and the line is "needs no fetched
   bytes" — not "easy".** An `mcp` entry is a registry reference resolved against
   a catalogue the platform already has; a `script` is an inline body. Every other
   category's entry names content that lives somewhere else, and reaching it needs
   the guarded fetcher (W2), credentials (W3) and the platform-side copy (W11) —
   none of which this item builds or depends on. `engine_config` is excluded by a
   separate decision (X2/T3, §4) and returns with its own materialiser: a
   top-level-key merge through the engine config service.

   Note what this does *not* mean: an `identity` entry with inline `content`
   needs no fetch either, and is still out of scope. The fetch is only half of
   what W5 and W6 own — the delivery target (device filesystem writes, the active
   skill set and its symlink reconcile) is the other half, and that half is theirs
   whatever the source form.

2. **A missing materialiser is an entry failure, not a special case.** Rather than
   branching on which categories are built, the orchestrator drives a
   category → materialiser registry and W4 registers two. A declared category with
   no entry in the registry fails its entries with a stable reason and aborts,
   which is the *same* path a fetch failure takes in W5. The consequences follow
   from rules already decided: nothing is overwritten, nothing is destroyed, the
   summary is `PARTIAL`, and W5/W6 close the window by registering rather than by
   deleting a branch.

   Rejected: capability-gating `skills`/`identity`/`resources` as unsupported
   until W5/W6. It would keep W1's "accepted means appliable" literally true, but
   it makes `PUT` start refusing documents it accepts today and makes W5/W6 delete
   three rows days later — churn on the public surface to describe a state that
   lasts one work item. Also rejected: silently ignoring them, which would report
   `SUCCEEDED` for an apply that did nothing.

3. **Apply is started, not awaited.** `POST …/apply` answers `202` with an
   `apply_id` and does the work in the background. Applying is device I/O today
   and network fetching from W5 — a request that blocks on it is a request that
   times out, and neither a UI nor a script can hold that connection. The id is
   the caller's handle, and it is what makes W13's `APPLYING` poll state
   possible at all: a state you can observe only exists if the work is something
   you start and then ask about.

   Three consequences worth stating, because each is a place this shape goes
   wrong if left implicit. The lock is taken and the document re-validated
   **before** an id is minted, so a caller never holds an id for an apply that
   did not start. The report carries `RUNNING` as a status of its own, so a
   poller can distinguish "still working" from "finished, partially" — the
   distinction W13 needs. And a report must not strand at `RUNNING` when the
   process dies mid-apply: the serialization lock's staleness rule bounds it,
   and a stale-locked `RUNNING` report reads as `FAILED`.

   `dry_run` stays synchronous: a preview whose answer arrives by polling is not
   a preview. That holds only while nothing is fetched, so W5 must revisit it —
   said here rather than left for W5 to discover.

4. **The summary is derived and inert.** `SUCCEEDED`/`PARTIAL`/`FAILED` exists for
   a human reading a report. No code branches on it, and nothing propagates it to
   the bot record. The one aggregate that *does* drive a decision is per-category
   and deliberately so: "may this category be written at all", scoped so one
   category's failure never touches another's.

5. **Apply serializes on its own lock, not the restart lock's row.** The pattern
   is reused — a uniqueness constraint arbitrating concurrent inserts, a token
   compared on release, staleness judged on the database clock — because it is
   proven here and a second mechanism would be a second set of failure modes. The
   *row* is separate because applying a manifest and restarting a bot are
   different operations and blocking one on the other would be an accident, not a
   design.

6. **Convergence compares materialised values, not document text.** Substitution
   happens between the document and the write, so a comparison against the raw
   text would report `updated` forever on any document using a placeholder. Each
   materialiser is responsible for comparing what it is about to write against
   what is there — which is also the only comparison that can be right when a
   value was set through the category's own endpoint rather than by a manifest.

7. **`Check(OWNER)` on an addressed bot is not wider than the owner-only
   categories, and the dominance test must be written to know that.** Three
   categories (`identity`, `resources`, and today's `startup-script`) are
   `OWNER_SCOPED`, which pins the bot to the caller's own. Apply is
   `Check(OWNER)` with `GRANT_CHECKED_ADDRESSED_BOT`, which takes the owner from
   the wire — but `OWNER` is reachable only by the actual owner (the collaborator
   vocabulary is admin/member), so the caller must *be* the addressed owner and
   the admitted set is the same. A test comparing raw enum ordering would flag
   this pairing as a widening; the test compares the admitted set, and the
   equivalence is written down here so a future reader does not have to
   re-derive it.

8. **The apply record and the apply report are one thing, not two.** §2.7 is
   explicit that the per-entry records *are* the report and are apply's only
   output. One store, written once per apply, read by `last-apply`. `keep_last`
   later reads the materialised content from W11's store, which is a different
   question — what bytes were delivered — and does not make this a second record.

9. **No notifications, no alerting, no push.** Recorded because an earlier
   revision of §2.7 required a surfaced notification and it was withdrawn: no
   design document ever specified one. The record is pull-only in the first
   phase.

10. **`mcp[].config` leaves schema v1, and the schema document is corrected —
   this is a defect found while specifying, not a scope cut.** §3.1 defines
   `config` as *"per-bot configuration, the same shape as the existing MCP
   config API"*. Those two halves cannot both be true. The platform's MCP
   configuration is `ac_user_mcp_config`, keyed `(user_id, server_code)`, and
   writing it calls `sync_mcp_detail_to_all_bots` — so materialising a declared
   `config` would make applying **one** bot's manifest change MCP configuration
   for **every** bot its owner has. That is a blast radius no other category has
   and one §3.2's per-category area rule does not sanction. Its payload is also
   `api_key` and `custom_headers`, which design §4.5 forbids a manifest from
   carrying at all.

   What genuinely *is* per-bot is the enabled-server set
   (`ac_bot_mcp_installation`) — which is exactly, and only, what §3.2 names as
   the `mcp` category's area. So the entry narrows to match: a v1 `mcp` entry is
   a bare `server_code`, `config` is refused by name the way
   `cli_tools.entrypoints` is, and §3.1 is rewritten to point callers at the
   already-public user-scoped endpoints (`GET`/`PUT
   /openapi/v1/bots/mcp/servers/{server_code}/config`) for credentials, headers,
   endpoint env and transport. Account-scoped configuration was never the
   manifest's to own.

   `ac_bot_mcp_call_config` (`call_type`: owner | caller) is the one other
   per-bot MCP fact, and it is deliberately **not** pulled in: it sits outside
   §3.2's stated area, and its write carries lock-epoch, draft-state and
   irreversibility semantics (`CallerLockEpochError`,
   `CallerIdentityReadOnlyError`) that an idempotent apply would have to reckon
   with. Adding a key to a closed set later is additive and non-breaking, so it
   is a follow-up rather than a thing to get wrong now.

## In Scope

- The apply orchestrator: two-phase, category-ordered, bot-serialized,
  per-entry-classifying, category-scoped-overwrite, all-or-nothing.
- The category → materialiser registry, and the two materialisers `mcp` and
  `script`.
- **Narrowing the `mcp` entry to `{server_code}`** — the validator rule, the
  capability reason, and the `manifest-schema.zh-CN.md` §3.1 correction, in one
  change (*Decisions* 10).
- The apply record: its storage, its per-entry detail, and its guarantee of
  consistency after a mid-way failure.
- `POST …/config-manifest/apply` with `dry_run`, and `GET …/config-manifest/last-apply`,
  including their `ADMISSION` and `AUTHORIZATION` rows.
- The dominance test protecting apply's bar.
- `${BOT_*}` substitution at materialisation time, through the existing resolver.
- A Context Boundary block for whatever module this adds, per
  `docs/arch/context-boundary-format.md`.

## Out of Scope

- **Fetching anything.** No HTTP fetch, no git, no archive unpacking, no
  credentials. W2, W3, W7.
- **`skills`, `identity`, `resources` materialisers.** W5, W6. They register into
  this item's registry; nothing here anticipates their internals.
- **`engine_config`**, excluded from the first phase by X2/T3. Its row exists in
  W10's seam and its materialiser returns with it.
- **`cli_tools`**, deferred (W9).
- **Lifecycle apply points** — creation, publish, republish, rebuild-restart —
  and **making `PUT` take effect immediately (§2.6)**. All W8. This item's only
  entry point is the explicit `POST …/apply`.
- **Creating a bot with a manifest.** W13.
- **The platform-side content store and retention.** W11. No longer a hard
  dependency of this item after D2's revision, and not built here.
- **Marking materialised entities.** §2.3 removed the need; a `managed by
  manifest` flag is a v2 product idea, not a mechanism this needs.
- **Deleting a bot's manifest and apply record on bot deletion.** W1 recorded
  this gap and named this item as where the purge belongs; it is left as a
  follow-up rather than absorbed, because bot deletion is a soft update with no
  cascade and wiring one is its own change.
- **Notifications.**

## Open Questions

None blocking. Two things are recorded as follow-ups rather than questions
because their answers do not change this item's design.

## Follow-ups

- **`PUT`'s authorization bar diverges from W10's spec, and W8 is where it
  starts to matter.** W10's *Apply Declares Its Own Bars* assigned `PUT
  …/config-manifest` apply's bars — `Check(OWNER, EDIT_LOCK)` — on the grounds
  that §2.6 makes `PUT` an apply trigger. W1 shipped it as `Check(ADMIN)` with no
  lock and a written rationale (a manifest is not drafted; reading and replacing
  configuration is the same split the channels rows make). Both readings are
  defensible **today**, because `PUT` triggers nothing. The moment W8 makes it
  take effect, an `ADMIN` collaborator gains, through `PUT`, exactly what apply
  reserves for `OWNER` — including overwriting identity files that
  `PUT …/identity/{file_type}` refuses them. This is not W4's to change (apply is
  this item's only entry point), but W8 cannot land without resolving it, and it
  is recorded here so it is decided rather than discovered.
- **Purging manifest state when a bot is deleted**, per W1's recorded gap.
- **Retention of apply records.** Every apply is recorded and nothing prunes.
  W11's acceptance criteria require an explicit retention policy stated against
  audit requirements; this record belongs in that conversation rather than
  getting a policy invented for it here.
- **Per-bot MCP `call_type` through a manifest**, if anyone asks for it. The
  fact is per-bot (`ac_bot_mcp_call_config`) and the endpoint exists
  (`PATCH …/mcps/{server_code}/call-type`, owner-only with the edit lock), so
  adding one key to the `mcp` entry is additive. What it needs first is an
  answer for how an idempotent, re-runnable apply behaves against that write's
  lock-epoch and irreversibility rules — which is a question in its own right,
  not a line of code.
- **W5, W6 and W13 all consume this**, and each is a test of whether the shape
  chosen here was right: W5/W6 by registering a materialiser and nothing else,
  W13 by calling Phase A and Phase B around container provisioning instead of
  bypassing them.
