# Agent Compute Demo

A self-contained Python demo of the **Apache Spark driver/executor model**,
where the executor is an **LLM-backed agent** instead of a JVM worker. It
mirrors the ocb-public BAAS **hexagonal architecture** (DI / SPI / Plugins).

```
goal ──► planner ──► DAG plan (JSON) ──► driver ──► per-node agent executors ──► final result
                                                    └─► status/progress tracking
                                                    └─► HTML/SVG DAG visualization
```

## What it does

1. You supply a **goal** and a **candidate agent list** (name + role + optional
   instructions) — the roster is *not* hardcoded; it comes from the caller.
2. The **planner** (an agent, via the LLM provider) splits the goal into an
   executable **DAG** of nodes and dependency edges, persisted as **JSON**.
3. The **driver** walks the DAG in dependency order, dispatching each node to a
   short-lived **executor** — an agent instance created on demand and torn down
   after the node completes.
4. Node **status and progress** are tracked (`PENDING → RUNNING → SUCCEEDED/FAILED/SKIPPED`).
5. The final result is assembled from the DAG sink node(s), and the plan is
   exported to a standalone **HTML/SVG** file.

## Quick start — configure `.env` and run a job with `just`

### 1. Configure the LLM backend (`.env`)

Copy the template and fill in your OpenAI-compatible endpoint:

```bash
cd app/agentcompute
cp .env.example .env
```

Edit `.env`:

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1   # or your OpenAI-compatible endpoint
OPENAI_API_KEY=sk-...                       # your API key
OPENAI_MODEL=gpt-4o-mini                    # model name
```

- Keys already present in the environment take precedence over `.env`.
- `.env` is gitignored — never commit your key.
- The API/CLI default provider is `openai` (reads `OPENAI_*` from `.env`).

### 2. Run a demo job with `just`

**Server mode** (submit a job, stream progress, fetch the report):

```bash
just install                 # first time: uv sync
just job-submit-and-wait "Compare Django vs FastAPI"
```

`just job-submit-and-wait` auto-starts the server, submits the job, streams
per-node progress to your terminal, then fetches the final report. It wraps the
whole `/jobs` flow. Use the granular recipes to step through it by hand:

```bash
just start                                   # start the HTTP server
just job-submit "Summarize the top AI trends for 2026"   # -> {"job_id": "..."}
just job-stream <job_id>                     # SSE per-node progress
just job-status <job_id>                     # poll status + final result
just job-report <job_id>                     # save output/report-<job_id>.html
just stop                                    # stop the server
```

**Inline mode** (no server, runs in one process):

```bash
just run "Summarize the top AI trends for 2026"
just example                                 # same goal, fixed agents
```

`just run` uses the real `openai` provider by default. For offline/deterministic
runs (no API key, no network), override the provider:

```bash
uv run acd "smoke test" -a "searcher:finds,summarizer:summarizes" --provider stub
```

### 3. Where things land

| Output | Path |
|--------|------|
| Plan JSON | `output/plan.json` |
| DAG visualization | `output/dag.html` |
| Report (HTML) | `output/report.html` |
| Report (markdown) | `output/result.md` |
| Server log | `~/logs/agentcompute/agentcompute.log` |

## HTTP API (FastAPI)

The demo ships as an HTTP service too (FastAPI/uvicorn). Start it with:

```bash
./scripts/serve.sh                 # base config, 0.0.0.0:8000
./scripts/serve.sh dev             # merge config/application-dev.yaml (DEPLOY_ENV=dev)
PORT=9000 ./scripts/serve.sh       # override the port

just serve                         # or with just
just serve dev
```

Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/health` | liveness probe |
| `POST` | `/run` | sync run — accepts `{goal, agents:[{name,role,instructions}], provider?, max_workers?}`, returns full result |
| `POST` | `/jobs` | submit async — returns `{job_id}` |
| `GET`  | `/jobs/{id}` | poll status + per-node statuses |
| `GET`  | `/jobs/{id}/stream` | Server-Sent Events of per-node progress |
| `GET`  | `/jobs/{id}/report` | the full task report as HTML |

