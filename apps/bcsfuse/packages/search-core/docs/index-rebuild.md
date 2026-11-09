# Shared index rebuild

`search-core` owns source repair, durable generation builds, immutable version
configuration, progress, retry and expired-owner continuation. Applications supply
their source data and document conversion. The shared implementation does not
import TC, Alipay, a platform SDK or an application Card model.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `core/index_builds.py` | Build execution, completion checks, retry/continuation and explicit completed-version selection |
| `core/source_rebuild.py` | Force preparation of one source, renewable ownership, guarded atomic publication and sanitized outcome logs |
| `config/index_builds.py` | Model, dimension, conversion version and filter fields bound to an immutable `index_key` |
| `providers/sql_index_builds.py` | Environment-scoped SQL task records using an injected DB-API connection factory |
| `adapters/http/index_builds.py` | Optional create/retry/status routes using an injected operator authenticator |
| Application | Read source revisions, convert business data into Docs, provide the atomic source/Doc publisher, and assemble model clients and runtime indexes |

## Business adapter

`IndexBuildService` takes an `IndexBuildSource`, an `IndexBuildRepository`, a model
factory, `conversion_version` and `filter_fields`. The source interface has three
operations:

- `page(after, limit)`: sorted `(source_id, revision, deleted)` tuples, including deletion records. `deleted` is operational state, not a business Card field.
- `prepared_revision(index_key, source_id)`: the source revision already published
  in this generation, or `None` when no artifact exists.
- `rebuild(source_id, config, model, lease_seconds)`: prepare and atomically publish
  this source's document group. Raise `IndexBuildConflict` for a changed source or
  competing producer; propagate all other errors. Never acknowledge a failed write.

The service reuses matching published revisions and verifies current source
revisions before marking a build completed. The model factory is a context manager
receiving model name and dimension; it owns model-client cleanup. Configuration
models use Pydantic, available through the package's `models` extra.

`total_count` counts nondeleted sources in the build pass; `completed_count`
counts those with a matching published source revision. Final verification
refreshes both counts, excluding sources deleted during the build. Tombstones
are still processed and must be current before a task can complete: an all-deleted
source set finishes as `completed` with `0/0`, without embedding deleted sources.
The counts are saved task progress, not current Qdrant inventory. Completed tasks
are not recounted after later source changes. Previously completed task records
retain their historical counts; this counting rule applies to subsequent build runs.
Hosts implementing `IndexBuildSource.page` must provide the operational deletion
flag along with each ID and revision.

Generation builds should rerun business conversion and reuse vectors only when
the model/dimension key and resulting search-text hash match a valid stored
vector. `IndexDocuments.load_vector_candidates(source_id, embedding_key)` reads
nondeleted candidates across generations in the same environment, newest first;
the same Doc ID can appear more than once. `catalog/documents.py` selects an exact
text match, including older candidates when a newer one has different text. This
lookup is only for embedding reuse, never index recovery or search. Filter labels
and rerank text are regenerated even when the vector is reused. A filter-only
generation still builds and stores new Docs, but does not call embedding for
documents with matching cached vectors. Missing/invalid vectors or changed
text/model/dimension require embedding. Explicit single-source repair may bypass
reuse to repair a bad cached vector.

`rebuild_source` is also usable directly for a single-source repair. Supply a
`read_source(id)` callback returning a record with `revision`, and a
`prepare_documents(record)` callback that deliberately bypasses artifacts being
repaired. A missing source returns `None` and produces `KeyError`. Its publisher
provides `claim_preparation`, `renew_preparation`, `release_preparation` and
`publish_documents`: publication must check source revision and unexpired ownership
in the same transaction as writing the whole document group. The core never holds
a database transaction open while conversion/model work runs. Successful repair
returns `published`, `index_key`, and `card_version`; publication does not mean
every serving replica has applied it.

## Persistence and activation

