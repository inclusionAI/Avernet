# Diagnostic navigation

Start with [Index Not Current](index-not-current.md) when applicable. Start with the symptom and preserve the target revision, environment and evidence. These routes identify investigation starting points, not proven root causes.

| Symptom | First evidence to distinguish | Existing guidance |
| --- | --- | --- |
| Search or indexing differs from expectation | Identify the selected index version and rebuild or activation stage; compare evidence with the provider contract. | [Reference](../../packages/search-core/docs/index-rebuild.md) |
| Vector data is missing or stale | Identify the configured persistence and synchronization mode before comparing the storage contract and Qdrant guidance. | [Reference](../architecture/vector-persistence-contract-v1.md) |
| Provider or startup configuration fails | Check the selected provider mode, composition and runtime configuration against the target build. | [Reference](../architecture/composition-contract.md) |

Keep credentials, private runtime data and raw sessions out of repository knowledge. Add a topic page only when a supported, reusable diagnosis is missing.

## Record and exclude

Record symptoms, competing causes, distinguishing evidence, next actions and limits. Do not store secrets, raw private incident records or unverified conclusions.

For the wider inventory of existing app documentation and domain context, see the [existing knowledge catalog](../agents/existing-knowledge.md). Check each entry's status and target version.