```bash
curl -X POST http://localhost:8000/run -H 'Content-Type: application/json' -d '{
  "goal": "Research the best Python web framework",
  "agents": [
    {"name": "searcher", "role": "Searches sources"},
    {"name": "summarizer", "role": "Summarizes findings"}
  ]
}'
```

### Config via application.yaml (env overlay)

Config is loaded from `config/application.yaml`, deep-merged with
`config/application-{env}.yaml` when `DEPLOY_ENV` / `SERVER_ENV` /
`ALIPAY_APP_ENV` is set — mirroring the BAAS convention. Plugin selection
(`logger`, `tracer`, `llm.provider`) and app/log settings live under
`user_config.plugins` and `log`, so local/dev overlays override only what they
need.

### Logger / Tracer (pluggable, mirrors BAAS)

`LoggerPlugin` and `TracerPlugin` SPIs are provider-based and config-selected
(`plugins.logger`, `plugins.tracer`), defaulting to stdlib implementations.
Every request gets an `X-Trace-Id` header (contextvar trace id).

## Architecture (hexagonal — mirrors BAAS)

```
app/agentcompute/
└── src/agentcompute/
    ├── community/              # SPI contracts + registry + DI (knows nothing concrete)
    │   ├── spi/                #   Agent, AgentSpec, LLMProvider protocols
    │   ├── plugin_registry.py  #   register_plugin_option(key, name, factory)
    │   └── bootstrap/          #   Config, Container, get_container()
    ├── enterprise/             # concrete LLM-backed implementations
    │   ├── llm/stub.py         #   StubLLMProvider (offline, deterministic)
    │   └── agents/base.py      #   LLMBackedAgent (spec-driven)
    ├── dag/                    # DAGNode, DAGPlan, NodeStatus (+ JSON, acyclicity)
    ├── planner/                # Planner: goal → DAGPlan
    ├── driver/                 # Driver: walk DAG, dispatch executors, track status
    └── reporter/              # export_plan: DAG → standalone HTML/SVG
```

**Dependency direction rule** (identical to BAAS): `community` never imports
`enterprise`. Concrete implementations register themselves into the community
registry; the DI container resolves them by config-driven key lookup.

## Run

```bash
cd app/agentcompute
uv sync                 # installs the package + dev deps (pytest, ruff)
uv run acd "Research the best Python web framework and summarize findings" \
    -a "searcher:Searches the web,summarizer:Summarizes findings" \
    --plan output/plan.json \
    --viz output/dag.html
```

Or with the convenience scripts:

```bash
./scripts/run.sh "Your goal" "searcher:finds,summarizer:summarizes"   # bash

just install                                  # or use `just`
just run "Your goal"
just example
```

Or without installing (zero runtime dependencies — pure stdlib):

```bash
PYTHONPATH=src python -m agentcompute.cli "..." -a "searcher:Searches,summarizer:Summarizes"
```

The candidate agent list `-a/--agents` accepts either `name:role` pairs
(comma-separated) or a path to a JSON file of `AgentSpec` objects
(`--agents @agents.json`).

### Programmatic API

```python
from agentcompute import run
from agentcompute.community.spi.agent import AgentSpec

summary = run(
    goal="Plan a team offsite",
    agents=[
        AgentSpec(name="searcher", role="Finds venue options"),
        AgentSpec(name="summarizer", role="Condenses options into a shortlist"),
    ],
    plan_path="output/plan.json",
    viz_path="output/dag.html",
)
```

`run` returns a dict with `succeeded`, `node_statuses`, `final_output`, and the
`plan`.

## Agent SPI (full lifecycle)

```python
class Agent(ABC):
    def setup(self) -> None: ...      # created on demand per node
    def execute(self, ctx) -> NodeResult: ...
    def teardown(self) -> None: ...   # destroyed after node completes
    def halt(self) -> None: ...
```

