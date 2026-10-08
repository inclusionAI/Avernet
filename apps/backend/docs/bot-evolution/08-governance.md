# Governance — gating, safety, rollout

> 中文版：[08-governance.zh-CN.md](08-governance.zh-CN.md)

> Status: DRAFT. Proposed decision:
> [DR-2](decisions/0002-promotion-is-platform-owned.md).

Self-improvement that the improver can grade is self-deception at scale. The
literature is consistent on this (DGM removed its own hallucination markers;
self-graded Hermes loops are lenient; LLM-authored skills often add nothing
without eval-guided revision; harness-evolution gains often vanish against a
budget-matched baseline). This document lists the rules that make the
platform trustworthy regardless of which strategy runs.

## 1. Separation of powers

| Power | Holder | Never held by |
| --- | --- | --- |
| Propose changes | Strategies (by submitting candidates), subject bots (inbox) | — |
| Choose which strategies run on a bot, what they may change, and how strictly they are verified | **Owner / tenant admin** (bindings) | Strategies, bots |
| Define what must never get worse | **Platform + owner** (regression, safety, holdout suites; platform floor) | Strategies, bots |
| Run evaluations | **Platform** (Verification Service, with verifier-owned executors and graders) | Strategies |
| Change the verifier (suites, graders, profiles, protocols, thresholds) | **Humans**, through reviewed changes ([03-verification.md §7](03-verification.md#7-verifier-integrity)) | Any automated loop, including level 3 |
| Adopt a new mechanism (level 3) | Platform mechanism gate + human approval | Meta-strategies |
| Decide promotion | **Platform gate** + owner/reviewer per risk tier | Strategies, bots |
| Change locked genes / policy | Owner, tenant admin | Strategies, bots |

## 2. The gate

A candidate is promotable only if **all** of these pass:

1. **Static floor** — schema valid; base matches; no locked gene or pinned
   item touched; no secrets/PII introduced (same scanner rules as the
   pre-push hook); size and rewrite thresholds; no new outbound URLs or MCP
   servers unless the gene is unlocked.
2. **Regression floor** — on the bot's `regression` and `safety` suites the
   candidate is not worse than the parent beyond a tolerance (default: zero
   newly failing cases on `safety`, ≤ 1 on `regression` with review).
3. **Verification verdict** — `accept` on `validation` under the binding's
   verification profile ([03-verification.md §4](03-verification.md#4-bot-verification-protocol-level-2)).
4. **Holdout check** — periodically (not every iteration, to avoid leaking
   holdout through selection) the promoted revision is scored on `holdout`;
   a drop opens an incident and blocks further auto-promotion for the bot.
5. **Budget-matched baseline** (optional per strategy, mandatory for
   platform-published strategies) — candidate must beat parent-with-extra-
   sampling at equal cost.
6. **Risk tier approval** (§3).

## 3. Risk tiers

Assigned per patch op; a patch takes the max of its ops.

| Tier | Examples | Default promotion |
| --- | --- | --- |
| **T0** | Annotations only | Auto |
| **T1** | Memory item add/retire; skill description tweak; resource content update | Auto if gate passes and owner policy allows |
| **T2** | Persona edits (SOUL/AGENTS/RULES); skill add/update; `engine_config` within allowlist; any `rewrite`-flagged edit | Human review (owner or delegate); owner may raise auto ceiling to T2 per strategy |
| **T3** | MCP/CLI tool changes, script, memory `replace` mode, permissions, anything in `policy` | Locked by default; when unlocked, always human review, never auto |

## 4. Anti-reward-hacking rules

- Evaluators, graders, suites, and their configs live **outside** the genome
  and are read-only to strategies and bots.
- Strategies never see validation per-case details (aggregates only),
  holdout, regression, or safety cases (ClawEvolve's review firewall,
  generalised and enforced by the `StrategyContext`, not by prompt).
- Diff audit: patches that mention or edit guardrail-like text (safety
  sections, refusal policies, logging/reporting instructions) are tagged and
  raised to T2+.
- Judges should come from a different model family than the strategy's where
  available; multiple graders for T2 promotions.
- Track cost and length per revision; a candidate that wins only by spending
  more is reported as such.
- Periodic re-audit of promoted lineage on fresh cases.

## 5. Sandboxing

- Strategies and evaluators work only on **sandbox materialisations** (eval
  bots via `eval_env`, or ephemeral workspaces). No production credentials,
  no access to the live phenotype.
- Each strategy declares the catalog capabilities it `needs` and its
  runtime isolation (R13); its context grants nothing else, and the
  orchestrator enforces network egress to model providers only unless
  declared otherwise and approved.
- Experience is **untrusted input** (prompt-injection and memory-poisoning
  vector). Strategies must treat episode text as data; patches derived from
  it get secret/PII/URL scanning; inbox submissions from subject bots are
  rate-limited.

## 6. Rollout and rollback

- **Shadow** (optional): candidate runs on mirrored traffic or replayed
  episodes; graded offline. For service bots this maps to the existing
  verify stage.
- **Canary** (multi-instance bots): a share of instances on `canary` ref;
  online metrics (task success, user feedback, error rate, cost) compared
  against `active`; auto-rollback rule optional per policy.
- **Promote**: move `active`, set `previous`, apply through Manifest /
  publish chain; record `revision_id` on the apply report.
- **Going back**: promote an earlier revision again (`avn genome promote
  --revision r41`). It is a normal, audited promotion: for a service bot it
  is published as the next version through the existing publish flow. The
  existing service-bot rollback feature is not involved.
- **Never delete**: rejected and retired revisions are archived.

## 7. Data handling

- Experience Store retention per tenant; redaction on ingest for secrets;
  PII policy configurable; export for training only with explicit tenant
  opt-in.
- Cross-tenant use of experience or strategies' learned artefacts is
  forbidden by default.
- Cross-bot skill transfer (P6) goes through Skill Center governance
  (ADR 0010: Skill Center distributes, write authority stays with the
  managing source), and is re-evaluated per consuming bot.

## 8. Budgets and kill switches

- Per run: tokens, USD, wall clock, rollouts, iterations — enforced by
  orchestrator, reported on the run.
- Per bot / per tenant: daily and monthly ceilings; max promotions per day.
- Escalation: N consecutive rejected iterations ⇒ stop run, notify owner.
- Kill switches: per strategy (disable everywhere), per bot (freeze
  evolution, keep `active`), global (pause orchestrator).

## 9. Audit

Every revision, gate decision, approval, promotion, and rollback is an
append-only event with actor, principal kind, reason, and links to evidence
and evaluations. The Archive read model is the audit UI.
