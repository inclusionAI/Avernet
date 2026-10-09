# agentcompute Operations Notes

Stage-B writeups for tasks 12.3 (schema), 12.6 (pool), 12.9 (artifact TTL),
12.10 (perf review). Names/defaults cite `config/settings.py`
(`AgentComputeConfig`) and `plugins/community/database.py` as shipped.

## 12.3 Schema evolution

- **Doctrine:** forward-only, additive, backward-readable (D38) — full policy,
  apply order, verification queries, and rollback note live in
  `sql/2026_10_09_agent_compute_validation.md`. CI enforcement (dated names,
  no destructive statements, additive ALTERs, ORM↔DDL column + unique-index
  parity) lives in `tests/community/core/agentcompute/test_agentcompute_schema_contract.py`.
- **Rule of thumb for a new change:** add a dated sql file (`YYYY_MM_DD_`),
  add the same columns/indexes to `repository/models.py` in the same change,
  keep every new column nullable or defaulted, and never repurpose an old one.

## 12.6 Connection-pool sizing (community profile)

**Mechanism (as shipped).** `CommunityDatabase`
(`plugins/community/database.py`) owns the pool: MySQL/OceanBase engines get
`pool_pre_ping=True, pool_recycle=3600` and otherwise SQLAlchemy QueuePool
defaults — `pool_size=5`, `max_overflow=10`, `pool_timeout=30s`. The
`database` yaml block (`CommunityDatabaseConfig`: `backend`, `url`,
`create_schema`) exposes **no pool knobs today**; changing pool sizing is an
engine-kwargs edit in that plugin plus a provider field, not a yaml value.

**Where connections actually go per pod.** The worker is a single loop
(`runtime/worker.py`): one `claim_batch` (+`reclaim_batch` at 2× batch) per
poll tick, then slices run sequentially — so at most **one slice** per pod is
in flight regardless of `batch_size` (default 10). Inside a slice, D23's
bounded queue (`maxsize = max_width`, default 8) caps in-flight subagents and
therefore their completion writes; they share the pod's single event loop with
the orchestrator, so repository writes are short and mostly serialized.

**Formula.** With `W` = workers/slice (=1 today), `F` = `max_width` (8),
`S` = concurrent SSE subscribers on the pod, and stream polls holding a
session ~25 ms at 1 Hz (duty ≈ 0.1):

```
pool_size        ≈ 2 + W·1            (worker claim + write overlap)
                   + ceil(S·0.1)      (steady SSE reservation)
max_overflow     ≥ worst-burst ≈ S·2  (subscriber sweeps landing together)
hard ceiling/pod ≈ 2 + W·F + S        (all subagents + all streams at once)
```

Defaults (2+1 ≈ 3 steady, overflow 10) comfortably fit `max_width=8` and
roughly ≤50 subscribers/pod. If you raise `max_width`, add slices-per-pod, or
exceed ~100 streams/pod, size from the ceiling formula and add the engine
kwargs. The D23 bounded queue is what keeps the subagent term bounded —
without it there is no upper bound to size against.

## 12.9 Artifact TTL sweeper

`runtime/retention_sweeper.py` (`AgentComputeRetentionSweeper`), store-scoped
over `ArtifactStore` via the additive `RetentionScanner` SPI (listing + mtime;
concrete impl: `FilesystemArtifactStore.retention_scanner()`). Knobs:
`retention.artifact_ttl_days` (7) and `retention.sweep_interval_seconds`
(3600). Behavior: dry-run (`would_delete` listing), `max_batch` bounding,
per-key errors logged-and-continued, non-conformant roots-skipping keys
(`{run_id}/{agent_id}/{result}` only), path-escape guard inherited from the
store; nothing outside `artifact_store_root` is ever touched.

**Wiring status:** constructed per its unit tests; scheduler + DI wiring is
wave-2 (`di/modules/agentcompute_module.py` was off-limits this wave). The
future scheduler calls `sweep()` every `sweep_interval_seconds`.

**Growth check:** puts ≈ spilled subagent results (payload >
`artifact_inline_threshold` = 16384) ≈ bounded by `max_total_agents` = 100 per
run. Steady-state files ≈ put_rate × TTL(168 h); at a heavy 100 spills/h that
is ~16.8 k files — one O(files) root walk per hour is negligible; walk cost
scales linearly, revisit only if rates grow ~100×.

## 12.10 Index / perf review

**Submit-time COUNT scans.** `submit_async` runs, per request:
`count_active_runs(tenant)` (`tenant` + `status='RUNNING'`),
`count_active_runs_global()` (`status='RUNNING'`), and
`count_pending_jobs()` (`status='PENDING'`). Intended indexes:
`idx_run_status (env,tenant,status,gmt_modified)` on `run` and
`idx_job_scope_status_run_at (env,tenant,status,run_at)` on `job`. **Gap:** no
query binds the leading `env` column, so OceanBase cannot seek the composite
prefix; the counters degrade to scans of every row, O(total fleet rows), not
O(tenant rows) — tenant admission stays correct but its cost grows with fleet
size. Recommendation (one-line, not made this wave): bind `env` in all three
counters, or add status-leading indexes; keep is_deleted in the predicate so
the covering index stays chosen.

**SSE fan-out.** Each `/runs/{id}/events` subscriber polls `svc.run_events`
every 1 s (router generator): 1 run fetch (`uk_run_id`) + 3 per-run lists
(agents, rounds, guards — indexed by `idx_agent_status`/`idx_message_run_agent`
family, all (env,tenant,run_id,…)). Row set per subscriber is bounded by
`max_total_agents` (100) / `max_turns` (50) so each poll is a few small
indexed reads. Cost/pod = subscribers × 4 queries/s — fine to ~50–100
streams/pod against default pool headroom (§12.6 formula), plus a soft bound
of ≈ `list_runs` `page_size` (20) concurrent subscribers per run as a sanity
cap. **When to switch to event broadcast:** when subscribers-per-run routinely
exceed the page_size cap or a pod's active streams approach its pool ceiling —
then replace per-subscriber polling with one poller per run + in-process
broadcast (or the platform's pub/sub), keeping the same SSE wire format.

**Drain interaction:** slice-bound writes are short; the pool math above
assumes `drain.grace_seconds` (30) ≥ a slice's final write batch — cross-check
with the drain runbook (task 12.2) before shrinking pool/recycle values.