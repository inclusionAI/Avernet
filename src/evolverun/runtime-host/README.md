# @avernet/runtime-host

Minimal public Node.js host for composable Avernet HTTP modules.

The host owns only process-level concerns: module registration, lifecycle,
health/readiness, optional static assets, listening, signal handling, and
graceful shutdown. Product policy, private authentication, deployment, and
environment-specific infrastructure stay outside this package.

## Context Boundary

```yaml
purpose: Start and supervise public Avernet modules without owning domain policy.
provides:
  - RuntimeModule
  - RuntimeHost
  - createRuntimeHost
  - startStandaloneHost
consumes:
  - Express Router
  - RuntimeModule lifecycle hooks
internal_dependencies: []
```

### Change impact

Contract changes affect every module mounted in this host and require matching
compatibility tests. Internal composition roots may wrap this package, but the
package must remain runnable without private services or configuration.

## Example

```ts
import { startStandaloneHost } from "@avernet/runtime-host";

const host = await startStandaloneHost({
  modules: [module],
  port: 3001,
});

await host.stop();
```