Executors are created via the container's `plugins().agent(name)` and are torn
down in a `finally` block, guaranteeing per-node lifecycle.

### Concrete implementations

- **`LLMBackedAgent`** — stateless; calls an OpenAI-compatible `/chat/completions`
  endpoint in `execute()`. `setup()` and `teardown()` are no-ops.
- **`AvernetAgent`** — stateful; manages a remote Avernet bot lifecycle through
  the gateway. `setup()` creates a bot via `POST /openapi/v1/bots/with-manifest`
  and polls until `READY`. `execute()` streams chat output via
  `POST /openapi/v1/chat/stream` (SSE). `teardown()` deletes the bot via
  `DELETE /openapi/v1/bots/{bot_id}`. This is the first implementation that
  actually exercises the full lifecycle contract for remote state management.

Dispatch is by `AgentSpec.metadata["type"]`:
`"avernet"` → `AvernetAgent`; anything else (or unset) → `LLMBackedAgent`.
A single DAG run can mix both agent classes.

## Test

```bash
cd app/agentcompute
uv run pytest -v                    # or: just test
uv run pytest --cov=agentcompute   # coverage (≥90% enforced) — or: just coverage
uv run ruff check src/ tests/       # or: just lint
```

Coverage is enforced at 90% (branch coverage) via `[tool.coverage.report]` in
`pyproject.toml`.

## Configuration / swapping the LLM provider

The provider is selected via `Config.llm_provider`. Out of the box there are
two options:

- **`stub`** (offline) — deterministic, no API key; **`openai`** — real LLM backend.
- **`openai`** — `OpenAICompatibleProvider`, a stdlib-only chat-completions
  client for any OpenAI-compatible endpoint.

Provider config can come from a **`.env` file** (copy `.env.example` to `.env`),
from the environment, or from `llm_options`:

```bash
# .env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

```bash
uv run acd "..." -a "..." --provider openai     # reads OPENAI_* from .env/env
```

```python
summary = run(goal, agents, provider="openai", llm_options={
    "base_url": "https://api.openai.com/v1",
    "api_key": "...",
    "model": "gpt-4o-mini",
})
```

Swapping the provider requires no change to the planner, driver, or agents.

### Avernet agent configuration

The `AvernetAgent` reads its gateway connection and manifest template from the
`avernet` config block (under `user_config.plugins.avernet` in `application.yaml`):

```yaml
user_config:
  plugins:
    avernet:
      gateway_base_url: "https://gateway.avernet.example.com"
      principal_token: "<signed X-Avernet-Principal JWT>"
      user_id: "owner-user-id"
      manifest_template: |
        name: {role}
        instructions: {instructions}
        goal: {goal}
      poll_timeout: 300
      poll_interval: 1
      http_timeout: 60
      http_retries: 2
      chat_stream_path: "/openapi/v1/chat/stream"
      create_bot_path: "/openapi/v1/bots/with-manifest"
      delete_bot_path: "/openapi/v1/bots/{bot_id}"
      status_path: "/openapi/v1/bots/{bot_id}/with-manifest/status"
```

To use an Avernet-backed executor in a DAG run, set `metadata.type` on the agent
spec:

```python
from agentcompute.community.spi import AgentSpec

specs = [
    AgentSpec(name="searcher", role="Searches the web"),  # LLMBackedAgent
    AgentSpec(
        name="avernet-bot",
        role="Deep researcher",
        metadata={"type": "avernet"},  # → AvernetAgent
    ),
]
```

The manifest template supports `{role}`, `{instructions}`, and `{goal}`
placeholders — substituted at runtime, leaving unknown `{...}` literals
intact. The manifest YAML is treated as an opaque string; the gateway owns
validation.

## Parallel execution

Ready nodes (all dependencies satisfied) run concurrently; waves advance in
dependency order. Bound concurrency with `max_workers` (default `4`), or set
`1` for strictly sequential execution:

```python
from agentcompute.driver import Driver
result = Driver(max_workers=8).run(plan)
```