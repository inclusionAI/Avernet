# Online Bot Supersede Cleanup

## Summary
When an online service-bot deployment has to stand up a **new** BaaS bot — because
a release failed and is retried, because the bot is re-published, or because a
restart finds its target gone — the **previous** BaaS bot is left registered and
(often) running. Over time a single logical service bot accumulates several
"online" BaaS bots: dirty data, and wasted provider compute. This feature makes
the deploy pipeline decide *reuse-vs-recreate* the same way everywhere, and
guarantees that whenever a new online bot replaces an old one, the old one is
retired — so exactly one online BaaS bot exists per (service bot, stage).

## Motivation
The publish pipeline has several places that can create a fresh online BaaS bot:
the online first release, its retry, an online re-publish, the online upgrade's
"target vanished" fallback, and the restart's "target vanished" recreate. Each
path decides independently whether to reuse the existing bot or create a new one,
and the rules disagree. Worse, **none** of the "create a new one" paths remove
the bot they just superseded.

Two concrete failure modes were confirmed in production:

- A **failed online first release** that is retried creates a brand-new bot and
  abandons the failed attempt's bot as a live `FAILED` record — even though, on
  platforms that rebuild in place, the failed attempt's bot could simply have
  been recovered. The abandoned bot never gets cleaned up by any later step.
- A recent change forced the online release down the "create new" path for a
  previous bot in `FAILED`/`STOPPED`/`STOPPING` state, to work around a
  re-publish-after-offline bug. That masked the missing cleanup: the previous
  online bot is left behind whenever this fires.

The result is a growing population of orphaned online bots per service bot that
looks like "duplicate/dirty data" and drives avoidable sandbox cost. Because the
decision logic is duplicated and the cleanup is missing everywhere, this must be
fixed as one shared behavior rather than patched path-by-path.

Note: multiple online bots that belong to **different callers** of an expert-chat
service bot are a separate, intended feature (one container per caller) and are
explicitly *not* the orphans this feature addresses.

## User Stories
- As an operator retrying a failed online publish, I want the retry to recover
  the existing bot where the platform allows, or replace it cleanly, so I never
  end up with a leftover failed bot alongside the new one.
- As an operator re-publishing an online service bot, I want the previous online
  bot to be gone once the new release is live, so there is only ever one online
  bot for my service.
- As an operator restarting an online bot whose underlying instance has vanished,
  I want the system to bring back a single healthy bot, never a second one
  alongside a stale record.
- As an engineer, I want the reuse-vs-recreate decision to be identical across
  release, retry, re-publish, upgrade-fallback, and restart, so the same
  guarantee (and any future fix) applies to all of them.
- As a platform owner, I want the pipeline to stop generating orphaned online
  bots, so per-service sandbox usage reflects one live instance, not a pile of
  abandoned ones.

## Acceptance Criteria
- [ ] After any online deploy operation (first release, retry, re-publish,
      upgrade, restart) completes successfully, there is **at most one live
      online BaaS bot** per (service bot, stage). No superseded bot is left
      registered or running.
- [ ] Retrying a **failed online first release** results in a single live online
      bot: the failed attempt's bot is either recovered in place or removed —
      never left behind as a separate live record.
- [ ] Re-publishing an online service bot whose previous online bot is
      `FAILED`/`STOPPED`/`STOPPING` results in a single live online bot; the
      previous one is retired.
- [ ] Restarting an online bot whose target instance has vanished results in a
      single live online bot; no second bot is created alongside a stale one.
- [ ] The reuse-vs-recreate decision is driven by the **current bot's actual
      state on the BaaS side** and is the **same** regardless of which operation
      triggered it.
- [ ] When the existing bot genuinely cannot be reused, the decision to recreate
      distinguishes "the bot record is already gone" (nothing to clean up) from
      "the bot record still lingers" (must be removed before/while creating the
      replacement), so a lingering record never becomes an orphan.
- [ ] Reuse-in-place, when it succeeds, keeps the same bot identity (no new bot
      is created and none is orphaned).
- [ ] The operation is crash-safe: a crash/redelivery during an online deploy
      does not produce a duplicate live bot, and does not skip the cleanup of a
      superseded bot.
- [ ] Expert-chat per-caller containers are unaffected — they are not treated as
      superseded bots and are never retired by this logic.

## Out of Scope / Non-Goals
- **Expert-chat caller-instance lifecycle** (per-caller containers, their reuse,
  TTL, or idle reclaim). Those are legitimate per-caller bots and a separate
  concern.
- **Retroactive cleanup of already-orphaned bots** currently live in production.
  This feature prevents *new* orphans; sweeping the existing backlog is a
  separate operational task (a one-off reconciliation), not part of this code
  change.
- **Switching the restart flow to the BaaS-native restart verb.** Restart
  continues to re-deploy via the update/create path; only its superseded-bot
  handling changes.
- Changing behavior for non-online stages (verify) beyond what naturally follows
  from sharing the deploy shape.

## Open Questions
- For the "bot record lingers but has no devices" case (a record present with
  zero device rows): retiring it removes a stale row with no running container
  and no compute cost. Confirm whether this cleanup is in scope now or left as a
  cosmetic follow-up.
