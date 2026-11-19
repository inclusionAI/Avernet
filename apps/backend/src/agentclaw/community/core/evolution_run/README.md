# evolution_run — compatibility imports only

Run orchestration, storage, Worker protocol and SDK are implemented once in
[apps/evolution](../../../../../../evolution/src/avernet_evolution/core/evolution_run/README.md). New code imports
`avernet_evolution.core.evolution_run`; these old paths re-export canonical
modules/classes and must not acquire domain logic.

## What remains in Backend

- Bot control ingress reuses the existing system identity and Bot permissions.
- Genome and Promotion remain platform-owned under ADR 0019/0020/0021.
- The Genome adapter moved to `plugins/evolution/genome_registry.py`; its former
  core path was removed (core must not import concrete platform plugins).
- The legacy composite builder moved to
  [explicit acceptance assembly](../../di/evolution_acceptance/README.md). The old `app.py` and lazy
  `EvolutionRuntime`/`build_evolution_runtime` exports remain compatibility-only.
  Normal core imports do not load acceptance assembly.

Backend startup no longer installs the legacy Jobs/Runtime modules, owns a Run
store or mounts Worker routes. `EVOLUTION_CONSUMER_ENABLED` no longer enables
execution in the default Backend application. Worker protocol builders are
`avernet_evolution.adapters.jobs`; the unused old Backend router is removed.
No public Bot control path, Genome or Promotion contract is removed.

## Verification and observation

- Neutral unit/Worker HTTP contracts: `apps/evolution/tests/unit/`,
  `apps/evolution/tests/contracts/`.
- Platform adapters, Registry/Engine integration and explicit legacy assembly:
  Backend's existing RSI platform tests and the linked acceptance guide.
- Read-only event monitor: `apps/evolution/scripts/evolution_events.py`.
- Independent startup, restart semantics and deployment caveats:
  [Evolution app guide](../../../../../../evolution/README.md).

01–06 completion does not imply 07 evaluation, 08 promotion or actual page
acceptance. Source cleanup is not a deployment or successful real Bot run.
