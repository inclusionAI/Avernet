# PR 2655 Review & Fix Campaign — issue tracker

**PR:** `feat(agentswarm): add emergent multi-agent swarm compute module`
**Branch:** `agentswarm` → `dev` · **Repo:** `inclusionAI/Avernet`
**Campaign:** 2026-10-14 · **5 review rounds** (6+5+2+4+2 reviewers)

> Rule in force: **the PR must not change non-agentswarm-scoped behavior.** Shared files may only gain additive agentswarm wiring.
> Status: `[x]` fixed.

---

## Non-agentswarm-scoped changes — RESTORED (rule compliance)

- [x] **R-1 · CronAdmin conformance pair restored.** `tests/community/architecture/test_service_api_conformance.py` — the PR had *replaced* the `(CronAdminServiceProtocol, CronAdminService)` pair; now appended (agentswarm pair added alongside).
- [x] **R-2 · `api/README.md` cron line restored** to its original dev position.
- [~] **R-3 · Other non-agentswarm files additive-only** (pyproject `jsonschema`, configs `agentswarm:` block, `test_all_bindings_resolve.py` BotPlatform allow-entry) — required by the feature, no behavior change to other modules.

---

## Round 1 + 2 — original review (34 issues)

P0: LLM token was the secret's *name*; `message.seq` int32 overflow; tenant client-controlled via `X-Tenant-Id`; `env=run.provider.value`; stored XSS in report; global/queue limit 500s; depth guard capped runs at ~6; congested-batch terminal NO_PROGRESS; host-adapter binding claims false.
P1: fencing gaps; sync DB on event loop; heartbeat exit race; `no_progress` unreachable; admission TOCTOU; pool teardown swallow; submit deadline truncation; purge no active-run guard; DI config ignored; artifact path traversal; LLM stream truncation; DecisionParser TypeError/brace; prompt-injection fence bypass.
P2: caps, docs, decode fallbacks, DB/query hygiene, conformance swap.

## Round 3 — verification + run-idempotency

- [x] `run.create` idempotency convergence (concurrent duplicate-submit race → 500).

## Round 4 — deep audit (4 specialized reviewers) + fixes

- [x] **Orphan run** on `enqueue` failure — compensation added.
- [x] **Unbounded orchestrator prompt** — tail-cap (`_PROMPT_MAX_SHARDS`) + per-shard truncation.
- [x] **Unbounded artifact size** — `_MAX_ARTIFACT_BYTES` (8 MiB), raises before any FS mutation.
- [x] **`output_schema` unvalidated** — dict-type + size cap at the parse boundary.
- [x] **`get_by_idempotency` env scope** — added.
- [x] **Untested fix branches** — fatal path, `_rejection_notes` clear, convergence raise-legs, worker fail-fallback.

## Round 5 — independent verification + residual fixes

- [x] **HIGH — orphan compensation was a no-op.** The R4 fix used the D28 job-epoch-fenced `update_status`, which matches zero rows when `enqueue` failed before the job committed (the common case) → run stranded RUNNING. Fixed with an **unfenced, status-predicated `run.fail_unclaimed`** submit-plane seam; the D28 worker fence is untouched.
- [x] **MED — shard `list_by_run` missing `ORDER BY`** — the orchestrator prompt tail-slice relied on unspecified DB order; added ascending-id order.
- [x] **MED — unbounded inline fallback** — a >8 MiB result could no longer spill, writing unbounded bytes to the DB; now a bounded truncated marker (`_MAX_INLINE_RESULT_CHARS`).

---

## Verification

`1387 passed, 0 failed` across:
`tests/community/{core/agentswarm,adapters/http/agentswarm,adapters/cli/agentswarm,plugins/local/agentswarm,plugins/community/agentswarm,plugin_api/agentswarm,di,architecture}`

Scope: agentswarm files only (+ the 2 deliberate restores). No weakened tests. No hardcoded secrets/URLs. No touched src file >1000 lines.

## Known, documented, not fixed (low / out-of-scope)

- SSE subscriber cap & max stream duration (README documents pool sizing as an assumption).
- Multi-env query scoping beyond `get_by_idempotency` (systemic; run-repo counters/reads env-agnostic by design).
- `put_file` stat→copy TOCTOU (no production caller).
- Two **test** files >1000 lines (`test_coverage_gaps.py`, `test_subagent_runtime.py`) — CI size guard scans `src/` only.