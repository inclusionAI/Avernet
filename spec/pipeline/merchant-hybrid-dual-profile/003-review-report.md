# Merchant Hybrid Dual-Profile — Focused Review

## Result: PASS

The implementation is opt-in and keeps the existing OpenClaw profile and
legacy 5+3 Claude JSON topology separate. The public command requires all
three merchant profile arguments, preventing an accidental partial topology.

## Review findings resolved

1. **NormalCC appeared started but could not answer.** The profile path was
   not selecting BaaS's established `mixed-claude-code` overlay. BaaS therefore
   used its local stub binding plugin and rejected `chat.send`. The overlay is
   now enabled for either supported Claude input form, with a shell regression
   asserting the non-secret overlay and downlink-token presence.
2. **Current Provider card has a distinct UI name.** The Provider lifecycle
   intentionally marks active cards with `（当前）`; the live regression now
   uses that identity, avoiding a false default-driver route from a stale base
   name mention.
3. **Profile data must not become workspace data.** The relay receives a
   one-time resolved system-prompt prefix. No `CLAUDE.md` is copied into the
   Claude workspace, and the resolver logs only metadata.

## Remaining operational notes

- Historical Provider cards created by pre-cleanup versions are intentionally
  not deleted when their old admin credential is unavailable. They do not
  share the current lifecycle state; select the `（当前）` card in the UI.
- The live acceptance group is intentionally retained as local BCS test data,
  consistent with the existing mixed-message probes. It contains only a
  read-only synthetic metric task.
- Existing Pydantic deprecation warnings occurred in the focused Backend test;
  they are unrelated to this change.
