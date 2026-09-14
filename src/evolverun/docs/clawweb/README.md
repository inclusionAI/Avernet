# AgentEvolve

AgentEvolve is Avernet's local workspace for evaluating and continuously improving personal OpenClaw Bots. It connects real sessions, diagnosis, Bench evaluation, optimization, and recoverable versions into one traceable workflow.

For Chinese documentation, see [README.zh-CN.md](README.zh-CN.md).

> Historical source paths, script names, and configuration fields may still contain `clawweb` for compatibility. AgentEvolve is the public product name.

## Product overview

| Capability | Purpose | Open-source edition |
|---|---|---|
| Bot diagnosis | Find issues in personal-Bot sessions, extract good/bad cases, and produce a follow-up plan | Available |
| Bench diagnosis | Evaluate a Bot with published local templates and inspect cases, metrics, sessions, and raw output | Available |
| Diagnosis-driven optimization | Continue from a completed diagnosis and plan | Available |
| Bench optimization | Establish a baseline and optimize against train/test domains | Available |
| Bot self-evolution | Run diagnosis-first or direct-goal full flows | Available |
| Pack and Apply | Create a recoverable local snapshot and apply a previous version | Available |
| Task cleanup | Remove evolution agents and sessions carrying explicit task markers | Available |
| Task and version management | Inspect steps, outputs, evaluation results, diffs, and Pack versions | Available |

Service-Bot flows, dedicated session diagnosis, Bot repair, governance optimization, and internal-environment integrations are not included in the current open-source edition.

## How it works

1. Select a personal Bot from Avernet Singlebox or the current user's local OpenClaw.
2. Provide a direct goal or diagnose real session history.
3. Produce an executable plan, Spec, and validation cases.
4. Record a baseline before changing the Bot.
5. Generate candidate workspace changes in traceable optimization rounds.
6. Re-run Bench cases and compare the result with the baseline.
7. Review the diff and save or apply a recoverable Pack.

Tasks use only the stages they need. Bot diagnosis is read-only, while Bench diagnosis evaluates without running an optimization loop.

## Quick start

### Requirements

- Node.js 20.19+ or 22.12+;
- a working model configuration;
- either an Avernet Singlebox personal Bot or a local `~/.openclaw` containing `openclaw.json` and `workspace/`.

### Install

From the Avernet repository root:

```bash
cd src/evolverun/clawweb
npm ci
cd ../../..
```

### Start

```bash
bash src/evolverun/clawweb/scripts/start-clawweb-open.sh
```

The launcher asks which Bot source to use:

1. **Auto (recommended):** reuse an existing Avernet Singlebox database, otherwise use `~/.openclaw`;
2. **Local OpenClaw:** use `~/.openclaw` directly without starting Singlebox;
3. **Avernet Singlebox:** reuse personal Bots created by Singlebox without starting it.

Explicit examples:

```bash
# Use ~/.openclaw directly
bash src/evolverun/clawweb/scripts/start-clawweb-open.sh \
  --bot-source openclaw

# Reuse an Avernet Singlebox Bot database
bash src/evolverun/clawweb/scripts/start-clawweb-open.sh \
  --bot-source singlebox \
  --user-id mock-user

# Select a model and port
bash src/evolverun/clawweb/scripts/start-clawweb-open.sh \
  --bot-source openclaw \
  --model provider/model \
  --port 5173
```

Open <http://127.0.0.1:5173/> and select a personal Bot.

The launcher starts only AgentEvolve. It does not start, stop, or restart Avernet Singlebox or an OpenClaw gateway. If port `5173` is occupied, it exits instead of replacing the running process; use `--port` to select another port.

## Feature guide

### Direct-goal self-evolution

Use this when the desired behavior is already clear.

1. Choose **Start evolution → Bot self-evolution**.
2. Select direct-goal evolution.
3. Select the target Bot and execution model.
4. Enter the objective, success criteria, and constraints.
5. Review the plan, baseline, optimization rounds, and validation cases.
6. Create a Pack only after the result and diff are acceptable.

The planning stage converts the user's goal into an executable Spec and Bench cases. Acceptance is based on actual validation, not literal text matching between the input and generated documents.

### Bot diagnosis

Use diagnosis when the root cause is unknown.

1. Choose **Start evolution → Bot diagnosis**.
2. Select a personal Bot, model, session window, and sample limit.
3. Describe what to prioritize, such as tool failures or incomplete tasks.
4. Review the diagnosis, good/bad cases, and generated plan.
5. Continue with diagnosis-driven optimization only when a change is needed.

Diagnosis reads session history and must not modify the target workspace.

### Bench diagnosis

