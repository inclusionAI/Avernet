# Index not current

Record the environment, source identity and revision, selected `index_key`, build ID, model/dimension and observed request time. Preserve actual build and serving evidence separately.

| Observation | Distinguishing evidence | Next place to inspect |
| --- | --- | --- |
| Configuration exists but no work starts | Was an explicit create or retry enqueued? | [Index rebuild](../../packages/search-core/docs/index-rebuild.md): registration alone does not enqueue. |
| Build is incomplete or conflicting | Task status, source revision, lease and per-source result | [Index rebuild](../../packages/search-core/docs/index-rebuild.md) and application publisher. |
| Build completed but search remains stale | Explicit active version and serving replica application, not only build counts | [Index rebuild](../../packages/search-core/docs/index-rebuild.md) and [composition contract](../architecture/composition-contract.md). |
| Vector persistence differs | Actual provider, environment and persistence mode | [Vector contract](../architecture/vector-persistence-contract-v1.md) and [Qdrant modes](../qdrant-modes.md). |

Task progress is not current vector inventory, and successful publication does not prove every replica applied it. Investigate the selected host and revision; do not infer a root cause from a single status.
