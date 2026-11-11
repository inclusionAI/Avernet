# Shared index rebuild

## Scope and participants

The application supplies source revisions, business-to-Doc conversion and publication; `search-core` owns build state, source repair, retries and explicit selection of a completed version. Enterprise adapters and deployment remain host-owned.

## Path and state

1. Register immutable model, dimension, conversion and filter settings under an environment-scoped `index_key`; configuration alone does not enqueue a build.
2. An explicit create or retry queues work. The service pages source revisions, prepares and publishes document groups, and checks revisions before completion. A publisher must guard revision and lease ownership atomically.
3. Completion and active selection are distinct. Saved progress counts describe the recorded build, not current vector-store inventory; publication does not prove every serving replica has applied it.

## Evidence and limitations

The [index rebuild contract](../../packages/search-core/docs/index-rebuild.md) owns complete behavior, including deletions, reuse and activation. Compare [provider contracts](../../packages/search-core/docs/provider-contracts.md) and the target application's assembly with the relevant tests and actual runtime evidence before concluding an index is current.
