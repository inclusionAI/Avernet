# @avernet/clawevolve

Public ClawEvolve core for Avernet.

This package is being extracted from an existing hosted implementation without redesigning its business behavior. Migrated files keep their original names where practical, while private hosting, storage, authentication, and deployment details remain outside this package.

## Current public surface

```ts
import {
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

The public registry contains only ClawEvolve task types. Host-only task types and integrations remain outside the package. Public command templates use placeholders; a host can provide its own reviewed command defaults through `createEvolveNodeRegistry()`.

Command YAML policy that contains host-specific system arguments remains in the private host until a public contract can be extracted without changing behavior.

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
