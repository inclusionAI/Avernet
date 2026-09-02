# @avernet/clawevolve

Public ClawEvolve core for Avernet.

This package is being extracted from an existing hosted implementation without redesigning its business behavior. Migrated files keep their original names where practical, while private hosting, storage, authentication, and deployment details remain outside this package.

## Current public surface

```ts
import {
  parseNodeCommandYamls,
  normalizeEvolutionGoal,
  quoteCommandArgument,
  renderCommand,
} from "@avernet/clawevolve/server/services/evolve/command";

import {
  createEvolveNodeRegistry,
  EVOLVE_TASK_REGISTRY,
} from "@avernet/clawevolve/server/services/evolve/task-registry";

import {
  createTaskDefinitionsResponse,
} from "@avernet/clawevolve/server/routes/evolve";

import {
  createClawevolveModule,
} from "@avernet/clawevolve/server/create-module";

import {
  createClawevolveRuntimeModule,
} from "@avernet/clawevolve/runtime";

import {
  dispatchPendingBusinessStep,
  startInitialEvolveStep,
} from "@avernet/clawevolve/server/services/evolve/task-start";

import {
  startRunAnalysisTimeoutSweeper,
} from "@avernet/clawevolve/server/services/evolve/run-analysis-timeout";

import {
  TaskSourceService,
} from "@avernet/clawevolve/server/services/evolve/task-source-service";

import {
  buildInsightPlanSource,
} from "@avernet/clawevolve/server/services/evolve/adapters/insight-plan-source-adapter";
```

The module mounts in the host's existing Express process:

```ts
app.use("/api/evolve", createClawevolveModule());
```

Hosts that support module lifecycle registration use the stable runtime
descriptor instead. Existing hosts can continue mounting the router while they
adopt the lifecycle contract.

```ts
const module = createClawevolveRuntimeModule();
await module.migrate();
await module.start();
app.use(module.apiBasePath, module.router);
```

The public registry contains the complete ClawEvolve task and step type set,
including repair, suggestion application, and run analysis. Deployment-specific
task types and integrations remain outside the package. Public command templates
use placeholders; a host can provide its own reviewed command defaults through
`createEvolveNodeRegistry()`.

Command YAML validation, initial step dispatch, and run-analysis timeout handling
are part of this package. Concrete repositories and dispatch transports are
supplied through the public ports; the package does not import a hosting
application's source tree.

Insight-driven evolution uses a minimal handoff contract rather than importing
the full Insight implementation. `TaskSourceService` freezes source references,
validates evidence integrity, and resolves a versioned Plan Source through an
injected repository and evidence reader. Deployment-specific producer metadata
can be injected without placing hosting details in this package.

## Development

```bash
npm ci
npm run check
npm test
npm run build
npm pack
```

## Boundary

The package must not import private host source or contain private service URLs, credentials, storage locations, internal SDKs, or deployment configuration.
