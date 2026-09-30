# Consolidate per-stage engine_overrides composition into one delivery seam

## Summary
Before a build artifact is handed to the BaaS layer for delivery, each publish
stage must have its per-stage `engine_overrides` composed onto it: the
`engine_ext.stage` field restamped to the target stage, and that stage's DingTalk
channel config overlaid. Today this composition is **repeated at every delivery
call site** — release, restart, rollback, and eval each remember (or forget) to
do it independently. This has already produced the same class of bug twice: a
delivery path that omits the step silently ships the raw artifact, dropping the
stage's channels. This change moves the composition to a **single seam** that
every delivery path goes through, so a call site can no longer skip it. It is a
pure refactor of the composing paths, with the one behavior change that the
rollback path — which currently ships the raw artifact — begins composing like
the others (folding in the fix tracked separately as #168).

## Motivation
The per-stage channel overlay (`engine_overrides.channels`, carrying e.g. the
DingTalk `card_template_id`) is not part of the build artifact — it rides in an
overlay applied at delivery time. Because applying it is **opt-in per call site**,
the four delivery paths compose inconsistently:

- Release (first-release / upgrade) — composes from a **live** re-fetch of the
  stage's channels.
- Restart — composes from the **stored** per-stage slot.
- Eval — composes from a **live** re-fetch.
- Rollback — **does not compose**; it ships the stored artifact raw.

This "single-config-slot hazard" has bitten twice: restart before it was fixed,
and rollback (the subject of #168). Each occurrence is a delivery path that
forgot the composition and therefore delivered the wrong (or missing) channel
config to the container. The root problem is structural: nothing forces a
delivery path to compose, so every new or existing path is one oversight away
from the same bug. The fix is to make composition the only way to obtain a
deliverable artifact — a seam every path must pass through — rather than a step
each path is trusted to remember.

## User Stories
- As a backend engineer adding or changing a delivery path, I want it to be
  impossible to hand the BaaS layer a raw, un-composed artifact by accident, so
  that the class of bug that hit restart and rollback cannot recur.
- As a backend engineer, I want the one legitimate per-path difference — whether
  a path composes from the live channel table or from the stored per-stage slot —
  to be an explicit, named choice at the call site, not logic re-derived and
  re-typed in each path.
- As a user who rolls back a bot after changing its DingTalk card template, I want
  the rollback to restore the target version's channel config (including the card
  id), so that rolling back actually reverts the channel state.
- As a maintainer, I want the composition logic (restamp + overlay + the
  live-vs-stored source choice) to live in one place, so that a change to how
  artifacts are composed is made once and applies to every delivery path.

## Acceptance Criteria
- [ ] All four delivery paths (release first/upgrade, restart, rollback, eval)
      obtain their delivery artifact from the single composition seam; none reads
      the raw stored artifact and passes it to the BaaS layer directly.
- [ ] It is not possible for publish-flow code to pass a raw, un-composed artifact
      to the BaaS delivery boundary — the boundary accepts only a composed
      delivery artifact produced by the seam.
- [ ] The live-vs-stored overrides choice is explicit per path: release and eval
      compose from a live channel re-fetch; restart and rollback compose from the
      stored per-stage slot. This matches today's behavior for release, restart,
      and eval.
- [ ] **(Intended change) Rollback composes.** Rollback now restamps the online
      stage and overlays the target version's stored online channel overrides,
      instead of shipping the raw artifact. After a rollback, the container's
      DingTalk channel config (including `card_template_id`) matches what the
      target version had when it was online. This folds in the fix tracked as
      #168.
- [ ] The ARCA mount path (no stored artifact, `migration_path` only) remains a
      no-op through the seam — nothing is fetched, restamped, or overlaid, and the
      artifact delivered stays absent as today.
- [ ] Pre-feature records with no stored per-stage overrides continue to deliver
      the base artifact unchanged (restamp only, no overlay), on the paths that
      compose from the stored slot.
- [ ] The stored record shape is unchanged: the `ext` JSON keys
      (`config_artifact`, `engine_overrides_by_stage`, …) written and read are
      byte-for-byte compatible with existing records; no data migration.
- [ ] Existing behavior is preserved and verified: the per-stage channel
      end-to-end suite (`test_publish_per_stage_channels.py`) and the publish-flow
      unit suite stay green (updated only where the delivery boundary's shape
      changed), and the rollback path gains unit coverage for the newly-composed
      behavior (stored overrides delivered; pre-feature record → no overlay).

## In Scope
- Introducing a single delivery-composition seam that every publish delivery path
  routes through, with the live-vs-stored overrides choice made explicit per path.
- Making the BaaS delivery boundary accept only a composed delivery artifact, so a
  raw artifact cannot be passed from flow code.
- Routing all four delivery paths (release, restart, rollback, eval) through the
  seam, and removing the per-path composition copies.
- Folding the rollback composition (#168) into the shared seam rather than as a
  separate inline patch.
- Removing now-dead composition helpers left behind on the flow facade.
- Updating the unit tests whose expectations reference the delivery boundary's
  shape, and adding rollback coverage for the newly-composed behavior.

## Out of Scope
- Any change to how per-stage channel overrides are *read* or *stored* (the
  channel reader, the `engine_overrides_by_stage` persistence, the restamp/overlay
  primitives) beyond relocating/wrapping them behind the seam.
- Any change to externally observable behavior other than the intended rollback
  composition — endpoints, state-machine semantics, messages, and stored record
  shape are preserved.
- The channel table itself is user config, not versioned by publish; reverting the
  channel row on rollback stays out of scope (only the rollback *delivery* is
  fixed, matching #168).
- Refactoring the BaaS/deploy collaborators beyond the minimal surface needed to
  type the delivery boundary.

## Open Questions
- **Relationship to #168.** *(Resolved — approved in issue #173.)* #168's rollback
  fix is not yet merged to `dev`; routing rollback through the shared seam subsumes
  it, so this change composes rollback and closes both. The one rollback unit test
  that asserted raw pass-through is reworked to assert composed delivery.
- **Boundary enforcement strength.** *(Resolved — approved in issue #173.)* The
  type is pushed all the way to the BaaS delivery boundary (it accepts only the
  composed delivery artifact), rather than relying on a call-site convention, so a
  raw artifact is un-passable from flow code.
