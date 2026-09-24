# Evolve Host integration

The public Clawevolve package owns Stage scheduling, protocol validation, Skill
versions, interactions and UI. A host supplies `BotSkillGateway`,
`SpaceDirectory`, request identity, object storage and optional extensions through
`createClawevolveModule`. Provider clients and policies belong to host-owned adapter modules; the
composition root supplies configuration and assembles their dependencies. `externalSkillId` / `external_skill_id` is an opaque
provider identifier; public code must not interpret it as a provider-specific ID.

## Singlebox and capability discovery

`GET /api/evolve/capabilities` reports `skillManagement` and
`stageCustomization`. The current implementation enables these together only
when a host supplies a Skill port and writable artifact storage. Singlebox does
not currently supply that port: it hides Skill management/custom Stage entries,
blocks their direct page URLs and returns 404 for their API routes. Task creation
also rejects disabled Skill targets and Stage bindings. Native Bot evolution is
unaffected. Adding a provider is a host wiring change, not a second Stage flow.

Runner launch storage is captured by each module's `dispatch` method. Hosts must
use that method for dispatch that belongs to the module; configuring one module
must not change another module's object storage.

## Skill application and recovery

Both manual version application and task candidate acceptance reserve a durable
application intent before calling the host's replace operation. Package bytes
and their digest are frozen in object storage; a different operation cannot
overtake an unfinished one. Version allocation, asset update, audit and intent
completion use a database transaction.

On a lost upload response, the service reads back the live package and compares
its file content with the candidate. It only treats a proven match as success.
Retrying the same operation recovers an upload followed by a database failure;
it must not create a second version. An unresolved external result keeps the
intent for the same operation to retry. This is not a distributed transaction
with the provider, nor a guarantee against changes made outside the platform.

## Business Agent protocol feedback

Before releasing the business Agent, the executor calls the read-only
`validate-output` endpoint for the current task/Step. That endpoint reuses the
report parser for interaction envelopes and form fields, and enforces the
feedback Loop restriction. A 422 protocol rejection is returned to the same
Agent/session through the existing bounded correction turn. Transport failures
are not treated as business mistakes. Preflight neither creates interactions
nor advances Steps; the report endpoint remains authoritative for Stage result
validation and state transitions.

Business Skills still own business decisions. This does not change their logic,
the native Stage chain, or pre/post Stage result contracts.

## Database delivery

See [the v135 schema delivery](evolve-schema-v135/README.md) for first install,
existing database upgrades, deployment order and rollback constraints.

## Execution environment selection

The host may inject `resolveExecutionOptions` to select a transport and Runner
environment per target. Internal local Bots use Message; online targets retain
their provider transport. Public dispatch does not interpret internal `dev`.
The shared Runner invokes `RunnerEnvironment` on the target Bot. Its public
local and container implementations own paths and environment preparation.
Singlebox keeps its existing direct process execution; it does not use the
internal Message adapter. Stage scheduling and business contracts are shared.

## Database-backed Skill task defaults

`ce_app_config` stores generic JSON values under unique `config_key` entries.
Its repository validates JSON syntax only. The Stage-binding consumer validates
`skill_task_stage_bindings` using this schema (space IDs remain opaque; Stage IDs reference development record primary keys):

```json
{
  "bindings": [
    {
      "spaceType": "TEAM",
      "spaceId": "space-example",
      "action": "optimize",
      "stage": "diagnose",
      "mode": "preprocess",
      "stageSkillId": "1"
    }
  ]
}
```

The six fields are required; unknown fields and duplicate positions are rejected.
`spaceType` is TEAM or PERSONAL. `action` is diagnose, hardening or optimize.
Stages and modes must match the existing official catalog and task flow. Multiple
positions can be bound; one position has at most one configured Stage Skill.
The lookup matches the target asset's stored space and action, then chooses the
latest registered, integration-tested, accessible implementation in that same
space with the exact Stage Skill, stage and mode. If any matched position has no
eligible implementation, the action returns an unavailable reason without a
partial default. No name, provider-specific ID format or particular space is
special-cased. An invalid enabled row makes the defaults endpoint return 503;
an absent/disabled row contributes no defaults.

The existing authenticated defaults route reads the row on each request, after
checking asset access. Existing host extensions retain their conflict detection:
two matching contributors are an error, not an undocumented precedence order.
These are launch defaults, not an authorization policy or mandatory server-side
binding. Task creation continues to validate and freeze the selected versions.
Config updates do not rewrite existing tasks or override explicit user choices.
Singlebox capability gating and native Bot evolution remain unchanged.

The internal Host no longer reads YAML `spacePolicies`; binding configuration is
owned by Clawevolve. Manage it through the [configuration API](./evolve-app-config.md)
or the environment's controlled database configuration process. All configuration
API reads and writes require the host's existing administrator authorization;
the asset-scoped task-defaults endpoint retains its existing access checks.
