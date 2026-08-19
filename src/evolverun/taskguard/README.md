# taskguard

Universal YAML DAG workflow engine with TaskFlow persistence.

## Overview

taskguard is a YAML-defined Directed Acyclic Graph (DAG) workflow orchestration system. It uses TaskFlow for state persistence, supporting complex multi-node workflow execution, human approval, sub-workflow nesting, and conditional branching.

### Core Features

- **YAML-defined workflows** — declarative DAG with `dependsOn` + `triggerRule` semantics
- **Multiple node executors** — embedded-agent, subagent, cli-script, mcp-call, baas-call, bcs-route, bcs-approval-batch, human-wait, subworkflow, collaboration, action, done, loop-group
- **TaskFlow persistence** — SQLite (dev) / MySQL (prod) / API mode (decoupled)
- **Workflow packs** — distribute business workflows via `workflow.pack.yaml` manifests, with facade slash commands
- **Multi-platform adaptation** — PlatformAdapter abstraction layer, supporting OpenClaw / Claude Code / Hermes / TeClaw

## Architecture

```
YAML workflow definition
       |
       v
   Runner (pure-logic DAG traversal)
       |
       v
  Controller (state machine — sole writer to TaskFlow)
       |
       v
   TaskFlow (persistence layer: SQLite / MySQL / API)
```

## Project Structure

| Path | Description |
|------|-------------|
| `src/controller.ts` | Core state machine — sole writer to TaskFlow |
| `src/index.ts` | Plugin entry, command dispatch, executor orchestration |
| `src/runner.ts` | DAG traversal logic (pure function, no side effects) |
| `src/types.ts` | Core type definitions (WorkflowSpec, FlowState, WorkflowNode, etc.) |
| `src/executors/` | Node executor implementations |
| `src/platform/` | Multi-platform adapter layer |
| `src/db/` | Database factory + repository implementations (sqlite/prod/api) |
| `src/validation/` | Zod workflow validation (schema + semantic + resource) |
| `src/facades/` | Slash command to workflow mapping |
| `src/actions/` | Action registry (Python scripts, template resolution) |
| `src/packs/` | Pack discovery and manifest parsing |
| `packs/` | Production workflow packs |
| `tests/` | Test files |

## Development

### Build

```bash
npm install
npm run build           # tshy + post-build scripts (bundle-runtime, copy-assets, generate-facade-skills)
npm run build:all       # Build all targets
```

### Test

```bash
npm test                # node --import tsx --test tests/*.test.ts

# Run a specific test
node --import tsx --test tests/workflow-validation.test.ts
node --import tsx --test tests/controller-node-retry.test.ts
node --import tsx --test tests/platform/openclaw-adapter.test.ts   # Platform adapter tests
node --import tsx --test tests/platform/adapter-to-deps.test.ts    # Bridge function tests
```

### Packaging

```bash
npm run dist:pack                # Pack all platforms
npm run dist:pack:openclaw       # OpenClaw plugin pack only
npm run dist:pack:claudecode     # Claude Code pack only
npm run dist:pack:hermes         # Hermes pack only
npm run dist:pack:teclaw         # TeClaw pack only
```

### Local Sync

Sync build artifacts to local platform installation directories for development:

```bash
npm run sync                     # Sync all platforms (openclaw + claudecode + hermes)
npm run sync:openclaw            # Sync OpenClaw only
npm run sync:claudecode          # Sync Claude Code only
npm run sync:hermes              # Sync Hermes only

# Build then sync (one-step)
npm run sync:all                 # npm run build + dist:pack + sync all platforms
```

## Multi-Platform Adaptation (Phase 1)

### Architecture

Phase 1 introduces a `PlatformAdapter` abstraction layer, decoupling the core engine from platform-specific APIs:

```
Index layer (index.ts)
      |
      v
createOpenClawAdapter()  -->  PlatformAdapter
      |                          |
      v                          v
buildControllerDeps()    -->  ControllerDeps
      |
      v
  Controller
```

### Module Overview

