# Caller-enabled targets force managed admission under a disabled delivery policy

## Problem

Production incident (2026-11-11, env `prod`): Human group speech routed to a
caller-enabled master failed delivery on every message with
`CALLER_EXECUTION_UNAVAILABLE`, surfacing to users as
「消息投递失败，请稍后重试。」.

Root cause chain (all steps log/code verified):

1. The caller-protected dispatch path exists ONLY on managed queue admission
   rows: `prepare_group_admission_with_message` selects targets by delivery
   policy (`manages_group` = `flow_enabled.group && bot.mode == Enforce`) or
   as pending context carriers; `DeliveryPolicy::default()` is fully Off, so
   a deployment that never enabled the policy delivers everything inline.
2. The web-send caller seam (`admit_caller_message`: park identity + mark
   `caller_required`) is mounted under `prepared_command = Some` only
   (`group_flow/web_send.rs` admission match), so with a disabled policy the
   seam is unreachable.
3. Inline delivery reaches `CallerConfigurationDeliveryGate`, whose frozen
   P1 contract refuses every ordinary content dispatch to a caller-enabled
   bot — correctly, but in this composition there was no alternative path:
   the gate refusal was the WHOLE delivery. The bot became unreachable.
4. The BCN registration/caller-sync flow writes `$.caller.enabled=true` for
   every new user agent, so each new agent recreated the dead end — a per-bot
   `bots.<bot_id>.mode="enforce"` policy patch was needed per affected bot.

The `pre` environment worked only because its policy row predates the
incident with `defaults.mode="enforce"` + `flow_enabled.group=true`.

## Solution

Invert the invariant: a caller-enabled routed target is never ordinary. The
web-send queue admission force-selects any target present in the message's
`caller_admission_targets` scan (the same per-environment batch read that
already arms the speaking-lease fence and the relay capture), regardless of
the delivery policy:

- `queued_admission::prepare_group_admission_with_message` gains the scan as
  a parameter and a third selection branch (after the policy handle and the
  pending-context-carrier case) with limits from the policy's per-bot values
  (mode-independent) and a queue-scale fallback (20) when no live policy is
  composed.
- `group_flow/web_send.rs` passes its already-computed `caller_admission_targets`
  into the prepare call — zero additional configuration reads.
- The selection ordering is unchanged for existing deployments: targets a
  policy already manages keep their limits and drain semantics; the forced
  branch only claims what would otherwise fall to inline and die at the gate.

Security posture is unchanged, not relaxed:

- The gate stays mounted on every `BotDeliveryPort` as the closed-world guard
  for transports that bypass the seam (bot relays, callbacks, legacy clients).
- Bot speech is untouched: `caller_admission_targets` is computed only for
  Human-authenticated requests, so bot→caller-bot collaboration keeps the
  relay capture + trusted task-assign design.
- Human speech keeps every existing guard: speaking lease, ingress presence
  and principal binding, `caller_required` marking, protected dispatch.

Operational effect: deployments with fully disabled policies (prod's shape)
are deliverable to caller-enabled bots with no per-bot policy patching, and
new agents registered with caller enabled become reachable automatically.

## Validation

- `cargo nextest run -p bcs-message-flow` — 894/894, including the new
  contract `caller_inline_policy_off_contract`:
  - `policy_off_speech_to_a_caller_target_is_force_admitted_as_a_managed_row`
    (red before the fix: the row never existed; green after: queued
    `caller_required` row, parked identity claimable once, no ordinary
    direct delivery to the target)
  - `policy_off_speech_to_plain_targets_keeps_the_ordinary_inline_course`
    (the forced branch never widens the queue to plain targets)
- `cargo nextest run -p bcs` — 854/854 (composition/wiring suites).
- `cargo nextest run -p bcs-bot` — 299/299 (gate contracts unchanged).
- Pre-existing dev-baseline breakage in `bcs-edge-permission*` /
  `bcs-app-register` / `bcs-fuse-client` is unrelated (verified by running
  the same `cargo check --workspace --tests` on a clean dev tree).

## Compatibility and risk

- Behavior change is scoped to inline-default deployments routing Human
  speech to caller-enabled targets: those messages change from "delivered
  inline then refused by the gate" (always failed) to "admitted as a managed
  caller_required row". No deployment where deliveries worked can regress:
  a policy-managed target never reaches the new branch.
- Queued semantics (max_queued / min_send_interval_ms / run budget) apply to
  these rows; the limits come from the same per-bot policy values that
  Enforce would use.
- Speech without a canonical session fails with the queue admission error
  (`queue admission requires the actual canonical Session`) instead of the
  downstream gate refusal — a clearer error, but a different message string
  for clients that matched the old failure text.
