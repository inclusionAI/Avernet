# Branch-deployed Cron governance

Maintain this entrypoint and both sibling project Skills in the same Git revision.
Deploy by pushing the dedicated development branch, refreshing the organization's
approved repository mirror, and checking out the exact branch/revision in the runtime.
Do not copy a second implementation outside the repository.

Use an independent JSON configuration outside the checkout. Start from
`config.example.json`, set `cron_only=true` and `allow_writes=false`, and reference
an existing deployment model configuration without copying its credentials.
Keep `output_dir` and `state_dir` under the independent JSON's parent directory.
When upgrading, retain the existing state directory and historical reports; do not
reset the publication journal, exclusion lock or receipts. Stop if another run owns
the lock. Dependencies must already be provided by the reviewed runtime image.

```bash
bash /path/to/checkout/src/evolverun/clawweb-governance-batch/deployment/run.sh \
  --config /path/to/runtime/config.json --check
bash /path/to/checkout/src/evolverun/clawweb-governance-batch/deployment/run.sh \
  --config /path/to/runtime/config.json --dry-run --top-bots 2
```

`--check` performs local checks, not business queries or model calls. `--dry-run`
executes actual reads and analysis but never mutates the effect center. Logs retain
the process exit code via `pipefail`. `--top-bots` limits unique Cron Owner+Bot slots;
`--top-per-lane` is retained as a compatibility alias. Counts are estimates, not
confirmed defects. Model-visible replies print once each turn completes, not token
by token. Outputs and timestamps do not themselves prove a deployed repair worked.

The public runner's previous default remains compatible; this dedicated entrypoint
requires Cron-only configuration explicitly. Do not enable production writes merely
to test the deployment. Create no schedules as a side effect of setup.

The configured `analysis_max_tokens` is forwarded to OpenClaw's model output budget
(including provider reasoning usage); incomplete turns are never accepted as valid.
The model adapter validates output structure. Evidence eligibility and later-success
counterevidence remain program gates: a veto yields WATCH, not a model transport
failure or a retry intended to force CREATE. Both proposed replies and final gated
results are printed, clearly labeled.

## Creation-only publication

`--governance-only` skips both verification queue reads and verification writes.
For an explicitly authorized creation run, use this flag together with `--apply`
and a separate reviewed configuration with `allow_writes=true`. The publication
adapter additionally rejects every endpoint/action except pending ASSIGN_OWNER
creation. Existing dry-run configuration remains unchanged. A zero-candidate run
is allowed and must not manufacture data merely to prove a POST succeeds.
