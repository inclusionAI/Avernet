# Online-Release Retry Regression Fix

## Summary
Retrying a failed online publish can send the record down the wrong recovery
path, so the retry either re-deploys the wrong thing or fails to re-run the work
it needed to. This fixes retry so an online-stage retry always re-drives the
normal online-release process (which itself decides whether to create a new bot
or upgrade the existing one), and clarifies the single remaining consumer of the
"is this release live?" question so it can never be misused again.

## Motivation
PR #341 correctly fixed a stranded-publish bug at the online-release **gate** (a
rolled-back-then-re-promoted record no longer skips its release). But the same
"is the online release recorded?" question is also consulted by the **retry**
path to choose between a BaaS restart and re-running the release. Those are two
different questions. After #341 the retry decision now turns on whether this
record's release is still the *live deployment on the shared bot* — the wrong
axis for "does the release work still need to run" — so a retry can pick a BaaS
restart that re-deploys against a stale binding, or otherwise take a recovery
path that doesn't match what actually failed. Operators hitting a transient
online-publish failure then get an unreliable retry.

A second, latent problem sits next to it: the online first-release, online
upgrade, and restart operations are near-identical in shape but each
re-implements the middle by hand, and the restart path's "target bot vanished"
recovery is documented as *not* crash-idempotent (it can leave a second orphan
bot). Fixing retry is the right moment to give these operations one shared,
correct shape.

## User Stories
- As an operator, when an online publish fails transiently, I want **Retry** to
  reliably resume the publish — creating the bot if it was never created, or
  updating it if it already exists — so I don't have to reason about which
  internal recovery path fires.
- As an operator, when I **Restart** a live online bot, I want it to always
  re-deploy through BaaS, even though its release is the current live
  deployment, so restart never silently no-ops.
- As an engineer maintaining the publish pipeline, I want the retry recovery
  decision to be simple and self-explanatory, and the "is this release live?"
  check to have exactly one, clearly-named purpose, so this class of regression
  can't recur.
- As an engineer, I want the three deploy-style operations to share one
  crash-safe shape, so a fix or guarantee added to one applies to all.

## Acceptance Criteria
- [ ] Retrying a failed publish whose pre-failure stage was the online stage
      always re-drives the online-release process; it never chooses a BaaS
      restart based on whether the release was already recorded/live.
- [ ] An online-stage retry whose release never landed re-runs the release work
      and reaches a live deployment (creating a new bot or upgrading the existing
      one as appropriate), without creating a duplicate bot.
- [ ] An online-stage retry whose release **did** land and is still live does not
      issue a redundant re-deploy; it resumes by waiting for/settling the
      existing deployment.
- [ ] An online-stage retry after the deploy was issued but its BaaS workflow
      ultimately **failed** re-issues the deploy as a fresh attempt — the failed
      deploy is never mistaken for a live one, so the retry can never skip the
      work and strand the record in a failure loop.
- [ ] The system's record of what is deployed reflects observed deploy
      failures: a deploy whose workflow failed does not count as the live
      deployment, and does not supersede a genuinely live earlier release on
      the same bot.
- [ ] Retrying a failed publish whose pre-failure stage was the verify stage, or
      whose pre-failure state was a fully-published (live) record, still uses the
      BaaS restart path — unchanged from today.
- [ ] A restart of a live online bot always re-deploys through BaaS; the
      "release is already the current live deployment" condition never causes a
      restart to skip the BaaS call.
- [ ] The online-release **gate** still skips re-issuing the release only when
      this record's release is the current live deployment on its bot (the #341
      behavior for the rolled-back-then-re-promoted record is preserved).
- [ ] The predicate that answers "is this record's online release the current
      live deployment?" has exactly one caller (the gate) and a name that states
      that meaning.
- [ ] When a restart's target bot no longer exists, recovery creates a fresh bot
      without risking a second orphan bot on a crash-resume — matching the
      guarantee the upgrade path already provides.
- [ ] The online first-release, online upgrade, and restart operations run
      through one shared crash-safe operation shape; existing crash-window
      guarantees for each are preserved.
- [ ] No change to the verify publish flow's behavior.
- [ ] There is test coverage for **cross-publish-boundary** scenarios — flows
      that span more than one publish record and more than one operation on a
      shared online bot — including at minimum: an upgrade chain across
      successive publish records; a rollback that demotes a record and
      re-deploys the previous version, followed by re-promoting the demoted
      record; a retry that interleaves with a later publish's deploy on the
      same bot; a failed deploy retried to success; and restart flows
      (including target-bot-gone recovery). Each asserts the record reaches
      the correct live deployment and that no duplicate/orphan bot is created.
- [ ] These cross-boundary scenarios run against the **production code path**
      end-to-end — driven through the public publish operations with only the
      system boundaries (the deployment platform, object storage, artifact
      build I/O) replaced by local in-memory implementations — so the tests
      exercise the real orchestration, persistence, and recovery logic rather
      than isolated units.

## In Scope
- The retry recovery decision for the online stage.
- Renaming the "is the online release live?" predicate and reducing it to its
  single (gate) consumer.
- Guaranteeing restart always re-deploys via BaaS (the live-deployment check
  stays out of the restart path).
- Recording observed deploy failure in the operation ledger, so "is this
  release live?" answers correctly after a failed deploy (the flaw that made
  retry able to skip necessary work).
- Extracting the shared deploy operation shape across online first-release,
  online upgrade, and restart.
- Fixing the restart "target bot vanished" recovery to be crash-idempotent, the
  same way the upgrade path's fallback is.
- New test coverage for cross-publish-boundary scenarios (multi-record,
  multi-operation flows on a shared online bot) that today's endpoint tests
  don't reach.

## Out of Scope
- **The verify publish flow is not touched.** Verify-stage retries continue to
  use restart; making verify and online recovery symmetric (running the verify
  release "within" its wait state with its own live-deployment gate) is deferred
  to a follow-up.
- Any change to the BaaS-side deploy/restart APIs or to approval semantics.
- Any change to how progress polling drives a record to its terminal state,
  beyond what the retry re-routing implies.
- Rollback, scale, offline/destroy, and eval operations, except insofar as they
  already share the deploy operation shape being extracted.

## Open Questions
- None outstanding — scope was settled with the user (Option B: online-only
  now, verify symmetry deferred). Merge target for the change is `REL20260723`.
