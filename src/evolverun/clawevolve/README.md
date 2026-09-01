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
```

The first extraction contains only command helpers that have no dependency on private infrastructure. Command YAML policy that contains host-specific system arguments remains in the private host until a public contract can be extracted without changing behavior.

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