1. Prepare or select a published template under Bench templates.
2. Choose **Start evolution → Bench diagnosis**.
3. Select the target Bot, model, and template.
4. Inspect aggregate metrics, per-case results, sessions, and raw output.

When report generation is not configured, AgentEvolve shows a neutral unavailable state; the evaluation data remains available.

### Diagnosis-driven optimization

1. Select a completed diagnosis task and the target Bot.
2. Review its findings, Spec, train cases, and validation cases.
3. Run the optimization loop.
4. Compare the baseline, candidate diff, and independent validation before accepting the version.

### Bench optimization

1. Select train and test domains, target Bot, and model.
2. Run the baseline first.
3. Review each tuning round's diff, train result, and independent test result.
4. Accept a candidate only when metrics and critical cases meet expectations.

### Pack, Apply, and cleanup

- **Create Pack** stores allowed workspace content as a local snapshot with integrity metadata.
- **Apply Pack** validates path, size, and SHA-256 before restoring it to the selected Bot.
- **Task cleanup** removes only historical evolution agents and sessions with explicit task markers.

Applying a Pack changes the target Bot. Verify the Bot, Pack source, diff, and evaluation result first, and keep a rollback version.

## Reading a task result

- **Step status:** locate the stage that stopped and inspect its error and technical output.
- **Goal and Spec:** verify the interpreted objective, boundaries, and success criteria.
- **Bench metrics:** compare completion and category scores across baseline and candidate runs.
- **Cases and sessions:** inspect real messages, tool calls, and failure evidence.
- **Diff:** ensure that only expected files changed.
- **Pack:** record its source, digest, size, and application status.

Do not accept a version based on aggregate score alone. Critical cases and the actual diff also require review.

## Local data and artifacts

| Bot source | Default task directory |
|---|---|
| Avernet Singlebox | `~/.local/share/clawweb-singlebox` |
| Local OpenClaw | `~/.local/share/clawweb-open` |

Pack files are stored under `dataDirectory/artifacts`. The protocol retains an `oss://` logical reference, but the public filesystem object store resolves it to a local file; it does not upload to an internal object store.

Use `--data-dir` to override the task directory. Different histories after switching Bot sources are expected because the sources use separate databases and data directories by default.

## Configuration

The launcher generates a machine-local runtime configuration. User-owned YAML/JSON configuration must stay outside the repository and must not contain committed credentials.

| Field | Required | Meaning |
|---|---:|---|
| `userId` | yes | Local user identifier |
| `model` | yes | Initial model; otherwise read from the OpenClaw default model |
| `botSource` | no | `singlebox` or `openclaw` |
| `backendDb` | in `singlebox` mode | SQLite database created by Avernet Backend/Singlebox |
| `openclawHome` | in `openclaw` mode | Directory containing `openclaw.json` and `workspace/` |
| `dataDirectory` | yes | Task, log, and Pack artifact storage |
| `skillsRoot` | no | Public Skills root; defaults to the Skills shipped in Avernet |
| `botsRoot` | no | Custom local Bot data root |
| `port` | no | Web port; default `5173` |
| `maxArtifactBytes` | no | Maximum streamed artifact size |

Model choices come from the AgentEvolve module configuration, and the open-source edition also accepts a custom `provider/model`. Credentials remain owned by the model provider or OpenClaw configuration.

## Development and safety boundaries

The public implementation is under `src/evolverun/clawweb`; public runtime Skills are under `src/evolverun/clawweb-skills/clawevolve-skills`.

Public functionality must not depend on private services, endpoints, or credentials. Environment-specific values belong in arguments, configuration, or environment variables. Runtime data must not be written into the source tree. Workspace changes, Pack application, and cleanup must remain path-bounded and traceable.

Useful checks:

```bash
bash src/evolverun/clawweb-skills/clawevolve-skills/scripts/verify_public_skills.sh
cd src/evolverun/clawweb
npm run check
npm run build
npm test
```

## Troubleshooting

- **No Bots are shown:** verify the Bot source, `backendDb`, `userId`, and `openclawHome`; only personal Bots owned by the current user are listed.
- **Task history disappeared:** check whether `singlebox`/`openclaw` or `dataDirectory` changed.
- **A Skill entry is missing:** run `verify_public_skills.sh` and restart from the same Avernet checkout.
- **Sessions or workspace are missing:** verify that the selected Bot has a workspace and local session history.
- **Styles or native bindings are missing:** remove `node_modules` and run `npm ci` on the current OS/CPU with a supported Node/npm version.
- **The port is occupied:** reuse the running process or pass another port with `--port`.

## Screenshots

Planned screenshots and naming guidance are in [assets/README.md](assets/README.md).
