# AgentEvolve web workspace

This app assembles the public AgentEvolve web/server packages. Historical
`clawweb` paths are retained for compatibility. Start with the repository's
[AgentEvolve guide](../../../docs/agent-evolve.md) for setup, configuration,
launching, and product boundaries.

## Source and verification

- `public/modules/workflow`: workflow APIs, persistence, and web UI.
- `public/shared`: shared server/web contracts and infrastructure.
- `public/app` and other `public/modules` packages: app composition and features.
- [CI and local validation](scripts/ci/README.md): public dependency installation,
  package build/check, and test entry points. Package exports normally use `dist`;
  the timeout policy documents a source-only server regression command.

## Workflow safety

- [Workflow timeout write policy](docs/workflow-timeout-write-policy.md): legacy
  watchdog validation, atomic write fences, failure-settlement compatibility,
  observer idempotence, and the remaining non-owner/native-store limits.
- [Rolling-release safety](docs/rolling-release-safety.md): static asset delivery
  and shutdown behavior; not a timeout authorization policy.
