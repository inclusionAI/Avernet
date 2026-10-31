# RSI-04: Direct delivery of a compiled revision

> Work item RSI-04 (`apps/backend/docs/bot-evolution/work-items.md`). Stands on
> RSI-03 (`specs/2026-10-19-bot-genome-registry/`, #2907) and the apply-engine
> desired-state refactor (`specs/2026-10-23-apply-engine-desired-state/`,
> #3083); their revision notes are settled decisions. Scope and the decisions
> below were agreed with the issue owner on 2026-10-27/28; service-bot
> verify/online promotion was split out as #3212 (sub-issues #3213–#3216).

## Summary

Promotion today refuses every delivery (`PendingDeliveryApplier`), so
`active` never moves. This item makes a promotion **deliver**, which means
making the bot's runtime hold the revision. The promotion starts an apply run
that carries only a pointer to the promotion (`StateRef("genome",
promotion_id)`, no copy of the revision). In the worker, the genome compiler
follows that pointer: it reads the promotion row, finds the revision it names,
and compiles the revision into the apply engine's desired state. The apply
engine writes that state to the bot, and the promotion row follows the apply
through to its end. The
refs move **when the apply finishes**, not when the promotion is asked for.

Two things that only a revision can carry become deliverable: a Skill Center
skill (found by its runtime name) and a shared-repository skill (found by its
path). A revision stops recording a Center skill's version: bots track the
latest published version (ADR 0002), and a new Center version is not drift.

### Terms

- **Promotion.** One request to make a bot run a revision, and its record.
  It is now a process with a lifecycle: `pending` → `applying` →
  `applied` / `applied_partial` / `apply_failed`.
- **Delivery apply.** The apply run a promotion starts, with state reference
  `StateRef("genome", promotion_id)`. Its report names the promotion; the
  promotion row names the apply.
- **Settling.** Turning a finished delivery apply into the promotion's final
  status, and, on success or partial success, moving `active`/`previous` by
  compare-and-swap and marking the revision `promoted`.
- **Partial delivery.** The apply ended `PARTIAL`: some declared entries were
  delivered, some failed (e.g. a Center skill no asset matches). `active`
  still moves, and the `active` ref row is marked **partial**, so a reader
  can tell "the bot holds this revision" from "the bot holds most of it".

## Motivation

- RSI-03 shipped promotion without delivery; `active` cannot move, so the
  first useful outcome on the critical path — versioned bots that can go back
  to any earlier revision — is not reachable.
- Moving `active` before the bot holds the revision makes `active` lie for as
  long as the apply runs, and forever if it fails. Following the apply and
  moving the ref at its end keeps `active` meaning "what the bot holds".
- Captured revisions can name Center and shared-repository skills, but the
  skills materialiser skips both (#3103). So when a revision captured from a
  bot that uses such skills is promoted, those skills are not installed: the
  bot cannot be brought back to that revision in full.

## User Stories

- As a bot owner, I promote a revision of my personal bot and it is
  delivered: identity files, skills, resources, MCP servers and the startup
  script converge to the revision, with no Manifest document written and no
  source fetched.
- As a bot owner, I go back two revisions by promoting an earlier one, and
  the bot is restored — including removing resources and the startup script
  the later revisions added.
- As a bot owner, I read a promotion and see where it is: waiting, applying
  (with the apply id, so I can read the apply report), applied, applied
  partially, or failed, with a one-line reason.
- As a bot owner, after a partial promotion I can see from `active` itself
  that the bot does not hold all of it.
- As an evolution pipeline (an automated actor), my promotion runs with the
  bot owner's permissions; the record still says it was mine.
- As a service-bot owner, I promote a revision to my bot's **draft**, the
  same way a personal bot is promoted.
- As a bot owner whose bot uses a Skill Center skill, a revision captured
  from it restores that skill by name, at whatever version the Center
  publishes now; a newer Center version does not make my bot look drifted.

## Acceptance Criteria

### Promotion lifecycle

- [ ] A promotion resolves the target, compiles, checks drift (automated
      actors only) and asks the applier whether it can take this bot at all
      (`RevisionApplier.preflight`, the seam's existing check; the direct
      applier accepts every bot today, and #3212 will use it to refuse a
      verify/online target); nothing is written before that.
- [ ] It then records the promotion row with status `pending` and starts the
      delivery apply. The apply engine takes its per-bot lock synchronously.
      - Lock held by another apply: the row becomes `apply_failed` ("another
        apply is running on this bot"), no ref moves, and the caller gets
        `409 apply_in_progress`.
      - Lock taken: the row becomes `applying` and carries the apply id; the
        caller gets the row (`201`). Refs have not moved.
      - Any other start failure: the row becomes `apply_failed` naming the
        error type; no ref moves; the caller gets the row.
- [ ] When the delivery apply ends, the promotion settles:
      - `SUCCEEDED` → in one transaction: `active` moves to the revision by
        compare-and-swap against the promotion's `from_revision`, `previous`
        to where `active` was, the revision becomes `promoted`, the `active`
        row is marked complete, the promotion becomes `applied`.
      - `PARTIAL` → the same transaction with `active` marked **partial**;
        the promotion becomes `applied_partial`, its detail naming how many
        entries failed.
      - `FAILED` → no ref moves; the promotion becomes `apply_failed` with
        the report's reason.
      - Compare-and-swap lost (`active` moved while the apply ran) → no ref
        moves; the promotion becomes `apply_failed` ("active moved during the
        apply").
- [ ] Every way an apply ends notifies the promotion (publish/subscribe:
      the apply engine publishes "apply X of kind K ended with outcome O",
      the genome subscribes to kind `genome`): a normal end, a refused
      compile, an engine exception, a worker that could not rebuild the
      apply, and the task queue giving up on the apply task. A publish that
      fails is not lost: the apply task is retried, and a run that finds its
      report already terminal only publishes. Settling twice is a no-op.
      Nothing settles a promotion on read.
- [ ] A worker that dies mid-apply does not end the apply: the apply is a
      durable task, so another worker re-claims it once its lease expires and
      runs it again from the start (compile and apply both again; apply
      converges, so this is safe). The promotion stays `applying` and settles
      when that run ends.
- [ ] The task queue gains an optional per-task-type **give-up hook**, called
      when it gives up on a task: retired at its deadline (in the claim scan
      or on a reschedule that would overshoot it) or failed by its handler.
      The apply task's hook writes the apply's `FAILED` report if no run
      wrote a terminal one, releases its lock and publishes, so an apply
      report is never left `RUNNING` and its promotion always settles. Task
      types without a hook behave as today.
- [ ] `going_back` keeps its meaning (target was promoted before and is not
      the current `active`).
- [ ] **Compile** (revision → desired state) and **apply** (desired state →
      bot) stay separate layers. The genome compiler reads the promotion row
      for its revision and compiles that revision; it checks no promotion
      status and refuses only a promotion that does not exist. The compile
      is pure, so the apply task compiling again on a re-run yields the same
      state; the task queue never re-runs a finished or retired task, and the
      apply lock keeps two promotions of a bot from overlapping. The
      promotion layer never retries anything.
- [ ] An automated actor's delivery apply runs with the bot owner as its
      principal (`actor_id` = owner id) and the actor's audit label
      (`audit_actor`); a user's runs as that user.
- [ ] A promotion of a service bot targets its draft and is delivered the same
      way; a personal bot's as described. (Verify/online: #3212.)
- [ ] `PendingDeliveryApplier` is gone; the bound applier is the direct one
      (the promotion records no applier kind: there is one seam).

### Labels on refs

- [ ] `ac_genome_ref` gains a `labels` column: a JSON list of strings
      (e.g. `["partial"]`), empty by default. The only label today is
      `partial`, set on `active` when a `PARTIAL` apply moved it; every move
      of a ref rewrites its labels (a complete apply leaves `[]`). The genome
      API's ref payload shows `labels`.

### Center and shared-repository skills

- [ ] A revision's Center skill entry is `{"name", "origin": "center"}`:
      `center_version` is removed from the revision schema, the models, the
      examples and the docs. Capture records no version.
- [ ] Delivering a Center skill finds the Center asset by runtime name among
      the Center assets the bot may install; none → the entry fails
      ("no Skill Center skill named …"), more than one → the entry fails
      ("ambiguous"). A market skill not yet imported into this tenant counts
      as none; delivery starts no import.
- [ ] Delivering a shared-repository skill finds the asset by its `git://`
      path; none → the entry fails. No commit is recorded or checked.
- [ ] A found asset becomes a Direct claim on the bot (the same claim
      semantics a local package gets: same-name Set/Default supply detached),
      and is reported `created`/`updated`. Removing an undeclared shared asset
      deactivates it and never deletes it (unchanged).
- [ ] #3103 is closed by this item; nothing is skipped any more.

### Drift after delivery

- [ ] After a `SUCCEEDED` promotion, `GenomeRegistry.drift(bot)` answers
      `None` for a personal bot whose revision covers identity, skills of all
      three origins, resources, MCP and script.

### Unchanged

- [ ] Every existing Manifest, creation, delivery-strategy, task, DI and HTTP
      apply test passes unchanged. The Manifest grammar, the `/config-manifest`
      endpoints, the Manifest's documents and the service-bot rollback feature
      are untouched.

### Done when (singlebox)

- [ ] In singlebox, promoting a captured revision of a personal bot delivers it
      with no fetch and no Manifest write; `drift` then reads the bot as
      holding `active`; promoting the revision two back restores the bot,
      removed resources and script included.

### Docs and hygiene

- [ ] `work-items.md` (+ zh-CN) RSI-03/RSI-04, `01-genome.md` §5.5/§11/§13.3,
      `08-promotion.md` §2.4/§7.1/§8.4/§10/§13 (P-2 recorded as decided, with
      #3212), the contract README and `manifest-v1-compatibility.md`, the
      `bot_genome`, `apply_engine` and `skill_center` (`AGENTS.md`) docs and
      `CONTEXT-MAP.md` say what the code does.
- [ ] No file over 1,000 lines; no formatting churn; `T | None` only for real
      domain absence.

## In Scope

- The direct applier, the promotion lifecycle, the completion
  publish/subscribe in the apply engine, settling.
- The task queue's give-up hook, and the apply task's use of it.
- Renaming the Skill Center's Manifest-named Direct-claim calls to apply
  names (`claim_manifest_skill` → `claim_apply_skill`, …): they are the apply
  engine's calls whichever compiler produced the state.
- Genome DDL edited in place (tables not deployed): promotion status values,
  `apply_id` on the promotion, `labels` on the ref.
- Center/repo skill delivery in the skills materialiser and the Skill Center
  claim path; `center_version` removal from the RSI-02 contract.
- Genome HTTP payloads and error mapping; docs; singlebox verification.

## Out of Scope

- Service-bot verify/online promotion and everything in #3212–#3216
  (stage-scoped refs and capture, the publish-pipeline lock, preserving the
  draft, the pipeline's manual steps).
- Capture of a platform-managed teclaw bot (RSI-03 follow-up).
- Bot-level Center version pinning; repository commit pinning (dropped by the
  owner's decision, not deferred).
- `engine_config` (#2858), `memory` (RSI-05), canary (RSI-16).
- The two open questions left on #3083 (drift over row-less bot-written files;
  the compose collector's `data/` fallback).

## Decisions taken with the issue owner (2026-10-27/28)

1. Personal bots and service-bot drafts are delivered by the apply run;
   service-bot verify/online go through the publish pipeline, and their ref
   moves only when the pipeline completes — tracked in #3212, not here.
2. The promotion row tracks the apply's lifecycle to its end.
3. An automated promotion runs with the owner as the domain principal; the
   real actor stays on the record.
4. A Center skill is found by name, a repo skill by path; ambiguity or absence
   fails the entry.
5. Refs move after the apply, under the apply's lock, which removes the race
   with a concurrent apply.
6. `center_version` comes out of the revision; no repo commit is recorded.
7. A `PARTIAL` apply moves the ref, and `active` carries a partial marker.
8. The marker is a `labels` JSON-list column on the ref (review 2026-10-28):
   `["partial"]`; no `complete` label, since that is the default.
9. Compile and apply are separate layers; the genome compiler checks no
   promotion status (review 2026-10-28).
10. Every termination of an apply publishes; the task queue gets a give-up
    hook so the deadline case publishes too; no read-time settle
    (review 2026-10-28).
11. `RevisionApplier.preflight` stays: it refuses nothing in this item and is
    where #3212 refuses a verify/online target (review 2026-10-28).
12. The Direct-claim calls are apply scope and lose "manifest" from their
    names (review 2026-10-28; the exact names await the owner's OK).

## Decisions taken here, flagged for review

- **The promotion status gains `applied_partial`** to match the ref label.
- **How the promotion learns its apply ended: a callback, not polling.** The
  apply engine already keeps a table from state kind to compiler
  (`"manifest"` → the Manifest compiler, `"genome"` → the genome compiler)
  and picks the compiler from the apply's state reference. This adds a
  second table of the same shape, state kind → *what to call when an apply
  of this kind ends*. The genome registers its settler under `"genome"`; the
  Manifest registers nothing. When the worker finishes an apply it calls the
  entry for that apply's kind, so the engine still knows nothing about
  promotions. The alternative was a background task per promotion that
  re-reads the apply report until it is terminal; it costs a read per
  promotion per poll and is one more thing that can die.
- **The promotion row regains an `apply_id` column.** #3083 removed
  `apply_reference` because it was used to infer what the bot holds; here it
  only says which apply to read for progress. Drift stays observed.
- **`pending` is a new promotion status** for the instant between the row and
  the lock. If the request process dies between the two writes, the row
  stays `pending` with no apply behind it; the bot's next promotion marks
  such a row `apply_failed` before inserting its own (there is no apply to
  publish for it).
- **A Center asset "the bot may install"** is one the Skill Center query
  service resolves for the bot and the acting principal (the same access rule
  as activating it by id).
