# ClawInsight governance and verification batch

A bounded batch process, not an autonomous tool-using Agent. Counts select investigation
order; raw evidence and mechanical gates select actionable administrator-review plans.
The existing realtime `clawweb-ais-base` runner remains the public entrypoint and is not
replaced or modified by this package.

## Public AIS runner contract

The package is selected by the existing runner with
`runtime.package.packageId = "clawweb-governance-batch"`. The runner invokes
`scripts/run.sh` with its standard `--config`, `--task-file`, `--input-dir`,
`--output-dir`, and `--result-file` arguments. The task input must contain:

```json
{
  "batchConfigPath": "/ossfs/workspace/clawinsight/config.json",
  "mode": "dry-run",
  "topPerLane": 2,
  "endDate": "20260928"
}
```

`batchConfigPath` is the independently reviewed governance configuration. The public
runner supplies the per-task output/state boundary; no AIStudio node ID is embedded in
this package. `mode=apply` is allowed only when the batch configuration explicitly has
`allow_writes=true`.

A successful run returns the public runner result contract and declares only artifacts
inside that task's output directory. The batch never calls approval, notification,
repair, stop-job, mark-handled, or force-close APIs.

## Context boundary

```yaml
purpose: Build reviewed governance and verification requests from bounded historical evidence
provides:
  - scripts/run.sh
  - clawweb_batch.pipeline.run
consumes:
  - DataSource
  - EffectCenter
  - Analyst
  - EvidenceStore
internal_dependencies: []
```

- `contracts.py`: v1 external capability protocols.
- `core.py`, `evidence.py`, `verification.py`: deterministic, transport-free policy.
- `pipeline.py`: bounded application orchestration; depends on protocols, not production adapters.
- `adapters/`: read-only PyODPS, source-scoped NAS, restricted HTTP and JSON-only analysis.
- `bootstrap.py`: configuration, secrets and implementation selection.
- `scripts/run.py`: thin adapter to the existing public AIS runner.
- `artifacts.py`: atomic artifacts, exclusion lock, frozen outbox and receipts.

## Run and safety

Python >=3.10. Local tests use only the standard library. AIStudio supplies its existing
`pypai`/PyODPS runtime and PyYAML when a YAML model configuration is used. No package
installation or shared Python changes are required.

```sh
python3 -m unittest discover -s src/evolverun/clawweb-governance-batch/tests -v
```

The application verifies every configured evidence root is mounted read-only before
reading it. It never writes to Bot NAS. Results, cache, outbox and logs stay in the
public runner's task workspace or the separately configured batch state directory.

`dry-run` performs reads and analysis but no effect-center mutations. Formal apply mode
creates only `ASSIGN_OWNER` items and requires both the task's `mode=apply` and the
configuration's `allow_writes=true`. The create receipt must remain `PENDING_ADMIN/PENDING`;
any implicit auto-execution response fails closed.

## Data and decision contract

- Counts are Task counts from the category table, not session-duration proxies.
- Non-Cron and Cron rank separately. Cron estimates and observed sample counts remain separate.
- Both data lanes must be present; absent partitions are not assumed to be empty/healthy.
- A seven-day window is seven calendar days. Missing paired days are disclosed, not filled with zeros.
- Partition presence is a minimal readiness check, not proof of upstream ingestion completeness.
- Query identity is exact Owner+Bot+Session; latest daily snapshots are deduplicated.
- Task indices and message ranges are preserved; malformed/oversized evidence stays unverified.
- Source/engine/device/agent/Cron identity is part of the root. Desktop evidence does not inherit NAS configuration.
- Only actual paired tool returns yield signatures; errors in a read Skill file do not.
- Creation needs at least two independent observed failure sessions, latest-day evidence, valid references and no newer comparable-success contradiction.
- Existing items are scoped/paginated. Read failures stop publication; they are not empty history.
- New roots use `clawinsight-v1:`. Verification and dedup remain compatible with the former `nightly-v1:` marker.
- Rejected same-root items are suppressed for the configured cooldown (default 15 days), require evidence newer than the rejection, and fail closed when the rejection timestamp is unavailable.
- The analysis cache suppresses repeat model analysis when the data date, evidence, current source configuration and scoped history are unchanged.
- Creation always uses `ASSIGN_OWNER` to preserve the manual-admin path.
- Verification matches only the versioned root's same operation/source after the observation boundary.
- `INSUFFICIENT_DATA` is recorded locally but not posted, so the observation window is not restarted every run.

## Artifacts and retry

`<task-output>/clawinsight-batch/<data-date>-dry-run/` or `-apply/` contains `review.md`,
`run.json`, `evidence/*.json`, `requests.json` and verification evidence. Runtime files
stay outside Git. The state directory has an exclusion lock and an analysis cache; a stale
lock requires operator inspection, not automatic deletion. Apply freezes the exact payload
before POST. Network ambiguity or conflict stops writes without blind retry.

## Cron-only deployment (2026-10-07)

The maintained OpenClaw composition root is now `deployment/`, not an unversioned
Downloads-only wrapper. Its independent configuration requires `cron_only=true`.
It consumes this module and sibling project Skills directly; no copied library or
personal Skill store is authoritative. The legacy public runner remains compatible
with configurations without this flag.

Cron-only selection aggregates failure counts by exact Owner + Bot, not by category.
All failure labels contribute; COMPLETED and UNKNOWN do not. Categories do not select
investigation slots. Estimated counts still depend on upstream completion labels and
are not confirmed root-cause counts. Only Cron sessions are queried in this mode;
non-Cron data is not required for the daily watermark. Verification queues remain
separate, but missing comparable Cron evidence cannot justify closing an item.

`deployment/run.sh` preserves failure exit codes and writes the same progress and
visible response text to its console and a unique protected `logs/console-*.log`.
Model replies appear immediately after each complete CLI turn; this is not token
streaming. Hidden reasoning and raw session bodies are never printed deliberately.
The existing CLI captures its final JSON; token streaming has not been established.

Verification with an already-determined INSUFFICIENT_DATA reason does not call the
model just to paraphrase the reason. Other verification explanations are batched by
mechanically matched Task IDs below the byte budget. Full evidence stays on disk;
large individual Tasks use explicitly marked metadata projections, never silent
truncation or an assertion of complete model review. Program outcomes cannot change.
An explanation failure is recorded and later items are still attempted, but the
whole run remains failed and any future publication remains blocked.

`--dry-run` performs actual reads and model analysis but no effect-center writes.
`--check` performs local preparation checks only. Neither schedules a task. Deployment
and runtime validation are separate from local tests; no run is authorized by this file.