| File | Description |
|------|-------------|
| `src/platform/types.ts` | PlatformAdapter interface + 6 sub-interfaces |
| `src/platform/openclaw-adapter.ts` | OpenClaw adapter factory |
| `src/platform/openclaw-types.ts` | OpenClaw PluginApi types (shared) |
| `src/platform/mcp-adapter.ts` | MCP Server adapter (Claude Code / TeClaw) |
| `src/platform/mcp-entry.ts` | MCP Server entry (stdio transport) |
| `src/platform/mcp-tools.ts` | Shared MCP tool registration |
| `src/platform/mcp-server-factory.ts` | MCP Server shared initialization factory |
| `src/platform/mcp-sampling-agent.ts` | MCP sampling replacement for runEmbeddedPiAgent |
| `src/platform/hermes-adapter.ts` | Hermes adapter (SSE, approval UI, multi-tenant) |
| `src/platform/hermes-entry.ts` | Hermes SSE entry |
| `src/platform/database-taskflow.ts` | Non-OpenClaw TaskFlow implementation (memory/API) |
| `src/platform/default-executor.ts` | Default executor dispatch |
| `src/platform/adapter-to-deps.ts` | PlatformAdapter to ControllerDeps bridge |
| `src/platform/logger.ts` | Platform structured logging |
| `src/dispatch.ts` | Platform-agnostic command dispatch |
| `src/platform/index.ts` | Barrel exports |

### Design Principles

- **PlatformAdapter wraps ControllerDeps construction, not replaces it**
- `executeNode` is not in the adapter — it depends on per-call context (actionRegistry, toolCtx, workflow spec)
- `api` field retained in ControllerDeps as Phase 1 passthrough (Phase 2 will remove)
- `chatInjectFn` injected as callback parameter (DingTalk logic not duplicated)

### Hermes Integration (SSE Transport)

taskguard supports Hermes via MCP SSE transport. Start the Hermes MCP server:

```bash
# Start Hermes MCP server on port 3100
node dist/esm/platform/hermes-entry.js --port 3100
```

Hermes-specific features:
- **SSE push**: Workflow progress events pushed to browser via SSE
- **Approval UI**: `workflow_confirm` routed to Hermes console
- **Multi-tenant**: Session isolation via `tenantId`/`teamId` namespacing

Configuration:
```typescript
import { createHermesServer } from "@avernet/taskguard/platform/hermes-entry";

const { server } = await createHermesServer({ port: 3100 });
```

### Multi-Tenant Support

`DatabaseTaskFlowAdapter` supports `tenantId` for session isolation:

```typescript
const adapter = new DatabaseTaskFlowAdapter({
  sessionKey: "session-1",
  tenantId: "tenant-A",  // Flows stored under "tenant-A:session-1"
  flowRunApiRepo,        // Optional: for API mode persistence
});
```

Flows created with different `tenantId` values are fully isolated — tenant A cannot access tenant B's flow state.

### TeClaw MCP stdio Integration

TeClaw connects to taskguard as an MCP client — **zero Rust code changes needed**.

The deprecated Rust module should be replaced by MCP auto-discovery:
```json
{
  "mcpServers": {
    "taskguard": {
      "command": "node",
      "args": ["dist/esm/platform/mcp-entry.js"],
      "env": { "DATABASE_MODE": "sqlite" }
    }
  }
}
```

### Platform Manifest Files

| File | Platform |
|------|----------|
| `openclaw.plugin.json` | OpenClaw plugin manifest |
| `claudecode.plugin.json` | Claude Code plugin manifest |
| `hermes.plugin.json` | Hermes plugin manifest |
| `teclaw.plugin.json` | TeClaw plugin manifest |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_MODE` | Database mode: sqlite / prod / api | sqlite |
| `SQLITE_PATH` | SQLite database path | `~/.openclaw/workflow/engine.db` |
| `BAAS_API_KEY` | Fallback API key for BaaS integration (used when not set under the `baas:` config section) | (none) |
| `BAAS_BASE_URL` | Fallback base URL for BaaS API (used when not set under the `baas:` config section) | (none) |

> **BaaS credentials** are resolved per call with priority `baas:` config section (local
> `application.yaml`, overridden by clawweb's `cm_app_config` table row `config_key="baas"`)
> → workflow YAML executor fields → environment variables. Never hardcode secrets in workflow YAML.
| `CLAWWEB_BASE_URL` | Base URL for web UI | (none) |

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
