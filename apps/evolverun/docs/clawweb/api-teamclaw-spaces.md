# Space-scoped evolution APIs

These are explicit adapters over the existing evolution and Bench business handlers. Legacy ClawWeb and Singlebox URLs retain their existing authorization and behavior.

## Entry points

| Client prefix | ClawWeb prefix | Resources |
| --- | --- | --- |
| `/openapi/v1/clawevolve/teamclaw` | `/api/evolve/teamclaw` | Bots, tasks, models, versions, Packs, task logs and Step operations |
| `/openapi/v1/clawevolve/bench/teamclaw` | `/api/bench/teamclaw` | Domains, templates, publication/uploads, Runs, results, sessions and artifacts |

Every request requires an explicit `space_id` query parameter and a Host-authenticated identity. `X-User-Id` is a Host integration contract, not a substitute for authenticating a caller. The Host must verify its inbound authentication before mounting these adapters.

The adapter resolves live membership through `SpaceDirectory`. `ADMIN` and `MEMBER` have the same resource permissions here. A resource's author and a task's creator do not confer membership; an existing global administrator flag does not bypass this check. Missing or unavailable space dependencies never select legacy authorization.

## Resource identity

- Tasks and Bot environment versions are authorized through the Bot's current `(Owner, Bot ID, environment)` reference. Tasks do not store a copied space ID. Missing historical environments are accepted only when the Host proves the runtime reference unambiguous.
- Domains use `(ownerUserId, domainId)`. Templates, their versions, Runs and Session artifacts inherit the Domain's space. A Run/artifact identifier alone is not authority to access it.
- Historical Domains with no `space_id` remain in their original author's personal space. An explicit space assignment takes precedence. Moving a Bot does not move an independent Domain.
- Task lists keep the five primary types: `diagnose`, `bench`, `bench_optimize`, `optimize`, `full`. Existing Bot evolution versions are not discarded solely because their historical source type has no creation form. Such sources remain read-only here. Skill targets are excluded.
- Pack restore retains the original registered-source, target compatibility and execution-state checks. Space membership does not override them.

## Creation and execution references

Payloads otherwise reuse their existing business contracts:

| Operation | Resource reference separate from Bot `userId` |
| --- | --- |
| Bench diagnosis | `benchDomainOwnerUserId`, `benchDomainId` |
| Bench optimization | `trainBenchOwnerUserId`, `trainBenchDomainId`, `testBenchOwnerUserId`, `testBenchDomainId` |

The request's `userId` remains the Bot runtime Owner; the authenticated actor remains the audit identity. Neither is replaced with a Bench author. Omitted resource Owners preserve the original single-Owner interpretation.

For scoped Bench optimization, the existing `config_json.pinnedBenchDomains` map is keyed by the JSON-encoded `[Owner, Domain]` pair. Workflow handlers use per-role resource Owners for frozen templates, Baselines and candidate metrics; reports are checked against those same references. Optional Optimize CLI arguments are `--train-bench-owner-id` and `--test-bench-owner-id`. The original Bot execution identity is unchanged.

Only the server-controlled creation adapter writes `entryPoint: "teamclaw"` in the existing task config. Skill-generated Domain creation continues through the original callback channel and [source-context contract](api-generated-bench-domain.md). Browser Domain creation cannot supply that Skill context. No browser cookie, Gateway URL or space ID is added to a Skill command.

## Responses and failures

Existing response shapes are retained, including `{items}` for versions and Packs. Task filtering occurs before counts/pagination; version aggregation still uses registered Packs and completed Optimize reports. A reported artifact is not equivalent to an accepted, registered version or a deployed Bot.

Missing parameters return 400; missing identity 401; missing membership 403; foreign/missing resources or mismatched parent identifiers 404. Unsupported operations and original business validations retain 422/state errors. Host space-service failures are returned as safe non-success responses, without retrying under Owner permissions or exposing upstream details.

Only `cm_bench_domains.space_id` is added to the database. Resource Owner fields and the entry-point marker above are contents of the existing config JSON, not additional schema columns.
