# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-01-15

### Added
- YAML-defined DAG workflow with `dependsOn` + `triggerRule` semantics
- Node executors: embedded-agent, subagent, cli-script, mcp-call, baas-call, bcs-route, bcs-approval-batch, human-wait, subworkflow, collaboration, action, done, loop-group
- TaskFlow persistence: SQLite (dev) / MySQL (prod) / API mode (decoupled)
- Workflow packs via `workflow.pack.yaml` manifests with facade slash commands
- PlatformAdapter abstraction layer: OpenClaw / Claude Code / Hermes / TeClaw
- Zod workflow validation (schema + semantic + resource)
- Scheduler with cron-based polling and missed-fire policy
- Alerting with DingTalk webhook integration
- Multi-tenant support via `tenantId` namespacing
