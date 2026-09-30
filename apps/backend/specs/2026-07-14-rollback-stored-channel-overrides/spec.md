# Spec: Rollback restores the target version's DingTalk channel overrides

**Issue:** inclusionAI/Avernet#168 · **Plan approved:** [issue comment](https://github.com/inclusionAI/Avernet/issues/168#issuecomment-4968630365) → ["Looks good. Let's fix it."](https://github.com/inclusionAI/Avernet/issues/168#issuecomment-4968877299)

## Problem (WHAT is broken)

Change the DingTalk channel's card template id (card A → card B), publish the bot
(V2 delivers card B), then roll back to the previous version: the bot keeps using
card B. The DingTalk channel config — including `card_template_id` — is not part
of the build artifact; it rides in the per-stage `engine_overrides` overlay. The
rollback delivery ships the target's stored artifact raw, so the container's
channel state is never restored to what the target version had when it was online.

## Why it matters

Rollback's contract is "put the container back to the target version's online
state". Channel config silently surviving a rollback breaks that contract in a
user-visible way (wrong card renders in DingTalk) with no error anywhere.

## Desired behavior

- After a rollback, the container's DingTalk channel config (incl.
  `card_template_id`) matches what the **target version** had when it was online —
  i.e. the per-stage overrides persisted at that version's online promotion.
- The rollback must **not** re-fetch live channel config: the live table holds the
  post-change state (card B), which is exactly what is being rolled away from.
- The channel **table** itself is user config, not versioned by publish: it still
  holds card B after rollback, and the next publish correctly delivers B again.
  Only the rollback *delivery* is in scope.

## Non-goals / unchanged behavior

- Release and restart delivery behavior unchanged.
- Pre-feature publish records (no stored per-stage overrides) unchanged: raw
  artifact delivered as before.
- ARCA mount path (no `config_artifact`) unaffected.
- Consolidating the per-path override-composition logic is explicitly deferred to
  follow-up issue inclusionAI/Avernet#173 (assigned to totalfrank).

## Acceptance criteria

1. Rollback delivers the target's **stored** online `engine_overrides` (unit +
   endpoint regression tests, red on unfixed HEAD).
2. No live channel re-fetch on the rollback path (reader not called).
3. Backward compat: target without stored overrides → artifact delivered unchanged.
4. Full `tests/community` suite green.
