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
