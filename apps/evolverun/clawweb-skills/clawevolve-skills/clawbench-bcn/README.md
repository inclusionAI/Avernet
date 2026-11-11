# clawbench-bcn — local P1

Standalone benchmark Skill for Singlebox's existing CEO/product/engineering/
verification/customer-service team. No ClawWeb routes, DB, upload, or changes to
Singlebox/BCS are required by this implementation. **Live testing has started, but repeatable P1 acceptance is still pending.
One group completed; a fresh isolated rerun timed out waiting for Manager completion.**

## Setup

Keep Singlebox base services (BCS/backend and its required dependencies) running.
This command starts only the five evaluation Bots, not the full demo or shared services.
Configure Singlebox with a real model first. Install dependencies once:
`python3 -m pip install -r requirements.txt`.

From this Skill directory:

```bash
./scripts/run.sh --case ./examples/task_01_singlebox_feedback_plan.md \
  --profile-dir ../../../../../singlebox/agents/5bots_profile
```

The repository is discovered from this Skill location. Default execution reuses
Singlebox's prepared `singlebox/.dependencies/openclaw/model-config/openclaw.json`
(or `SINGLEBOX_MODEL_CONFIG_FILE`), without generating Runtime YAML or changing
model mode. The Judge uses the same configured OpenAI-compatible model; credentials
are read in memory and never printed. Referenced key environment variables must
already be available. Other Judge protocols require an explicit compatible Judge
via `--runtime`. No automatic fallback to mock or a different model.

Defaults: BCS `http://127.0.0.1:21000`, Bot ports 32001/32011/32021/32031/32041,
output `~/.cache/clawbench-bcn-p1/runs`. Use optional `--runtime` with
`examples/runtime.example.yaml` to override these settings. Add `--check` for
input validation without service/model calls (not a live health check).
Activate your Python/Node environment first; `PYTHON` can override the executable.
TLS verification stays enabled; certifi provides default CA roots, explicit
`SSL_CERT_FILE`/`SSL_CERT_DIR` settings take precedence.

## Behavior and output

- Predefined Profile is read-only; uniquely named BCN identities, instances and new workspaces
  are prepared under the private run directory. Singlebox logs may include private
  data and must not be uploaded. Model service startup is not included.
- CEO's existing Bot identity authenticates group creation. Prompt is passed once
  as group context, not a CEO chat message. No extra initiator Agent is created.
- Poll the canonical Session for `completed`; use its string `output` as final
  delivery. `error_message`, absent output and timeout are not success.
- Stop the five owned Bots, terminate the owned group, and archive exact matching
  OpenClaw Session transcripts and BCN messages. No newest-file fallback. Raw member activity is reported, not automatically treated as meaningful contribution.
- `run.json`, `artifacts/final.md`, `evidence/`, `result.json`, `report.md` contain
  run linkage, execution, evidence and grading. Missing usage stays null.
- Reviewed Python Judge checks nonempty final delivery (10%); independent API
  Judge evaluates four quality dimensions (90%). Errors leave total score null.
- A separate process and timeout limit the code Judge, but are NOT a security
  sandbox. Run only trusted Cases. Same-host Bots may retain filesystem access;
  only the task Prompt is intentionally supplied to them.

## Boundaries and remaining verification

Current CLI contract is read from `apps/bcs/crates/tools/bcs-cli`: group creation
uses `--manager`, `--participants`, `--context`, `--topic`; get/messages are
Session-scoped. Group creation output is labelled text even under `--json`.
The runner does not retry a mutation with an uncertain outcome.

Live startup, Manager completion, exact OpenClaw route mappings, full member
participation and cleanup require local verification. Group history currently
collects a bounded 1000-message snapshot and marks a full cap incomplete; it does
not claim arbitrary-history pagination. Profiles/logs remain local for debugging;
stopping is not equivalent to removing registered Bots or securely erasing files.
No online-existing-group mode, material distribution, batch evaluation or RSI.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Compatibility / source

The Markdown section names, `grade(transcript, workspace)` and weighting fields
follow clawbench-base (derived from MIT-licensed PinchBench). This Skill uses a
small independently implemented reader and API evaluator, not sibling imports.
Exact OpenClaw Session lookup follows the existing lib_agent lookup approach;
workflow-engine embedded-session collection is intentionally not reused.

## Local TLS configuration

If Python reports `CERTIFICATE_VERIFY_FAILED`, configure a trusted CA bundle
via `SSL_CERT_FILE` (for example the installed certifi bundle, or your managed
corporate CA bundle). Do not disable TLS verification. Keys remain environment-only.

## Analysis report

After execution, a built-in Prompt generates `analysis-report.md` from archived
results and evidence, including failed/timed-out runs. It does not change scores.
`report-generation.json` records report status, model usage and truncated sources.
Report failure preserves `result.json`, basic `report.md` and original evidence.
Regenerate without starting Bots: `python3 scripts/report.py --run-dir <run-directory>`.
Use `--prompt <file.md>` to override the built-in `prompts/report.md`; `--runtime`
is optional. This follows clawbench-report's evidence-based Markdown structure,
without importing or changing that sibling Skill. Evidence is bounded; the report
is an analysis aid, not a substitute for full transcripts.

## Console progress

The unbuffered CLI prints preflight, Bot startup, group/session IDs, execution
status every 30 seconds, cleanup, member evidence/usage, grading and report paths.
Run-stage messages are also saved to `runner.log`; startup and shutdown details
remain in `singlebox-start.log` / `singlebox-stop.log`. Raw configuration, keys
and model responses are not echoed. `--check` performs no model or service calls.
