# 04 — Supply-Agent Compliance

- Parent: [BCN Cloud Orchestration for Personal Agents](README.md)
- Status: Draft for team review

## Problem

The correctness guarantees of Doc 03 hold only where supply agents implement
the BCN A2A profile (durable dedup by idempotency key, atomic
check-and-execute, retention floor, probe semantics). The fleet is
heterogeneous — four in-house integration paths plus external platforms we do
not control — and will stay mixed for a long time (plugin versions on user
machines upgrade slowly; external adoption is negotiated). We support **both
in-house and external providers**.

Framing that makes this tractable: for every in-house category the "agent" is
really *engine + our adapter*, and the profile lives **entirely in the
adapter**. The engines themselves (OpenClaw, Claude Code, Codex, DeepSeek
Harness, …) never change.

## The categories

![Supply-agent category map: dial-in adapters we own (bcs-bridge, the OpenClaw and DeepSeek Harness channel plugins, scripted bots) and dial-out providers (the in-house engine adapter and external platforms) around BCN's dispatch gate. The idempotency profile lives in each adapter; external platforms earn the side-effecting tier through the conformance suite and run read-only until then.](assets/supply-agent-categories.svg)

| # | Category | Members | Transport | Adapter ownership | Durable storage today | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | OpenClaw plugin bots | singlebox bots, agency-agent teams, local OpenClaw users | dial-in WS | ours — `plugins/openclaw-channel-bcn` (TS) | JSON files under the bot data dir | low–medium |
| A2 | DeepSeek Harness bots | DSH community bots | dial-in WS | ours — `plugins/deepseek-harness-channel-bcn` (Node) | file-based session identity | low–medium |
| B | Bridge-wrapped CLI engines | Claude Code, Codex, QwenWork, future engines | dial-in WS / gateway | ours — `tools/bcs-bridge` (Rust) | **SQLite** runs/sessions, background finalization | **low** |
| C1 | In-house platform provider | engine adapter hosting backend-created bots (OpenClaw/Moltis; DWS hosting) | dial-out webhook+SSE | ours — `engine/adapter` (Python) | hosting state stores, recovery store, outbox | low–medium |
| C2 | External provider platforms | third parties per `docs/bot-provider-integration.md` | dial-out webhook+SSE | **theirs** — contract only | unknown | **contractual, unbounded timeline** |
| D | Scripted/utility bots | rule bot, demo/test bots, test fakes | various | ours | n/a | trivial |

## Per-category plan

- **B — bridge, first.** Already has the substrate: durable SQLite run store
  and a hold-results-until-delivered finalization path. Compliance = one key
  table + atomic claim-on-execute + window GC, in one codebase, covering
  every engine behind the bridge now and later.
- **C1 — engine adapter, highest leverage.** It is the component mid-migration
  to A2A, and that design already contains the `retryOnly` judgment — built
  process-local. Compliance here is largely *landing the in-flight design
  plus one change*: move the window into the durable store it already has.
  This adapter hosts the platform-created bots most likely to serve as the
  launch experts.
- **A1/A2 — the two channel plugins.** Same small feature twice (a durable
  key store with an atomic claim — JSON files lack check-and-set, so SQLite
  or lockfile discipline), in code we own. The real cost is **fleet
  rollout**: these run inside users' OpenClaw/DSH installs, so old versions
  coexist with new for a long time — which is why negotiation/enforcement
  below is mandatory, not optional.
- **C2 — external platforms.** No code on our side; instead: publish the
  profile spec, ship a runnable conformance suite, and tier capability (next
  section). Adoption is negotiated and unbounded in time — the one genuinely
  expensive category, contained rather than solved.
- **D** — trivial; plus the shared conformance fixtures land in
  `bcs-test-support`.

## Capability tiering and enforcement (BCN-side, mandatory)

The mixed fleet is permanent, so BCN must *know* and *enforce*, per bot:

1. **Declaration** — the agent (adapter/platform) declares profile support:
   the profile's extension URI in its AgentCard, or an equivalent capability
   flag at registration/handshake for non-A2A channels.
2. **Verification** — declaration alone is a claim. In-house adapters are
   covered by our CI against the conformance suite; external platforms run
   the same suite to be marked verified. The suite must cover the hard races:
   probe vs. in-flight first delivery, window expiry, duplicate key with
   conflicting payload.
3. **Enforcement** — the dispatch engine gates by side-effect class × tier:

| Agent tier | `read_only` / `idempotent` steps | `external_side_effect` steps |
| --- | --- | --- |
| Baseline (any A2A/vanilla agent) | ✅ dispatch | ❌ refused — step fails placement, surfaced to the workflow |
| Profile-verified | ✅ | ✅ |

Non-compliant agents are therefore *scoped, not blocked* — they participate
fully in search/summarize/plan steps and unlock transactional steps when they
pass conformance. This is also the honest answer to "what about agents we
don't control": they cannot be made safe by our engineering, so they are not
offered unsafe work.

## Costs and risks

- Four in-house implementations: B low; C1 low–medium (mostly landing
  existing design work); A1/A2 low–medium each plus a long rollout tail.
- One BCN-side feature (declare/verify/enforce) — budgeted as part of the
  cloud track itself, since it is what keeps a mixed fleet honest.
- One profile spec + conformance suite, maintained and versioned by us.
- **Product-timeline risk**: if launch transactional experts (booking,
  payment) come from C2 vendors, "safe autonomous booking via external
  agents" is gated on *their* conformance adoption, not our engineering.
  In-house launch experts (C1/B) defer this risk; it does not remove it.

## Rejected alternatives

- **Require the profile fleet-wide before launch**: blocks on the slowest
  external vendor and the plugin upgrade tail; rejected for tiering.
- **Implement dedup only on the BCN side**: cannot work — the window that
  matters is at the *executor* of the side effect; BCN-side dedup covers
  BCN's inbound traffic only (Doc 03).
- **Trust declaration without conformance**: a mis-declared agent converts a
  failover into a double purchase; verification is the cheap insurance.
