# Waiver: Hermes Service Build Layout Compatibility Adapter

- Status: active, approval required from the Manager or architecture owner
- Owner: `@FreddieSun`
- Review/expiry date: 2026-09-30
- Removal issue: [#1700](https://github.com/inclusionAI/Avernet/issues/1700)
- Related delivery: [#1177](https://github.com/inclusionAI/Avernet/issues/1177)

## Rule and scope

This waiver records a temporary conflict under `docs/arch/arch.rules.md` Rule
18 and the Governance Addendum. It waives only:

- repository `AGENTS.md`, Skills Architecture bullet 2; and
- `src/backend/src/agentclaw/community/adapters/http/skill_center/CLAUDE.md`,
  section 9, Engine physical-layout ownership,

for `WorkspaceConfig.hermes_root` and the Hermes service-build compatibility
adapter's build, session, MCP-config, rsync, and read-only paths.

It does not waive Center corpus paths. `pool_center` must continue to come only
from the versioned Engine Runtime layout evidence, be frozen into the service
artifact, and be consumed from that historical artifact. It does not authorize
a fifth static filesystem provider or any additional path field.

## Reason and alternatives

Issue #1177 must deliver a working Hermes Service Artifact consumer. The
existing Backend `EngineSandboxProvider` contract still owns the complete
service-build plan for OpenClaw, Claude Code, and AICoding, while the current
Engine layout evidence owns only Skills layout. Moving Hermes alone would leave
an asymmetric contract; moving all four providers to a complete versioned
Engine build plan would materially expand this Group.

The compliant target is a versioned Engine-owned
`ResolvedEngineServiceBuildPlan`, consumed without Backend path inference. The
temporary exception is the Hermes compatibility provider guarded below.

## Risk

Backend and Engine can drift on physical paths. Drift can copy sessions or
secrets, miss a shared-corpus exclusion, apply the wrong read-only rules, or
produce an artifact that cannot start.

## Compensating controls

1. Hermes Service build requires matching `READY` Engine layout evidence and a
   terminal Pool layout; missing, unknown, or mismatched evidence fails closed.
2. `hermes_service_build_layout_v1.json` is an Engine-owned golden contract
   consumed by Backend and Engine conformance tests. It covers engine root,
   sessions, MCP config, active Skills, Pool Repo/Center, and read-only roots.
3. A missing Hermes provider never falls back to OpenClaw.
4. Artifact tests enforce dynamic Center corpus exclusion, exact symlink and
   manifest agreement, exact Store verification, independent read-only mount,
   and frozen release/restart/rollback replay.
5. This waiver permits no new filesystem-path fields.

## Removal plan

Issue #1700 moves all four filesystem engines to the Engine-owned versioned
service-build plan, adds full cross-component conformance, removes static
Backend engine roots/providers, and deletes this waiver by the review date.
