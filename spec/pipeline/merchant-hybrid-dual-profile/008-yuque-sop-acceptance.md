# Merchant hybrid Yuque SOP acceptance

## Scope

- Acceptance source: `https://yuque.antfin.com/xisi.wh/vydvv9/ae0428w330t0nxye`
- Date: 2026-08-11
- Topology under test: three OpenClaw bots plus one Claude Code bot
- Collaboration group: `bcs_grp_09bb59efb5ed454a85cd055aa7f503f7`
- Session: `bcs_grp_09bb59efb5ed454a85cd055aa7f503f7:babd3988`
- State-machine run: `sm-ee6927bd-e34c-41b6-97b5-fd4489f076ce`

Credentials and complete conversation text are intentionally omitted.

## Result

**Overall: FAIL.** The mixed stack and chat transports work, including the real
Claude Code response and HumanInput submission. The generated one-shot graph
does not implement the acceptance document's required review/judge workflow,
and its final node returns `NO_REPLY` instead of the required delivery contract.

## Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| AntChat/Kimi BCS configuration | PASS | BCS startup reports an OpenAI-compatible judge with model `Kimi-K2.6` and the AntChat base URL. The credential value is loaded from ignored `.env.local` through `ANTCHAT_API_KEY` and is not logged. |
| Mixed topology | PASS | Three OpenClaw role bots and one Claude Code platform-data bot reached running status. |
| Multi-bot discovery and chat | PASS | The manager created a worker group; marketing, supply-chain, and platform-data workers returned role-specific responses. The Claude Code provider produced streaming deltas and a terminal response. |
| Private-value boundary | NOT PASSED | The sensitive clarification was entered after the manager had already moved to the worker group, so this run cannot prove private-ledger-only handling. |
| Required worker review graph | FAIL | Runtime graph has three nodes and one participant. It has no marketing, data, or supply-chain review node and no accepted/changes/blocked marker flow. |
| Kimi Judge execution | FAIL | All three graph nodes have `judge=false`; the configured judge was therefore never invoked by this run. |
| Feedback loop | FAIL | Graph is a single acyclic chain with two edges and no up-to-three-round revision path. |
| HumanInput transport | PASS | The active HumanInput node accepted the exact text `接受当前版本` through the frontend and transitioned to the final node. |
| Initial node scheduling | FAIL | `verify_constraints` attempt 0 timed out after 180 seconds while the same manager bot was still completing the parent chat turn; attempt 1 later succeeded. |
| Final delivery contract | FAIL | Run status is `completed`, but run output is `NO_REPLY`. It lacks `DELIVERY_DECISION=ACCEPTED`, the run ID/version, Plan A/B, and the required external-action list. |

## Root-cause summary

1. The local AntChat configuration is healthy; startup recognizes Kimi-K2.6.
2. The manager-generated YAML is semantically incomplete even though schema
   validation accepts it. It reduces the SOP to manager check, HumanInput, and
   manager delivery, so no Worker or LLM judge can run.
3. Launching a state-machine task back to the manager from the manager's still
   active parent response creates a same-bot concurrency window. The first node
   times out and only succeeds after the parent turn finishes and retry starts.
4. The final manager node emits `NO_REPLY`, so a completed runtime status is not
   sufficient evidence of business delivery.

## Verification commands

```bash
bash scripts/test_singlebox_mixed_claude_bots.sh
(cd src/bcs && cargo test -p bcs config_loader::tests::test_real_local_config --lib)
curl -fsS \
  http://127.0.0.1:21000/state-machine-runs/sm-ee6927bd-e34c-41b6-97b5-fd4489f076ce/graph
```

## Follow-up required for a PASS

- Reject or regenerate one-shot definitions that omit the three Worker review
  nodes, the Kimi judge, revision outcomes, and the bounded feedback loop.
- Decouple state-machine launch from the manager's active parent turn, or defer
  the first manager-assigned node until that turn has terminated.
- Enforce the final output contract so `NO_REPLY` cannot complete a delivery
  run that requires acceptance markers and plan contents.
- Repeat the privacy step in the private manager group before publishing only
  the derived public contract to the worker group.
