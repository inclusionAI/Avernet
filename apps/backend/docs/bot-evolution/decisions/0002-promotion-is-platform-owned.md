# Promotion of evolved bot revisions is platform-owned

Status: proposed (draft decision record; promote to `docs/adr/` on acceptance).

## Decision

Evolution strategies are pluggable, but **promotion is not**. Only the
platform gate can move a bot's `active` genome ref. Strategies may propose
patches and contribute an acceptance policy; bots may submit observations and
draft patches to an inbox. Neither can write to a live bot, its workspace, or
its genome refs.

A candidate is promotable only when the platform floor passes (schema,
locked genes and pins untouched, secret/PII scan, no permission escalation,
no regression on platform-owned regression and safety suites, budget), the
strategy's acceptance policy passes, and the patch's risk tier is approved
(auto for low tiers under owner policy, human review otherwise; tools, script,
and policy changes are locked by default and never auto-promoted).

Evaluation suites, graders, and their configuration live outside the genome
and are read-only to strategies and bots. Proposer inputs never include
holdout, regression, or safety cases.

Design: [`../governance.md`](../governance.md).

## Consequences

- ClawEvolve's tune stage must stop editing the live workspace and instead
  emit patches from a sandbox; pack/restore leaves the evolution flow.
- Any third-party strategy can be enabled without trusting it with
  production write access.
- Bot owners get a review queue and per-bot policy (enabled strategies,
  auto-promote ceiling, budgets).
- Every promotion and rollback is audited with evidence and evaluation links.

## Alternatives

- **Each strategy promotes by its own rules** (today's ClawEvolve accept rule
  plus in-place edits). Rejected: reward hacking and self-grading are the
  dominant failure mode reported for self-improving agents.
- **Always require human approval.** Rejected as default: blocks low-risk
  memory updates; kept available as an owner policy.