The SQL task table remains `bcsfuse_index_builds`, with the existing
`(environment, build_id)` identity and serialized configuration. `SQLITE_SCHEMA`
is available for local host initialization; production provisioning belongs to
the deployment. No additional columns or tables are needed for vector reuse.

`IndexBuildConfig.index_key` is exactly `build_id`, not a configuration hash.
Names are 1–64 characters, starting with an ASCII letter/digit and then allowing
letters, digits, `_`, `-` and `.`. Configuration is immutable within an environment.
Changing a name isolates artifacts; it does not force new embedding calls.

`default(model, dimension)` creates the fixed `default` record on first use and
freezes code-selected conversion/version and filter defaults. Later calls use the
saved configuration, even if deployment defaults have changed. Incompatible
conversion code fails explicitly. Hosts treating absent/empty `active_index` as
`default` report both effective `active_index` and `index_key` as `default`.
The first registration has status `configured`: configuration is saved, but no
build task is scheduled. Repeated selection does not retry a failed build. Only
an explicit create/retry request queues a configured or failed record as `pending`.
The repository's `create(config, enqueue=False)` registers configuration without
changing existing task state; its default `enqueue=True` preserves explicit build
creation/retry behavior. This requires provisioning the build table before
upgrading these hosts.

Startup restores saved Docs into Qdrant without conversion or embedding. Readiness
becomes true after restoration and successful index checks, including when the
saved index is empty. It does not promise every Card is already indexed. The
existing background revision scan prepares missing/stale Cards and retries failed
preparation; restarting retains this recovery through persisted source revisions.
If every Doc is missing, background preparation processes every Card, but readiness
does not wait for it. Failed Qdrant application still prevents readiness.

Old hash-scoped Docs remain separate from `default`. The normal background scan
can populate missing `default` Docs and reuse matching vectors from old generations;
there is no startup migration or automatic full-build job. A missing or invalid
vector is regenerated in background preparation. Old Docs and collections remain
intact. The legacy hash helper stays available only for older direct library
callers; explicit version paths never call it. TC's composition is unchanged.

HTTP creation requires model and dimension; optional `filter_fields` selects the
version's filtering rules. Omission uses application defaults for a new version,
or saved filters for a retry. The application may add mandatory business fields.
GET/PUT responses expose the version name once as `index_key`, plus resolved
`conversion_version` and `filter_fields`. They do not repeat a `build_id` field.
For `configured` records, no build has run, so `completed_count` and `total_count`
are omitted; queued/running/finished tasks return their recorded counts.
There is no builder registry or executable rule JSON: hosts bind their built-in
converter, and the system records its version. New rules require matching code.
Incremental processing uses the selected version's persisted model/filters and
the compatible converter, not changing deployment defaults.

A build writes independent Doc artifacts. It does not change any running query
model or Qdrant collection. `select(build_id)` only accepts completed builds with
the configured conversion version; the host binds model, dimension and collection
at startup and uses existing restoration/readiness before accepting traffic.
Source updates after completion are reconciled by normal background sync.
No task, HTTP route, cleanup or background thread starts merely by importing these
modules. The host starts build polling explicitly. Old artifacts are not deleted.

When applying a Card Doc to Qdrant, the shared synchronizer adds `index_key` to
the point payload. Dense and keyword collections receive the same value on both
incremental writes and recovery. This is system metadata outside the business
Card and embedding text; it cannot be a business filter field. It identifies
ownership, not global activation: compare it with the responding instance's
`/ready.index_key`. No per-point `active` flag is maintained.

## TC compatibility

TC's current tables, recommend/Fuse entrypoints, model defaults and task lifecycle
remain unchanged. To adopt this capability later, TC supplies its own source
adapter and conversion/publisher rather than importing an Alipay implementation.
The HTTP create/retry/status contract remains `PUT /api/v2/index-builds/{build_id}`
and `GET /api/v2/index-builds/{build_id}`; it is mounted only by opting-in hosts.
