# RSI-04: Direct delivery of a compiled revision

> Work item RSI-04 (`apps/backend/docs/bot-evolution/work-items.md`). Stands on
> RSI-03 (`specs/2026-10-19-bot-genome-registry/`, #2907) and the apply-engine
> desired-state refactor (`specs/2026-10-23-apply-engine-desired-state/`,
> #3083); their revision notes are settled decisions. Scope and the decisions
> below were agreed with the issue owner on 2026-10-27/28; service-bot
> verify/online promotion was split out as #3212 (sub-issues #3213–#3216).

## Summary

Promotion today refuses every delivery (`PendingDeliveryApplier`), so
`active` never moves. This item makes a promotion **deliver**: it starts an
apply run whose state reference names the promotion, the genome compiler
turns that back into the revision's desired state, the apply engine writes it
to the bot, and the promotion row follows the apply through to its end. The
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
  skills materialiser skips both (#3103): a captured bot cannot be restored.

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
      actors only) and preflights as today; nothing is written before that.
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
- [ ] Settling happens on every terminal path of the apply: normal end, a
      refused compile, an engine exception, a worker that could not rebuild
      the apply. A delivery apply abandoned by a dead worker (read back as
      `FAILED`) settles the next time the promotion is read. Settling twice
      is a no-op.
- [ ] `going_back` keeps its meaning (target was promoted before and is not
      the current `active`).
- [ ] The genome compiler delivers a promotion only while it is `pending` or
      `applying` and `active` still points where the promotion expected; a
      settled promotion is refused (`CompileRefused`), so a retried or
      redelivered apply never re-applies a finished promotion.
- [ ] An automated actor's delivery apply runs with the bot owner as its
      principal (`actor_id` = owner id) and the actor's audit label
      (`audit_actor`); a user's runs as that user.
- [ ] A promotion of a service bot targets its draft and is delivered the same
      way; a personal bot's as described. (Verify/online: #3212.)
- [ ] `PendingDeliveryApplier` is gone; the bound applier is the direct one
      (`apply_kind: direct`).

### Partial marker on `active`

- [ ] `ac_genome_ref` gains a nullable `delivery` column: `complete` or
      `partial` on the `active` row, set whenever a promotion moves it; null
      on every other ref. The ref payload of the genome API shows it on
      `active`.

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

- The direct applier, the promotion lifecycle, the completion seam in the
  apply engine, settling, the read-time settle.
- Genome DDL edited in place (tables not deployed): promotion status values,
  `apply_id` on the promotion, `delivery` on the ref.
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

## Decisions taken here, flagged for review

- **The partial marker is a `delivery` column on the ref row**, meaningful
  only on `active`, rather than a second ref name. The promotion status gains
  `applied_partial` to match.
- **Settling is a completion hook in the apply engine**, looked up by state
  kind like the compilers, with a read-time settle as the safety net, rather
  than a polling task per promotion.
- **The promotion row regains an `apply_id` column.** #3083 removed
  `apply_reference` because it was used to infer what the bot holds; here it
  only says which apply to read for progress. Drift stays observed.
- **`pending` is a new promotion status** for the instant between the row and
  the lock; a row never stays there unless the process dies between the two
  writes, and such a row settles as `apply_failed` when read.
- **A Center asset "the bot may install"** is one the Skill Center query
  service resolves for the bot and the acting principal (the same access rule
  as activating it by id).
</content>
</invoke>
