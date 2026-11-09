# bcn-runtime-model

Neutral versioned model contracts.

See [BCN runtime contracts v1](../../../../../docs/architecture/bcn-runtime.md)
for lifecycle, limits, compatibility, isolation and composition boundaries.

Run focused tests with `cargo test -p bcn-runtime-model` from `apps/bcs`.

Repository integration adds CommittedEffectRef, deterministic typed
`effect_command_id`, logical `session_shard`, admission TaskMetadata,
ConnectorProjection and required DispatchContext. Recovery records retain
command/session/task/node routing without parsing hashed identifiers or retaining
a live command map. Task-global event epochs differ from dispatch ownership
epochs. Projection inputs cannot supply status or admission metadata. These are
pre-release v1 corrections; payloads missing required dispatch context must be
explicitly migrated/discarded before writers are enabled. No legacy runtime
payload or session is changed.
